"""Single resolution point for turning a SupersetInstance into a plain API config, and the
reachability/credentials/version test — mirrors services/airflow_instances.py's shape.
"""
from dataclasses import dataclass

from app.core.security import decrypt_secret
from app.models.superset_instance import SupersetInstance, SupersetInstanceStatus
from app.services import superset_api

# Pinned against the apache/superset image actually provisioned by Module 1 (see
# services/compose.py SUPERSET_IMAGE_TAG / templates/superset/Dockerfile.j2) — verified by
# building and running that exact image: apache/superset:latest resolved to 6.1.0 at test
# time. Kept as a single named constant, never hard-coded elsewhere (same convention as
# AIRFLOW_MIN_VERSION).
SUPERSET_MIN_VERSION = "6.1.0"


class SupersetInstanceError(Exception):
    pass


class NoSupersetInstanceConfigured(SupersetInstanceError):
    """Raised when a project has never had a Superset instance attached — distinct from a
    misconfigured/unreachable one, so the frontend can offer a picker instead of an error."""
    pass


def parse_version(raw: str) -> tuple[int, ...]:
    """Semantic (major, minor, patch) tuple comparison — '3.1.10' > '3.1.6' must hold,
    which a lexical string comparison would get wrong."""
    parts = []
    for chunk in raw.strip().split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def meets_min_version(raw: str) -> bool:
    return parse_version(raw) >= parse_version(SUPERSET_MIN_VERSION)


@dataclass
class SupersetConfig:
    base_url: str
    username: str
    password: str
    verify_tls: bool


def get_superset_config(instance: SupersetInstance) -> SupersetConfig:
    return SupersetConfig(
        base_url=instance.base_url,
        username=instance.admin_username,
        password=decrypt_secret(instance.secret_encrypted),
        verify_tls=instance.verify_tls,
    )


async def test_instance_connection(config: SupersetConfig) -> tuple[SupersetInstanceStatus, str, str | None]:
    """Reachability + credentials + version (semantic, >= SUPERSET_MIN_VERSION), in that
    order. Returns (status, message, superset_version); `message` is a ready-to-display
    French string."""
    try:
        await superset_api.get_token(config.base_url, config.username, config.password, force_refresh=True)
    except superset_api.SupersetAPIError as exc:
        if exc.status_code == 401:
            return SupersetInstanceStatus.bad_credentials, "Authentification refusée : vérifiez le nom d'utilisateur et le mot de passe.", None
        return SupersetInstanceStatus.unreachable, str(exc), None

    try:
        version = await superset_api.get_version(config.base_url)
    except superset_api.SupersetAPIError as exc:
        return SupersetInstanceStatus.unreachable, f"Authentifié, mais version illisible : {exc}", None

    if not version or not meets_min_version(version):
        detected = version or "inconnue"
        return (
            SupersetInstanceStatus.unsupported_version,
            f"version {detected} détectée — la plateforme requiert Superset ≥ {SUPERSET_MIN_VERSION}.",
            version or None,
        )
    return SupersetInstanceStatus.reachable, f"Connexion réussie — Superset {version}.", version


def resolve_project_instance(db, project) -> SupersetInstance:
    """Same principle as airflow_instance_id: an explicit, persisted, per-project choice —
    except optional (nullable), since a project can be created without one and have it
    attached later, right when it's first published."""
    if project.superset_instance_id is None:
        raise NoSupersetInstanceConfigured("Aucune instance Superset choisie pour ce projet.")
    instance = db.get(SupersetInstance, project.superset_instance_id)
    if instance is None or not instance.is_active:
        raise SupersetInstanceError("L'instance Superset de ce projet n'est plus disponible — choisissez-en une autre.")
    return instance
