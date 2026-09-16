from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, ProjectStatus, TransformType
from app.schemas.medallion import DatasetCreate

_LAYER_ORDER = {MedallionLayer.bronze: 0, MedallionLayer.silver: 1, MedallionLayer.gold: 2}


def validate_lineage(db: Session, pid: int, layer: MedallionLayer, upstream_ids: list[int], transform_type: TransformType = TransformType.dbt) -> None:
    if not upstream_ids:
        return
    upstreams = db.query(MedallionDataset).filter(MedallionDataset.id.in_(upstream_ids), MedallionDataset.project_id == pid).all()
    if len(upstreams) != len(set(upstream_ids)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Un ou plusieurs datasets amont sont introuvables.")
    for u in upstreams:
        if transform_type == TransformType.python:
            # Python/ML nodes may read an equal-or-lower layer (e.g. another gold mart),
            # unlike dbt nodes which must strictly move up a layer.
            if _LAYER_ORDER[u.layer] > _LAYER_ORDER[layer]:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{u.name}' ({u.layer.value}) est d'une couche supérieure à {layer.value}.")
        elif _LAYER_ORDER[u.layer] >= _LAYER_ORDER[layer]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{u.name}' ({u.layer.value}) n'est pas d'une couche inférieure à {layer.value}.")


def create_dataset_internal(db: Session, project: MedallionProject, payload: DatasetCreate) -> MedallionDataset:
    """Shared by the HTTP route (routes/medallion.py) and the Module 13 execution machine
    (services/pipeline_execute.py) — a plan-generated dataset must be indistinguishable from
    one created by hand (§6.5.4), so both paths go through the exact same construction.
    Flushes (not commits) — the caller controls the transaction boundary, since Module 13
    creates bronze/silver/gold in one pass and needs earlier rows' real ids before the later
    ones' upstream_dataset_ids can be resolved."""
    validate_lineage(db, project.id, payload.layer, payload.upstream_dataset_ids, payload.transform_type)
    dataset = MedallionDataset(project_id=project.id, **payload.model_dump())
    db.add(dataset)
    db.flush()
    if project.status in (ProjectStatus.deployed, ProjectStatus.paused):
        # A paused project still has a real, live DAG in Airflow (just not currently
        # triggering) — an edit staled it exactly as much as it would a running one. Missing
        # this case meant editing anything on a paused project never showed "à redéployer".
        project.has_pending_changes = True
    return dataset


def upsert_dataset_internal(db: Session, project: MedallionProject, payload: DatasetCreate) -> MedallionDataset:
    """Module 13 execution only (services/pipeline_execute.py) — create_dataset_internal()
    always creates, which is right for the manual "Ajouter un dataset" flow but wrong here:
    execution_state.dataset_ids (the only thing that normally makes create_datasets() a
    one-time step) gets wiped every time the engineer re-runs map-intent/plan on the same
    project, so re-executing after a reformulated instruction would otherwise blindly create
    a second "sales" bronze dataset alongside the first — same name, same source — and
    Airflow's DAG then fails outright with a duplicate task id. Matching by (project, layer,
    name) first, and updating in place when found, makes re-execution idempotent by name
    regardless of what execution_state remembers."""
    existing = (
        db.query(MedallionDataset)
        .filter(MedallionDataset.project_id == project.id, MedallionDataset.layer == payload.layer, MedallionDataset.name == payload.name)
        .first()
    )
    if existing is None:
        return create_dataset_internal(db, project, payload)

    validate_lineage(db, project.id, payload.layer, payload.upstream_dataset_ids, payload.transform_type)
    for field, value in payload.model_dump(exclude={"layer", "name"}).items():
        setattr(existing, field, value)
    if project.status in (ProjectStatus.deployed, ProjectStatus.paused):
        project.has_pending_changes = True
    return existing
