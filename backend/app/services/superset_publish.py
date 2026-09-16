"""Publish a gold MedallionDataset as a Superset dataset (Module 11 étape 2) — idempotent:
republishing reuses the same `dp_`-prefixed database/dataset objects instead of duplicating
them, tracked via SupersetPublication. Read-only towards the warehouse (Superset does the
reading); this module only ever creates/updates objects it owns (the `dp_` namespace).
"""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.dashboard_spec import DashboardSpec
from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.models.superset_instance import SupersetInstance
from app.models.superset_publication import SupersetPublication
from app.services import chart_catalog, connections, superset_api, superset_templates
from app.services.medallion_stats import SCHEMA_BY_LAYER, table_name
from app.services.superset_instances import NoSupersetInstanceConfigured, SupersetConfig, SupersetInstanceError, get_superset_config, resolve_project_instance

GOLD_SCHEMA = SCHEMA_BY_LAYER[MedallionLayer.gold]


@dataclass
class PublishOutcome:
    status: str  # ok | not_materialized | not_gold | unreachable | bad_credentials | superset_error
    message: str | None = None
    url: str | None = None
    superset_dataset_id: int | None = None
    columns_count: int | None = None


def _warehouse_database_name(warehouse: DataSource) -> str:
    # Keyed by the warehouse DataSource's own id, not the project's dbt_project_name — several
    # projects can share one warehouse, and the spec's invariant ("une base par (instance,
    # warehouse)") requires they all resolve to the SAME Superset database, not one each.
    return f"dp_wh_{warehouse.id}"


def _dataset_name(project: MedallionProject, dataset: MedallionDataset) -> str:
    return f"dp_{project.dbt_project_name}__{dataset.name}"


def _warehouse_sqlalchemy_uri(warehouse: DataSource) -> str:
    secret = decrypt_secret(warehouse.secret_encrypted)
    return f"postgresql+psycopg2://{warehouse.username}:{secret}@{warehouse.host}:{warehouse.port}/{warehouse.database_name}"


def _classify_error(exc: superset_api.SupersetAPIError) -> str:
    if exc.status_code in (401, 403):
        return "bad_credentials"
    if exc.status_code is None:
        return "unreachable"
    return "superset_error"


async def publish_dataset(
    db: Session, project: MedallionProject, dataset: MedallionDataset, published_by_id: int, instance: SupersetInstance | None = None,
) -> PublishOutcome:
    if dataset.layer != MedallionLayer.gold:
        return PublishOutcome(status="not_gold")

    table = table_name(dataset)
    if not table:
        return PublishOutcome(status="not_materialized")

    warehouse = db.get(DataSource, project.warehouse_source_id)
    if warehouse is None:
        return PublishOutcome(status="not_materialized", message="Aucun warehouse configuré pour ce projet.")

    try:
        exists = connections.table_exists(
            warehouse.type, warehouse.host, warehouse.port, warehouse.database_name, warehouse.username, decrypt_secret(warehouse.secret_encrypted), GOLD_SCHEMA, table,
        )
    except Exception as exc:
        return PublishOutcome(status="unreachable", message=connections.clean_error(exc))
    if not exists:
        return PublishOutcome(status="not_materialized")

    if instance is None:
        try:
            instance = resolve_project_instance(db, project)
        except NoSupersetInstanceConfigured:
            return PublishOutcome(status="no_instance")
        except SupersetInstanceError as exc:
            return PublishOutcome(status="superset_error", message=str(exc))

    config = get_superset_config(instance)

    try:
        database = await superset_api.ensure_database(
            config.base_url, config.username, config.password, _warehouse_database_name(warehouse), _warehouse_sqlalchemy_uri(warehouse),
        )
        existing = await superset_api.find_dataset(config.base_url, config.username, config.password, database["id"], GOLD_SCHEMA, table)
        if existing is None:
            created = await superset_api.create_dataset(config.base_url, config.username, config.password, database["id"], GOLD_SCHEMA, table)
            superset_dataset_id = created["id"]
        else:
            superset_dataset_id = existing["id"]
            await superset_api.refresh_dataset(config.base_url, config.username, config.password, superset_dataset_id)
        detail = await superset_api.get_dataset(config.base_url, config.username, config.password, superset_dataset_id)
    except superset_api.SupersetAPIError as exc:
        return PublishOutcome(status=_classify_error(exc), message=str(exc))

    columns_count = len(detail.get("columns", []))
    published_url = f"{config.base_url.rstrip('/')}/explore/?datasource_type=table&datasource_id={superset_dataset_id}"

    pub = (
        db.query(SupersetPublication)
        .filter(SupersetPublication.medallion_dataset_id == dataset.id, SupersetPublication.superset_instance_id == instance.id)
        .first()
    )
    pub = pub or SupersetPublication(medallion_dataset_id=dataset.id, project_id=project.id, superset_instance_id=instance.id)
    pub.superset_database_id = database["id"]
    pub.superset_dataset_id = superset_dataset_id
    pub.published_url = published_url
    pub.published_by = published_by_id
    pub.last_published_at = datetime.now(timezone.utc)
    db.add(pub)
    db.commit()

    return PublishOutcome(status="ok", url=published_url, superset_dataset_id=superset_dataset_id, columns_count=columns_count)


