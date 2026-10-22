import re

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.data_source import DataSource
from app.models.dbt_macro import DbtMacro
from app.models.dq_flag_registry import DqFlagRegistryEntry
from app.models.file_import import FileImport, FileImportStatus
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.models.payload_structuration import PayloadStructuration
from app.schemas.project_portability import (
    ExportedBronzeHint,
    ExportedDataset,
    ExportedDqFlag,
    ExportedMacro,
    ExportedStructuration,
    ProjectExportBundle,
    ProjectImportCommit,
)
from app.services import medallion_crud, payload_structure

# Platform built-ins (payload_structure.STRUCTURATION_MACROS) — written unconditionally into
# every project by dbt_project.generate_project_files, so they already exist on any deployment
# of this same platform codebase. Only a DbtMacro row BEYOND this set is something the target
# deployment doesn't already have and actually needs shipped in the bundle.
_BUILTIN_MACRO_NAMES = {
    "clean_string", "safe_cast", "cast_issue", "normalize_for_matching", "clean_vat",
    "clean_phone", "format_flag", "placeholder_name_flag", "garbage_flag", "date_range_flag",
    "unmapped_boolean_flag",
}

_LAYER_ORDER = {MedallionLayer.bronze: 0, MedallionLayer.silver: 1, MedallionLayer.gold: 2}


def build_export_bundle(db: Session, project: MedallionProject) -> ProjectExportBundle:
    datasets = (
        db.query(MedallionDataset)
        .filter(MedallionDataset.project_id == project.id)
        .order_by(MedallionDataset.layer, MedallionDataset.id)
        .all()
    )
    local_id_by_real_id = {d.id: i + 1 for i, d in enumerate(datasets)}

    exported_datasets = [
        ExportedDataset(
            local_id=local_id_by_real_id[d.id], layer=d.layer, name=d.name, load_mode=d.load_mode,
            dbt_model_name=d.dbt_model_name, sql=d.sql, materialization=d.materialization,
            transform_type=d.transform_type, description=d.description,
            upstream_local_ids=[local_id_by_real_id[u] for u in d.upstream_dataset_ids if u in local_id_by_real_id],
        )
        for d in datasets
    ]

    bronze = [d for d in datasets if d.layer == MedallionLayer.bronze]
    structurations: list[ExportedStructuration] = []
    bronze_hints: list[ExportedBronzeHint] = []
    if bronze:
        rows = db.query(PayloadStructuration).filter(PayloadStructuration.dataset_id.in_([d.id for d in bronze])).all()
        by_dataset_id = {r.dataset_id: r for r in rows}
        for d in bronze:
            row = by_dataset_id.get(d.id)
            if row is not None:
                structurations.append(ExportedStructuration(dataset_local_id=local_id_by_real_id[d.id], column_mapping=row.column_mapping))

            # source_object is always "imports.<target_table>" (the real bronze mirror table —
            # see render_unpacked_typed_models's docstring on why dataset.name must equal it).
            target_table = d.source_object.split(".", 1)[1] if d.source_object and "." in d.source_object else d.name
            fi = (
                db.query(FileImport)
                .filter(FileImport.target_source_id == d.source_id, FileImport.target_table == target_table, FileImport.status == FileImportStatus.imported)
                .order_by(FileImport.id.desc())
                .first()
            )
            bronze_hints.append(ExportedBronzeHint(
                dataset_local_id=local_id_by_real_id[d.id], target_table=target_table,
                suggested_filename=fi.source_file_name if fi else None,
            ))

    sql_blob = "\n".join(d.sql for d in datasets if d.sql)
    macro_names = {m.name for m in db.query(DbtMacro.name).all()} - _BUILTIN_MACRO_NAMES
    referenced_macro_names = {name for name in macro_names if re.search(rf"\b{re.escape(name)}\s*\(", sql_blob)}
    macros = [
        ExportedMacro(name=m.name, description=m.description, definition=m.definition)
        for m in db.query(DbtMacro).filter(DbtMacro.name.in_(referenced_macro_names)).all()
    ] if referenced_macro_names else []

    flag_names = {f.flag_name for f in db.query(DqFlagRegistryEntry.flag_name).all()}
    referenced_flag_names = {name for name in flag_names if f"'{name}'" in sql_blob}
    dq_flags = [
        ExportedDqFlag(flag_name=f.flag_name, category=f.category.value, source_rule=f.source_rule, issue_type=f.issue_type)
        for f in db.query(DqFlagRegistryEntry).filter(DqFlagRegistryEntry.flag_name.in_(referenced_flag_names)).all()
    ] if referenced_flag_names else []

    return ProjectExportBundle(
        project_name=project.name, dbt_project_name=project.dbt_project_name,
        target=project.home_binding.target.value if project.home_binding else "dev",
        description=project.description, datasets=exported_datasets, structurations=structurations,
        macros=macros, dq_flags=dq_flags, bronze_hints=bronze_hints,
    )


