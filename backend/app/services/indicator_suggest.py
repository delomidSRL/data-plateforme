"""Hybrid indicator suggestion (Module 12 étape 2, extended by the "Annexe catalogue viz") —
Mistral proposes a semantic plan from the gold profile (services/gold_profile.py), never
Superset payloads (services/superset_publish.py owns that translation). Output is validated
against the catalogue (services/chart_catalog.py) plus the real profiled columns; anything
hallucinated, out-of-catalogue, or lacking a captured template is dropped, never reaches the
caller. Mistral unreachable or zero valid indicators → a simple deterministic heuristic takes
over — this module supplies its own, per spec §4.3/§4.7 (no prior heuristic exists to reuse).

The AI's own output contract is `slots` (annexe §3 decision 3), not the old flat
metric_column/aggregation/dimension_columns/time_column shape — chart_catalog.CATALOG's
data_slots is the single source of truth for what each viz_type needs. Validated slots are
then resolved into an IndicatorSpec: flattened onto the legacy fields whenever the type fits
that shape (every single-metric type — the large majority of the catalogue), so routes/
frontend/build code that only ever read the flat fields keep working unchanged; the raw
`slots` dict is always attached too, for multi-slot types that don't flatten losslessly.
"""
import json
import logging
import re
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.services import ai_client, chart_catalog
from app.services.ai_config import AIConfig
from app.services.gold_profile import ColumnProfile
from app.services.superset_publish import IndicatorSpec

logger = logging.getLogger(__name__)

MAX_HEURISTIC_INDICATORS = 5
# A suggestion completion generates far more tokens than the health-check probe — self-hosted
# models under modest hardware can legitimately take longer than AIConfig's default timeout.
SUGGESTION_TIMEOUT_FLOOR = 90.0


@dataclass
class SuggestionResult:
    indicators: list[IndicatorSpec]
    source: str  # ai | heuristic
    # Raffinage §4.3 — visible reasons for every AI-proposed indicator the deterministic
    # role/precondition guard dropped (e.g. a temporal viz_type on a gold with no temporal
    # column). Always [] on the heuristic path (nothing AI-proposed to reject).
    warnings: list[str] = field(default_factory=list)


class _RawSlotFill(BaseModel):
    columns: list[str] = Field(default_factory=list)
    aggregation: str | None = None

    @field_validator("aggregation", mode="before")
    @classmethod
    def _coerce_false_to_none(cls, v):
        """Confirmed empirically against the real self-hosted model (qwen3-coder): it
        sometimes writes `false` instead of `null`/omitting the key for a slot that takes no
        aggregation — clearly the same intent as "none", coerced here rather than losing an
        otherwise well-formed suggestion (all its OTHER indicators too, since one bad slot
        used to fail the whole response's Pydantic validation) over a one-token format slip."""
        return None if v is False else v


class _RawRanking(BaseModel):
    limit: int = 10
    direction: str = "top"


class _RawIndicator(BaseModel):
    title: str = ""
    viz_type: str = ""
    slots: dict[str, _RawSlotFill] = Field(default_factory=dict)
    ranking: _RawRanking | None = None


class _RawSuggestion(BaseModel):
    indicators: list[_RawIndicator] = Field(default_factory=list)


def _profile_sheet(profile: list[ColumnProfile]) -> str:
    rows = []
    for c in profile:
        row = {"name": c.name, "sql_type": c.sql_type, "role": c.role, "distinct_count": c.distinct_count, "null_rate": c.null_rate}
        if c.min_value is not None:
            row["min"] = c.min_value
        if c.max_value is not None:
            row["max"] = c.max_value
        if c.sample_values:
            row["sample_values"] = c.sample_values
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False)


