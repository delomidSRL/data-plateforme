"""Module 17 §5 — mapping des sources bronze dev -> prod, puis promotion : redeploy the
version already active on the HOME (dev) binding onto the prod binding, verbatim (§0: "on
promeut une version, on ne régénère pas"). No new MedallionVersion is captured here — the
version being promoted already exists (dev's own build captured it); promotion just makes
the prod binding's active_version_id point at that same id, resolving its connections/dag_id
fresh for prod (§2), never touching a live dataset row (that's a restore-only concern).

Adaptation disclosed: the spec's "un check en Bloquer bloque la promotion" (§0/§5.4.3)
describes a per-check severity vocabulary this codebase's actual Module 16 doesn't carry
(DataQualityCheck has no block/observe/warn field) — the real, closest signal it does carry is
DataQualityAlert.severity (info|warning|critical). An OPEN CRITICAL alert is treated as the
"Bloquer" case here; warning/info alerts surface in the promotion preview as advisories only,
never blocking — same spirit, real vocabulary.
"""
import difflib
import posixpath
from dataclasses import dataclass

import yaml
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.security import decrypt_secret
from app.models.airflow_instance import AirflowInstance
from app.models.binding_source_mapping import BindingSourceMapping
from app.models.data_quality import AlertSeverity, AlertStatus, DataQualityAlert
from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, MedallionVersion, ProjectStatus, ProjectTarget
from app.models.project_environment_binding import ProjectEnvironmentBinding
from app.models.server import Environment
from app.services import airflow_instances, dag_render, dbt_project, version_diff
from app.services.medallion_deploy import _deploy_dag_via_ssh, _deploy_files_via_ssh, _resolve_endpoint, _upsert_connection
from app.services import ssh

DEV_INITIAL_DEPLOY_LABEL = "déploiement initial"


class PromotionError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# §5.2/§5.3 — source mapping (suggested, always confirmed by hand)
# ---------------------------------------------------------------------------

def bronze_source_ids(db: Session, project_id: int) -> list[int]:
    """Distinct DataSource ids the project's BRONZE datasets reference — the mapping's whole
    scope (§5.2). Warehouse/object store are excluded on purpose: those are direct fields of
    the binding itself, not something this table maps."""
    rows = (
        db.query(MedallionDataset.source_id)
        .filter(MedallionDataset.project_id == project_id, MedallionDataset.layer == MedallionLayer.bronze, MedallionDataset.source_id.isnot(None))
        .distinct()
        .all()
    )
    return [r[0] for r in rows]


def _suggest_target(db: Session, origin: DataSource) -> DataSource | None:
    """Match by name/type (§5.3.2) — a same-type DataSource with the closest name, by plain
    string similarity. Never authoritative: every row still requires an explicit confirm
    (§0 "jamais de mapping silencieux"), so a soft heuristic here is a convenience, not a risk."""
    candidates = db.query(DataSource).filter(DataSource.type == origin.type, DataSource.id != origin.id).all()
    if not candidates:
        return None
    best = max(candidates, key=lambda c: difflib.SequenceMatcher(None, origin.name.lower(), c.name.lower()).ratio())
    return best


def sync_source_mappings(db: Session, project: MedallionProject, binding: ProjectEnvironmentBinding) -> None:
    """Keeps binding's BindingSourceMapping rows in sync with the project's CURRENT bronze
    datasets — adds a (suggested, unconfirmed) row for a newly-referenced source, drops rows
    for a source no bronze dataset references anymore, and never touches an already-confirmed
    row (re-syncing after editing datasets must not silently erase a validated mapping)."""
    origin_ids = set(bronze_source_ids(db, project.id))
    existing = {
        m.origin_source_id: m
        for m in db.query(BindingSourceMapping).filter(BindingSourceMapping.binding_id == binding.id).all()
    }

    for stale_id, row in existing.items():
        if stale_id not in origin_ids:
            db.delete(row)

    for new_id in origin_ids - set(existing):
        origin = db.get(DataSource, new_id)
        suggestion = _suggest_target(db, origin) if origin else None
        db.add(BindingSourceMapping(
            binding_id=binding.id, origin_source_id=new_id,
            target_source_id=suggestion.id if suggestion else None, confirmed=False,
        ))
    db.commit()


# ---------------------------------------------------------------------------
# §5.3 — prod binding creation/update
# ---------------------------------------------------------------------------

def upsert_prod_binding(
    db: Session, project: MedallionProject, airflow_instance_id: int, warehouse_source_id: int,
    object_store_source_id: int, schedule: str | None, target: ProjectTarget,
) -> ProjectEnvironmentBinding:
    binding = (
        db.query(ProjectEnvironmentBinding)
        .filter(ProjectEnvironmentBinding.project_id == project.id, ProjectEnvironmentBinding.environment == Environment.prod)
        .first()
    )
    if binding is None:
        binding = ProjectEnvironmentBinding(project_id=project.id, environment=Environment.prod, is_home=False, status=ProjectStatus.draft)
        db.add(binding)
    binding.airflow_instance_id = airflow_instance_id
    binding.warehouse_source_id = warehouse_source_id
    binding.object_store_source_id = object_store_source_id
    binding.schedule = schedule
    binding.target = target
    db.commit()
    db.refresh(binding)
    sync_source_mappings(db, project, binding)
    db.refresh(binding)
    return binding


