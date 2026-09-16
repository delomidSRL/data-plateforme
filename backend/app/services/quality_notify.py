import json
import logging

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret, encrypt_secret
from app.models.data_quality import AlertSeverity, DataQualityAlert, NotificationChannel, NotificationChannelType
from app.models.medallion import MedallionProject
from app.services.email import send_email

logger = logging.getLogger("app.quality_notify")

MASK = "••••••••"
_SEVERITY_RANK = {AlertSeverity.info: 0, AlertSeverity.warning: 1, AlertSeverity.critical: 2}


def decode_config(channel: NotificationChannel) -> dict:
    return json.loads(decrypt_secret(channel.config_encrypted))


def encode_config(config: dict) -> str:
    return encrypt_secret(json.dumps(config))


def mask_config(channel_type: NotificationChannelType, config: dict) -> dict:
    """Never echoes secrets back — the webhook signing secret is replaced with a mask;
    recipients/URL are not secrets and stay visible so the UI can display/edit them."""
    if channel_type == NotificationChannelType.email:
        return {"recipients": config.get("recipients", [])}
    return {"url": config.get("url", ""), "secret": MASK if config.get("secret") else None}


def _digest_html(project: MedallionProject, alerts: list[DataQualityAlert]) -> str:
    rows = "".join(
        f"<li><b>{a.severity.value.upper()}</b> [{a.type.value}] {a.message}</li>" for a in alerts
    )
    return f"<p>Projet <b>{project.name}</b> — {len(alerts)} nouvelle(s) alerte(s) qualité :</p><ul>{rows}</ul>"


def _digest_text(project: MedallionProject, alerts: list[DataQualityAlert]) -> str:
    lines = [f"[{a.severity.value}] ({a.type.value}) {a.message}" for a in alerts]
    return f"Projet {project.name} — {len(alerts)} nouvelle(s) alerte(s) qualité :\n" + "\n".join(lines)


def send_digest(channel: NotificationChannel, project: MedallionProject, alerts: list[DataQualityAlert]) -> None:
    config = decode_config(channel)
    if channel.type == NotificationChannelType.email:
        for recipient in config.get("recipients", []):
            send_email(recipient, f"Data Plateforme — Qualité des données : {project.name}", _digest_html(project, alerts))
    else:
        url = config.get("url")
        if not url:
            raise ValueError("URL webhook manquante.")
        payload = {
            "project": project.name,
            "project_id": project.id,
            "alerts": [
                {"dataset_id": a.dataset_id, "type": a.type.value, "severity": a.severity.value, "message": a.message, "created_at": a.created_at.isoformat()}
                for a in alerts
            ],
        }
        headers = {}
        if config.get("secret"):
            headers["Authorization"] = f"Bearer {config['secret']}"
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()


def notify_alerts(db: Session, project: MedallionProject, alerts: list[DataQualityAlert]) -> None:
    """Best-effort, called once per collection batch (§6.3 — regroupement par run) so a run
    with several breaches sends one digest per channel instead of one notification per alert."""
    if not alerts:
        return
    channels = (
        db.query(NotificationChannel)
        .filter(NotificationChannel.enabled.is_(True), or_(NotificationChannel.project_id == project.id, NotificationChannel.project_id.is_(None)))
        .all()
    )
    for channel in channels:
        relevant = [a for a in alerts if _SEVERITY_RANK[a.severity] >= _SEVERITY_RANK[channel.min_severity]]
        if not relevant:
            continue
        try:
            send_digest(channel, project, relevant)
        except Exception as exc:
            logger.warning("quality notify: channel %s failed for project %s: %s", channel.id, project.id, exc)
