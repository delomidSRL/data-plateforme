import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, TransformType

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), keep_trailing_newline=True)

DBT_BIN = "/home/airflow/dbt_venv/bin/dbt"


def _safe_identifier(name: str) -> str:
    ident = re.sub(r"\W", "_", name.strip().lower())
    if ident and ident[0].isdigit():
        ident = f"_{ident}"
    return ident or "dataset"


def conn_id_for_source(source_id: int) -> str:
    return f"dp_source_{source_id}"


def conn_id_for_project_role(project_id: int, role: str) -> str:
    """Module 17 — a project-level, environment-STABLE connection id for the project's own
    warehouse/object store. Unlike conn_id_for_source (keyed by a DataSource's own id, which
    differs between the dev and prod bindings), this is a pure function of (project_id, role):
    the same string whether the frozen DAG it's baked into gets deployed to dev or promoted to
    prod (§2's "connexions résolues au déploiement" — only the Airflow connection's VALUE
    changes per binding, never the KEY the rendered DAG references). Bronze sources don't need
    this: MedallionDataset.source_id is already project-level/environment-agnostic, so
    conn_id_for_source(dataset.source_id) is already stable across bindings as-is."""
    return f"dp_project_{project_id}_{role}"


def dag_id_for_project(project: MedallionProject) -> str:
    """The BASE, environment-agnostic dag_id — what render_dag() always embeds, and the only
    form ever captured into a MedallionVersion snapshot (§2: an instantané never contains a
    suffixed dag_id). The actually-deployed, environment-suffixed id is dag_id_for_environment,
    applied to a rendered/captured DAG's text only at deployment time (apply_environment_suffix)."""
    return f"medallion_{project.id}_{_safe_identifier(project.dbt_project_name)}"


def dag_id_for_environment(project: MedallionProject, environment: str) -> str:
    """Module 17 §2 — the dag_id actually registered in Airflow and used for the deployed
    file's name, resolved at deployment time, never baked into a captured snapshot."""
    return f"{dag_id_for_project(project)}__{environment}"


def apply_environment_suffix(dag_content: str, project: MedallionProject, environment: str) -> str:
    """The one deterministic, targeted substitution that turns a captured/rendered DAG's
    text (always carrying the BASE dag_id, §2) into what actually gets deposited for a given
    binding — never a re-render, so every other line of the "frozen" DAG stays byte-identical.
    Safe because render_dag() emits the DAG constructor's `dag_id=` argument in exactly one,
    fixed, quoted form; a target file that doesn't contain it (template shape changed, or this
    was already suffixed) fails loudly rather than silently deploying the wrong dag_id."""
    base = dag_id_for_project(project)
    old = f'dag_id="{base}",'
    new = f'dag_id="{dag_id_for_environment(project, environment)}",'
    if old not in dag_content:
        raise ValueError(f"apply_environment_suffix: base dag_id anchor {old!r} not found in DAG content for project {project.id}.")
    return dag_content.replace(old, new, 1)


def dbt_project_host_dir(project: MedallionProject, stack_proj_dir: str) -> str:
    """Path on the target server's filesystem, where we SFTP the dbt project to."""
    return f"{stack_proj_dir}/dbt/{project.dbt_project_name}"


def dbt_project_container_dir(project: MedallionProject) -> str:
    """Path inside the Airflow containers — dbt/ is always mounted at /opt/airflow/dbt,
    regardless of where it lives on the host. This is what dbt/BashOperator must use."""
    return f"/opt/airflow/dbt/{project.dbt_project_name}"


def _dataset_table_name(ds: MedallionDataset) -> str:
    if ds.layer == MedallionLayer.bronze:
        return ds.name
    if ds.transform_type == TransformType.python:
        return ds.output_table or ds.name
    return ds.dbt_model_name or ds.name


def render_dag(project: MedallionProject, datasets: list[MedallionDataset], object_store: DataSource, dbt_bin: str = DBT_BIN) -> str:
    bronze = [d for d in datasets if d.layer == MedallionLayer.bronze]
    gold = [d for d in datasets if d.layer == MedallionLayer.gold]
    ml_nodes = [d for d in gold if d.transform_type == TransformType.python]
    by_id = {d.id: d for d in datasets}

    bronze_ctx = [
        {
            "safe_name": _safe_identifier(b.name),
            "name": b.name,
            "source_conn_id": conn_id_for_source(b.source_id),
            "source_object": b.source_object,
            "load_mode": b.load_mode.value if b.load_mode else "full",
            "incremental_key": b.incremental_key,
        }
        for b in bronze
    ]

    ml_ctx = [
        {
            "safe_name": _safe_identifier(m.name),
            "output_table": _dataset_table_name(m),
            "input_schemas": {
                _dataset_table_name(by_id[uid]): by_id[uid].layer.value
                for uid in m.upstream_dataset_ids
                if uid in by_id
            },
            "python_code_repr": repr(m.python_code or ""),
        }
        for m in ml_nodes
    ]

    ctx = {
        "project": project,
        "dag_id": dag_id_for_project(project),
        "dbt_project_dir": dbt_project_container_dir(project),
        "dbt_bin": dbt_bin,
        # Module 17 — project-role-based, not source-id-based: stays the same string whether
        # this DAG (rendered once, at capture time) is later redeployed to a different binding
        # whose warehouse/object store is a completely different DataSource (see
        # conn_id_for_project_role's own docstring).
        "warehouse_conn_id": conn_id_for_project_role(project.id, "warehouse"),
        "object_store_conn_id": conn_id_for_project_role(project.id, "object_store"),
        "bronze_bucket": object_store.name and _default_bucket(object_store),
        "bronze_datasets": bronze_ctx,
        "ml_nodes": ml_ctx,
        # Literal, never-moving start_date (Module 3 correctif, décision D) — the project's
        # own creation date, baked into the .py at render time. Never datetime.now(): a
        # moving start_date makes the scheduler's notion of "missed intervals" moving too.
        "created_year": project.created_at.year,
        "created_month": project.created_at.month,
        "created_day": project.created_at.day,
    }

    template = _env.get_template("medallion/dag.py.j2")
    rendered = template.render(**ctx)

    compile(rendered, filename=f"{ctx['dag_id']}.py", mode="exec")
    return rendered


def _default_bucket(object_store: DataSource) -> str:
    # Same "bucket" option already exposed on the source form to scope test/introspect to
    # one bucket for least-privilege IAM (see connections.py) — reused here so a bucket the
    # user pre-created for that reason is also the one the pipeline archives bronze into,
    # instead of the platform silently assuming it can create its own "dataplateforme-bronze".
    return (object_store.options or {}).get("bucket") or "dataplateforme-bronze"
