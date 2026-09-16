"""7-check compatibility gate for deploying medallion projects to an *external* Airflow
(§4.2). A platform instance is always deployable by construction (the platform
provisioned it, dbt included) — this module only ever runs against `deploy_access`.

Every witness artifact (SFTP probe file, scratch DAG) is cleaned up before returning,
even when an earlier check already failed — a preflight must never leave residue on the
client's Airflow.

Messages are structured as (message_key, message_params) rather than pre-formatted
strings — the frontend renders the template in whichever language the UI is set to,
interpolating the real diagnostic values (paths, versions, raw command output/errors),
which are never translated themselves.
"""
import asyncio
import posixpath
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from app.core.security import decrypt_secret
from app.models.airflow_instance import AirflowInstance, AirflowInstanceOrigin
from app.services import airflow_api, ssh
from app.services.airflow_instances import AirflowInstanceError, get_airflow_config, test_instance_connection

WITNESS_FILE = ".dp_preflight"
WITNESS_DAG_ID = "dp_preflight_check"
GIT_SYNC_WAIT_SECONDS = 3
# Airflow's own default dag-processor scan interval (`dag_dir_list_interval`) is 300s —
# a client running that default genuinely needs up to 5 minutes before a new file is
# even looked at. There is no reliable "force reparse now" API (confirmed by the existing
# per-stack deploy_dag_file() comment in routes/airflow.py) — the wait must cover it.
REPARSE_POLL_SECONDS = 330
REPARSE_POLL_INTERVAL = 5

REQUIRED_PY_PACKAGES = ["pandas", "pyarrow", "minio", "psycopg2", "pymysql", "oracledb"]

WITNESS_DAG_CONTENT = '''"""Deposited by Data Plateforme's preflight check — safe to ignore, removed automatically
at the end of the check. If you see this file outside of a preflight run, delete it."""
from datetime import datetime

from airflow import DAG
from airflow.providers.standard.operators.empty import EmptyOperator

with DAG(
    dag_id="dp_preflight_check",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["dataplateforme", "preflight"],
) as dag:
    EmptyOperator(task_id="noop")
'''

_ALL_KEYS = [
    ("sftp_dags", "Accès SFTP au dossier dags/"),
    ("git_sync", "Détection git-sync / lecture seule"),
    ("sftp_dbt", "Accès SFTP au dossier dbt/"),
    ("dbt_present", "dbt présent"),
    ("python_deps", "Dépendances Python de l'ingestion"),
    ("reparse", "Reparse fonctionnel"),
]


@dataclass
class CheckResult:
    key: str
    label: str
    passed: bool
    message_key: str
    message_params: dict = field(default_factory=dict)


def _connect_ssh_kwargs(deploy_access: dict) -> dict:
    return {
        "hostname": deploy_access["ssh_host"],
        "port": deploy_access.get("ssh_port", 22),
        "username": deploy_access["ssh_user"],
        "auth_method": deploy_access["auth_method"],
        "secret": decrypt_secret(deploy_access["secret_encrypted"]),
    }