def commit_import(db: Session, payload: ProjectImportCommit, create_project_fn, current_user, background_tasks) -> MedallionProject:
    """`create_project_fn` is routes.medallion.create_project itself, passed in by the caller
    (the route) to reuse its exact validation + home-binding + auto-scaffold logic rather than
    re-deriving it here — an import's project-creation step must be indistinguishable from the
    normal "Nouveau projet" flow."""
    from app.schemas.medallion import ProjectCreate

    bundle = payload.bundle
    warehouse = db.get(DataSource, payload.warehouse_source_id)
    if warehouse is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Entrepôt cible introuvable.")

    project = create_project_fn(
        ProjectCreate(
            name=payload.project_name, description=bundle.description,
            object_store_source_id=payload.object_store_source_id, warehouse_source_id=payload.warehouse_source_id,
            airflow_instance_id=payload.airflow_instance_id, superset_instance_id=payload.superset_instance_id,
            dbt_project_name=payload.dbt_project_name, target=bundle.target,
        ),
        background_tasks, db, current_user,
    )

    for m in bundle.macros:
        existing = db.query(DbtMacro).filter(DbtMacro.name == m.name).first()
        if existing is None:
            db.add(DbtMacro(name=m.name, description=m.description, definition=m.definition))
        else:
            existing.description = m.description
            existing.definition = m.definition

    for f in bundle.dq_flags:
        existing = db.query(DqFlagRegistryEntry).filter(DqFlagRegistryEntry.flag_name == f.flag_name).first()
        if existing is None:
            db.add(DqFlagRegistryEntry(flag_name=f.flag_name, category=f.category, source_rule=f.source_rule, issue_type=f.issue_type))
        else:
            existing.category = f.category
            existing.source_rule = f.source_rule
            existing.issue_type = f.issue_type
    db.flush()

    hints_by_local_id = {h.dataset_local_id: h for h in bundle.bronze_hints}
    structurations_by_local_id = {s.dataset_local_id: s for s in bundle.structurations}
    local_to_real: dict[int, int] = {}

    for ds in sorted(bundle.datasets, key=lambda d: _LAYER_ORDER[d.layer]):
        if ds.layer == MedallionLayer.bronze:
            hint = hints_by_local_id.get(ds.local_id)
            if hint is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Dataset bronze '{ds.name}' sans indication de fichier source dans le bundle.")
            fi = (
                db.query(FileImport)
                .filter(FileImport.target_source_id == payload.warehouse_source_id, FileImport.target_table == hint.target_table, FileImport.status == FileImportStatus.imported)
                .order_by(FileImport.id.desc())
                .first()
            )
            if fi is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Importez d'abord le fichier source pour '{hint.target_table}' (onglet Imports de fichiers) avant de terminer l'import du projet.",
                )
            dataset = medallion_crud.create_dataset_internal(db, project, _dataset_payload(ds, source_id=payload.warehouse_source_id, source_object=f"{fi.target_schema}.{fi.target_table}"))
            db.flush()
            local_to_real[ds.local_id] = dataset.id

            structuration = structurations_by_local_id.get(ds.local_id)
            if structuration is not None:
                column_mapping = structuration.column_mapping
                payload_structure.validate_column_mapping(column_mapping)
                payload_structure.render_unpacked_typed_models(column_mapping, dataset.name)
                payload_structure.materialize_unpacked_typed_sync(warehouse, column_mapping, dataset.name)
                db.add(PayloadStructuration(dataset_id=dataset.id, column_mapping=column_mapping, contract_hash=payload_structure.canonical_contract(column_mapping)))
        else:
            upstream_ids = [local_to_real[u] for u in ds.upstream_local_ids if u in local_to_real]
            dataset = medallion_crud.create_dataset_internal(db, project, _dataset_payload(ds, upstream_dataset_ids=upstream_ids))
            db.flush()
            local_to_real[ds.local_id] = dataset.id

    db.commit()
    db.refresh(project)
    return project


def _dataset_payload(ds: ExportedDataset, **overrides):
    from app.schemas.medallion import DatasetCreate

    fields = dict(
        layer=ds.layer, name=ds.name, load_mode=ds.load_mode, dbt_model_name=ds.dbt_model_name,
        sql=ds.sql, materialization=ds.materialization, transform_type=ds.transform_type,
        description=ds.description, upstream_dataset_ids=[],
    )
    fields.update(overrides)
    return DatasetCreate(**fields)
