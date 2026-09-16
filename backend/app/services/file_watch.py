"""Module 6 extension §4.3/§5.3/§6.3 — file watcher core: scrutation, completeness,
deduplication, triggering the real M6 reimport on a genuinely new/complete file with a safe-
failure drift guard, and (Étape 3) an arrival SLA check + incident notification. No file is
ever handed to Airflow (§0/§2 invariant, inherited unchanged from Module 6).

Deliberately fully synchronous (matches file_import.py's own style): the only async entry
point is watch_loop() in app/main.py, which offloads the whole tick to a thread via
run_in_threadpool so a slow scan (e.g. a stable_size delay, or a real import) never blocks the
event loop / the rest of the app's request handling.
"""
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models.file_import import FileImport
from app.models.file_watch import CompletenessStrategy, FileWatch, FileWatchEvent, FileWatchStatus, PostProcessMode, WatchOutcome, WatchSeverity
from app.services import file_import as file_import_service
from app.services import watch_notify
from app.services import watch_transport
from app.services.watch_transport import WatchTransportError

logger = logging.getLogger("app.file_watch")

# §6.3 — arrival SLA: how far back/forward the brute-force cron search looks for a matching
# minute. No croniter/cron dependency (§1 "aucune dépendance nouvelle") — cron has 1-minute
# granularity, so a bounded minute-by-minute scan is simple, correct, and fast enough for a
# background loop (tens of thousands of cheap field comparisons, not I/O). 35 days covers daily
# and weekly schedules (the ones the drawer's own presets — "chaque jour avant HH:MM" — produce)
# and monthly ones; anything rarer is a disclosed limitation, not silently wrong.
_CRON_SEARCH_DAYS = 35


def _cron_field_matches(field: str, value: int, field_min: int) -> bool:
    """`field_min` matters for a bare `*/N`: minute/hour/day-of-week start at 0, but
    day-of-month and month start at 1 — `*/2` on month means 1,3,5,7,9,11, not 0,2,4... which
    isn't even a valid month. Getting this wrong silently shifts every step-based month/dom
    schedule by one, so it's passed explicitly rather than assumed."""
    for part in field.split(","):
        if part == "*":
            return True
        if "/" in part:
            base, _, step_s = part.partition("/")
            step = int(step_s)
            if base == "*":
                start, end = field_min, None
            elif "-" in base:
                start, end = map(int, base.split("-"))
            else:
                start, end = int(base), None
            if value < start or (end is not None and value > end):
                continue
            if (value - start) % step == 0:
                return True
        elif "-" in part:
            lo, hi = map(int, part.split("-"))
            if lo <= value <= hi:
                return True
        else:
            if int(part) == value:
                return True
    return False


def validate_cron_expr(cron_expr: str) -> None:
    """§6.6 — creation/edit-time validation: a malformed value would never raise later (a bad
    field just never matches, _last_cron_slot/_next_cron_slot silently return None after a
    35-day scan), so garbage would otherwise be accepted silently and simply never fire."""
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"Cron invalide : « {cron_expr} » — 5 champs attendus (minute heure jour mois jour_semaine), {len(parts)} trouvé(s).")
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    for field, (lo, hi) in zip(parts, ranges):
        for part in field.split(","):
            base = part.split("/")[0]
            for sub in ([base] if base == "*" else base.split("-")):
                if sub == "*":
                    continue
                if not sub.lstrip("-").isdigit() or not (lo <= int(sub) <= hi):
                    raise ValueError(f"Cron invalide : valeur « {sub} » hors de la plage [{lo}, {hi}] dans « {cron_expr} ».")


def _cron_matches(cron_expr: str, dt: datetime) -> bool:
    parts = cron_expr.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    cron_dow = (dt.weekday() + 1) % 7  # Python Mon=0..Sun=6 -> cron Sun=0..Sat=6
    return (
        _cron_field_matches(minute, dt.minute, 0) and _cron_field_matches(hour, dt.hour, 0)
        and _cron_field_matches(dom, dt.day, 1) and _cron_field_matches(month, dt.month, 1)
        and _cron_field_matches(dow, cron_dow, 0)
    )


def _last_cron_slot(cron_expr: str, before: datetime) -> datetime | None:
    candidate = before.replace(second=0, microsecond=0)
    limit = candidate - timedelta(days=_CRON_SEARCH_DAYS)
    while candidate >= limit:
        if _cron_matches(cron_expr, candidate):
            return candidate
        candidate -= timedelta(minutes=1)
    return None


