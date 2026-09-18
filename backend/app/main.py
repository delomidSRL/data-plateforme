import asyncio
import contextlib
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from app.api.routes import ai, airflow, airflow_instances, auth, dashboard, dbt_macros, file_watch, imports, medallion, medallion_admin, medallion_agent, medallion_folders, ml_templates, quality, servers, sources, stacks, superset_instances, users
from app.core.config import get_settings
from app.services import file_watch as file_watch_service

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


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_watch_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Data Plateforme API", version="0.1.0", lifespan=lifespan)

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
app.include_router(medallion_admin.router)
app.include_router(medallion_agent.router)
app.include_router(medallion_folders.router)
app.include_router(quality.router)
app.include_router(ml_templates.router)
app.include_router(dbt_macros.router)
app.include_router(imports.router)
app.include_router(file_watch.router)
app.include_router(dashboard.router)
app.include_router(ai.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
