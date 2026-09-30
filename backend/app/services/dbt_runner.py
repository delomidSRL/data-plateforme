"""Module 19 étape 4 §6.2 — `dbt compile` / `dbt build` against the project's OWN dev binding.
§2 "exécution dev seulement" (non-négociable) — this module resolves EXCLUSIVELY the
`environment=dev` binding, never any other one, and `run_dev` refuses outright if the project
doesn't have one. Unlike workspace_sync.sync()'s throwaway `dbt parse` (a fake, never-connected
profile), compile/run-dev use the dev binding's REAL warehouse credentials — that's the whole
point of "tester sur dev, voir les données" (§6.1); compile still works without a live
connection when there's no dev binding at all (§6.6 DoD #4), same as workspace_sync's own
never-connected parse."""
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt_secret
from app.db.session import engine
from app.models.data_source import DataSource
from app.models.medallion import MedallionProject
from app.models.project_environment_binding import ProjectEnvironmentBinding
from app.models.server import Environment
from app.services import dbt_project, workspace
from app.services.workspace_sync import dbt_bin, ensure_packages_cache

# Distinct namespace from Module 6 extension's watch_lock_key (727001) — same
# pg_try_advisory_lock mechanic, but keyed per-project (§6.2 "un seul run concurrent par
# projet"), not a single platform-wide lock.
DBT_RUNNER_LOCK_NAMESPACE = 727010

_PATH_IN_MSG_RE = re.compile(r"\(([^()]+\.(?:sql|yml|yaml))\)")
_LINE_IN_MSG_RE = re.compile(r"\bline (\d+)\b")


class DbtRunnerError(Exception):
    pass


class NoDevBindingError(DbtRunnerError):
    """§6.6 DoD #4 — compile still works (no connection needed without one); run-dev refuses
    with this instead."""


class RunnerBusyError(DbtRunnerError):
    """§6.2 — a second concurrent run-dev on the same project gets this, cleanly."""


@dataclass
class RunnerError:
    path: str | None
    line: int | None
    column: int | None
    message: str

    def as_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "column": self.column, "message": self.message}


@dataclass
class CompileResult:
    ok: bool
    compiled_sql: dict[str, str] = field(default_factory=dict)
    errors: list[RunnerError] = field(default_factory=list)


@dataclass
class NodeResult:
    unique_id: str
    name: str
    resource_type: str
    status: str
    execution_time: float | None = None


@dataclass
class RunResult:
    ok: bool
    nodes: list[NodeResult] = field(default_factory=list)
    errors: list[RunnerError] = field(default_factory=list)


def dev_binding(project: MedallionProject) -> ProjectEnvironmentBinding | None:
    for b in project.bindings:
        if b.environment == Environment.dev:
            return b
    return None


def _warehouse_dict(db: Session, binding: ProjectEnvironmentBinding) -> dict:
    warehouse = db.get(DataSource, binding.warehouse_source_id)
    return {
        "host": warehouse.host, "port": warehouse.port, "username": warehouse.username,
        "password": decrypt_secret(warehouse.secret_encrypted), "database_name": warehouse.database_name,
    }


def _fake_profiles_yml(project: MedallionProject) -> dict:
    """Same shape-only, never-connected profile as workspace_sync's own — used only when
    there is no dev binding at all, for a compile-only call (§6.6 DoD #4)."""
    return {project.dbt_project_name: {"target": "dev", "outputs": {"dev": {
        "type": "postgres", "host": "localhost", "port": 5432, "user": "x", "password": "x",
        "dbname": "x", "schema": "silver", "threads": 4,
    }}}}


def _prepare_project_dir(db: Session, project: MedallionProject, binding: ProjectEnvironmentBinding | None) -> Path:
    tree = workspace.export_tree(db, project)
    tmpdir = Path(tempfile.mkdtemp(prefix="dbtrun_"))
    for rel_path, content in tree.items():
        full = tmpdir / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    profiles = dbt_project._profiles_yml(project, _warehouse_dict(db, binding)) if binding is not None else _fake_profiles_yml(project)
    (tmpdir / "profiles.yml").write_text(yaml.safe_dump(profiles, sort_keys=False), encoding="utf-8")

    if "packages.yml" in tree:
        cache_dir = ensure_packages_cache(get_settings())
        pkg_dir = tmpdir / "dbt_packages"
        pkg_dir.mkdir(exist_ok=True)
        for child in cache_dir.iterdir():
            if child.name == ".seeded":
                continue
            shutil.copytree(child, pkg_dir / child.name)

    return tmpdir


def _run_dbt_json(cmd: list[str], timeout: int) -> tuple[int, list[dict]]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, [{"info": {"level": "error"}, "data": {"exc": f"Délai dépassé ({timeout}s)."}}]
    log_lines = []
    for line in result.stdout.splitlines():
        try:
            log_lines.append(json.loads(line))
        except ValueError:
            continue
    return result.returncode, log_lines