async def unpublish_dataset(db: Session, dataset_id: int) -> bool:
    """Deletes only the dataset object this control plane manages for this (dataset,
    instance) pair — never the shared `dp_wh_*` database, which other published datasets on
    the same warehouse may still depend on."""
    pub = db.query(SupersetPublication).filter(SupersetPublication.medallion_dataset_id == dataset_id).first()
    if pub is None:
        return False
    instance = db.get(SupersetInstance, pub.superset_instance_id)
    if instance is not None:
        config = get_superset_config(instance)
        try:
            await superset_api.delete_dataset(config.base_url, config.username, config.password, pub.superset_dataset_id)
        except superset_api.SupersetAPIError:
            pass  # best-effort — the local record is removed regardless (e.g. already gone upstream)
    db.delete(pub)
    db.commit()
    return True


# ---------------- Module 12 — chart generation from a semantic indicator ----------------

@dataclass
class IndicatorSpec:
    """The validated, human-adjusted contract for one chart — never Mistral's raw output
    (see services/indicator_suggest.py), never SQL, never a Superset payload. Just semantics.

    Annexe catalogue viz §3 decision 3 — gained an optional `slots` field for viz_type that
    don't fit the original flat shape (bubble_v2's x/y/size, gantt_chart's start/end/series,
    mixed_timeseries' two metric sets, radar's multi-metric, pivot_table_v2's rows/columns...).
    The flat fields below remain the PRIMARY shape and stay fully populated for every
    single-metric type — the large majority of the catalogue, including every NEW simple type
    the annexe adds — so every existing caller (routes/schemas/frontend) that only ever reads
    the flat fields keeps working completely unchanged. `build_chart_payload` dispatches
    through `slots` only when it's set; otherwise it uses the flat fields exactly as before."""
    title: str
    viz_type: str
    metric_column: str = ""
    aggregation: str = ""
    dimension_columns: list[str] = field(default_factory=list)
    time_column: str | None = None
    slots: dict[str, dict] | None = None  # {slot_name: {"columns": [...], "aggregation": str|None}}
    # Top/Bottom N — only meaningful for RANKING_CAPABLE_VIZ_TYPES (chart_catalog's own
    # supports_ranking flag); {"limit": int, "direction": "top"|"bottom"}. AI-proposed only —
    # no manual UI for it yet, same scope boundary as multi-slot types.
    ranking: dict | None = None


