import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, TestStatus, TransformType
from app.services import ssh
from app.services.airflow_instances import DeployTarget

SCHEMA_BY_LAYER = {MedallionLayer.bronze: "bronze", MedallionLayer.silver: "silver", MedallionLayer.gold: "gold"}


def table_name(ds: MedallionDataset) -> str:
    if ds.layer == MedallionLayer.bronze:
        return ds.name
    if ds.transform_type == TransformType.python:
        return ds.output_table or ds.name
    return ds.dbt_model_name


def list_columns(warehouse: DataSource, ds: MedallionDataset) -> list[dict]:
    """On-demand column list for a single dataset's physical table — used by the dataset
    editor to help authoring dbt SQL. Empty (not an error) if the table doesn't exist yet,
    e.g. a dataset that has never been through a successful pipeline run."""
    table = table_name(ds)
    if not table:
        return []
    try:
        warehouse_secret = decrypt_secret(warehouse.secret_encrypted)
        engine = create_engine(
            f"postgresql+psycopg://{warehouse.username}:{warehouse_secret}@{warehouse.host}:{warehouse.port}/{warehouse.database_name}",
            connect_args={"connect_timeout": 5},
        )
    except Exception:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position"
                ),
                {"schema": SCHEMA_BY_LAYER[ds.layer], "table": table},
            ).fetchall()
        return [{"column": r[0], "type": r[1]} for r in rows]
    except Exception:
        return []
    finally:
        engine.dispose()


def refresh_dataset_stats(db: Session, project: MedallionProject, datasets: list[MedallionDataset], warehouse: DataSource, target: DeployTarget) -> dict:
    """After a run, pulls row counts per dataset from the warehouse (queried directly over
    the network, same as Module 2's introspection) and overall dbt test status from the
    last `dbt test` run_results.json (fetched via SSH, since that's a file on the server)."""
    now = datetime.now(timezone.utc)
    layer_stats: dict[str, int] = {"bronze": 0, "silver": 0, "gold": 0}

    warehouse_secret = decrypt_secret(warehouse.secret_encrypted)
    engine = create_engine(
        f"postgresql+psycopg://{warehouse.username}:{warehouse_secret}@{warehouse.host}:{warehouse.port}/{warehouse.database_name}",
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as conn:
            for ds in datasets:
                schema = SCHEMA_BY_LAYER[ds.layer]
                table = table_name(ds)
                if not table:
                    continue
                try:
                    count = conn.execute(text(f'SELECT count(*) FROM {schema}."{table}"')).scalar()
                except Exception:
                    continue
                ds.last_row_count = count
                ds.last_loaded_at = now
                layer_stats[ds.layer.value] = layer_stats.get(ds.layer.value, 0) + count
    finally:
        engine.dispose()

    # overall dbt test status from the last `dbt test` invocation's run_results.json
    tests_summary = {"passed": 0, "failed": 0}
    run_results = None
    try:
        with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
            sftp = client.open_sftp()
            try:
                with sftp.file(f"{target.dbt_dir}/target/run_results.json", "r") as f:
                    run_results = json.loads(f.read().decode())
            finally:
                sftp.close()
    except Exception:
        pass

    overall_status = TestStatus.none
    if run_results:
        results = run_results.get("results", [])
        passed = sum(1 for r in results if r.get("status") == "pass")
        failed = sum(1 for r in results if r.get("status") in ("fail", "error"))
        tests_summary = {"passed": passed, "failed": failed}
        overall_status = TestStatus.failed if failed else (TestStatus.passed if passed else TestStatus.none)

    for ds in datasets:
        if ds.layer in (MedallionLayer.silver, MedallionLayer.gold) and ds.tests:
            ds.last_test_status = overall_status

    db.commit()
    return {"layer_stats": layer_stats, "tests_summary": tests_summary}