def _extract_errors(log_lines: list[dict]) -> list[RunnerError]:
    errors = []
    for j in log_lines:
        info = j.get("info", {})
        if info.get("level") != "error":
            continue
        msg = (j.get("data", {}) or {}).get("exc") or info.get("msg") or "Erreur dbt inconnue."
        path_m = _PATH_IN_MSG_RE.search(msg)
        line_m = _LINE_IN_MSG_RE.search(msg)
        errors.append(RunnerError(
            path=path_m.group(1).replace("\\", "/") if path_m else None,
            line=int(line_m.group(1)) if line_m else None,
            column=None, message=msg.strip(),
        ))
    if not errors:
        errors.append(RunnerError(path=None, line=None, column=None, message="dbt a échoué sans message d'erreur exploitable."))
    return errors


def compile(db: Session, project: MedallionProject, select: str | None = None) -> CompileResult:
    """§6.2 — works with or without a dev binding (no connection is actually opened by a
    plain `dbt compile` on well-formed models; the jinja guard already forbids the constructs
    that would need one, e.g. `run_query`)."""
    settings = get_settings()
    binding = dev_binding(project)
    tmpdir = _prepare_project_dir(db, project, binding)
    try:
        cmd = [dbt_bin(), "compile", "--project-dir", str(tmpdir), "--profiles-dir", str(tmpdir), "--no-use-colors", "--log-format", "json"]
        if select:
            cmd += ["--select", select]
        returncode, log_lines = _run_dbt_json(cmd, settings.dbt_runner_timeout_s)
        if returncode != 0:
            return CompileResult(ok=False, errors=_extract_errors(log_lines))

        compiled_root = tmpdir / "target" / "compiled" / project.dbt_project_name
        compiled_sql: dict[str, str] = {}
        if compiled_root.exists():
            for f in compiled_root.rglob("*.sql"):
                rel = f.relative_to(compiled_root)
                compiled_sql[str(rel).replace("\\", "/")] = f.read_text(encoding="utf-8")
        return CompileResult(ok=True, compiled_sql=compiled_sql)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _extract_node_results(log_lines: list[dict]) -> list[NodeResult]:
    """dbt's JSON log emits one `LogModelResult`/`LogTestResult`/`LogSeedResult` event per
    finished node (verified live against dbt-core 1.8.8), each carrying a `node_info` block
    (unique_id, node_name, resource_type, node_status) — matched on the presence of that
    block rather than the specific event name, so a node kind this hasn't been tested against
    (e.g. a snapshot) still surfaces instead of silently vanishing."""
    nodes: dict[str, NodeResult] = {}
    for j in log_lines:
        data = j.get("data", {})
        node_info = data.get("node_info")
        if not node_info or not node_info.get("unique_id"):
            continue
        status = node_info.get("node_status")
        # A failed node also emits a later `RunResultError` event whose own `node_status` is
        # the literal STRING "None" (verified live, dbt-core 1.8.8) — never let that clobber
        # the real terminal status ("error"/"fail"/…) `LogModelResult`/`LogTestResult` already
        # recorded for this node.
        if status in (None, "None", "started"):
            continue
        unique_id = node_info["unique_id"]
        nodes[unique_id] = NodeResult(
            unique_id=unique_id,
            name=node_info.get("node_name") or unique_id,
            resource_type=node_info.get("resource_type") or "model",
            status=status,
            execution_time=data.get("execution_time"),
        )
    return list(nodes.values())


def run_dev(db: Session, project: MedallionProject, select: str) -> RunResult:
    """§6.2/§6.4 — `select` is mandatory (§6.4: "pas de run complet depuis l'éditeur en v1").
    One run at a time per project (advisory lock); a project without a dev binding is refused
    outright, never silently falls back to another environment."""
    if not select or not select.strip():
        raise DbtRunnerError("Un `select` est obligatoire pour exécuter en dev.")
    binding = dev_binding(project)
    if binding is None:
        raise NoDevBindingError("Ce projet n'a pas de binding dev — exécution impossible.")

    settings = get_settings()
    lock_conn = engine.connect()
    try:
        got_lock = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:ns, :pid)"), {"ns": DBT_RUNNER_LOCK_NAMESPACE, "pid": project.id},
        ).scalar()
        if not got_lock:
            raise RunnerBusyError("Une exécution en dev est déjà en cours pour ce projet.")
        try:
            tmpdir = _prepare_project_dir(db, project, binding)
            try:
                cmd = [
                    dbt_bin(), "build", "--select", select,
                    "--project-dir", str(tmpdir), "--profiles-dir", str(tmpdir),
                    "--no-use-colors", "--log-format", "json",
                ]
                returncode, log_lines = _run_dbt_json(cmd, settings.dbt_runner_timeout_s)
                nodes = _extract_node_results(log_lines)
                if not nodes:
                    return RunResult(ok=False, errors=_extract_errors(log_lines))
                ok = returncode == 0 and all(n.status in ("success", "pass") for n in nodes)
                return RunResult(ok=ok, nodes=nodes, errors=[] if ok else _extract_errors(log_lines))
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            try:
                lock_conn.execute(text("SELECT pg_advisory_unlock(:ns, :pid)"), {"ns": DBT_RUNNER_LOCK_NAMESPACE, "pid": project.id})
            except Exception:
                pass
    finally:
        lock_conn.close()