def _resolve_slots_for_template(viz_type: str, indicator_slots: dict[str, dict], by_name: dict[str, dict]) -> dict:
    """Annexe catalogue viz §7 — turns the validated, name-only `IndicatorSpec.slots` (as
    produced by indicator_suggest._validate_indicator) into what TEMPLATE_REGISTRY's lambdas
    consume: every present slot resolves to a LIST (list[MetricSpec] for a measure-role slot,
    list[str] of real column names otherwise), uniformly, regardless of the catalogue's own
    min/max for that slot — a single unfilled optional slot is simply absent from the result.
    Each lambda alone knows whether ITS OWN Superset params field wants that list as-is or a
    scalar out of it (`slots["metric"][0]`), same as it always has for the original 5 types."""
    slot_defs = chart_catalog.CATALOG[viz_type]["data_slots"]
    resolved: dict = {}
    for slot_name, fill in indicator_slots.items():
        cols = fill["columns"]
        for c in cols:
            if c not in by_name:
                raise ValueError(f"Colonne introuvable dans le dataset : {c}")
        if slot_defs[slot_name]["role"] == "measure":
            resolved[slot_name] = [superset_templates.MetricSpec(column=by_name[c], aggregate=fill["aggregation"]) for c in cols]
        else:
            resolved[slot_name] = [by_name[c]["column_name"] for c in cols]
    return resolved


# Frontend follow-up to the annexe (IndicatorEditorList.jsx's manual editor) — every catalogue
# type whose shape is exactly one measure-role slot + at most one dimension-role slot + at most
# one temporal-role slot, so the legacy metric_column/aggregation/dimension_columns/time_column
# fields carry enough information to reconstruct a `slots` fill unambiguously. Every OTHER new
# type (pivot_table_v2's rows vs columns, bubble_v2's x/y/size, gantt_chart's start/end/series,
# box_plot's/histogram_v2's aggregation-less measures...) has more than one slot of the same
# role or an aggregation-forbidding measure slot — the flat contract can't disambiguate those,
# so they stay reachable only via an AI proposal (which always carries a real `slots` dict).
SIMPLE_FLAT_VIZ_TYPES = (
    "big_number", "pop_kpi", "funnel", "gauge_chart", "treemap_v2", "ag_grid", "sunburst_v2",
    "partition", "word_cloud", "cal_heatmap", "time_pivot", "time_table", "bullet",
    "echarts_area", "echarts_timeseries_smooth", "echarts_timeseries_step", "echarts_timeseries", "echarts_timeseries_scatter",
)


def _slots_from_flat(viz_type: str, metric_column: str, aggregation: str, dimension_columns: list[str], time_column: str | None) -> dict[str, dict]:
    """Synthesizes a `slots` fill from the legacy flat fields for a SIMPLE_FLAT_VIZ_TYPES
    entry — role-driven (chart_catalog's own data_slots), not name-driven, so it works
    unchanged regardless of whether the catalogue calls its dimension slot "groupby",
    "columns", or "series". Raises ValueError, same style/spirit as the original per-type
    checks below, when a required slot has no column to fill it."""
    slot_defs = chart_catalog.CATALOG[viz_type]["data_slots"]
    slots: dict[str, dict] = {}
    for slot_name, spec in slot_defs.items():
        role = spec["role"]
        if role == "measure":
            cols = [metric_column] if metric_column else []
            agg = aggregation if aggregation else None
            if cols and spec.get("aggregation") and agg not in superset_templates.WHITELISTED_AGGREGATIONS:
                raise ValueError(f"Agrégation non autorisée : {aggregation}")
        elif role == "temporal":
            cols = [time_column] if time_column else []
            agg = None
        elif role == "dimension":
            cols = list(dimension_columns or [])
            agg = None
        else:
            continue  # not reachable for a SIMPLE_FLAT_VIZ_TYPES entry (geo/node/hierarchy/raw_column)
        if not cols:
            if spec["required"]:
                raise ValueError(f"{viz_type} nécessite une colonne pour « {slot_name} ».")
            continue
        slots[slot_name] = {"columns": cols, "aggregation": agg}
    return slots


# Top/Bottom N — deliberately narrow: only viz_types built around a plain row-per-dimension-
# value shape with a real "sort by this column" field (table/ag_grid's `order_by_cols`) can
# express a ranked, row-limited leaderboard. Every other type either has no such field in its
# captured template (funnel, treemap_v2...) or already hard-codes its own sort semantics (pie's
# `sort_by_metric`) — extending ranking to those would mean capturing/verifying a new per-type
# mechanism each, not done here.
RANKING_CAPABLE_VIZ_TYPES = ("table", "ag_grid")


