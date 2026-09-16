from pydantic import BaseModel


class AIHealthOut(BaseModel):
    status: str  # reachable | unreachable | model_not_found | bad_credentials
    model: str | None = None
    base_url: str | None = None
    message: str | None = None
