"""Superset API client (Module 11) — mirrors airflow_api.py's shape (token cache, renewal
on 401, readable errors never a stack trace), plus what Airflow's pure-bearer v2 API
doesn't need: a CSRF token that must travel together with the session cookie issued
alongside it (confirmed empirically against apache/superset — Flask-WTF validates the
CSRF token against that same session, not the JWT).
"""
import html
import json
import re

import httpx

TIMEOUT = 10.0
_token_cache: dict[str, str] = {}
_csrf_cache: dict[str, tuple[str, dict[str, str]]] = {}  # key -> (csrf_token, session_cookies)


class SupersetAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _cache_key(base_url: str) -> str:
    return base_url.rstrip("/")


_RISON_BARE_TOKEN = re.compile(r"^[A-Za-z0-9_.~/-]+$")


def _rison_string(value: str) -> str:
    """Encodes a string for embedding in a rison `q=(filters:!(...))` query param — needed
    since chart slice_name is now the free-text, AI-generated indicator title (accents,
    spaces, punctuation), not the old slug-only naming scheme. A bare, punctuation-free token
    round-trips unquoted; anything else must be single-quoted with `!`/`'` escaped per the
    rison spec (confirmed empirically: an un-escaped space in the filter value made Superset's
    API reject the whole query with "Not a valid rison/json argument")."""
    if _RISON_BARE_TOKEN.match(value):
        return value
    escaped = value.replace("!", "!!").replace("'", "!'")
    return f"'{escaped}'"


async def get_token(base_url: str, username: str, password: str, force_refresh: bool = False) -> str:
    key = _cache_key(base_url)
    if not force_refresh and key in _token_cache:
        return _token_cache[key]

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.post(f"{key}/api/v1/security/login", json={"username": username, "password": password, "provider": "db", "refresh": True})
        except httpx.RequestError as exc:
            raise SupersetAPIError(f"Impossible de joindre l'API Superset : {exc}")

    if resp.status_code == 401:
        raise SupersetAPIError("Authentification refusée : vérifiez le nom d'utilisateur et le mot de passe.", 401)
    if resp.status_code not in (200, 201):
        raise SupersetAPIError(f"Authentification Superset refusée ({resp.status_code}).", resp.status_code)

    token = resp.json().get("access_token")
    if not token:
        raise SupersetAPIError("Réponse Superset inattendue : token absent.")
    _token_cache[key] = token
    _csrf_cache.pop(key, None)  # a fresh JWT needs a fresh CSRF token + session pair too
    return token


async def _get_csrf(base_url: str, username: str, password: str, force_refresh: bool = False) -> tuple[str, dict[str, str]]:
    key = _cache_key(base_url)
    if not force_refresh and key in _csrf_cache:
        return _csrf_cache[key]

    token = await get_token(base_url, username, password)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(f"{key}/api/v1/security/csrf_token/", headers={"Authorization": f"Bearer {token}"})
        except httpx.RequestError as exc:
            raise SupersetAPIError(f"Impossible de joindre l'API Superset : {exc}")

    if resp.status_code == 401:
        if not force_refresh:
            await get_token(base_url, username, password, force_refresh=True)
            return await _get_csrf(base_url, username, password, force_refresh=True)
        raise SupersetAPIError("Authentification refusée lors de la récupération du jeton CSRF.", 401)
    if resp.status_code not in (200, 201):
        raise SupersetAPIError(f"Jeton CSRF Superset illisible ({resp.status_code}).", resp.status_code)

    csrf = resp.json().get("result")
    if not csrf:
        raise SupersetAPIError("Réponse Superset inattendue : jeton CSRF absent.")
    # Flask-WTF checks the CSRF token against ITS session, not the JWT — the session cookie
    # issued on this same response must be replayed on every mutating call alongside it.
    cookies = dict(resp.cookies)
    _csrf_cache[key] = (csrf, cookies)
    return csrf, cookies