def _apply_ranking(params: dict, viz_type: str, ranking: dict) -> dict:
    """Mutates a built table/ag_grid `params` dict to express "Top N" (direction=top, highest
    values first) or "Bottom N" (direction=bottom, lowest values first) on its first metric —
    verified against the real Superset instance: `order_by_cols` takes a list of one
    JSON-stringified `[metric_or_column_dict, ascending_bool]` pair."""
    if viz_type not in RANKING_CAPABLE_VIZ_TYPES:
        raise ValueError(f"Le classement (Top/Bottom N) n'est pas pris en charge pour {viz_type}.")
    limit = ranking.get("limit")
    direction = ranking.get("direction")
    if not isinstance(limit, int) or not (1 <= limit <= 100):
        raise ValueError("ranking.limit doit être un entier entre 1 et 100.")
    if direction not in ("top", "bottom"):
        raise ValueError("ranking.direction doit être « top » ou « bottom ».")
    metrics = params.get("metrics") or []
    if not metrics:
        return params  # nothing to sort by — leave row_limit/order untouched
    ascending = direction == "bottom"
    params["row_limit"] = limit
    params["order_by_cols"] = [json.dumps([metrics[0], ascending])]
    params["order_desc"] = not ascending
    return params


def build_chart_payload(indicator: IndicatorSpec, superset_columns: list[dict], datasource: str) -> dict:
    """Semantic indicator + the dataset's REAL reflected columns -> a Superset chart `params`
    dict. Every referenced column is resolved against `superset_columns` (never trusted as
    typed by the caller) before anything is built — an out-of-catalogue value or unknown
    column is rejected here, never reaches Superset.

    Annexe catalogue viz §7 — an indicator carrying a validated `slots` dict (every AI-path
    indicator now, for every catalogue type including the original 5) dispatches through
    TEMPLATE_REGISTRY. `slots is None` (a manually created/edited indicator, the heuristic
    fallback's output, or any dashboard spec persisted before this annexe) synthesizes one from
    the flat fields for SIMPLE_FLAT_VIZ_TYPES, or — for the original 5 — keeps using the exact
    original flat-field dispatch below, unchanged: zero regression for both."""
    by_name = {c["column_name"]: c for c in superset_columns}

    if indicator.slots is not None or indicator.viz_type in SIMPLE_FLAT_VIZ_TYPES:
        if not chart_catalog.has_template(indicator.viz_type):
            raise ValueError(f"Aucun template disponible pour ce type de graphique : {indicator.viz_type}")
        entry = chart_catalog.CATALOG.get(indicator.viz_type)
        if entry is None or entry["status"] != "active":
            raise ValueError(f"Type de graphique non autorisé : {indicator.viz_type}")
        indicator_slots = indicator.slots
        if indicator_slots is None:
            indicator_slots = _slots_from_flat(indicator.viz_type, indicator.metric_column, indicator.aggregation, indicator.dimension_columns, indicator.time_column)
        for fill in indicator_slots.values():
            if fill["aggregation"] is not None and fill["aggregation"] not in superset_templates.WHITELISTED_AGGREGATIONS:
                raise ValueError(f"Agrégation non autorisée : {fill['aggregation']}")
        resolved_slots = _resolve_slots_for_template(indicator.viz_type, indicator_slots, by_name)
        params = superset_templates.TEMPLATE_REGISTRY[indicator.viz_type](datasource, resolved_slots)
        if indicator.ranking is not None:
            params = _apply_ranking(params, indicator.viz_type, indicator.ranking)
        return params

    if indicator.viz_type not in superset_templates.WHITELISTED_VIZ_TYPES:
        raise ValueError(f"Type de graphique non autorisé : {indicator.viz_type}")
    if indicator.aggregation not in superset_templates.WHITELISTED_AGGREGATIONS:
        raise ValueError(f"Agrégation non autorisée : {indicator.aggregation}")

    def resolve(name: str) -> dict:
        if name not in by_name:
            raise ValueError(f"Colonne introuvable dans le dataset : {name}")
        return by_name[name]

    metric = superset_templates.MetricSpec(column=resolve(indicator.metric_column), aggregate=indicator.aggregation)
    dims = [resolve(c)["column_name"] for c in (indicator.dimension_columns or [])]

    if indicator.viz_type == "big_number_total":
        return superset_templates.big_number_total(datasource, metric)

    if indicator.viz_type == "echarts_timeseries_line":
        if not indicator.time_column:
            raise ValueError("echarts_timeseries_line nécessite une colonne temporelle.")
        x_axis = resolve(indicator.time_column)["column_name"]
        return superset_templates.echarts_timeseries("echarts_timeseries_line", datasource, x_axis, metric, dims)

    if indicator.viz_type == "echarts_timeseries_bar":
        if not dims:
            raise ValueError("echarts_timeseries_bar nécessite au moins une dimension.")
        return superset_templates.echarts_timeseries("echarts_timeseries_bar", datasource, dims[0], metric, dims[1:])

    if indicator.viz_type == "pie":
        if not dims:
            raise ValueError("pie nécessite au moins une dimension.")
        return superset_templates.pie(datasource, dims, metric)

    # "table" — the only remaining whitelisted type.
    return superset_templates.table(datasource, dims, [metric])