def get_prod_binding(db: Session, project_id: int) -> ProjectEnvironmentBinding | None:
    return (
        db.query(ProjectEnvironmentBinding)
        .filter(ProjectEnvironmentBinding.project_id == project_id, ProjectEnvironmentBinding.environment == Environment.prod)
        .first()
    )


@dataclass
class SourceMappingRow:
    origin_source_id: int
    origin_name: str
    origin_type: str
    target_source_id: int | None
    target_name: str | None
    confirmed: bool


def list_source_mappings(db: Session, binding: ProjectEnvironmentBinding) -> list[SourceMappingRow]:
    rows = db.query(BindingSourceMapping).filter(BindingSourceMapping.binding_id == binding.id).all()
    out = []
    for m in rows:
        origin = db.get(DataSource, m.origin_source_id)
        target = db.get(DataSource, m.target_source_id) if m.target_source_id else None
        out.append(SourceMappingRow(
            origin_source_id=m.origin_source_id, origin_name=origin.name if origin else "?",
            origin_type=origin.type.value if origin else "?",
            target_source_id=m.target_source_id, target_name=target.name if target else None, confirmed=m.confirmed,
        ))
    return out


def confirm_source_mapping(db: Session, binding: ProjectEnvironmentBinding, origin_source_id: int, target_source_id: int) -> BindingSourceMapping:
    mapping = (
        db.query(BindingSourceMapping)
        .filter(BindingSourceMapping.binding_id == binding.id, BindingSourceMapping.origin_source_id == origin_source_id)
        .first()
    )
    if mapping is None:
        raise PromotionError("Cette source n'est pas dans le périmètre de mapping de ce projet.", 404)
    if db.get(DataSource, target_source_id) is None:
        raise PromotionError("Source cible introuvable.", 400)
    mapping.target_source_id = target_source_id
    mapping.confirmed = True
    db.commit()
    db.refresh(mapping)
    return mapping


# ---------------------------------------------------------------------------
# §5.4 — promotion preview (diff + mapping recap + quality gate) and execution
# ---------------------------------------------------------------------------

def promotion_preview(db: Session, project: MedallionProject) -> dict:
    dev_binding = project.home_binding
    prod_binding = get_prod_binding(db, project.id)

    dev_version = db.get(MedallionVersion, dev_binding.active_version_id) if dev_binding.active_version_id else None
    prod_version = db.get(MedallionVersion, prod_binding.active_version_id) if prod_binding and prod_binding.active_version_id else None

    diff_result = None
    if dev_version is not None:
        if prod_version is not None:
            diff_result = version_diff.diff(prod_version, dev_version)
        else:
            # No prod history yet — every dev dataset reads as "added", same shape version_diff
            # itself returns, so the frontend renders identically either way (§5.4.2).
            diff_result = {
                "datasets_added": sorted(d["name"] for d in dev_version.datasets_snapshot),
                "datasets_removed": [], "sql_changed": [], "tests_changed": [],
            }

    mappings = list_source_mappings(db, prod_binding) if prod_binding else []
    mapping_complete = all(m.confirmed and m.target_source_id for m in mappings)

    blocking = db.query(DataQualityAlert).filter(
        DataQualityAlert.project_id == project.id, DataQualityAlert.severity == AlertSeverity.critical, DataQualityAlert.status == AlertStatus.open,
    ).all()
    advisory = db.query(DataQualityAlert).filter(
        DataQualityAlert.project_id == project.id, DataQualityAlert.severity.in_([AlertSeverity.warning, AlertSeverity.info]), DataQualityAlert.status == AlertStatus.open,
    ).all()

    can_promote = bool(prod_binding) and dev_version is not None and mapping_complete and not blocking

    return {
        "dev_deployed": dev_version is not None,
        "dev_version_number": dev_version.version_number if dev_version else None,
        "prod_configured": prod_binding is not None,
        "prod_version_number": prod_version.version_number if prod_version else None,
        "diff": diff_result,
        "mappings": mappings,
        "mapping_complete": mapping_complete,
        "blocking_alerts": blocking,
        "advisory_alerts": advisory,
        "can_promote": can_promote,
    }