async def _request(base_url: str, username: str, password: str, method: str, path: str, **kwargs) -> httpx.Response:
    key = _cache_key(base_url)
    token = await get_token(base_url, username, password)
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    cookies = {}
    if method.upper() != "GET":
        csrf, cookies = await _get_csrf(base_url, username, password)
        headers["X-CSRFToken"] = csrf
        headers["Referer"] = key

    async def do_request(hdrs: dict, ck: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            return await client.request(method, f"{key}{path}", headers=hdrs, cookies=ck, **kwargs)

    try:
        resp = await do_request(headers, cookies)
        if resp.status_code == 401:
            headers["Authorization"] = f"Bearer {await get_token(base_url, username, password, force_refresh=True)}"
            if method.upper() != "GET":
                csrf, cookies = await _get_csrf(base_url, username, password, force_refresh=True)
                headers["X-CSRFToken"] = csrf
            resp = await do_request(headers, cookies)
        return resp
    except httpx.RequestError as exc:
        raise SupersetAPIError(f"Requête Superset échouée : {exc}")


def _raise_for_error(resp: httpx.Response, action: str) -> None:
    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise SupersetAPIError(f"{action} a échoué ({resp.status_code}) : {detail}", resp.status_code)


async def get_version(base_url: str) -> str:
    """No dedicated REST endpoint publishes the version (confirmed empirically against
    apache/superset 6.1.0) — the login page's embedded bootstrap JSON does, unauthenticated,
    at common.menu_data.navbar_right.version_string."""
    key = _cache_key(base_url)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            resp = await client.get(f"{key}/login/")
        except httpx.RequestError as exc:
            raise SupersetAPIError(f"Impossible de joindre l'API Superset : {exc}")
    if resp.status_code != 200:
        raise SupersetAPIError(f"Page de connexion Superset illisible ({resp.status_code}).", resp.status_code)

    match = re.search(r'data-bootstrap="([^"]*)"', resp.text)
    if not match:
        return ""
    try:
        data = json.loads(html.unescape(match.group(1)))
        return data.get("common", {}).get("menu_data", {}).get("navbar_right", {}).get("version_string") or ""
    except (ValueError, AttributeError):
        return ""


async def ensure_database(base_url: str, username: str, password: str, name: str, sqlalchemy_uri: str) -> dict:
    """Creates the `dp_`-managed database if none by this name exists yet, else reuses it
    as-is — Superset has no upsert, so this is search-by-name then create-or-reuse."""
    resp = await _request(base_url, username, password, "GET", "/api/v1/database/", params={"q": f"(filters:!((col:database_name,opr:eq,value:{name})))"})
    _raise_for_error(resp, "Lister les bases Superset")
    results = resp.json().get("result", [])
    if results:
        return {"id": resp.json()["ids"][0], **results[0]}

    create_resp = await _request(base_url, username, password, "POST", "/api/v1/database/", json={"database_name": name, "sqlalchemy_uri": sqlalchemy_uri, "expose_in_sqllab": True})
    _raise_for_error(create_resp, "Créer la base Superset")
    body = create_resp.json()
    return {"id": body["id"], **body.get("result", {})}


async def find_dataset(base_url: str, username: str, password: str, database_id: int, schema: str, table_name: str) -> dict | None:
    q = f"(filters:!((col:database,opr:rel_o_m,value:{database_id}),(col:table_name,opr:eq,value:{table_name}),(col:schema,opr:eq,value:{schema})))"
    resp = await _request(base_url, username, password, "GET", "/api/v1/dataset/", params={"q": q})
    _raise_for_error(resp, "Lister les datasets Superset")
    body = resp.json()
    results = body.get("result", [])
    ids = body.get("ids", [])
    if not results:
        return None
    return {"id": ids[0], **results[0]}


async def create_dataset(base_url: str, username: str, password: str, database_id: int, schema: str, table_name: str) -> dict:
    resp = await _request(base_url, username, password, "POST", "/api/v1/dataset/", json={"database": database_id, "schema": schema, "table_name": table_name})
    _raise_for_error(resp, "Créer le dataset Superset")
    body = resp.json()
    return {"id": body["id"]}


async def refresh_dataset(base_url: str, username: str, password: str, dataset_id: int) -> None:
    resp = await _request(base_url, username, password, "PUT", f"/api/v1/dataset/{dataset_id}/refresh")
    _raise_for_error(resp, "Rafraîchir le dataset Superset")


async def get_dataset(base_url: str, username: str, password: str, dataset_id: int) -> dict:
    """Canonical column count + explore_url — fetched after create/refresh either way, since
    the create/refresh responses' own shapes aren't reliable for that (confirmed empirically:
    refresh returns just {"message": "OK"})."""
    resp = await _request(base_url, username, password, "GET", f"/api/v1/dataset/{dataset_id}")
    _raise_for_error(resp, "Lire le dataset Superset")
    return resp.json().get("result", {})


async def delete_dataset(base_url: str, username: str, password: str, dataset_id: int) -> None:
    resp = await _request(base_url, username, password, "DELETE", f"/api/v1/dataset/{dataset_id}")
    if resp.status_code == 404:
        return
    _raise_for_error(resp, "Supprimer le dataset Superset")


# ---------------- Charts & dashboards (Module 12) ----------------

async def find_chart_by_name(base_url: str, username: str, password: str, slice_name: str, datasource_id: int) -> dict | None:
    # Scoped by datasource_id, not just slice_name: since Module 14/UX change, slice_name is
    # the AI-generated indicator title verbatim (no more dp_<project>__<dataset>__ prefix), so
    # two different gold datasets can legitimately produce identically-titled indicators (e.g.
    # both a "Total" KPI) — without this scope, ensure_chart's create-or-reuse-by-name would
    # find and silently overwrite the WRONG dataset's chart.
    q = f"(filters:!((col:slice_name,opr:eq,value:{_rison_string(slice_name)}),(col:datasource_id,opr:eq,value:{datasource_id})))"
    resp = await _request(base_url, username, password, "GET", "/api/v1/chart/", params={"q": q})
    _raise_for_error(resp, "Lister les graphiques Superset")
    body = resp.json()
    results = body.get("result", [])
    ids = body.get("ids", [])
    if not results:
        return None
    return {"id": ids[0], **results[0]}


async def create_chart(base_url: str, username: str, password: str, slice_name: str, viz_type: str, datasource_id: int, params: dict) -> dict:
    payload = {"slice_name": slice_name, "viz_type": viz_type, "datasource_id": datasource_id, "datasource_type": "table", "params": json.dumps(params)}
    resp = await _request(base_url, username, password, "POST", "/api/v1/chart/", json=payload)
    _raise_for_error(resp, "Créer le graphique Superset")
    return {"id": resp.json()["id"]}


async def update_chart(base_url: str, username: str, password: str, chart_id: int, params: dict) -> None:
    resp = await _request(base_url, username, password, "PUT", f"/api/v1/chart/{chart_id}", json={"params": json.dumps(params)})
    _raise_for_error(resp, "Mettre à jour le graphique Superset")


async def set_chart_dashboards(base_url: str, username: str, password: str, chart_id: int, dashboard_ids: list[int]) -> None:
    """Sets a chart's own `dashboards` relationship field — belt-and-suspenders alongside
    position_json (a known Superset behavior: a dashboard can render as broken/empty when a
    chart is only referenced from position_json and never assigned this field directly)."""
    resp = await _request(base_url, username, password, "PUT", f"/api/v1/chart/{chart_id}", json={"dashboards": dashboard_ids})
    _raise_for_error(resp, "Lier le graphique au dashboard Superset")


async def get_chart(base_url: str, username: str, password: str, chart_id: int) -> dict:
    resp = await _request(base_url, username, password, "GET", f"/api/v1/chart/{chart_id}")
    _raise_for_error(resp, "Lire le graphique Superset")
    return resp.json().get("result", {})


async def delete_chart(base_url: str, username: str, password: str, chart_id: int) -> None:
    resp = await _request(base_url, username, password, "DELETE", f"/api/v1/chart/{chart_id}")
    if resp.status_code == 404:
        return
    _raise_for_error(resp, "Supprimer le graphique Superset")


async def find_dashboard_by_title(base_url: str, username: str, password: str, title: str) -> dict | None:
    q = f"(filters:!((col:dashboard_title,opr:eq,value:{title})))"
    resp = await _request(base_url, username, password, "GET", "/api/v1/dashboard/", params={"q": q})
    _raise_for_error(resp, "Lister les dashboards Superset")
    body = resp.json()
    results = body.get("result", [])
    ids = body.get("ids", [])
    if not results:
        return None
    return {"id": ids[0], **results[0]}


async def create_dashboard(base_url: str, username: str, password: str, title: str, position_json: dict) -> dict:
    payload = {"dashboard_title": title, "position_json": json.dumps(position_json)}
    resp = await _request(base_url, username, password, "POST", "/api/v1/dashboard/", json=payload)
    _raise_for_error(resp, "Créer le dashboard Superset")
    return {"id": resp.json()["id"]}


async def update_dashboard(base_url: str, username: str, password: str, dashboard_id: int, position_json: dict) -> None:
    resp = await _request(base_url, username, password, "PUT", f"/api/v1/dashboard/{dashboard_id}", json={"position_json": json.dumps(position_json)})
    _raise_for_error(resp, "Mettre à jour le dashboard Superset")


async def delete_dashboard(base_url: str, username: str, password: str, dashboard_id: int) -> None:
    resp = await _request(base_url, username, password, "DELETE", f"/api/v1/dashboard/{dashboard_id}")
    if resp.status_code == 404:
        return
    _raise_for_error(resp, "Supprimer le dashboard Superset")
