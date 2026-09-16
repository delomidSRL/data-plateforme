from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.models.user import User
from app.schemas.ai import AIHealthOut
from app.services import ai_client
from app.services.ai_config import get_ai_config

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/health", response_model=AIHealthOut)
async def get_ai_health(_: User = Depends(require_admin)):
    """Module 12 — reachability + credentials + model check for the self-hosted Mistral.
    Admin-only: this reports on shared platform infrastructure, not per-project data."""
    config = get_ai_config()
    status_, message = await ai_client.health_check(config)
    return AIHealthOut(status=status_, model=config.model, base_url=config.base_url, message=message)
