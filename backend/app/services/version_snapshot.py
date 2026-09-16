import logging

import yaml
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.medallion import MedallionDataset, MedallionProject, MedallionVersion
from app.models.project_environment_binding import ProjectEnvironmentBinding
from app.models.user import User

logger = logging.getLogger("app.version_snapshot")

_SECRET_PLACEHOLDER = "<reinjecte depuis data_sources au redeploiement>"


def _strip_profile_secrets(profiles_yml_content: str) -> str:
    """profiles.yml as actually deployed has real host/user/password/dbname inline (see
    dbt_project._profiles_yml) — a snapshot must never persist that. Keeps the YAML shape
    intact (useful for diffing) but blanks the secret values; version_restore.py rebuilds
    the real file fresh from the project's current data_sources instead of reusing this."""
    data = yaml.safe_load(profiles_yml_content) or {}
    for profile in data.values():
        for output in (profile.get("outputs") or {}).values():
            for key in ("host", "user", "password", "dbname"):
                if key in output:
                    output[key] = _SECRET_PLACEHOLDER
    return yaml.safe_dump(data, sort_keys=False)


def _serialize_datasets(datasets: list[MedallionDataset]) -> list[dict]:
    by_id = {d.id: d for d in datasets}
    out = []
    for d in datasets:
        out.append({
            "name": d.name,
            "layer": d.layer.value,
            "source_id": d.source_id,
            "source_object": d.source_object,
            "load_mode": d.load_mode.value if d.load_mode else None,
            "incremental_key": d.incremental_key,
            "partition_by": d.partition_by,
            "bronze_location": d.bronze_location,
            "dbt_model_name": d.dbt_model_name,
            "materialization": d.materialization.value if d.materialization else None,
            "sql": d.sql,
            "transform_type": d.transform_type.value,
            "ml_objective": d.ml_objective.value,
            "python_code": d.python_code,
            "output_table": d.output_table,
            "upstream_dataset_ids": list(d.upstream_dataset_ids or []),
            # Lineage by stable key (name), not just id — so a restore can rebuild the
            # graph even if primary keys differ after reconciliation (see version_restore).
            "upstream_dataset_names": [by_id[uid].name for uid in (d.upstream_dataset_ids or []) if uid in by_id],
            "tests": d.tests or [],
            "description": d.description,
        })
    return out


def capture(
    db: Session,
    project: MedallionProject,
    binding: ProjectEnvironmentBinding,
    datasets: list[MedallionDataset],
    dbt_files: dict[str, str],
    dag_content: str,
    dag_id: str,
    dag_file_path: str,
    user: User | None,
) -> MedallionVersion:
    """Snapshots the artifacts a successful build *already generated* — nothing is
    regenerated here. Best-effort by design: call sites must catch and log, never let a
    capture failure undo an already-successful deploy.

    Module 17 — `binding` is the ProjectEnvironmentBinding this build/restore/promotion just
    deployed to (home, or any other environment); its `active_version_id` is what gets
    updated, never `project.active_version_id` directly (that's a read/write SHIM onto the
    HOME binding specifically — correct only when `binding.is_home`, wrong for any other one).
    `dag_id`/`dag_content` passed in here are always the BASE, unsuffixed form (§2) — callers
    (medallion_deploy.build_project's own `dag_id_base`/`dag_content`) already guarantee this;
    this function has no way to tell a suffixed one apart, so it never re-derives anything."""
    snapshot_files = dict(dbt_files)
    if "profiles.yml" in snapshot_files:
        snapshot_files["profiles.yml"] = _strip_profile_secrets(snapshot_files["profiles.yml"])

    next_number = (
        db.query(func.max(MedallionVersion.version_number))
        .filter(MedallionVersion.project_id == project.id)
        .scalar()
        or 0
    ) + 1

    version = MedallionVersion(
        project_id=project.id,
        version_number=next_number,
        created_by_id=user.id if user else None,
        dbt_project_snapshot=snapshot_files,
        dag_snapshot=dag_content,
        datasets_snapshot=_serialize_datasets(datasets),
        dag_id=dag_id,
        dag_file_path=dag_file_path,
        is_restore_of=None,
    )
    db.add(version)
    db.flush()
    binding.active_version_id = version.id
    db.commit()
    db.refresh(version)
    logger.info("captured version %s for project %s (binding %s, environment %s)", version.version_number, project.id, binding.id, binding.environment.value)
    return version
