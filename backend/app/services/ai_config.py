"""Single resolution point for the self-hosted Mistral config (Module 12) — exactly the
role services/superset_instances.py's get_superset_config() plays for Superset. Today this
reads plain env vars (the VM isn't a Module 1 stack, so there's no DB model to resolve);
callers only ever see AIConfig, so swapping the source for a future `external` AI instance
row never requires touching them.
"""
from dataclasses import dataclass

from app.core.config import get_settings


@dataclass
class AIConfig:
    base_url: str
    api_key: str
    model: str
    verify_tls: bool
    timeout: float


def get_ai_config() -> AIConfig:
    settings = get_settings()
    return AIConfig(
        base_url=settings.mistral_base_url.rstrip("/"),
        api_key=settings.mistral_api_key,
        model=settings.mistral_model,
        verify_tls=settings.mistral_verify_tls,
        timeout=settings.mistral_timeout,
    )
