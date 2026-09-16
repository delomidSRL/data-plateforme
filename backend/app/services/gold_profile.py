"""Profiles a published gold table for indicator suggestion (Module 12 étape 2) — schema +
aggregated statistics only, never raw rows (spec §4.2/§2 "hygiène de la donnée"). Reuses the
same warehouse-resolution mechanics as Module 10's preview and Module 11's publish.
"""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.services import connections
from app.services.medallion_stats import SCHEMA_BY_LAYER, table_name


@dataclass
class ColumnProfile:
    name: str
    sql_type: str
    role: str  # measure | dimension | temporal
    distinct_count: int
    null_rate: float
    min_value: str | None = None
    max_value: str | None = None
    sample_values: list[str] = field(default_factory=list)
    # Module 14 extension "primitives gold" — connections.profile_table() now always includes
    # this key (used by source_profile.py's own ColumnProfile for gold filter validation);
    # accepted here too so profile_gold_dataset()'s `ColumnProfile(**c)` doesn't choke on it,
    # even though it has no use for indicator suggestion (a gold table's own filter primitives
    # aren't validated against its own column values).
    filter_enum_values: list[str] | None = None
    # Annexe catalogue viz §4 — optional geo/relational flags, all False by default. No
    # detector sets any of these yet (v1 scope, §10: "à ouvrir sur cas d'usage client réel") —
    # their only purpose right now is to keep chart_catalog.py's Tier 3 (geo/graph/hierarchy)
    # entries automatically unproposable, since their `requires` preconditions can never be
    # satisfied without a real detector flipping one of these to True.
    is_geo_lat: bool = False
    is_geo_lon: bool = False
    is_geo_region_code: bool = False
    is_geo_polygon: bool = False
    is_node_id: bool = False
    is_hierarchy_id: bool = False
    is_hierarchy_parent: bool = False


@dataclass
class ProfileResult:
    status: str  # ok | not_gold | not_materialized | unreachable
    message: str | None = None
    columns: list[ColumnProfile] = field(default_factory=list)


def profile_gold_dataset(db: Session, project: MedallionProject, dataset: MedallionDataset) -> ProfileResult:
    if dataset.layer != MedallionLayer.gold:
        return ProfileResult(status="not_gold")

    table = table_name(dataset)
    if not table:
        return ProfileResult(status="not_materialized")

    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        return ProfileResult(status="not_materialized", message="Aucun warehouse configuré pour ce projet.")

    schema = SCHEMA_BY_LAYER[dataset.layer]
    secret = decrypt_secret(warehouse.secret_encrypted)

    try:
        exists = connections.table_exists(warehouse.type, warehouse.host, warehouse.port, warehouse.database_name, warehouse.username, secret, schema, table)
        if not exists:
            return ProfileResult(status="not_materialized")
        raw_columns = connections.profile_table(warehouse.type, warehouse.host, warehouse.port, warehouse.database_name, warehouse.username, secret, schema, table)
    except connections.SampleTimeout as exc:
        return ProfileResult(status="unreachable", message=str(exc))
    except Exception as exc:
        return ProfileResult(status="unreachable", message=connections.clean_error(exc))

    columns = [ColumnProfile(**c) for c in raw_columns]
    return ProfileResult(status="ok", columns=columns)