async def promote(db: Session, project: MedallionProject) -> ProjectEnvironmentBinding:
    dev_binding = project.home_binding
    if dev_binding.active_version_id is None:
        raise PromotionError("Le binding dev n'a jamais été déployé — rien à promouvoir.")

    prod_binding = get_prod_binding(db, project.id)
    if prod_binding is None:
        raise PromotionError("Aucun binding prod configuré pour ce projet — configurez-le d'abord.")

    mappings = list_source_mappings(db, prod_binding)
    if any(not m.confirmed or not m.target_source_id for m in mappings):
        raise PromotionError("Toutes les sources bronze doivent être mappées et confirmées avant de promouvoir.")

    blocking = db.query(DataQualityAlert).filter(
        DataQualityAlert.project_id == project.id, DataQualityAlert.severity == AlertSeverity.critical, DataQualityAlert.status == AlertStatus.open,
    ).all()
    if blocking:
        raise PromotionError(f"{len(blocking)} alerte(s) qualité critique(s) ouverte(s) — corrigez avant de promouvoir.")

    target_version = db.get(MedallionVersion, dev_binding.active_version_id)
    if target_version is None:
        raise PromotionError("Version active du binding dev introuvable.")

    instance = db.get(AirflowInstance, prod_binding.airflow_instance_id)
    if instance is None:
        raise PromotionError("Instance Airflow introuvable pour le binding prod.")
    try:
        deploy_target = airflow_instances.resolve_deploy_target(db, instance, project.dbt_project_name)
    except airflow_instances.AirflowInstanceError as exc:
        raise PromotionError(str(exc)) from exc

    prod_warehouse = db.get(DataSource, prod_binding.warehouse_source_id)
    prod_object_store = db.get(DataSource, prod_binding.object_store_source_id)
    if prod_warehouse is None or prod_object_store is None:
        raise PromotionError("Warehouse ou object store du binding prod introuvable.")

    # 1. dbt files — verbatim, except profiles.yml (credentials always re-injected fresh from
    # PROD's own current data_sources, never from the snapshot or dev's secrets, §2).
    wh_host, wh_port = _resolve_endpoint(prod_warehouse, deploy_target)
    warehouse_dict = {
        "host": wh_host, "port": wh_port, "username": prod_warehouse.username,
        "password": decrypt_secret(prod_warehouse.secret_encrypted), "database_name": prod_warehouse.database_name,
    }
    dbt_files = dict(target_version.dbt_project_snapshot)
    dbt_files["profiles.yml"] = yaml.safe_dump(dbt_project._profiles_yml(project, warehouse_dict), sort_keys=False)
    await run_in_threadpool(_deploy_files_via_ssh, deploy_target, deploy_target.dbt_dir, deploy_target.plugins_dir, dbt_files)

    # 2. connections — KEYS always derived from what the frozen DAG text actually references
    # (project-role for warehouse/object store, origin dev source id for bronze — both stable
    # across environments by construction, see dag_render.conn_id_for_project_role); VALUES
    # resolved from prod's own sources / the confirmed mapping. Never dev's secrets (§2).
    base_url, admin_user, admin_password = deploy_target.airflow.base_url, deploy_target.airflow.username, deploy_target.airflow.password
    connections_created: list[str] = []

    async def _upsert(source: DataSource, conn_id: str):
        await _upsert_connection(base_url, admin_user, admin_password, source, deploy_target, conn_id)
        connections_created.append(conn_id)

    await _upsert(prod_warehouse, dag_render.conn_id_for_project_role(project.id, "warehouse"))
    await _upsert(prod_object_store, dag_render.conn_id_for_project_role(project.id, "object_store"))

    for m in mappings:
        target_source = db.get(DataSource, m.target_source_id)
        if target_source is not None:
            await _upsert(target_source, dag_render.conn_id_for_source(m.origin_source_id))

    # 3. dag — suffixed for prod; orphan cleanup if this binding was previously deployed
    # under a different path (e.g. re-promoting after a project rename).
    dag_id = dag_render.dag_id_for_environment(project, Environment.prod.value)
    dag_content = dag_render.apply_environment_suffix(target_version.dag_snapshot, project, Environment.prod.value)
    dag_path = posixpath.join(deploy_target.dags_dir, f"{dag_id}.py")

    if prod_binding.dag_file_path and prod_binding.dag_file_path != dag_path:
        def _delete_stale_dag():
            try:
                with ssh.ssh_session(deploy_target.ssh_host, deploy_target.ssh_port, deploy_target.ssh_user, deploy_target.ssh_auth_method, deploy_target.ssh_secret) as client:
                    ssh.run_command(client, f"rm -f {prod_binding.dag_file_path}")
            except Exception:
                pass
        await run_in_threadpool(_delete_stale_dag)

    await run_in_threadpool(_deploy_dag_via_ssh, deploy_target, dag_path, dag_content)

    # 4. no reconcile (datasets are already shared/agnostic — nothing to "undo" for a
    # different binding, unlike restore), no new MedallionVersion (the one being promoted
    # already exists, §0 "on promeut, on ne régénère pas"), no run triggered (§5.4.5).
    prod_binding.dag_id = dag_id
    prod_binding.dag_file_path = dag_path
    prod_binding.status = ProjectStatus.deployed
    prod_binding.active_version_id = target_version.id
    db.commit()
    db.refresh(prod_binding)
    return prod_binding