async def ensure_chart(config: SupersetConfig, superset_dataset_id: int, superset_columns: list[dict], indicator: IndicatorSpec, slice_name: str) -> dict:
    """Create-or-reuse-by-name, exactly like ensure_database/ensure_dataset (Module 11) —
    republishing updates the same chart instead of duplicating it. Lookup is scoped to
    `superset_dataset_id` (see find_chart_by_name): `slice_name` is the AI-generated indicator
    title verbatim, and two different gold datasets can legitimately share a title."""
    params = build_chart_payload(indicator, superset_columns, f"{superset_dataset_id}__table")

    existing = await superset_api.find_chart_by_name(config.base_url, config.username, config.password, slice_name, superset_dataset_id)
    if existing is None:
        created = await superset_api.create_chart(config.base_url, config.username, config.password, slice_name, indicator.viz_type, superset_dataset_id, params)
        chart_id = created["id"]
    else:
        chart_id = existing["id"]
        await superset_api.update_chart(config.base_url, config.username, config.password, chart_id, params)

    detail = await superset_api.get_chart(config.base_url, config.username, config.password, chart_id)
    return {"id": chart_id, "uuid": detail.get("uuid")}


# ---------------- Module 12 étape 3 — dashboard assembly ----------------

CHART_GRID_WIDTH = 6  # of 12 — two charts per row
CHART_GRID_HEIGHT = 50


def _dashboard_title(project: MedallionProject, dataset: MedallionDataset) -> str:
    # Named per DATASET, not per project: dashboard_specs' own unique constraint is
    # (dataset, instance), and every étape-3 endpoint is scoped to one dataset — a
    # project-wide `dp_<project>` title would make two gold datasets in the same project
    # silently overwrite each other's charts every time one of them regenerates. Follows the
    # same "dp_<project>__<dataset>" convention already used for the published dataset itself.
    return _dataset_name(project, dataset)


def build_position_json(charts: list[dict], title: str) -> dict:
    """`charts`: ordered list of {id, uuid, slice_name}. Deterministic 2-per-row grid — no
    user input into layout, matching the spec's "pas d'IA sur la géométrie" stance."""
    position: dict = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "parents": ["ROOT_ID"], "children": []},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
    }
    row_ids = []
    for row_index, start in enumerate(range(0, len(charts), 2)):
        row_id = f"ROW-{row_index}"
        chart_node_ids = []
        for chart in charts[start:start + 2]:
            node_id = f"CHART-{chart['id']}"
            position[node_id] = {
                "type": "CHART", "id": node_id, "children": [],
                "parents": ["ROOT_ID", "GRID_ID", row_id],
                "meta": {"chartId": chart["id"], "uuid": chart["uuid"], "width": CHART_GRID_WIDTH, "height": CHART_GRID_HEIGHT, "sliceName": chart["slice_name"]},
            }
            chart_node_ids.append(node_id)
        position[row_id] = {"type": "ROW", "id": row_id, "parents": ["ROOT_ID", "GRID_ID"], "children": chart_node_ids, "meta": {"background": "BACKGROUND_TRANSPARENT"}}
        row_ids.append(row_id)
    position["GRID_ID"]["children"] = row_ids
    return position


