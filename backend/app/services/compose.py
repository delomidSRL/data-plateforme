from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from app.models.infra_stack import InfraStack
from app.services.stack_secrets import decrypt_services, mask_services

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    keep_trailing_newline=True,
)

AIRFLOW_IMAGE_TAG = "dataplateforme-airflow-dbt:latest"
AIRFLOW_UID = 50000
SUPERSET_IMAGE_TAG = "dataplateforme-superset-pg:latest"
JUPYTER_IMAGE_TAG = "dataplateforme-jupyter-ml:latest"


def stack_proj_dir(stack_id: int) -> str:
    return f"/opt/dataplateforme/stacks/{stack_id}"


def _build_context(stack: InfraStack, masked: bool = False) -> dict:
    decrypted = decrypt_services(stack.services)
    if masked:
        decrypted = mask_services(decrypted)

    postgres = decrypted.get("postgres") if (decrypted.get("postgres") or {}).get("enabled") else None
    minio = decrypted.get("minio") if (decrypted.get("minio") or {}).get("enabled") else None

    superset_raw = decrypted.get("superset")
    superset = None
    if superset_raw and superset_raw.get("enabled"):
        superset = {**superset_raw, "image": SUPERSET_IMAGE_TAG, "proj_dir": stack_proj_dir(stack.id)}

    airflow_raw = decrypted.get("airflow")
    airflow = None
    if airflow_raw and airflow_raw.get("enabled"):
        airflow = {
            **airflow_raw,
            "image": AIRFLOW_IMAGE_TAG,
            "uid": AIRFLOW_UID,
            "proj_dir": stack_proj_dir(stack.id),
        }

    jupyter_raw = decrypted.get("jupyter")
    jupyter = None
    if jupyter_raw and jupyter_raw.get("enabled"):
        jupyter = {
            **jupyter_raw,
            "image": JUPYTER_IMAGE_TAG,
            "proj_dir": stack_proj_dir(stack.id),
            "mount_dbt": bool(jupyter_raw.get("mount_dbt", True)) and bool(airflow),
        }

    return {
        "services": {"postgres": postgres, "minio": minio, "superset": superset, "airflow": airflow, "jupyter": jupyter},
        "postgres": postgres or {},
        "minio": minio or {},
        "superset": superset or {},
        "airflow": airflow or {},
        "jupyter": jupyter or {},
    }


def render_compose(stack: InfraStack, masked: bool = False) -> str:
    ctx = _build_context(stack, masked=masked)
    template = _env.get_template("compose/root.yml.j2")
    rendered = template.render(**ctx)

    # validate structural correctness before handing it back
    yaml.safe_load(rendered)
    return rendered