def _build_prompt(profile: list[ColumnProfile], context: str | None = None) -> list[dict]:
    sheet = chart_catalog.build_capability_sheet(profile)
    system = (
        "You are an assistant who proposes dashboard indicators for a data table. "
        "You respond ONLY with a valid JSON object, no text before or after, no markdown tags."
    )
    context_line = f"\nProject business objective (context, prioritize indicators that answer it): {context}\n" if context else ""
    user = f"""Here are the chart types available for this table (propose none other), each with its business intent and data slots ("slots") to fill. For each slot: "role" indicates what kind of column it expects, "required" whether it's mandatory, "min"/"max" the number of columns expected, "aggregation" whether it needs an aggregation. "supports_ranking": true means this type additionally accepts an optional "ranking" field:
{json.dumps(sheet, ensure_ascii=False)}

Allowed aggregations: {", ".join(chart_catalog.AGGREGATION_WHITELIST)}.

Ranking (Top N / Bottom N): only for types with "supports_ranking": true. If the business request evokes a ranking ("top 10 products", "worst performers", "bottom 10"...), add a "ranking": {{"limit": 10, "direction": "top"}} field on that indicator (highest values first) or {{"limit": 10, "direction": "bottom"}} (lowest values first) — limit between 1 and 100. Do NOT add this field for types that don't support it, nor when no ranking is requested.
{context_line}
Here is the table's profile (real columns, never raw rows):
{_profile_sheet(profile)}

Propose between 2 and 6 relevant indicators for a business dashboard from these columns, varying chart types when relevant rather than always proposing the same ones. Only propose a chart type with a temporal axis (evolution line/area...) if this table actually has a column of role "temporal" listed above.
Respond with exactly this JSON object (no invented column, only those listed above; each "slots" must contain ONLY slot names listed for the chosen viz_type, with the real columns filling that slot; "aggregation" must be one of the allowed aggregations above if the slot requires one, otherwise exactly `null` — never `false`; "ranking" is optional, only for types that support it):
{{"indicators": [{{"title": "...", "viz_type": "...", "slots": {{"<slot_name>": {{"columns": ["real_column_name"], "aggregation": "SUM"}}, "<other_slot_without_aggregation>": {{"columns": ["real_column_name"], "aggregation": null}}}}, "ranking": {{"limit": 10, "direction": "top"}}}}]}}"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if not match:
        raise ValueError("Aucun objet JSON trouvé dans la réponse.")
    return json.loads(match.group(0))


def _role_matches(col: ColumnProfile, expected_role: str) -> bool:
    return {
        "measure": col.role == "measure",
        "dimension": col.role == "dimension",
        "temporal": col.role == "temporal",
        "raw_column": True,
        "geo_point": col.is_geo_lat or col.is_geo_lon,
        "geo_region_code": col.is_geo_region_code,
        "geo_polygon": col.is_geo_polygon,
        "node": col.is_node_id,
        "hierarchy_id": col.is_hierarchy_id,
        "hierarchy_parent": col.is_hierarchy_parent,
    }.get(expected_role, False)


def _flatten_for_compat(viz_type: str, resolved_slots: dict[str, dict]) -> dict:
    """Best-effort mapping of a validated slots dict onto the legacy flat fields (annexe §3
    decision 3) — exact and lossless for every single-metric type (the large majority of the
    catalogue, including every new simple type this annexe adds): one measure slot becomes
    metric_column/aggregation, dimension-role slots become dimension_columns, the temporal
    slot becomes time_column. For a genuinely multi-slot type (more than one measure slot —
    bubble_v2's x/y/size, radar's multi-metric...) this only captures the FIRST measure slot
    as a display-only summary; the real data for those lives in `slots` on the returned spec."""
    flat: dict = {"metric_column": "", "aggregation": "", "dimension_columns": [], "time_column": None}
    for slot_name, data in resolved_slots.items():
        cols, agg = data["columns"], data["aggregation"]
        role = chart_catalog.CATALOG[viz_type]["data_slots"][slot_name]["role"]
        if role == "temporal" and flat["time_column"] is None:
            flat["time_column"] = cols[0] if cols else None
        elif role == "measure" and not flat["metric_column"]:
            flat["metric_column"] = cols[0] if cols else ""
            flat["aggregation"] = agg or ""
        elif role == "dimension":
            flat["dimension_columns"].extend(c for c in cols if c not in flat["dimension_columns"])
    return flat


def _validate_indicator(raw: "_RawIndicator", profile_by_name: dict[str, ColumnProfile]) -> tuple[IndicatorSpec | None, str | None]:
    """Annexe catalogue viz §5 — catalogue+profile+template driven (chart_catalog.py),
    replacing the old hard-coded whitelist check. An indicator is accepted only if: its
    viz_type is `active` in the catalogue, has a real captured template
    (chart_catalog.has_template), its `requires` preconditions are satisfied by the profile,
    every slot it fills is a real slot for that viz_type with correct cardinality/role/
    aggregation, and every column it cites actually exists. This is ALSO the deterministic
    role↔viz_type coherence guard (raffinage §4.3) — `requires: ["has_temporal", ...]` already
    rejects a temporal viz_type on a gold with no temporal column, and per-slot `_role_matches`
    already rejects a slot filled with a column of the wrong role; nothing new needed there,
    it just used to be silent (logger.info only). Returns (spec, None) on success, (None,
    reason) on rejection — the reason surfaces to the caller as a visible warning instead of
    only a server log line, same "never silently drop" discipline as pipeline_plan.py's
    gold_warnings."""
    entry = chart_catalog.CATALOG.get(raw.viz_type)
    if entry is None or entry["status"] != "active":
        reason = f"« {raw.title or raw.viz_type} » écarté : type de graphique « {raw.viz_type} » inconnu ou inactif."
        logger.info(reason)
        return None, reason
    if not chart_catalog.has_template(raw.viz_type):
        reason = f"« {raw.title or raw.viz_type} » écarté : aucun template capturé pour « {raw.viz_type} »."
        logger.info(reason)
        return None, reason
    profile_columns = list(profile_by_name.values())
    if not all(chart_catalog.satisfies(p, profile_columns) for p in entry.get("requires", [])):
        reason = f"« {raw.title or raw.viz_type} » écarté : « {raw.viz_type} » exige {entry.get('requires')}, non satisfait par les colonnes réelles du gold (ex. aucune colonne temporelle pour un axe temporel)."
        logger.info(reason)
        return None, reason

    slot_defs: dict[str, dict] = entry["data_slots"]
    for name in raw.slots:
        if name not in slot_defs:
            reason = f"« {raw.title or raw.viz_type} » écarté : slot inconnu « {name} » pour « {raw.viz_type} »."
            logger.info(reason)
            return None, reason

    resolved_slots: dict[str, dict] = {}
    for slot_name, spec in slot_defs.items():
        fill = raw.slots.get(slot_name)
        provided = fill.columns if fill else []
        lo, hi = spec.get("min", 0), spec.get("max")

        if spec["required"] and len(provided) < max(lo, 1):
            reason = f"« {raw.title or raw.viz_type} » écarté : slot requis « {slot_name} » ({spec['role']}) manquant ou insuffisant pour « {raw.viz_type} »."
            logger.info(reason)
            return None, reason
        if hi is not None and len(provided) > hi:
            reason = f"« {raw.title or raw.viz_type} » écarté : slot « {slot_name} » dépasse le maximum ({hi}) pour « {raw.viz_type} »."
            logger.info(reason)
            return None, reason

        for cname in provided:
            col = profile_by_name.get(cname)
            if col is None:
                reason = f"« {raw.title or raw.viz_type} » écarté : colonne « {cname} » inexistante (slot {slot_name})."
                logger.info(reason)
                return None, reason
            if not _role_matches(col, spec["role"]):
                reason = f"« {raw.title or raw.viz_type} » écarté : colonne « {cname} » incompatible avec le rôle « {spec['role']} » attendu par le slot « {slot_name} »."
                logger.info(reason)
                return None, reason

        wants_agg = bool(spec.get("aggregation"))
        if fill and provided:
            if wants_agg and (not fill.aggregation or fill.aggregation not in chart_catalog.AGGREGATION_WHITELIST):
                reason = f"« {raw.title or raw.viz_type} » écarté : agrégation requise/invalide pour le slot « {slot_name} »."
                logger.info(reason)
                return None, reason
            if not wants_agg and fill.aggregation is not None:
                reason = f"« {raw.title or raw.viz_type} » écarté : agrégation interdite pour le slot « {slot_name} »."
                logger.info(reason)
                return None, reason
            resolved_slots[slot_name] = {"columns": provided, "aggregation": fill.aggregation if wants_agg else None}

    ranking = None
    if raw.ranking is not None:
        if not entry.get("supports_ranking"):
            reason = f"« {raw.title or raw.viz_type} » écarté : classement demandé pour « {raw.viz_type} », qui ne le supporte pas."
            logger.info(reason)
            return None, reason
        if not (1 <= raw.ranking.limit <= 100) or raw.ranking.direction not in ("top", "bottom"):
            reason = f"« {raw.title or raw.viz_type} » écarté : classement invalide."
            logger.info(reason)
            return None, reason
        ranking = {"limit": raw.ranking.limit, "direction": raw.ranking.direction}

    flat = _flatten_for_compat(raw.viz_type, resolved_slots)
    spec = IndicatorSpec(title=raw.title or raw.viz_type, viz_type=raw.viz_type, slots=resolved_slots, ranking=ranking, **flat)
    return spec, None


async def suggest_indicators_ai(config: AIConfig, profile: list[ColumnProfile], context: str | None = None) -> tuple[list[IndicatorSpec], list[str]] | None:
    """None means the AI attempt itself failed (unreachable/unparsable) — distinct from an
    empty (indicators, warnings) pair, which means Mistral answered but nothing (or not
    everything) it proposed survived validation; `warnings` explains why per indicator
    (raffinage §4.3). `context` (Module 13 §6.2) is the pipeline agent's original business
    instruction, passed through so the suggestion leans toward what the engineer actually
    asked for."""
    profile_by_name = {c.name: c for c in profile}
    call_config = config if config.timeout >= SUGGESTION_TIMEOUT_FLOOR else replace(config, timeout=SUGGESTION_TIMEOUT_FLOOR)
    try:
        content = await ai_client.chat_completion(call_config, _build_prompt(profile, context), temperature=0.2)
        parsed = _extract_json_object(content)
        suggestion = _RawSuggestion.model_validate(parsed)
    except (ai_client.AIClientError, ValueError, ValidationError) as exc:
        logger.info("Suggestion Mistral indisponible ou invalide : %s", exc)
        return None

    validated: list[IndicatorSpec] = []
    warnings: list[str] = []
    for raw in suggestion.indicators:
        spec, reason = _validate_indicator(raw, profile_by_name)
        if spec is not None:
            validated.append(spec)
        elif reason:
            warnings.append(reason)
    return validated, warnings


def suggest_indicators_heuristic(profile: list[ColumnProfile]) -> list[IndicatorSpec]:
    """Simple, deterministic fallback — no Mistral, no guesswork: one KPI, one breakdown, one
    trend (if a temporal column exists), one detail table. Per spec §4.3 ("repli sur
    l'heuristique"), kept intentionally basic."""
    measures = [c for c in profile if c.role == "measure"]
    dimensions = [c for c in profile if c.role == "dimension" and 2 <= c.distinct_count <= 20]
    temporals = [c for c in profile if c.role == "temporal"]

    indicators: list[IndicatorSpec] = []
    if measures:
        primary_measure = measures[0].name
        indicators.append(IndicatorSpec(title=f"Total {primary_measure}", viz_type="big_number_total", metric_column=primary_measure, aggregation="SUM", dimension_columns=[]))

        if temporals:
            indicators.append(IndicatorSpec(title=f"{primary_measure} dans le temps", viz_type="echarts_timeseries_line", metric_column=primary_measure, aggregation="SUM", dimension_columns=[], time_column=temporals[0].name))

        if dimensions:
            primary_dimension = dimensions[0].name
            indicators.append(IndicatorSpec(title=f"{primary_measure} par {primary_dimension}", viz_type="echarts_timeseries_bar", metric_column=primary_measure, aggregation="SUM", dimension_columns=[primary_dimension]))
            indicators.append(IndicatorSpec(title=f"Répartition de {primary_measure} par {primary_dimension}", viz_type="pie", metric_column=primary_measure, aggregation="SUM", dimension_columns=[primary_dimension]))

    all_dims = [c.name for c in profile if c.role in ("dimension", "temporal")]
    all_measures = [c.name for c in measures] or ([profile[0].name] if profile else [])
    if all_measures:
        indicators.append(IndicatorSpec(title="Détail", viz_type="table", metric_column=all_measures[0], aggregation="SUM", dimension_columns=all_dims[:3]))

    return indicators[:MAX_HEURISTIC_INDICATORS]


async def suggest_indicators(config: AIConfig, profile: list[ColumnProfile], context: str | None = None) -> SuggestionResult:
    ai_result = await suggest_indicators_ai(config, profile, context)
    if ai_result and ai_result[0]:
        indicators, warnings = ai_result
        return SuggestionResult(indicators=indicators, source="ai", warnings=warnings)
    # Falling back to the heuristic (AI unreachable, or it answered but nothing survived) still
    # carries forward whatever rejection reasons exist — the engineer should see WHY the AI's
    # own proposals were dropped even though the safety net is what's actually used.
    return SuggestionResult(indicators=suggest_indicators_heuristic(profile), source="heuristic", warnings=ai_result[1] if ai_result else [])
