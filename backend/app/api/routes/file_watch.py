"""Module 6 extension §4.4 — /api/watches. Création/édition/pause/reprise/test ouverts à tout
utilisateur authentifié (engineer et admin, §2 — ce module ne distingue pas plus finement,
comme /api/imports) ; suppression réservée à admin (§2)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.data_source import DataSource, DataSourceType
from app.models.file_import import FileImport
from app.models.file_watch import FileWatch, FileWatchEvent, FileWatchStatus, FileWatchWriteMode, WatchOutcome, WatchPatternType
from app.models.user import User
from app.schemas.file_watch import WatchCreate, WatchEventOut, WatchOut, WatchTestCandidateOut, WatchTestDraftIn, WatchTestOut, WatchUpdate
from app.services import watch_transport
from app.services.file_watch import _is_complete, sla_view, validate_cron_expr
from app.services.watch_transport import WatchTransportError

router = APIRouter(prefix="/api/watches", tags=["watches"])


def _get_watch(db: Session, watch_id: int) -> FileWatch:
    w = db.get(FileWatch, watch_id)
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Surveillance introuvable.")
    return w


def _watch_out(db: Session, watch: FileWatch) -> WatchOut:
    """§6.6 — every response carries next_expected_arrival/sla_state, computed at read time
    (services/file_watch.sla_view) — a plain from_attributes mapping can't reach these, they
    need a DB lookup + arrival_cron arithmetic, not just column values."""
    out = WatchOut.model_validate(watch)
    view = sla_view(db, watch)
    out.next_expected_arrival = view["next_expected_arrival"]
    out.sla_state = view["sla_state"]
    return out


def _validate_location(transport: str, location: dict, db: Session) -> None:
    if transport == "local":
        watch_transport.validate_local_path(location.get("path", ""))
        return
    if not location.get("source_id") or not location.get("bucket"):
        raise WatchTransportError("source_id et bucket sont requis pour un transport MinIO.")
    source = db.get(DataSource, int(location["source_id"]))
    if source is None or source.type != DataSourceType.minio:
        raise WatchTransportError("Source MinIO introuvable ou invalide.")


def _validate_common(payload, db: Session) -> None:
    try:
        watch_transport.validate_pattern(payload.pattern, WatchPatternType(payload.pattern_type))
        _validate_location(payload.transport, payload.location, db)
    except WatchTransportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if payload.arrival_cron:
        try:
            validate_cron_expr(payload.arrival_cron)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/", response_model=list[WatchOut])
def list_watches(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    watches = db.query(FileWatch).order_by(FileWatch.created_at.desc()).all()
    return [_watch_out(db, w) for w in watches]


@router.post("/", response_model=WatchOut, status_code=status.HTTP_201_CREATED)
def create_watch(payload: WatchCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    fi = db.get(FileImport, payload.file_import_id)
    if fi is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Import cible introuvable.")
    if fi.write_mode.value not in ("append", "replace"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"L'import cible doit être en mode « append » ou « replace » pour être surveillé (actuellement : « {fi.write_mode.value} »). Basculez son mode d'écriture avant de créer la surveillance.",
        )
    _validate_common(payload, db)

    watch = FileWatch(
        name=payload.name, created_by=current_user.id, file_import_id=fi.id,
        transport=payload.transport, location=payload.location,
        pattern=payload.pattern, pattern_type=payload.pattern_type,
        poll_interval_seconds=payload.poll_interval_seconds,
        completeness_strategy=payload.completeness_strategy,
        stable_size_delay_seconds=payload.stable_size_delay_seconds,
        control_file_suffix=payload.control_file_suffix,
        post_process=payload.post_process, done_target=payload.done_target, error_target=payload.error_target,
        write_mode=FileWatchWriteMode(payload.write_mode),
        arrival_cron=payload.arrival_cron, arrival_grace_minutes=payload.arrival_grace_minutes,
        status=FileWatchStatus.active,
    )
    db.add(watch)
    db.commit()
    db.refresh(watch)
    return _watch_out(db, watch)


def _dry_run(transport, watch_like) -> list[WatchTestCandidateOut]:
    out = []
    for ref in transport.list_candidates(watch_like.pattern, watch_like.pattern_type):
        try:
            complete = _is_complete(transport, ref, watch_like)
            if complete:
                reason = None
            elif watch_like.completeness_strategy == "stable_size":
                reason = "taille instable (fichier probablement encore en cours d'écriture)"
            else:
                reason = "fichier témoin absent"
        except Exception as exc:
            complete, reason = False, f"vérification impossible : {exc}"
        out.append(WatchTestCandidateOut(name=ref.name, size=ref.size, complete=complete, reason=reason))
    return out


@router.post("/test", response_model=WatchTestOut)
def test_watch_draft(payload: WatchTestDraftIn, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """§4.5 — the creation drawer's own « Tester », before any FileWatch row exists. Same
    dry-run semantics as POST /{id}/test (writes nothing), just against an unsaved draft."""
    try:
        watch_transport.validate_pattern(payload.pattern, WatchPatternType(payload.pattern_type))
        _validate_location(payload.transport, payload.location, db)
    except WatchTransportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    try:
        transport = watch_transport.build_transport(db, payload.transport, payload.location)
    except WatchTransportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Connexion impossible : {exc}")

    try:
        return WatchTestOut(candidates=_dry_run(transport, payload))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Listage impossible : {exc}")


@router.get("/{watch_id}", response_model=WatchOut)
def get_watch(watch_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return _watch_out(db, _get_watch(db, watch_id))


@router.put("/{watch_id}", response_model=WatchOut)
def update_watch(watch_id: int, payload: WatchUpdate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    watch = _get_watch(db, watch_id)
    _validate_common(payload, db)

    watch.name = payload.name
    watch.transport = payload.transport
    watch.location = payload.location
    watch.pattern = payload.pattern
    watch.pattern_type = payload.pattern_type
    watch.poll_interval_seconds = payload.poll_interval_seconds
    watch.completeness_strategy = payload.completeness_strategy
    watch.stable_size_delay_seconds = payload.stable_size_delay_seconds
    watch.control_file_suffix = payload.control_file_suffix
    watch.post_process = payload.post_process
    watch.done_target = payload.done_target
    watch.error_target = payload.error_target
    watch.write_mode = FileWatchWriteMode(payload.write_mode)
    watch.arrival_cron = payload.arrival_cron
    watch.arrival_grace_minutes = payload.arrival_grace_minutes
    db.commit()
    db.refresh(watch)
    return _watch_out(db, watch)


@router.post("/{watch_id}/pause", response_model=WatchOut)
def pause_watch(watch_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    watch = _get_watch(db, watch_id)
    watch.status = FileWatchStatus.paused
    db.commit()
    db.refresh(watch)
    return _watch_out(db, watch)


@router.post("/{watch_id}/resume", response_model=WatchOut)
def resume_watch(watch_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    watch = _get_watch(db, watch_id)
    watch.status = FileWatchStatus.active
    watch.consecutive_failures = 0
    db.commit()
    db.refresh(watch)
    return _watch_out(db, watch)


@router.post("/{watch_id}/test", response_model=WatchTestOut)
def test_watch(watch_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """§4.4 — dry-run: lists what matches *now* and its completeness, writes nothing (no
    FileWatchEvent, no watch field touched). Reuses the exact same completeness check the real
    scan uses, so for completeness_strategy=stable_size this genuinely waits
    stable_size_delay_seconds — an explicit, user-initiated click, not a hidden background wait."""
    watch = _get_watch(db, watch_id)
    try:
        transport = watch_transport.build_transport(db, watch.transport, watch.location)
    except WatchTransportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Connexion impossible : {exc}")

    try:
        return WatchTestOut(candidates=_dry_run(transport, watch))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Listage impossible : {exc}")


@router.get("/{watch_id}/events", response_model=list[WatchEventOut])
def list_watch_events(watch_id: int, outcome: str | None = Query(default=None), db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _get_watch(db, watch_id)
    q = db.query(FileWatchEvent).filter(FileWatchEvent.watch_id == watch_id)
    if outcome is not None:
        try:
            q = q.filter(FileWatchEvent.outcome == WatchOutcome(outcome))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"outcome invalide : {outcome}")
    return q.order_by(FileWatchEvent.detected_at.desc()).limit(200).all()


@router.post("/{watch_id}/events/{event_id}/ack", response_model=WatchEventOut)
def ack_watch_event(watch_id: int, event_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """§6.5 — aligned on the Module 5 alert-acknowledgement pattern: marks an incident seen,
    never re-emitted (that's already guaranteed by the dedup-on-create design, §6.7 DoD #3 —
    this is purely a visibility/triage concern, not a notification-suppression one)."""
    event = db.query(FileWatchEvent).filter(FileWatchEvent.id == event_id, FileWatchEvent.watch_id == watch_id).first()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Événement introuvable.")
    if event.acknowledged_at is None:
        event.acknowledged_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(event)
    return event


@router.delete("/{watch_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watch(watch_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    watch = _get_watch(db, watch_id)
    db.delete(watch)
    db.commit()
    return None
