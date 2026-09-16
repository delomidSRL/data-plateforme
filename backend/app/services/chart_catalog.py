"""Module 12 — Annexe catalogue viz §4: the catalogue of viz_type capabilities (source of
truth: superset_templates/chart_catalog.json) and the "capability sheet" built from it — the
ONLY thing the AI ever sees about chart types (`intent` + `data_slots`, never a style
parameter). Replaces the old hard-coded WHITELISTED_VIZ_TYPES-only approach with a
data-driven one: a viz_type is proposable only if it's `status=active` in the catalogue, its
`requires` preconditions are satisfied by the real profiled table, AND a real, verified
template function exists for it (superset_templates.TEMPLATE_REGISTRY) — the AI is never
offered a type this platform can't actually build.

Tier 3 (geo/graph/hierarchy) entries stay in the catalogue for completeness but are never
proposable in v1: no detector sets is_geo_*/is_node_id/is_hierarchy_* True yet
(gold_profile.ColumnProfile, §10 — deferred to real client use cases). `legacy`/`excluded`
entries are likewise never proposed (status filter alone excludes them)."""
import json
from pathlib import Path

from app.services.gold_profile import ColumnProfile
from app.services.superset_templates import TEMPLATE_REGISTRY

_CATALOG_PATH = Path(__file__).parent / "superset_templates" / "chart_catalog.json"


def _load_catalog() -> tuple[dict[str, dict], tuple[str, ...]]:
    """({viz_type: entry}, aggregation_whitelist). Loaded once, frozen into the module-level
    constants below. The aggregation whitelist is read from the catalogue's own `_meta` block
    — same single source of truth, not a second hard-coded copy."""
    raw = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    catalog = {c["viz_type"]: c for c in raw["charts"]}
    agg_whitelist = tuple(raw["_meta"]["aggregation_whitelist"])
    return catalog, agg_whitelist


CATALOG, AGGREGATION_WHITELIST = _load_catalog()


def satisfies(precondition: str, columns: list[ColumnProfile]) -> bool:
    """Evaluates one of chart_catalog.json's `preconditions_vocab` entries against a real
    profiled table's columns. An unknown precondition name evaluates False (fail closed —
    a typo in the catalogue must never accidentally make a type MORE proposable)."""
    measures = [c for c in columns if c.role == "measure"]
    dimensions = [c for c in columns if c.role == "dimension"]
    temporals = [c for c in columns if c.role == "temporal"]
    checks = {
        "has_measure": len(measures) >= 1,
        "has_dimension": len(dimensions) >= 1,
        "has_temporal": len(temporals) >= 1,
        "measures_gte_2": len(measures) >= 2,
        "measures_gte_3": len(measures) >= 3,
        "has_geo_point": any(c.is_geo_lat for c in columns) and any(c.is_geo_lon for c in columns),
        "has_geo_region_code": any(c.is_geo_region_code for c in columns),
        "has_geo_polygon": any(c.is_geo_polygon for c in columns),
        "has_graph_edges": sum(c.is_node_id for c in columns) >= 2,
        "has_hierarchy": any(c.is_hierarchy_id for c in columns) and any(c.is_hierarchy_parent for c in columns),
    }
    return checks.get(precondition, False)


def has_template(viz_type: str) -> bool:
    """A real, verified template function exists for this type (superset_templates.py) — a
    build-time concern, checked at validation (chart_catalog.validate_indicator, §5) rather
    than gating the capability sheet itself: the sheet mirrors the catalogue's own
    active+satisfied set exactly (§4's own DoD expects ~35 Tier 1+2 types from the catalogue
    alone), and a type the AI proposes without a captured template yet is rejected at
    validation with a clear reason instead of silently vanishing from the sheet."""
    return viz_type in TEMPLATE_REGISTRY


def is_proposable(viz_type: str, columns: list[ColumnProfile]) -> bool:
    entry = CATALOG.get(viz_type)
    if entry is None or entry["status"] != "active":
        return False
    return all(satisfies(p, columns) for p in entry.get("requires", []))


def build_capability_sheet(columns: list[ColumnProfile]) -> list[dict]:
    """Filters the catalogue to what's ACTUALLY proposable for this table. Exposes only
    `intent` + `data_slots` per entry — no style parameter ever reaches this shape, by
    construction (chart_catalog.json's own entries don't carry any)."""
    sheet = []
    for viz_type, entry in CATALOG.items():
        if not is_proposable(viz_type, columns):
            continue
        sheet.append({
            "viz_type": viz_type,
            "intent": entry["intent"],
            "supports_ranking": entry.get("supports_ranking", False),
            "data_slots": {
                slot: {
                    "role": spec["role"], "required": spec["required"],
                    "min": spec.get("min", 0), "max": spec.get("max"),
                    "aggregation": spec.get("aggregation", False),
                }
                for slot, spec in entry["data_slots"].items()
            },
        })
    return sheet
