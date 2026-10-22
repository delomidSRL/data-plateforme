from pydantic import BaseModel, Field

from app.models.medallion import LoadMode, Materialization, MedallionLayer, TransformType

# Bundle format for moving one medallion project between platform deployments ("Exporter" /
# "Importer un projet"). Real database ids never survive a move (the target deployment has its
# own id space entirely) — every cross-reference inside the bundle uses a bundle-local sequential
# id instead, remapped to real ids only once each dataset is actually (re)created on import.


class ExportedDataset(BaseModel):
    local_id: int
    layer: MedallionLayer
    name: str
    load_mode: LoadMode | None = None
    dbt_model_name: str | None = None
    sql: str | None = None
    materialization: Materialization | None = None
    transform_type: TransformType = TransformType.dbt
    upstream_local_ids: list[int] = Field(default_factory=list)
    description: str | None = None


class ExportedStructuration(BaseModel):
    dataset_local_id: int
    column_mapping: list[dict]


class ExportedMacro(BaseModel):
    name: str
    description: str | None = None
    definition: str


class ExportedDqFlag(BaseModel):
    flag_name: str
    category: str
    source_rule: str | None = None
    issue_type: str | None = None


class ExportedBronzeHint(BaseModel):
    """What the target deployment needs to (re)create this bronze entity's real data before
    the dataset itself can be created: the physical bronze table name every reference inside
    the bundle already assumes (dataset.name == bronze mirror table == FileImport.target_table,
    the same convention `_sources_yml` requires — see payload_structure.render_unpacked_typed_models),
    and, when still known, the original file name purely as a human hint in the re-upload wizard."""

    dataset_local_id: int
    target_table: str
    suggested_filename: str | None = None


class ProjectExportBundle(BaseModel):
    format_version: int = 1
    project_name: str
    dbt_project_name: str
    target: str
    description: str | None = None
    datasets: list[ExportedDataset]
    structurations: list[ExportedStructuration]
    macros: list[ExportedMacro]
    dq_flags: list[ExportedDqFlag]
    bronze_hints: list[ExportedBronzeHint]


class ProjectImportCommit(BaseModel):
    bundle: ProjectExportBundle
    project_name: str = Field(min_length=1, max_length=120)
    dbt_project_name: str = Field(min_length=1, max_length=120)
    object_store_source_id: int
    warehouse_source_id: int
    airflow_instance_id: int
    superset_instance_id: int | None = None
