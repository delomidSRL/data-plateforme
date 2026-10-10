"""Bouton RUN par nœud §2 — lit, par SSH, le fichier de progression qu'un dbt_run_silver/
dbt_run_gold en cours écrit au fil de l'eau (voir dbt_run.py.j2) ou l'instantané de son
run_results.json une fois terminé, pour donner à chaque nœud qui partage cette tâche Airflow
son propre statut plutôt que le statut global, grossier, de la tâche entière."""
import json

from app.services import ssh
from app.services.airflow_instances import DeployTarget

_PROGRESS_FILE = {"dbt_run_silver": "dp_progress_silver.jsonl", "dbt_run_gold": "dp_progress_gold.jsonl"}
_RESULT_SNAPSHOT = {"dbt_run_silver": "dp_results_silver.json", "dbt_run_gold": "dp_results_gold.json"}


def _read_remote_file(target: DeployTarget, path: str) -> str | None:
    try:
        with ssh.ssh_session(target.ssh_host, target.ssh_port, target.ssh_user, target.ssh_auth_method, target.ssh_secret) as client:
            sftp = client.open_sftp()
            try:
                with sftp.file(path, "r") as f:
                    return f.read().decode()
            finally:
                sftp.close()
    except Exception:
        return None


def fetch_incremental_progress(target: DeployTarget, task_id: str) -> dict[str, str]:
    """{model_name: status} from the task's own in-flight progress file — meaningful only
    while that task is still running; empty (not an error) if the file doesn't exist yet
    (task just started, or hasn't started) or the server is unreachable."""
    filename = _PROGRESS_FILE.get(task_id)
    if not filename:
        return {}
    content = _read_remote_file(target, f"{target.dbt_dir}/target/{filename}")
    if not content:
        return {}
    result: dict[str, str] = {}
    for line in content.splitlines():
        try:
            j = json.loads(line)
        except ValueError:
            continue
        name = j.get("name")
        if name:
            result[name] = j.get("status")
    return result


def fetch_final_results(target: DeployTarget, task_id: str) -> dict[str, dict]:
    """{model_name: {"status", "message"}} from THIS task's own preserved run_results.json
    snapshot — authoritative once the task has reached a terminal state. Reading the shared
    `target/run_results.json` directly (like medallion_stats's aggregate count does) would
    give silver's answer back for a gold node once dbt_run_gold has since overwritten it —
    each run_dbt() invocation copies its own result_snapshot right after it finishes, before
    the next one in the DAG can clobber the shared file."""
    filename = _RESULT_SNAPSHOT.get(task_id)
    if not filename:
        return {}
    content = _read_remote_file(target, f"{target.dbt_dir}/target/{filename}")
    if not content:
        return {}
    try:
        run_results = json.loads(content)
    except ValueError:
        return {}
    result: dict[str, dict] = {}
    for r in run_results.get("results", []):
        unique_id = r.get("unique_id") or ""
        name = unique_id.rsplit(".", 1)[-1] if unique_id else None
        if name:
            result[name] = {"status": r.get("status"), "message": r.get("message")}
    return result