async def ensure_dashboard(config: SupersetConfig, title: str, charts: list[dict]) -> dict:
    """Create-or-reuse-by-title, same idempotent pattern as ensure_chart. position_json is
    always rebuilt fresh from the current chart list — regeneration never accumulates stale
    layout."""
    position_json = build_position_json(charts, title)
    existing = await superset_api.find_dashboard_by_title(config.base_url, config.username, config.password, title)
    if existing is None:
        created = await superset_api.create_dashboard(config.base_url, config.username, config.password, title, position_json)
        dashboard_id = created["id"]
    else:
        dashboard_id = existing["id"]
        await superset_api.update_dashboard(config.base_url, config.username, config.password, dashboard_id, position_json)
    return {"id": dashboard_id, "url": f"{config.base_url.rstrip('/')}/superset/dashboard/{dashboard_id}/"}


@dataclass
class GenerateOutcome:
    status: str  # ok | not_published | unreachable | bad_credentials | superset_error
    message: str | None = None
    dashboard_url: str | None = None
    charts_count: int | None = None


async def _generate_dataset_charts(
    config: SupersetConfig, pub: SupersetPublication, indicators: list[IndicatorSpec], existing_spec: DashboardSpec | None,
) -> list[dict]:
    """Builds/updates every chart for one dataset's indicator set (create-or-reuse by name,
    scoped to pub.superset_dataset_id) and sweeps charts for indicators no longer proposed.
    Returns the chart refs ({id, uuid, slice_name}) only — never touches a dashboard, so the
    caller decides whether these belong to their own dashboard (_run_generation, one dataset)
    or get folded into a larger one spanning several datasets (generate_combined_dashboard,
    annexe "élargir le scope de l'assistant IA")."""
    # Chart labels are the AI-generated indicator title verbatim (no dp_<project>__<dataset>__
    # prefix) — collisions across datasets are prevented by scoping find_chart_by_name to
    # pub.superset_dataset_id instead, not by namespacing the visible name.
    previous_slice_names = {(ind["title"] or "").strip() for ind in (existing_spec.indicators if existing_spec else [])}

    detail = await superset_api.get_dataset(config.base_url, config.username, config.password, pub.superset_dataset_id)
    superset_columns = detail.get("columns", [])

    chart_refs = []
    for indicator in indicators:
        slice_name = indicator.title.strip() or "Indicateur"
        result = await ensure_chart(config, pub.superset_dataset_id, superset_columns, indicator, slice_name)
        chart_refs.append({"id": result["id"], "uuid": result["uuid"], "slice_name": slice_name})

    # An indicator renamed or removed since the last generation leaves its old chart behind —
    # still linked to whatever dashboard it was on via its own `dashboards` field even though
    # position_json no longer references it, rendering as a broken orphan tile. Sweep it.
    stale_slice_names = previous_slice_names - {c["slice_name"] for c in chart_refs}
    for stale_name in stale_slice_names:
        stale_chart = await superset_api.find_chart_by_name(config.base_url, config.username, config.password, stale_name, pub.superset_dataset_id)
        if stale_chart is not None:
            await superset_api.delete_chart(config.base_url, config.username, config.password, stale_chart["id"])

    return chart_refs


def _persist_dashboard_spec(db: Session, project: MedallionProject, dataset: MedallionDataset, instance: SupersetInstance, existing_spec: DashboardSpec | None, indicators: list[IndicatorSpec], source: str, validated_by_id: int) -> None:
    spec = existing_spec or DashboardSpec(project_id=project.id, medallion_dataset_id=dataset.id, superset_instance_id=instance.id)
    spec.indicators = [asdict(ind) for ind in indicators]
    spec.source = source
    # Annexe catalogue viz §6/§8 — every (re)generation through this catalogue-aware path
    # writes "slots", regardless of whether any individual indicator here actually needed a
    # multi-slot type; a plain "flat" marker is left untouched only on rows never written by
    # this path (persisted before the annexe, never regenerated since).
    spec.contract_version = "slots"
    spec.validated_by = validated_by_id
    spec.last_generated_at = datetime.now(timezone.utc)
    db.add(spec)