def _run_ssh_checks(deploy_access: dict) -> tuple[list[CheckResult], bool]:
    """Checks 2-6, plus depositing the witness DAG that check 7 will look for — all
    share one SSH session. Synchronous (paramiko) — called via run_in_threadpool.
    Returns (checks, witness_dag_deposited)."""
    checks: list[CheckResult] = []
    witness_dag_deposited = False
    dags_path = deploy_access["dags_path"].rstrip("/")
    dbt_path = deploy_access["dbt_path"].rstrip("/")
    dbt_bin = deploy_access.get("dbt_bin") or "dbt"
    exec_prefix = deploy_access.get("exec_prefix") or ""
    kwargs = _connect_ssh_kwargs(deploy_access)

    try:
        with ssh.ssh_session(**kwargs) as client:
            sftp = client.open_sftp()
            try:
                # --- check 2: SFTP access to dags_path ---
                dags_witness = posixpath.join(dags_path, WITNESS_FILE)
                sftp_dags_ok = False
                try:
                    with sftp.file(dags_witness, "w") as f:
                        f.write("preflight")
                    sftp.stat(dags_witness)
                    sftp_dags_ok = True
                    checks.append(CheckResult("sftp_dags", "Accès SFTP au dossier dags/", True, "sftp_write_read_ok", {"path": dags_path}))
                except Exception as exc:
                    checks.append(CheckResult("sftp_dags", "Accès SFTP au dossier dags/", False, "sftp_write_failed", {"error": str(exc)}))
                    checks.append(CheckResult("git_sync", "Détection git-sync / lecture seule", False, "git_sync_skipped", {}))

                # --- check 3: git-sync / read-only detection ---
                if sftp_dags_ok:
                    time.sleep(GIT_SYNC_WAIT_SECONDS)
                    try:
                        sftp.stat(dags_witness)
                        checks.append(CheckResult("git_sync", "Détection git-sync / lecture seule", True, "git_sync_ok", {}))
                    except FileNotFoundError:
                        checks.append(CheckResult("git_sync", "Détection git-sync / lecture seule", False, "git_sync_detected", {}))
                    finally:
                        try:
                            sftp.remove(dags_witness)
                        except Exception:
                            pass

                # --- check 4: SFTP access to dbt_path ---
                dbt_witness = posixpath.join(dbt_path, WITNESS_FILE)
                try:
                    ssh.run_command(client, f"mkdir -p {dbt_path}")
                    with sftp.file(dbt_witness, "w") as f:
                        f.write("preflight")
                    sftp.stat(dbt_witness)
                    checks.append(CheckResult("sftp_dbt", "Accès SFTP au dossier dbt/", True, "sftp_write_read_ok", {"path": dbt_path}))
                except Exception as exc:
                    checks.append(CheckResult("sftp_dbt", "Accès SFTP au dossier dbt/", False, "sftp_write_failed", {"error": str(exc)}))
                finally:
                    try:
                        sftp.remove(dbt_witness)
                    except Exception:
                        pass

                # --- check 5: dbt present (in the actual runtime Airflow executes in —
                # exec_prefix bridges an SSH host that isn't itself that runtime, e.g. a
                # bare host next to a dockerized Airflow: "docker exec <worker> ") ---
                dbt_cmd = f"{exec_prefix} {dbt_bin} --version".strip()
                dbt_result = ssh.run_command(client, dbt_cmd)
                if dbt_result.ok:
                    first_line = dbt_result.stdout.splitlines()[0] if dbt_result.stdout else "dbt --version"
                    checks.append(CheckResult("dbt_present", "dbt présent", True, "raw", {"value": first_line}))
                else:
                    checks.append(CheckResult("dbt_present", "dbt présent", False, "dbt_not_found", {"cmd": dbt_cmd, "error": dbt_result.stderr[:300]}))

                # --- check 6: Python dependencies of the ingestion operator (same runtime as check 5) ---
                import_stmt = ", ".join(REQUIRED_PY_PACKAGES)
                deps_cmd = f'{exec_prefix} python3 -c "import {import_stmt}"'.strip()
                deps_result = ssh.run_command(client, deps_cmd)
                if deps_result.ok:
                    checks.append(CheckResult("python_deps", "Dépendances Python de l'ingestion", True, "python_deps_ok", {"packages": import_stmt}))
                else:
                    checks.append(CheckResult("python_deps", "Dépendances Python de l'ingestion", False, "python_deps_missing", {"error": deps_result.stderr[:300]}))

                # --- check 7 (part 1): deposit the witness DAG; appearance is polled async afterwards ---
                try:
                    ssh.run_command(client, f"mkdir -p {dags_path}")
                    witness_dag_path = posixpath.join(dags_path, f"{WITNESS_DAG_ID}.py")
                    with sftp.file(witness_dag_path, "w") as f:
                        f.write(WITNESS_DAG_CONTENT)
                    witness_dag_deposited = True
                except Exception as exc:
                    checks.append(CheckResult("reparse", "Reparse fonctionnel", False, "reparse_deposit_failed", {"error": str(exc)}))
            finally:
                sftp.close()
    except ssh.SSHError as exc:
        already = {c.key for c in checks}
        for key, label in _ALL_KEYS:
            if key not in already:
                checks.append(CheckResult(key, label, False, "raw", {"value": str(exc)}))

    return checks, witness_dag_deposited


