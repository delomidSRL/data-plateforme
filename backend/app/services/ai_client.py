"""OpenAI-compatible client for a self-hosted Mistral (Module 12). No auth flow to manage —
a single static bearer token from AIConfig — so this is far simpler than airflow_api.py /
superset_api.py's token-cache dance; same principle otherwise: readable errors, never a
stack trace, short timeouts.
"""
import logging
from contextvars import ContextVar

import httpx

from app.services.ai_config import AIConfig

logger = logging.getLogger(__name__)

# Debug capture (annexe "élargir le scope de l'assistant IA") — a per-request accumulator of
# every AI call this request made (a repair loop or a multi-step map/plan call can trigger
# several), so a route handler can attach the exact prompt(s)/raw response(s) to its own API
# response for the browser DevTools to show, without threading a debug param through every
# service function that calls chat_completion(). ContextVar (not a module-level list) so
# concurrent requests never see each other's entries.
_debug_log: ContextVar[list[dict] | None] = ContextVar("ai_debug_log", default=None)


def reset_debug_log() -> None:
    """Call at the start of a route handler that wants to capture this request's AI calls."""
    _debug_log.set([])


def get_debug_log() -> list[dict]:
    """Call at the end of that same route handler, to attach to the response."""
    return _debug_log.get() or []


class AIClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _headers(config: AIConfig) -> dict:
    return {"Authorization": f"Bearer {config.api_key}"}


def _record_debug(messages: list[dict], **fields) -> None:
    log = _debug_log.get()
    if log is None:
        return  # reset_debug_log() was never called for this request — nothing to capture into
    log.append({"prompt": messages, **fields})


async def chat_completion(config: AIConfig, messages: list[dict], temperature: float = 0.3, max_tokens: int | None = None, **extra) -> str:
    body = {"model": config.model, "messages": messages, "temperature": temperature, **extra}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    # Every AI call in the platform (mapping, plan, indicateurs...) goes through this one
    # function — logged/captured here, once, covers everywhere, no need to instrument each caller.
    prompt_dump = "\n".join(f"--- {m.get('role', '?')} ---\n{m.get('content', '')}" for m in messages)
    logger.info("=== PROMPT IA (%s) ===\n%s\n=== FIN PROMPT ===", config.model, prompt_dump)

    try:
        async with httpx.AsyncClient(timeout=config.timeout, verify=config.verify_tls) as client:
            resp = await client.post(f"{config.base_url}/v1/chat/completions", json=body, headers=_headers(config))
    except httpx.HTTPError as exc:
        logger.info("=== REPONSE IA : ERREUR RESEAU === %s", exc)
        _record_debug(messages, error=f"Erreur réseau : {exc}")
        raise AIClientError(f"Impossible de joindre le service Mistral : {exc}")

    if resp.status_code in (401, 403):
        logger.info("=== REPONSE IA (%s) : AUTH REFUSEE ===", resp.status_code)
        _record_debug(messages, error="Authentification refusée.")
        raise AIClientError("Authentification refusée : clé API Mistral invalide.", resp.status_code)
    if resp.status_code >= 400:
        logger.info("=== REPONSE IA (%s) BRUTE ===\n%s\n=== FIN REPONSE ===", resp.status_code, resp.text)
        _record_debug(messages, error=f"HTTP {resp.status_code}", response=resp.text[:2000])
        raise AIClientError(f"Appel Mistral échoué ({resp.status_code}) : {resp.text[:500]}", resp.status_code)

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        logger.info("=== REPONSE IA BRUTE (forme inattendue) ===\n%s\n=== FIN REPONSE ===", resp.text)
        _record_debug(messages, error="Réponse inattendue.", response=resp.text[:2000])
        raise AIClientError("Réponse Mistral inattendue : contenu absent.")

    logger.info("=== REPONSE IA BRUTE ===\n%s\n=== FIN REPONSE ===", content)
    _record_debug(messages, response=content)
    return content


async def health_check(config: AIConfig) -> tuple[str, str | None]:
    """Reachability + credentials + model presence, in that order. Returns
    (status, message) — status ∈ reachable | unreachable | model_not_found | bad_credentials.
    `message` is a ready-to-display French string, None when status is "reachable"."""
    if not config.base_url:
        return "unreachable", "MISTRAL_BASE_URL n'est pas configuré."

    try:
        async with httpx.AsyncClient(timeout=config.timeout, verify=config.verify_tls) as client:
            resp = await client.get(f"{config.base_url}/v1/models", headers=_headers(config))
    except httpx.HTTPError as exc:
        return "unreachable", f"Impossible de joindre le service Mistral : {exc}"

    if resp.status_code in (401, 403):
        return "bad_credentials", "Authentification refusée : clé API Mistral invalide."
    if resp.status_code >= 400:
        return "unreachable", f"Service Mistral injoignable ({resp.status_code})."

    try:
        model_ids = {m.get("id") for m in resp.json().get("data", [])}
    except (ValueError, AttributeError):
        return "unreachable", "Réponse Mistral illisible."

    if config.model not in model_ids:
        available = ", ".join(sorted(m for m in model_ids if m)) or "aucun"
        return "model_not_found", f"Modèle « {config.model} » introuvable — modèles disponibles : {available}."

    return "reachable", None
