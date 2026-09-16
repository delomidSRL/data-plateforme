from datetime import datetime, timezone

import httpx

TIMEOUT = 10.0

_token_cache: dict[str, str] = {}


class AirflowAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _cache_key(base_url: str) -> str:
    return base_url.rstrip("/")


async def get_token(base_url: str, username: str, password: str, force_refresh: bool = False) -> str:
    key = _cache_key(base_url)
    if not force_refresh and key in _token_cache:
        return _token_cache[key]

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(f"{key}/auth/token", json={"username": username, "password": password})
        except httpx.RequestError as exc:
            raise AirflowAPIError(f"Impossible de joindre l'API Airflow : {exc}")

    if resp.status_code not in (200, 201):
        raise AirflowAPIError(f"Authentification Airflow refusée ({resp.status_code}).", resp.status_code)

    token = resp.json().get("access_token")
    if not token:
        raise AirflowAPIError("Réponse Airflow inattendue : token absent.")
    _token_cache[key] = token
    return token


async def _request(base_url: str, username: str, password: str, method: str, path: str, **kwargs) -> httpx.Response:
    key = _cache_key(base_url)
    token = await get_token(base_url, username, password)

    async def do_request(tok: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await client.request(method, f"{key}{path}", headers={"Authorization": f"Bearer {tok}"}, **kwargs)

    try:
        resp = await do_request(token)
        if resp.status_code == 401:
            token = await get_token(base_url, username, password, force_refresh=True)
            resp = await do_request(token)
        return resp
    except httpx.RequestError as exc:
        raise AirflowAPIError(f"Requête Airflow échouée : {exc}")


def _raise_for_error(resp: httpx.Response, action: str) -> None:
    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise AirflowAPIError(f"{action} a échoué ({resp.status_code}) : {detail}", resp.status_code)


async def list_connections(base_url: str, username: str, password: str) -> list[dict]:
    resp = await _request(base_url, username, password, "GET", "/api/v2/connections")
    _raise_for_error(resp, "Lister les connexions")
    return resp.json().get("connections", [])


async def create_connection(
    base_url: str, username: str, password: str,
    connection_id: str, conn_type: str,
    host: str | None = None, login: str | None = None, conn_password: str | None = None,
    schema: str | None = None, port: int | None = None, extra: str | None = None,
    description: str | None = None,
) -> dict:
    body = {
        "connection_id": connection_id,
        "conn_type": conn_type,
        "host": host,
        "login": login,
        "password": conn_password,
        "schema": schema,
        "port": port,
        "extra": extra,
        "description": description,
    }
    resp = await _request(base_url, username, password, "POST", "/api/v2/connections", json=body)
    _raise_for_error(resp, "Créer la connexion")
    return resp.json()


async def update_connection(
    base_url: str, username: str, password: str,
    connection_id: str, conn_type: str,
    host: str | None = None, login: str | None = None, conn_password: str | None = None,
    schema: str | None = None, port: int | None = None, extra: str | None = None,
    description: str | None = None,
) -> dict:
    body = {
        "connection_id": connection_id,
        "conn_type": conn_type,
        "host": host,
        "login": login,
        "password": conn_password,
        "schema": schema,
        "port": port,
        "extra": extra,
        "description": description,
    }
    resp = await _request(base_url, username, password, "PATCH", f"/api/v2/connections/{connection_id}", json=body)
    _raise_for_error(resp, "Mettre à jour la connexion")
    return resp.json()


async def test_connection(
    base_url: str, username: str, password: str,
    conn_type: str, host: str | None = None, login: str | None = None,
    conn_password: str | None = None, schema: str | None = None, port: int | None = None, extra: str | None = None,
) -> dict:
    body = {"conn_type": conn_type, "host": host, "login": login, "password": conn_password, "schema": schema, "port": port, "extra": extra}
    resp = await _request(base_url, username, password, "POST", "/api/v2/connections/test", json=body)
    _raise_for_error(resp, "Tester la connexion")
    return resp.json()


async def get_version(base_url: str, username: str, password: str) -> str:
    resp = await _request(base_url, username, password, "GET", "/api/v2/version")
    _raise_for_error(resp, "Lire la version")
    return resp.json().get("version", "")


async def get_dag(base_url: str, username: str, password: str, dag_id: str) -> dict | None:
    resp = await _request(base_url, username, password, "GET", f"/api/v2/dags/{dag_id}")
    if resp.status_code == 404:
        return None
    _raise_for_error(resp, "Lire le DAG")
    return resp.json()


async def delete_dag(base_url: str, username: str, password: str, dag_id: str) -> None:
    resp = await _request(base_url, username, password, "DELETE", f"/api/v2/dags/{dag_id}")
    if resp.status_code == 404:
        return
    _raise_for_error(resp, "Supprimer le DAG")


async def list_dags(base_url: str, username: str, password: str) -> list[dict]:
    """Paginates through the full result set — Airflow's default page size (100) silently
    truncates instances with more DAGs than that, which defeats the whole point of
    "observe every DAG on this instance" once a real client Airflow is involved."""
    page_size = 100
    dags: list[dict] = []
    offset = 0
    while True:
        resp = await _request(base_url, username, password, "GET", "/api/v2/dags", params={"limit": page_size, "offset": offset})
        _raise_for_error(resp, "Lister les DAGs")
        body = resp.json()
        page = body.get("dags", [])
        dags.extend(page)
        total = body.get("total_entries", len(dags))
        offset += len(page)
        if not page or offset >= total:
            break
    return dags


async def list_dag_runs(base_url: str, username: str, password: str, dag_id: str) -> list[dict]:
    resp = await _request(base_url, username, password, "GET", f"/api/v2/dags/{dag_id}/dagRuns")
    _raise_for_error(resp, "Lister les runs")
    return resp.json().get("dag_runs", [])


async def set_dag_paused(base_url: str, username: str, password: str, dag_id: str, is_paused: bool) -> dict:
    resp = await _request(base_url, username, password, "PATCH", f"/api/v2/dags/{dag_id}", json={"is_paused": is_paused})
    _raise_for_error(resp, "Mettre à jour le DAG")
    return resp.json()


async def trigger_dag_run(base_url: str, username: str, password: str, dag_id: str, logical_date: datetime | None = None, conf: dict | None = None) -> dict:
    body = {
        "logical_date": (logical_date or datetime.now(timezone.utc)).isoformat(),
        "conf": conf or {},
    }
    resp = await _request(base_url, username, password, "POST", f"/api/v2/dags/{dag_id}/dagRuns", json=body)
    _raise_for_error(resp, "Déclencher le DAG")
    return resp.json()


async def get_dag_run(base_url: str, username: str, password: str, dag_id: str, dag_run_id: str) -> dict:
    resp = await _request(base_url, username, password, "GET", f"/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}")
    _raise_for_error(resp, "Lire le run")
    return resp.json()


async def get_task_log(base_url: str, username: str, password: str, dag_id: str, dag_run_id: str, task_id: str, try_number: int = 1) -> str:
    resp = await _request(base_url, username, password, "GET", f"/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/{task_id}/logs/{try_number}")
    _raise_for_error(resp, "Lire les logs")
    return resp.text