def _cleanup_witness_dag_file(deploy_access: dict) -> None:
    dags_path = deploy_access["dags_path"].rstrip("/")
    witness_dag_path = posixpath.join(dags_path, f"{WITNESS_DAG_ID}.py")
    try:
        with ssh.ssh_session(**_connect_ssh_kwargs(deploy_access)) as client:
            ssh.run_command(client, f"rm -f {witness_dag_path}")
    except ssh.SSHError:
        pass  # best-effort — a cleanup failure must not mask the check results already collected


def _all_failed(message_key: str, message_params: dict | None = None) -> list[CheckResult]:
    return [CheckResult(key, label, False, message_key, message_params or {}) for key, label in _ALL_KEYS]


async def run_preflight(instance: AirflowInstance) -> dict:
    """Runs all 7 checks in order and returns the `capabilities` dict to persist:
    {deployable, checks, checked_at}. Never raises for a failed check — only for a
    genuinely wrong call (e.g. invoked on a platform instance)."""
    if instance.origin != AirflowInstanceOrigin.external:
        raise AirflowInstanceError("Le préflight ne s'applique qu'aux instances externes — une instance platform est toujours déployable.")

    checks: list[CheckResult] = []

    # --- check 1: API v2 + version + credentials (re-verified — the client's Airflow may
    # have changed or been downgraded since the last connection test) ---
    config = get_airflow_config(instance)
    api_status, _api_message, version, api_key, api_params = await test_instance_connection(config)
    checks.append(CheckResult("api_v2", "API v2 joignable, version ≥ 3.0.6, identifiants valides", api_status.value == "reachable", api_key, api_params))
    if version:
        instance.airflow_version = version

    deploy_access = instance.deploy_access
    if not deploy_access:
        checks.extend(_all_failed("deploy_access_missing"))
        return _finalize(checks)

    ssh_checks, witness_deposited = await run_in_threadpool(_run_ssh_checks, deploy_access)
    checks.extend(ssh_checks)

    if witness_deposited:
        try:
            found = None
            elapsed = 0
            while elapsed < REPARSE_POLL_SECONDS:
                found = await airflow_api.get_dag(config.base_url, config.username, config.password, WITNESS_DAG_ID)
                if found is not None:
                    break
                await asyncio.sleep(REPARSE_POLL_INTERVAL)
                elapsed += REPARSE_POLL_INTERVAL
            if found is not None:
                checks.append(CheckResult("reparse", "Reparse fonctionnel", True, "reparse_ok", {"seconds": elapsed + REPARSE_POLL_INTERVAL}))
                try:
                    await airflow_api.delete_dag(config.base_url, config.username, config.password, WITNESS_DAG_ID)
                except airflow_api.AirflowAPIError:
                    pass
            else:
                checks.append(CheckResult("reparse", "Reparse fonctionnel", False, "reparse_timeout", {"seconds": REPARSE_POLL_SECONDS}))
        finally:
            await run_in_threadpool(_cleanup_witness_dag_file, deploy_access)

    return _finalize(checks)


def _finalize(checks: list[CheckResult]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "deployable": all(c.passed for c in checks),
        "checks": [{"key": c.key, "label": c.label, "passed": c.passed, "message_key": c.message_key, "message_params": c.message_params} for c in checks],
        "checked_at": now.isoformat(),
    }