async def _run_generation(
    db: Session, project: MedallionProject, dataset: MedallionDataset, instance: SupersetInstance,
    pub: SupersetPublication, indicators: list[IndicatorSpec], validated_by_id: int, source: str,
) -> GenerateOutcome:
    config = get_superset_config(instance)
    title = _dashboard_title(project, dataset)

    existing_spec = (
        db.query(DashboardSpec)
        .filter(DashboardSpec.medallion_dataset_id == dataset.id, DashboardSpec.superset_instance_id == instance.id)
        .first()
    )

    try:
        chart_refs = await _generate_dataset_charts(config, pub, indicators, existing_spec)
        dashboard = await ensure_dashboard(config, title, chart_refs)
        # Belt-and-suspenders (spec §5.2): assign each chart's own `dashboards` field, not
        # just position_json — mitigates a known Superset issue where a chart referenced only
        # from position_json can render broken on a dashboard.
        for chart in chart_refs:
            await superset_api.set_chart_dashboards(config.base_url, config.username, config.password, chart["id"], [dashboard["id"]])
    except superset_api.SupersetAPIError as exc:
        return GenerateOutcome(status=_classify_error(exc), message=str(exc))
    except ValueError as exc:
        return GenerateOutcome(status="superset_error", message=str(exc))

    pub.superset_dashboard_id = dashboard["id"]
    db.add(pub)
    _persist_dashboard_spec(db, project, dataset, instance, existing_spec, indicators, source, validated_by_id)
    db.commit()

    return GenerateOutcome(status="ok", dashboard_url=dashboard["url"], charts_count=len(chart_refs))


async def generate_combined_dashboard(
    db: Session, project: MedallionProject, entries: list[tuple[MedallionDataset, list[IndicatorSpec], str]], validated_by_id: int,
) -> GenerateOutcome:
    """Module 13 embedded flow, annexe "élargir le scope de l'assistant IA" — assembles every
    dataset's indicators into ONE shared, project-level Superset dashboard instead of one
    dashboard per gold dataset. A multi-gold plan routinely produces several gold datasets from
    a single instruction now (mart_top_products, mart_top_customers...) — a separate dashboard
    per dataset just fragments what the engineer asked for as one tableau de bord. Charts
    themselves stay scoped per dataset exactly as before (create-or-reuse by name within that
    dataset's own datasource_id, via the SAME _generate_dataset_charts every single-dataset
    path uses) — only the final assembly step (one ensure_dashboard call, one shared title)
    and the persisted superset_dashboard_id (same value across every involved publication) are
    new. Each dataset still gets its own DashboardSpec row (unchanged shape/uniqueness), so
    Module 12's standalone per-dataset views (régénérer, statut) keep working unmodified."""
    resolved = []
    instance: SupersetInstance | None = None
    for dataset, indicators, source in entries:
        pub, inst = _resolve_publication_and_instance(db, dataset)
        if pub is None or inst is None:
            continue
        instance = instance or inst
        existing_spec = (
            db.query(DashboardSpec)
            .filter(DashboardSpec.medallion_dataset_id == dataset.id, DashboardSpec.superset_instance_id == inst.id)
            .first()
        )
        resolved.append((dataset, pub, indicators, source, existing_spec))

    if not resolved or instance is None:
        return GenerateOutcome(status="not_published")

    config = get_superset_config(instance)
    title = f"dp_{project.dbt_project_name}"
    all_chart_refs: list[dict] = []
    try:
        for dataset, pub, indicators, source, existing_spec in resolved:
            all_chart_refs.extend(await _generate_dataset_charts(config, pub, indicators, existing_spec))

        dashboard = await ensure_dashboard(config, title, all_chart_refs)
        for chart in all_chart_refs:
            await superset_api.set_chart_dashboards(config.base_url, config.username, config.password, chart["id"], [dashboard["id"]])
    except superset_api.SupersetAPIError as exc:
        return GenerateOutcome(status=_classify_error(exc), message=str(exc))
    except ValueError as exc:
        return GenerateOutcome(status="superset_error", message=str(exc))

    for dataset, pub, indicators, source, existing_spec in resolved:
        pub.superset_dashboard_id = dashboard["id"]
        db.add(pub)
        _persist_dashboard_spec(db, project, dataset, instance, existing_spec, indicators, source, validated_by_id)
    db.commit()

    return GenerateOutcome(status="ok", dashboard_url=dashboard["url"], charts_count=len(all_chart_refs))


