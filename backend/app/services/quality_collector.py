import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_quality import DataQualitySnapshot
from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.services import ssh
from app.services.airflow_instances import DeployTarget
from app.services.medallion_stats import table_name
from app.services.quality_notify import notify_alerts
from app.services.quality_rules import evaluate_snapshot

logger = logging.getLogger("app.quality_collector")


def _schema_fingerprint(conn, table: str) -> tuple[str | None, list[dict]]:
    rows = conn.execute(
        text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'gold' AND table_name = :table ORDER BY ordinal_position"
        ),
        {"table": table},
    ).fetchall()
    schema_json = [{"column": r[0], "type": r[1]} for r in rows]
    if not schema_json:
        return None, []
    schema_hash = hashlib.sha256(json.dumps(schema_json, sort_keys=True).encode()).hexdigest()[:16]
    return schema_hash, schema_json


def _read_tests_summary(project: MedallionProject, target: DeployTarget) -> dict:
    """Same run_results.json this pipeline already produces for medallion_stats.py — reused,
    not recollected, per the module's "on n'écrase rien de neuf" principle."""
    try:
        with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
            sftp = client.open_sftp()
            try:
                with sftp.file(f"{target.dbt_dir}/target/run_results.json", "r") as f:
                    run_results = json.loads(f.read().decode())
            finally:
                sftp.close()
        results = run_results.get("results", [])
        return {
            "passed": sum(1 for r in results if r.get("status") == "pass"),
            "failed": sum(1 for r in results if r.get("status") in ("fail", "error")),
        }
    except Exception as exc:
        logger.warning("quality collector: could not read run_results.json for project %s: %s", project.id, exc)
        return {"passed": 0, "failed": 0}


def collect_quality(
    db: Session,
    project: MedallionProject,
    datasets: list[MedallionDataset],
    warehouse: DataSource,
    target: DeployTarget,
    run_id: int | None,
    loaded_at: datetime | None = None,
) -> list[DataQualitySnapshot]:
    """Best-effort snapshot of the four quality signals for every gold dataset of a project.

    `run_id=<int>` is the normal post-run trigger (idempotent per (dataset_id, run_id)).
    `run_id=None` is a manual/backfill collection (§3.4 POST .../quality/collect) — not tied
    to a specific run, so it is intentionally allowed to repeat.
    Never raises: any failure here must not affect the pipeline run that triggered it.
    """
    gold_datasets = [d for d in datasets if d.layer == MedallionLayer.gold]
    if not gold_datasets:
        return []

    pending = gold_datasets
    if run_id is not None:
        already = {
            row[0]
            for row in db.query(DataQualitySnapshot.dataset_id).filter(DataQualitySnapshot.run_id == run_id).all()
        }
        pending = [d for d in gold_datasets if d.id not in already]
    if not pending:
        return []

    tests_summary = _read_tests_summary(project, target)

    try:
        warehouse_secret = decrypt_secret(warehouse.secret_encrypted)
        engine = create_engine(
            f"postgresql+psycopg://{warehouse.username}:{warehouse_secret}@{warehouse.host}:{warehouse.port}/{warehouse.database_name}",
            connect_args={"connect_timeout": 5},
        )
    except Exception as exc:
        logger.warning("quality collector: could not build warehouse engine for project %s: %s", project.id, exc)
        return []

    now = datetime.now(timezone.utc)
    snapshots: list[DataQualitySnapshot] = []
    snapshot_datasets: list[MedallionDataset] = []
    try:
        with engine.connect() as conn:
            for ds in pending:
                table = table_name(ds)
                if not table:
                    continue

                row_count = None
                try:
                    row_count = conn.execute(text(f'SELECT count(*) FROM gold."{table}"')).scalar()
                except Exception as exc:
                    logger.warning("quality collector: row count failed for gold.%s: %s", table, exc)

                schema_hash, schema_json = None, []
                try:
                    schema_hash, schema_json = _schema_fingerprint(conn, table)
                except Exception as exc:
                    logger.warning("quality collector: schema read failed for gold.%s: %s", table, exc)

                snapshot_datasets.append(ds)
                snapshots.append(
                    DataQualitySnapshot(
                        dataset_id=ds.id,
                        project_id=project.id,
                        run_id=run_id,
                        loaded_at=loaded_at or now,
                        row_count=row_count,
                        tests_passed=tests_summary["passed"] if ds.tests else 0,
                        tests_failed=tests_summary["failed"] if ds.tests else 0,
                        schema_hash=schema_hash,
                        schema_json=schema_json,
                    )
                )
    except Exception as exc:
        logger.warning("quality collector: warehouse unreachable for project %s: %s", project.id, exc)
        return []
    finally:
        engine.dispose()

    for snap in snapshots:
        db.add(snap)
    db.commit()
    for snap in snapshots:
        db.refresh(snap)

    new_alerts = []
    for snap, ds in zip(snapshots, snapshot_datasets):
        try:
            new_alerts.extend(evaluate_snapshot(db, snap, ds))
        except Exception as exc:
            logger.warning("quality collector: rule evaluation failed for dataset %s: %s", ds.id, exc)

    try:
        notify_alerts(db, project, new_alerts)
    except Exception as exc:
        logger.warning("quality collector: notification failed for project %s: %s", project.id, exc)

    return snapshots
