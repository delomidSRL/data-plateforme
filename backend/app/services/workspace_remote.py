"""Sync bidirectionnel entre le workspace DB (`project_files`, onglet Code) et le dossier dbt
physique sur le serveur déployé — "je travaille directement sur le projet physique, je le
trouve en temps réel dans la plateforme, et vice versa."

Sens plateforme -> disque : push_file()/delete_file(), appelés en best-effort juste après
chaque écriture réussie dans project_files (medallion_workspace.py) — jamais bloquant : la DB
reste la source de vérité, has_pending_changes couvre déjà le cas où le push échoue (le
prochain "Build & déployer" rattrape).

Sens disque -> plateforme : poll_all_projects(), un sondage périodique (voir main.py) qui lit
le contenu courant du dossier dbt de chaque projet déployé et le repasse tel quel à
workspace_merge.apply_generated() — exactement le même mécanisme qu'une régénération de build
(generator="remote_disk") : mise à jour silencieuse si rien d'autre n'a changé côté DB,
proposition/conflit arbitrable (onglet Code, même UI) sinon. Aucun état de synchronisation
séparé à maintenir : base_content/base_hash d'un ProjectFile EST déjà "la dernière valeur
connue en commun", que la divergence vienne d'une régénération de build ou d'une édition
distante.
"""
import concurrent.futures
import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models.airflow_instance import AirflowInstance
from app.models.medallion import MedallionDataset, MedallionProject
from app.models.project_file_conflict import ConflictTrigger
from app.services import airflow_instances, ssh, workspace_merge
from app.services.airflow_instances import DeployTarget

logger = logging.getLogger("app.workspace_remote")

# Même liste que medallion_workspace._ALLOWED_EXTENSIONS + le marqueur de dossier vide — tenue
# séparément plutôt qu'importée (medallion_workspace.py est une route, pas un service) ; les
# deux doivent rester synchronisées si l'une change.
_SCAN_EXTENSIONS = (".sql", ".yml", ".yaml", ".md", ".csv")
_FOLDER_MARKER = ".gitkeep"
_MANAGED_FILES = {"packages.yml", "profiles.yml", "airflow/dag.py"}


def _home_target(db: Session, project: MedallionProject) -> DeployTarget | None:
    try:
        binding = project.home_binding
        instance = db.get(AirflowInstance, binding.airflow_instance_id)
        if instance is None:
            return None
        return airflow_instances.resolve_deploy_target(db, instance, project.dbt_project_name)
    except Exception:
        # RuntimeError (no home binding yet — shouldn't happen but see home_binding's own
        # docstring), AirflowInstanceError (not deployable/misconfigured), or anything else —
        # every caller here is best-effort and must never raise past this point.
        return None


def push_file(db: Session, project: MedallionProject, path: str, content: str) -> None:
    """Best-effort — never raises, the caller's own DB write already succeeded and remains the
    source of truth (has_pending_changes covers a failed push until the next deploy)."""
    target = _home_target(db, project)
    if target is None:
        return
    try:
        ssh.write_remote_file(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret, f"{target.dbt_dir}/{path}", content)
    except Exception:
        logger.warning("workspace_remote: push failed for project %s path %s", project.id, path, exc_info=True)


def delete_file(db: Session, project: MedallionProject, path: str) -> None:
    target = _home_target(db, project)
    if target is None:
        return
    try:
        with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
            ssh.run_command(client, f"rm -f {target.dbt_dir}/{path}")
    except Exception:
        logger.warning("workspace_remote: delete failed for project %s path %s", project.id, path, exc_info=True)


def move_file(db: Session, project: MedallionProject, from_path: str, to_path: str, content: str) -> None:
    push_file(db, project, to_path, content)
    delete_file(db, project, from_path)


def _list_remote_files(client, dbt_dir: str) -> list[str]:
    name_clauses = " -o ".join(f"-name '*{ext}'" for ext in _SCAN_EXTENSIONS)
    cmd = (
        f"find {dbt_dir} -type f \\( {name_clauses} -o -name '{_FOLDER_MARKER}' \\) "
        f"! -path '*/target/*' ! -path '*/dbt_packages/*' ! -path '*/logs/*' 2>/dev/null"
    )
    result = ssh.run_command(client, cmd, timeout=30)
    if not result.ok or not result.stdout:
        return []
    prefix = dbt_dir.rstrip("/") + "/"
    return [line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]


def poll_project(db: Session, project: MedallionProject) -> None:
    """One project's worth of the periodic sweep — reads every workspace-eligible file
    currently on disk and reconciles it through the exact same merge machinery a build-time
    regeneration uses. Never raises (the caller sweeps every project in one tick; one
    unreachable server must not stop the rest)."""
    target = _home_target(db, project)
    if target is None:
        return
    try:
        with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
            paths = _list_remote_files(client, target.dbt_dir)
            paths = [p for p in paths if p not in _MANAGED_FILES]
            if not paths:
                return
            sftp = client.open_sftp()
            files: dict[str, str] = {}
            try:
                for path in paths:
                    try:
                        with sftp.file(f"{target.dbt_dir}/{path}", "r") as f:
                            files[path] = f.read().decode("utf-8", errors="replace")
                    except Exception:
                        continue  # vanished between find and read, or genuinely unreadable — skip, next tick retries
            finally:
                sftp.close()
    except Exception:
        logger.warning("workspace_remote: poll failed for project %s", project.id, exc_info=True)
        return

    if not files:
        return
    datasets = db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id).all()
    workspace_merge.apply_generated(db, project, files, generator="remote_disk", trigger=ConflictTrigger.remote_sync, datasets=datasets)
    db.commit()


# Measured live against 31 real projects on one Airflow stack: sequential (one SSH session at
# a time) took ~140s/sweep — far past this loop's 20s tick, so sweeps would always overlap and
# "temps réel" would really mean "whenever the last sweep finally finishes". Parallelizing
# across projects (each its own SSH connection, nothing shared) cut that to tens of seconds.
# Each worker opens its own SessionLocal() — a Session is never safe to share across threads.
_MAX_PARALLEL_POLLS = 8


def _poll_one(project_id: int) -> bool:
    db = SessionLocal()
    try:
        project = db.get(MedallionProject, project_id)
        if project is None:
            return False
        poll_project(db, project)
        return True
    except Exception:
        logger.warning("workspace_remote: unhandled error polling project %s", project_id, exc_info=True)
        db.rollback()
        return False
    finally:
        db.close()


def poll_all_projects() -> int:
    """Called once per tick by main.py's background loop — same advisory-locked single-active-
    worker pattern as file_watch.poll_due_watches(), distinct lock key. Returns how many
    projects were swept (0 if the lock wasn't acquired, i.e. another worker already is)."""
    settings = get_settings()
    lock_conn = engine.connect()
    try:
        got_lock = lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": settings.code_sync_lock_key}).scalar()
        if not got_lock:
            return 0

        db = SessionLocal()
        try:
            project_ids = [pid for (pid,) in db.query(MedallionProject.id).all()]
        finally:
            db.close()

        count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_PARALLEL_POLLS) as pool:
            for ok in pool.map(_poll_one, project_ids):
                if ok:
                    count += 1
        return count
    finally:
        try:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": settings.code_sync_lock_key})
        except Exception:
            pass
        lock_conn.close()
