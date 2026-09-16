"""Module 6 extension §6.4 — thin delivery layer for watch incidents. Rides entirely on
Module 5's own NotificationChannel model and delivery primitives (SMTP via services/email.py,
webhook via httpx, config encode/decode from services/quality_notify.py) — no new channel
model, no new config screen (§6.5: "réutilise la config de canaux du Module 5, pas de nouvel
écran de canaux"). The only new code here is FileWatchEvent-specific formatting: the M5
digest functions are hardcoded to DataQualityAlert's own shape and can't be reused as-is."""
import logging

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.data_quality import NotificationChannel, NotificationChannelType
from app.models.file_import import FileImport
from app.models.file_watch import FileWatch, FileWatchEvent
from app.services.email import send_email
from app.services.quality_notify import decode_config

logger = logging.getLogger("app.watch_notify")

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_NOTIFIABLE_OUTCOMES = {"absent_sla", "drift_rejected", "fetch_error"}
_OUTCOME_LABEL = {
    "absent_sla": "fichier attendu absent",
    "drift_rejected": "dérive de forme détectée",
    "fetch_error": "erreur de lecture",
}


def _resolve_project_id(db: Session, fi: FileImport) -> int | None:
    """§6.5 — "une surveillance pointe le projet de son import" : FileImport carries no
    project_id itself (a target table isn't owned by one project — imports.* is a generic
    staging area). This walks the exact same join routes/medallion.py's origin-node derivation
    already uses (target_source_id/schema/table -> a bronze MedallionDataset's own
    source_id/source_object) to find which project references it. Zero or more than one match
    both fall back to project-independent (global) channels — a shared or not-yet-wired
    staging table has no single project to notify."""
    from app.models.medallion import MedallionDataset, MedallionLayer

    if not fi.target_table:
        return None
    source_object = f"{fi.target_schema}.{fi.target_table}"
    rows = (
        db.query(MedallionDataset.project_id)
        .filter(
            MedallionDataset.layer == MedallionLayer.bronze,
            MedallionDataset.source_id == fi.target_source_id,
            MedallionDataset.source_object == source_object,
        )
        .distinct()
        .all()
    )
    project_ids = {r[0] for r in rows}
    return project_ids.pop() if len(project_ids) == 1 else None


def _message_for(watch: FileWatch, event: FileWatchEvent) -> str:
    label = _OUTCOME_LABEL.get(event.outcome.value, event.outcome.value)
    detail = event.error or event.file_name or ""
    return f"[{event.severity.value.upper()}] {label}{f' — {detail}' if detail else ''}"


def _send_email(channel: NotificationChannel, watch: FileWatch, fi: FileImport, events: list[FileWatchEvent]) -> None:
    config = decode_config(channel)
    lines = "".join(f"<li>{_message_for(watch, e)}</li>" for e in events)
    subject = f"Data Plateforme — Surveillance « {watch.name} »"
    body = f"<p>Surveillance <b>{watch.name}</b> (import « {fi.name} ») — {len(events)} incident(s) :</p><ul>{lines}</ul>"
    for recipient in config.get("recipients", []):
        send_email(recipient, subject, body)


def _send_webhook(channel: NotificationChannel, watch: FileWatch, fi: FileImport, events: list[FileWatchEvent]) -> None:
    config = decode_config(channel)
    url = config.get("url")
    if not url:
        raise ValueError("URL webhook manquante.")
    payload = {
        "watch": watch.name, "watch_id": watch.id, "file_import": fi.name, "file_import_id": fi.id,
        "events": [
            {"outcome": e.outcome.value, "severity": e.severity.value, "message": e.error, "file_name": e.file_name or None, "detected_at": e.detected_at.isoformat()}
            for e in events
        ],
    }
    headers = {}
    if config.get("secret"):
        headers["Authorization"] = f"Bearer {config['secret']}"
    with httpx.Client(timeout=10) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()


def notify_events(db: Session, watch: FileWatch, fi: FileImport, events: list[FileWatchEvent]) -> None:
    """§6.4 — one call per scan tick: every notifiable incident from THIS tick is grouped into
    one digest per channel (same anti-spam framing as quality_notify.notify_alerts), never one
    notification per event. Best-effort — a channel failure is logged, never raised (never
    affects the scan that produced these events, already committed by the time this runs)."""
    notifiable = [e for e in events if e.outcome.value in _NOTIFIABLE_OUTCOMES]
    if not notifiable:
        return

    project_id = _resolve_project_id(db, fi)
    q = db.query(NotificationChannel).filter(NotificationChannel.enabled.is_(True))
    q = q.filter(or_(NotificationChannel.project_id == project_id, NotificationChannel.project_id.is_(None))) if project_id is not None else q.filter(NotificationChannel.project_id.is_(None))
    channels = q.all()

    for channel in channels:
        relevant = [e for e in notifiable if _SEVERITY_RANK[e.severity.value] >= _SEVERITY_RANK[channel.min_severity.value]]
        if not relevant:
            continue
        try:
            if channel.type == NotificationChannelType.email:
                _send_email(channel, watch, fi, relevant)
            else:
                _send_webhook(channel, watch, fi, relevant)
        except Exception as exc:
            logger.warning("watch_notify: channel %s failed for watch %s: %s", channel.id, watch.id, exc)
