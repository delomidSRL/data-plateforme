import asyncio
import contextlib
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.routes import ai, airflow, airflow_instances, auth, dashboard, dbt_macros, dq_flag_registry, file_watch, imports, medallion, medallion_admin, medallion_agent, medallion_folders, medallion_workspace, ml_templates, quality, servers, sources, stacks, superset_instances, users
from app.core.config import get_settings
from app.services import file_watch as file_watch_service
from app.services import workspace_remote

settings = get_settings()

logging.basicConfig(level=logging.INFO if settings.env == "dev" else logging.WARNING)
logger = logging.getLogger("app.main")


async def _watch_loop() -> None:
    """Module 6 extension, Décision B — the in-process scrutation loop. One task, started
    once in lifespan below; each tick runs the whole (advisory-locked, sequential) pass in a
    thread so a slow scan never blocks the event loop / any concurrent HTTP request."""
    while True:
        try:
            await run_in_threadpool(file_watch_service.poll_due_watches)
        except Exception:
            logger.warning("watch loop: unhandled error in poll_due_watches", exc_info=True)
        await asyncio.sleep(settings.watch_tick_seconds)


async def _code_sync_loop() -> None:
    """Code tab <-> dossier dbt physique, sens disque -> plateforme (voir workspace_remote.py)
    — même pattern que _watch_loop ci-dessus, verrou/tick distincts."""
    while True:
        try:
            await run_in_threadpool(workspace_remote.poll_all_projects)
        except Exception:
            logger.warning("code sync loop: unhandled error in poll_all_projects", exc_info=True)
        await asyncio.sleep(settings.code_sync_tick_seconds)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = [asyncio.create_task(_watch_loop()), asyncio.create_task(_code_sync_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Data Plateforme API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def _catch_unhandled_exceptions(request: Request, call_next):
    """Must be registered BEFORE add_middleware(CORSMiddleware) below so it ends up the
    *inner* layer (CORSMiddleware wraps outside it, last-added = outermost) — a plain
    `@app.exception_handler(Exception)` looks like the obvious fix but is a documented
    Starlette gap (github.com/encode/starlette issue #1175, reproduced here too):
    CORSMiddleware's header-injecting `send` wrapper is simply never invoked for a response
    built that way, only for ones that flow through an ordinary middleware `call_next`. A
    route exception that isn't already an HTTPException (e.g. promotion.promote()'s SSH/
    Airflow calls, which can raise ssh.SSHError/airflow_api.AirflowAPIError past the route's
    own `except PromotionError`) used to propagate with no CORS headers on the resulting
    500 at all — the browser then reports a misleading "CORS policy" / net::ERR_FAILED,
    hiding the real error and its actual cause entirely (confirmed: a genuine promote()
    failure surfaced to a user as a pure CORS error, nothing in the real stack trace)."""
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"Erreur interne inattendue : {exc}"})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(servers.router)
app.include_router(stacks.router)
app.include_router(airflow.router)
app.include_router(airflow_instances.router)
app.include_router(superset_instances.router)
app.include_router(sources.router)
app.include_router(medallion.router)
app.include_router(medallion_workspace.router)
app.include_router(medallion_admin.router)
app.include_router(medallion_agent.router)
app.include_router(medallion_folders.router)
app.include_router(quality.router)
app.include_router(ml_templates.router)
app.include_router(dbt_macros.router)
app.include_router(dq_flag_registry.router)
app.include_router(imports.router)
app.include_router(file_watch.router)
app.include_router(dashboard.router)
app.include_router(ai.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