def _next_cron_slot(cron_expr: str, after: datetime) -> datetime | None:
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate + timedelta(days=_CRON_SEARCH_DAYS)
    while candidate <= limit:
        if _cron_matches(cron_expr, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    return None


def check_sla(db: Session, watch: FileWatch) -> FileWatchEvent | None:
    """§6.3 — independent check, same tick as the file scan: at the last expected slot + grace
    period, has anything been imported since that slot? One absent_sla event per slot, ever
    (§6.3 anti-bruit) — enforced the same way as a real file's dedup (Decision F): a
    deterministic pseudo-checksum keyed on the slot itself, so re-evaluating the same slot on
    every subsequent tick hits the (watch_id, file_checksum) unique constraint and inserts
    nothing new."""
    if not watch.arrival_cron or watch.arrival_grace_minutes is None:
        return None
    now = datetime.now(timezone.utc)
    last_slot = _last_cron_slot(watch.arrival_cron, now)
    if last_slot is None:
        return None
    check_time = last_slot + timedelta(minutes=watch.arrival_grace_minutes)
    if now < check_time:
        return None  # still inside the grace window — nothing to report yet

    arrived = (
        db.query(FileWatchEvent.id)
        .filter(FileWatchEvent.watch_id == watch.id, FileWatchEvent.outcome == WatchOutcome.imported, FileWatchEvent.detected_at >= last_slot)
        .first()
    )
    if arrived is not None:
        return None  # on time — no event for a nominal slot (§6.7 DoD #6)

    pseudo_checksum = _checksum(f"__absent_sla__:{watch.id}:{last_slot.isoformat()}".encode())
    if _event_exists(db, watch.id, pseudo_checksum):
        return None  # already alerted for this exact slot

    event = FileWatchEvent(
        watch_id=watch.id, file_name="", file_checksum=pseudo_checksum, file_size=0,
        outcome=WatchOutcome.absent_sla, severity=WatchSeverity.critical,
        error=f"Aucun fichier importé depuis le créneau attendu du {last_slot.strftime('%d/%m/%Y %H:%M')} UTC.",
    )
    db.add(event)
    db.commit()
    return event


def sla_view(db: Session, watch: FileWatch) -> dict:
    """§6.6 — the drawer/detail's own "prochaine arrivée attendue" + "état SLA" fields, derived
    read-only from arrival_cron at read time; never stored (same "computed at read time"
    discipline as Module 16's composite score). "late" means: the last expected slot has
    already passed its grace period and nothing has been imported since — the exact same
    condition check_sla itself uses to decide whether to log an absent_sla event, just without
    writing one (a GET request is never the place to mutate state)."""
    if not watch.arrival_cron or watch.arrival_grace_minutes is None:
        return {"next_expected_arrival": None, "sla_state": None}
    now = datetime.now(timezone.utc)
    next_slot = _next_cron_slot(watch.arrival_cron, now)
    last_slot = _last_cron_slot(watch.arrival_cron, now)
    state = "on_time"
    if last_slot is not None:
        arrived = (
            db.query(FileWatchEvent.id)
            .filter(FileWatchEvent.watch_id == watch.id, FileWatchEvent.outcome == WatchOutcome.imported, FileWatchEvent.detected_at >= last_slot)
            .first()
        )
        if arrived is None and now >= last_slot + timedelta(minutes=watch.arrival_grace_minutes):
            state = "late"
    return {"next_expected_arrival": next_slot.isoformat() if next_slot else None, "sla_state": state}


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _event_exists(db: Session, watch_id: int, checksum: str) -> bool:
    return (
        db.query(FileWatchEvent.id)
        .filter(FileWatchEvent.watch_id == watch_id, FileWatchEvent.file_checksum == checksum)
        .first()
        is not None
    )


def _is_complete(transport, ref: "watch_transport.FileRef", watch: FileWatch) -> bool:
    if watch.completeness_strategy == CompletenessStrategy.none:
        return True
    if watch.completeness_strategy == CompletenessStrategy.control_file:
        return transport.exists(ref.name + watch.control_file_suffix)
    # stable_size (default) — §4.3 trade-off, assumed: a double-stat separated by a real
    # delay, sequential and bounded, sized for modest per-tenant volumes; control_file is the
    # documented alternative for large/slow transfers (no wait, a clean witness-file verdict).
    size1, _ = transport.stat(ref)
    time.sleep(watch.stable_size_delay_seconds)
    try:
        size2, _ = transport.stat(ref)
    except Exception:
        return False  # disappeared mid-check — not complete; re-evaluated (or simply gone) next tick
    return size1 == size2


def _move_after_outcome(transport, ref: "watch_transport.FileRef", watch: FileWatch, success: bool) -> None:
    """§5.3 step 3 — record_only (default) never moves anything; the checksum-based dedup
    (Decision F) is already sufficient to never reprocess a file, so a move is a convenience,
    not something correctness depends on. A missing target for the outcome at hand is treated
    as "not configured for this path" and silently skipped, not an error — most watches will
    only ever set one of done_target/error_target, not both."""
    if watch.post_process != PostProcessMode.move:
        return
    target = watch.done_target if success else watch.error_target
    if not target:
        return
    try:
        transport.move(ref, target)
    except Exception as exc:
        logger.info("file_watch: post-process move failed for watch %s file %s -> %s: %s", watch.id, ref.name, target, exc)


def scan_watch(db: Session, watch: FileWatch) -> None:
    """One watch, one tick. Never raises past this function — a broken transport/listing
    bumps last_error and is retried next tick; it never takes down poll_due_watches's loop
    over the other watches (§2 "indépendant par indicateur"-style isolation, same discipline
    Module 5's collectors already established)."""
    try:
        transport = watch_transport.build_transport(db, watch.transport, watch.location)
        candidates = transport.list_candidates(watch.pattern, watch.pattern_type)
    except (WatchTransportError, Exception) as exc:
        logger.info("file_watch: scan failed for watch %s (%s): %s", watch.id, watch.name, exc)
        watch.last_error = str(exc)[:2000]
        watch.last_poll_at = datetime.now(timezone.utc)
        db.commit()
        return

    fi = db.get(FileImport, watch.file_import_id)
    if fi is None:
        # FK is CASCADE — this shouldn't happen (the watch row would already be gone with its
        # import), but never crash the loop over a data inconsistency either way.
        watch.last_error = "Import cible introuvable."
        watch.last_poll_at = datetime.now(timezone.utc)
        db.commit()
        return

    settings = get_settings()
    new_events: list[FileWatchEvent] = []  # §6.4 — every notifiable event from THIS tick, sent as one grouped digest per channel at the end

    for ref in candidates:
        try:
            if not _is_complete(transport, ref, watch):
                continue  # §4.2 note — no event for a still-arriving file, re-evaluated next tick
        except Exception as exc:
            logger.info("file_watch: completeness check failed for watch %s file %s: %s", watch.id, ref.name, exc)
            continue

        try:
            content = transport.fetch(ref)
        except Exception as exc:
            # A fetch failure (incl. §2.1's size ceiling) still deserves a real, visible event —
            # unlike a listing/completeness hiccup, this IS a concrete file the watch found and
            # then failed to bring in. There's no real content to hash, but the unique
            # constraint on (watch_id, file_checksum) still applies — a name+size pseudo-
            # checksum keeps two DIFFERENT failing files from colliding, while also meaning a
            # file that keeps failing the same way logs exactly one event, not one per tick
            # forever (same "one row per terminal verdict" spirit as a real checksum).
            logger.warning("file_watch: fetch failed for watch %s file %s: %s", watch.id, ref.name, exc)
            pseudo_checksum = _checksum(f"__fetch_error__:{ref.name}:{ref.size}".encode())
            if not _event_exists(db, watch.id, pseudo_checksum):
                fetch_error_event = FileWatchEvent(
                    watch_id=watch.id, file_name=ref.name, file_checksum=pseudo_checksum, file_size=ref.size,
                    outcome=WatchOutcome.fetch_error, severity=WatchSeverity.critical, error=str(exc)[:2000],
                )
                db.add(fetch_error_event)
                new_events.append(fetch_error_event)
                watch.consecutive_failures += 1
                if watch.consecutive_failures >= settings.watch_max_consecutive_failures:
                    watch.status = FileWatchStatus.error
                    watch.last_error = f"{watch.consecutive_failures} échecs consécutifs — voir le journal pour le détail. Dernier : {str(exc)[:300]}"
                _move_after_outcome(transport, ref, watch, success=False)
            watch.last_file = ref.name
            db.commit()
            if watch.status == FileWatchStatus.error:
                break
            continue

        checksum = _checksum(content)
        if _event_exists(db, watch.id, checksum):
            # Decision F: the (watch_id, file_checksum) unique constraint IS the dedup
            # mechanism — a second row for an already-seen checksum can never be inserted, by
            # design. So "skipped_duplicate" is never itself a persisted row in this path: the
            # journal simply doesn't grow, which is the literal, testable behavior §4.6 DoD #3
            # asks for ("il est ignoré... pas de double détection"). last_file still reflects
            # that this name was seen again.
            watch.last_file = ref.name
            continue

        # §5.2/§5.3 — replay the real M6 contract; never re-invents the import itself.
        result = file_import_service.watch_reimport(db, fi, ref.name, content)

        if result["outcome"] == "imported":
            db.add(FileWatchEvent(
                watch_id=watch.id, file_name=ref.name, file_checksum=checksum, file_size=ref.size,
                outcome=WatchOutcome.imported, rows_imported=result["row_count"], cast_errors=result["cast_errors"],
                file_import_reimport_ref=result["imported_at"],
                severity=WatchSeverity.warning if result["cast_errors"] else WatchSeverity.info,
            ))
            watch.last_triggered_at = datetime.now(timezone.utc)
            watch.consecutive_failures = 0
        elif result["outcome"] == "drift":
            drift_event = FileWatchEvent(
                watch_id=watch.id, file_name=ref.name, file_checksum=checksum, file_size=ref.size,
                outcome=WatchOutcome.drift_rejected, error=result["drift_message"], severity=WatchSeverity.critical,
            )
            db.add(drift_event)
            new_events.append(drift_event)
            watch.consecutive_failures += 1
        else:  # "error" — read/shape/write failures all land here (§4.2 has no dedicated write-error outcome)
            error_event = FileWatchEvent(
                watch_id=watch.id, file_name=ref.name, file_checksum=checksum, file_size=ref.size,
                outcome=WatchOutcome.fetch_error, error=result["error"], severity=WatchSeverity.critical,
            )
            db.add(error_event)
            new_events.append(error_event)
            watch.consecutive_failures += 1

        watch.last_file = ref.name
        if watch.consecutive_failures >= settings.watch_max_consecutive_failures:
            # §5.3 step 4 — self-pause rather than keep hammering a broken location/contract;
            # "Réactiver" (routes/file_watch.py) clears this back to active.
            watch.status = FileWatchStatus.error
            reason = result.get("drift_message") or result.get("error") or "voir le journal pour le détail."
            watch.last_error = f"{watch.consecutive_failures} échecs consécutifs — dernier : {reason[:300]}"
        db.commit()

        _move_after_outcome(transport, ref, watch, success=(result["outcome"] == "imported"))

        if watch.status == FileWatchStatus.error:
            break  # stop processing further candidates this tick — matches "la scrutation s'arrête"

    watch.last_poll_at = datetime.now(timezone.utc)
    if watch.status != FileWatchStatus.error:
        watch.last_error = None
    db.commit()

    # §6.3 — independent of the file loop above (runs even on a tick with zero candidates).
    try:
        sla_event = check_sla(db, watch)
        if sla_event is not None:
            new_events.append(sla_event)
    except Exception:
        logger.warning("file_watch: SLA check failed for watch %s", watch.id, exc_info=True)

    # §6.4 — one grouped digest per channel for everything notifiable this tick.
    try:
        watch_notify.notify_events(db, watch, fi, new_events)
    except Exception:
        logger.warning("file_watch: notification dispatch failed for watch %s", watch.id, exc_info=True)


def poll_due_watches() -> int:
    """Décision B — one advisory-locked pass: select watches whose poll_interval has elapsed,
    scan them sequentially, release the lock. Returns the number of watches scanned (0 if the
    lock wasn't acquired — another worker is already scrutinizing). Never raises: a connection-
    level failure here is a transient infra hiccup, retried at the next tick by watch_loop."""
    settings = get_settings()
    lock_conn = engine.connect()
    try:
        got_lock = lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": settings.watch_lock_key}).scalar()
        if not got_lock:
            return 0

        db = SessionLocal()
        scanned = 0
        try:
            now = datetime.now(timezone.utc)
            active = db.query(FileWatch).filter(FileWatch.status == FileWatchStatus.active).all()
            due = [w for w in active if w.last_poll_at is None or (now - w.last_poll_at).total_seconds() >= w.poll_interval_seconds]
            for watch in due:
                try:
                    scan_watch(db, watch)
                    scanned += 1
                except Exception:
                    logger.warning("file_watch: unhandled error scanning watch %s", watch.id, exc_info=True)
                    db.rollback()
        finally:
            db.close()
        return scanned
    finally:
        try:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": settings.watch_lock_key})
        except Exception:
            pass
        lock_conn.close()