def _resolve_publication_and_instance(db: Session, dataset: MedallionDataset) -> tuple[SupersetPublication | None, SupersetInstance | None]:
    pub = db.query(SupersetPublication).filter(SupersetPublication.medallion_dataset_id == dataset.id).first()
    if pub is None:
        return None, None
    instance = db.get(SupersetInstance, pub.superset_instance_id)
    return pub, instance


async def generate_dashboard(
    db: Session, project: MedallionProject, dataset: MedallionDataset, indicators: list[IndicatorSpec], validated_by_id: int, source: str,
) -> GenerateOutcome:
    pub, instance = _resolve_publication_and_instance(db, dataset)
    if pub is None or instance is None:
        return GenerateOutcome(status="not_published")
    return await _run_generation(db, project, dataset, instance, pub, indicators, validated_by_id, source)


async def regenerate_dashboard(db: Session, project: MedallionProject, dataset: MedallionDataset) -> GenerateOutcome:
    """Replays the persisted contract — never calls Mistral (spec §5.4: "sans réappeler
    Mistral")."""
    pub, instance = _resolve_publication_and_instance(db, dataset)
    if pub is None or instance is None:
        return GenerateOutcome(status="not_published")
    spec = (
        db.query(DashboardSpec)
        .filter(DashboardSpec.medallion_dataset_id == dataset.id, DashboardSpec.superset_instance_id == instance.id)
        .first()
    )
    if spec is None:
        return GenerateOutcome(status="not_published", message="Aucun tableau de bord à régénérer — générez-en un d'abord.")
    indicators = [IndicatorSpec(**ind) for ind in spec.indicators]
    return await _run_generation(db, project, dataset, instance, pub, indicators, spec.validated_by, spec.source)


def get_dashboard_status(db: Session, dataset: MedallionDataset) -> dict:
    pub, instance = _resolve_publication_and_instance(db, dataset)
    if pub is None or pub.superset_dashboard_id is None or instance is None:
        return {"generated": False}
    spec = (
        db.query(DashboardSpec)
        .filter(DashboardSpec.medallion_dataset_id == dataset.id, DashboardSpec.superset_instance_id == instance.id)
        .first()
    )
    config = get_superset_config(instance)
    return {
        "generated": True,
        "dashboard_url": f"{config.base_url.rstrip('/')}/superset/dashboard/{pub.superset_dashboard_id}/",
        "charts_count": len(spec.indicators) if spec else None,
        "last_generated_at": spec.last_generated_at if spec else None,
    }


async def delete_dashboard_and_charts(db: Session, dataset: MedallionDataset) -> bool:
    """Deletes only the `dp_`-managed dashboard and charts this contract created — never an
    object a client built themselves (spec §5.4)."""
    pub, instance = _resolve_publication_and_instance(db, dataset)
    if pub is None or pub.superset_dashboard_id is None:
        return False
    spec = (
        db.query(DashboardSpec)
        .filter(DashboardSpec.medallion_dataset_id == dataset.id, DashboardSpec.superset_instance_id == pub.superset_instance_id)
        .first()
    )

    if instance is not None:
        config = get_superset_config(instance)
        try:
            await superset_api.delete_dashboard(config.base_url, config.username, config.password, pub.superset_dashboard_id)
        except superset_api.SupersetAPIError:
            pass  # best-effort — the local record is removed regardless
        if spec is not None:
            for indicator in spec.indicators:
                slice_name = (indicator["title"] or "").strip() or "Indicateur"
                try:
                    chart = await superset_api.find_chart_by_name(config.base_url, config.username, config.password, slice_name, pub.superset_dataset_id)
                    if chart is not None:
                        await superset_api.delete_chart(config.base_url, config.username, config.password, chart["id"])
                except superset_api.SupersetAPIError:
                    pass

    pub.superset_dashboard_id = None
    db.add(pub)
    if spec is not None:
        db.delete(spec)
    db.commit()
    return True
