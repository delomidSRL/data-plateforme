"""Module 16 §6.3/§7.3 — the AI's two bounded, upstream roles in the quality module (§0):
amorcer la baseline (this file, §6.3 — extended with suggest_checks in Étape 4, §7.3) and
explain a measured defect downstream (§8.3, Étape 5). The AI NEVER computes an indicator, never
asserts a ground-truth value — every candidate it's shown here is already deterministically
resolved from the real, validated plan/mapping (never raw rows), and every value it proposes
back is re-validated against that same real data before ever reaching the engineer.
"""
import json
import logging
import re
from dataclasses import replace

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.models.data_quality import CheckType, DataQualityAlert, DataQualityMetric, DataQualitySnapshot, QualityIndicator
from app.models.data_source import DataSource
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject
from app.models.pipeline_plan import PipelinePlan
from app.models.semantic_annotation import SemanticAnnotation
from app.services import ai_client
from app.services.ai_config import AIConfig, get_ai_config
from app.services.ai_json import extract_json_object
from app.services.medallion_stats import list_columns
from app.services.sql_validator import validate_predicate_sql
from app.services import quality_intrinsic
from app.schemas.quality import BaselineAssertions, BaselineSuggestionOut, CheckSuggestion, CheckSuggestOut, ConservativeMeasureAssertion, ExpectedSourceVolume, RequiredDimensionAssertion

logger = logging.getLogger("app.quality_contract")

SUGGESTION_TIMEOUT_FLOOR = 60.0
_MAX_PATTERN_LEN = 200


def _candidates(db: Session, project: MedallionProject) -> dict:
    """Deterministic, from the already-validated plan/mapping — never a fresh profiling pass.
    Empty candidate lists are a legitimate outcome (a project with no gold SUM measure has
    nothing to suggest for conservative_measures), not an error."""
    plan_row = db.query(PipelinePlan).filter(PipelinePlan.project_id == project.id).order_by(PipelinePlan.id.desc()).first()
    mapping = (plan_row.mapping if plan_row else None) or {}
    plan = (plan_row.plan if plan_row else None) or {}

    fact_tables = [t["table"].split(".")[-1] for t in mapping.get("tables", []) if t.get("role") == "fact"]
    bronze_names = {d.name for d in db.query(MedallionDataset).filter(MedallionDataset.project_id == project.id, MedallionDataset.layer == MedallionLayer.bronze).all()}
    fact_bronze_candidates = [n for n in fact_tables if n in bronze_names]

    sum_measures: dict[str, str] = {}  # column -> gold name that uses it, for rationale
    dimension_columns: dict[str, str] = {}
    for g in plan.get("gold", []):
        if g.get("aggregation") == "SUM" and g.get("metric_column"):
            sum_measures.setdefault(g["metric_column"], g["name"])
        for dim in g.get("dimension_columns") or []:
            dimension_columns.setdefault(dim, g["name"])

    return {
        "fact_bronze_candidates": fact_bronze_candidates,
        "bronze_row_counts_hint": None,  # kept out deliberately — see module docstring: never a number the AI could anchor on
        "sum_measure_candidates": sum_measures,
        "dimension_candidates": dimension_columns,
    }


class _RawVolumeSuggestion(BaseModel):
    source_ref: str = ""
    rationale: str = ""


class _RawMeasureSuggestion(BaseModel):
    column: str = ""
    rationale: str = ""


class _RawDimensionSuggestion(BaseModel):
    column: str = ""
    rationale: str = ""


class _RawBaselineSuggestion(BaseModel):
    expected_source_volume: _RawVolumeSuggestion | None = None
    conservative_measures: list[_RawMeasureSuggestion] = Field(default_factory=list)
    required_dimensions: list[_RawDimensionSuggestion] = Field(default_factory=list)


def _build_prompt(candidates: dict) -> list[dict]:
    # English: empirically verified this session (controlled A/B test on this same model,
    # qwen3-coder:30b) to follow strict structured-output instructions far more reliably than
    # French — all 4 other real prompts in this codebase were translated for the same reason.
    system = (
        "You propose a draft data-quality baseline for a data pipeline. Reply "
        "ONLY with a valid JSON object, no surrounding text or markdown, in this exact format: "
        '{"expected_source_volume": {"source_ref": "<table_name>", "rationale": "..."} | null, '
        '"conservative_measures": [{"column": "<column_name>", "rationale": "..."}], '
        '"required_dimensions": [{"column": "<column_name>", "rationale": "..."}]}. '
        "Strict rules: choose ONLY among the provided candidates, NEVER invent a table or "
        "column name absent from the lists. For expected_source_volume, pick the most central "
        "fact table of the pipeline among fact_bronze_candidates (or null if none stands out) — "
        "NEVER propose a numeric value, you do not know the real expected volume, only the "
        "engineer can assert it. For conservative_measures and required_dimensions, select "
        "columns from sum_measure_candidates/dimension_candidates that represent a real "
        "business stake (a financial amount, a structuring analysis axis) rather than "
        "mechanically listing all of them — 1 to 4 relevant choices are enough. 'rationale' is "
        "a short sentence explaining the choice."
    )
    user = json.dumps(candidates, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _heuristic_suggestion(candidates: dict) -> BaselineSuggestionOut:
    """Deterministic fallback (AI unreachable) — same "never worse than a simple default"
    pattern as indicator_suggest.suggest_indicators_heuristic: takes every real candidate found
    rather than leaving the draft empty."""
    assertions = BaselineAssertions(
        expected_source_volume=ExpectedSourceVolume(source_ref=candidates["fact_bronze_candidates"][0], value=None) if candidates["fact_bronze_candidates"] else None,
        conservative_measures=[ConservativeMeasureAssertion(column=c) for c in list(candidates["sum_measure_candidates"])[:4]],
        required_dimensions=[RequiredDimensionAssertion(column=c) for c in list(candidates["dimension_candidates"])[:4]],
    )
    rationale = {}
    if assertions.expected_source_volume:
        rationale["expected_source_volume"] = f"Table de faits candidate du pipeline (rôle 'fact' au mapping) : {assertions.expected_source_volume.source_ref}."
    for m in assertions.conservative_measures:
        rationale[f"conservative_measures.{m.column}"] = f"Mesure agrégée en SUM par {candidates['sum_measure_candidates'][m.column]}."
    for d in assertions.required_dimensions:
        rationale[f"required_dimensions.{d.column}"] = f"Dimension structurante du gold {candidates['dimension_candidates'][d.column]}."
    return BaselineSuggestionOut(assertions=assertions, rationale=rationale)


async def suggest_baseline(db: Session, project: MedallionProject, config: AIConfig | None = None) -> BaselineSuggestionOut:
    """§6.3 — non-persistent draft. Never raises: AI unreachable/invalid falls back to the
    deterministic heuristic above rather than leaving the screen empty."""
    candidates = _candidates(db, project)
    if not any([candidates["fact_bronze_candidates"], candidates["sum_measure_candidates"], candidates["dimension_candidates"]]):
        return BaselineSuggestionOut(assertions=BaselineAssertions(), rationale={})

    config = config or get_ai_config()
    call_config = config if config.timeout >= SUGGESTION_TIMEOUT_FLOOR else replace(config, timeout=SUGGESTION_TIMEOUT_FLOOR)
    try:
        raw_text = await ai_client.chat_completion(call_config, _build_prompt(candidates), temperature=0.1)
        raw = _RawBaselineSuggestion.model_validate(extract_json_object(raw_text))
    except (ai_client.AIClientError, ValueError, ValidationError) as exc:
        logger.info("quality_contract: baseline suggestion AI unavailable, falling back to heuristic: %s", exc)
        return _heuristic_suggestion(candidates)

    # Whitelist against the real candidates — never trust the AI's own naming (same discipline
    # as intent_mapping.resolve_mapping / pipeline_plan.resolve_plan).
    assertions = BaselineAssertions()
    rationale: dict[str, str] = {}
    if raw.expected_source_volume and raw.expected_source_volume.source_ref in candidates["fact_bronze_candidates"]:
        assertions.expected_source_volume = ExpectedSourceVolume(source_ref=raw.expected_source_volume.source_ref, value=None)
        rationale["expected_source_volume"] = raw.expected_source_volume.rationale[:300]
    for m in raw.conservative_measures:
        if m.column in candidates["sum_measure_candidates"] and len(assertions.conservative_measures) < 6:
            assertions.conservative_measures.append(ConservativeMeasureAssertion(column=m.column))
            rationale[f"conservative_measures.{m.column}"] = m.rationale[:300]
    for d in raw.required_dimensions:
        if d.column in candidates["dimension_candidates"] and len(assertions.required_dimensions) < 6:
            assertions.required_dimensions.append(RequiredDimensionAssertion(column=d.column))
            rationale[f"required_dimensions.{d.column}"] = d.rationale[:300]

    if not assertions.expected_source_volume and not assertions.conservative_measures and not assertions.required_dimensions:
        # Everything the AI proposed was hallucinated/off-list — degrade to the heuristic
        # rather than hand back an empty draft when real candidates did exist.
        return _heuristic_suggestion(candidates)
    return BaselineSuggestionOut(assertions=assertions, rationale=rationale)


# ---------------------------------------------------------------------------------------------
# §7.3 — Étape 4: parameterized quality-check suggestion (type_conformity, format_validity,
# intra_row_consistency, plausibility, conditional_completeness).
# ---------------------------------------------------------------------------------------------


def validate_check_parameters(check_type: CheckType, target_column: str, parameters: dict, real_columns: set[str]) -> list[str]:
    """The single validation boundary for a check's shape+safety — reused by BOTH the AI-
    suggestion whitelist below and the write endpoint (POST/PUT /quality/checks), so a manually-
    authored check is held to exactly the same bar as an AI-proposed one (§7.6.2/§7.6.4). Never
    trust a check's `parameters` from any other source. Returns human-readable errors; empty =
    valid."""
    errors: list[str] = []
    needs_target_column = check_type in (CheckType.type_conformity, CheckType.format_validity, CheckType.plausibility)
    if needs_target_column and (not target_column or target_column not in real_columns):
        errors.append(f"colonne cible « {target_column} » absente du schéma réel du dataset.")

    if check_type == CheckType.type_conformity:
        target_type = parameters.get("target_type")
        if target_type not in quality_intrinsic.TYPE_CONFORMITY_PATTERNS:
            errors.append(f"type_cible « {target_type} » hors du vocabulaire fermé ({sorted(quality_intrinsic.TYPE_CONFORMITY_PATTERNS)}).")

    elif check_type == CheckType.format_validity:
        pattern = parameters.get("pattern") or ""
        if not pattern or len(pattern) > _MAX_PATTERN_LEN:
            errors.append(f"motif absent ou trop long (max {_MAX_PATTERN_LEN} caractères).")
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append(f"motif regex invalide : {exc}")

    elif check_type == CheckType.intra_row_consistency:
        result = validate_predicate_sql(parameters.get("predicate") or "", "postgres", real_columns)
        if not result.valid:
            errors.extend(result.errors)

    elif check_type == CheckType.plausibility:
        mn, mx = parameters.get("min"), parameters.get("max")
        if mn is None and mx is None:
            errors.append("au moins une borne min ou max est requise.")
        elif mn is not None and mx is not None and float(mn) > float(mx):
            errors.append("min > max.")

    elif check_type == CheckType.conditional_completeness:
        result = validate_predicate_sql(parameters.get("condition") or "", "postgres", real_columns)
        if not result.valid:
            errors.extend(result.errors)
        target = parameters.get("target") or ""
        if not target or target not in real_columns:
            errors.append(f"colonne cible « {target} » absente du schéma réel du dataset.")

    return errors


class _RawCheckSuggestion(BaseModel):
    check_type: CheckType
    target_column: str | None = None
    parameters: dict = Field(default_factory=dict)
    rationale: str = ""


def _build_checks_prompt(candidates: dict) -> list[dict]:
    system = (
        "You propose parameterized data-quality contract checks for ONE dataset. You receive "
        "its column list (name, real Postgres type, null rate already measured, optional "
        "business annotation) — NEVER raw row values, only schema and aggregated statistics. "
        "Reply ONLY with a valid JSON object, no surrounding text or markdown, in this exact "
        'format: {"checks": [{"check_type": "...", "target_column": "<name>" | null, '
        '"parameters": {...}, "rationale": "..."}]}. check_type is one of exactly 5 values, '
        "each with its own parameters shape: "
        '"type_conformity" {"target_type": one of "integer"|"numeric"|"date"|"email"|"boolean"|"phone"} — '
        "target_column required; "
        '"format_validity" {"pattern": "<POSIX regex>"} — target_column required, propose a '
        "regex only when the column's real type is textual and its name/annotation strongly "
        "suggests a structured format (an identifier, a code); "
        '"intra_row_consistency" {"predicate": "<SQL boolean expression using only this '
        'table\'s own column names, no subquery, no other table>"} — target_column null, e.g. '
        '"end_date >= start_date"; '
        '"plausibility" {"min": number|null, "max": number|null} — target_column required, only '
        "on a numeric column with a real, obvious business bound (an age, a percentage, never "
        "an unbounded amount you cannot justify); "
        '"conditional_completeness" {"condition": "<SQL boolean expression>", "target": '
        '"<column name that must be non-null when condition holds>"} — target_column null. '
        "Strict rules: choose target_column/predicate/condition/target ONLY among the real "
        "columns provided, NEVER invent a name. When a column carries 'real_distinct_values', "
        "any string literal your predicate/condition writes against it MUST be copied VERBATIM "
        "from that list (exact spelling and case) — never reformat or guess a value. Propose "
        "only checks with a real, disclosed business rationale from the column names/types/"
        "annotations shown — 0 to 6 relevant checks total, fewer is better than a mechanical "
        "one-per-column sweep. 'rationale' is a short sentence explaining the choice."
    )
    user = json.dumps(candidates, ensure_ascii=False, default=str)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _check_candidates(db: Session, dataset: MedallionDataset, warehouse: DataSource) -> tuple[dict, set[str]]:
    """Deterministic context for the prompt: real introspected columns (never re-profiled from
    scratch — `list_columns` is the same on-demand introspection the dataset editor already
    uses), the latest already-collected null_rate per column (§2 "on historise ce qu'on mesure
    déjà"), and any engineer-authored SemanticAnnotation — bronze only, since silver/gold
    datasets have no original external DataSource for an annotation to be keyed against."""
    real_columns_info = list_columns(warehouse, dataset)
    real_columns = {c["column"] for c in real_columns_info}
    if not real_columns:
        return {}, real_columns

    null_rates: dict[str, float | None] = {}
    latest_snapshot = (
        db.query(DataQualitySnapshot)
        .filter(DataQualitySnapshot.dataset_id == dataset.id)
        .order_by(DataQualitySnapshot.collected_at.desc())
        .first()
    )
    if latest_snapshot is not None:
        for m in db.query(DataQualityMetric).filter(DataQualityMetric.snapshot_id == latest_snapshot.id, DataQualityMetric.indicator == QualityIndicator.null_rate).all():
            null_rates[m.target_column] = m.defect_rate

    annotations: dict[str, str] = {}
    table_annotation: str | None = None
    if dataset.layer == MedallionLayer.bronze and dataset.source_id:
        table_ref = dataset.source_object or dataset.name
        for a in db.query(SemanticAnnotation).filter(SemanticAnnotation.data_source_id == dataset.source_id, SemanticAnnotation.table_name == table_ref).all():
            if a.column_name is None:
                table_annotation = a.description
            else:
                annotations[a.column_name] = a.description

    # Module 14's already-profiled real distinct values (source_profile.ColumnProfile.
    # filter_enum_values, persisted per-column in PipelinePlan.mapping) — reused, never a fresh
    # profiling pass. Without this the AI has to GUESS the real spelling/casing of any string
    # literal it writes into a condition/predicate (confirmed live: it proposed "= 'discharged'"
    # lowercase when the real, only, value is 'Discharged') — a wrong-cased literal doesn't
    # error, it silently mismeasures every row, exactly the failure mode this closes.
    enum_values: dict[str, list[str]] = {}
    plan_row = db.query(PipelinePlan).filter(PipelinePlan.project_id == dataset.project_id).order_by(PipelinePlan.id.desc()).first()
    if plan_row and plan_row.mapping:
        for t in plan_row.mapping.get("tables", []):
            if t.get("table", "").split(".")[-1] == dataset.name:
                enum_values = t.get("filter_enum_values") or {}
                break

    candidates = {
        "table": dataset.name,
        "layer": dataset.layer.value,
        "table_annotation": table_annotation,
        "columns": [
            {
                "name": c["column"], "type": c["type"], "null_rate": null_rates.get(c["column"]), "annotation": annotations.get(c["column"]),
                "real_distinct_values": enum_values.get(c["column"]),
            }
            for c in real_columns_info
        ],
    }
    return candidates, real_columns


async def suggest_checks(db: Session, dataset: MedallionDataset, warehouse: DataSource, config: AIConfig | None = None) -> CheckSuggestOut:
    """§7.3 — non-persistent draft, exactly like suggest_baseline: the engineer reviews then
    POSTs their own (possibly edited) selection separately. Unlike suggest_baseline, there is
    NO deterministic heuristic fallback here — a check requires genuine semantic understanding
    (is this column an email? a bounded age?) that a naive fallback can't safely guess; an
    unreachable/invalid AI response degrades to an empty list, never a mechanically-wrong
    check."""
    candidates, real_columns = _check_candidates(db, dataset, warehouse)
    if not real_columns:
        return CheckSuggestOut(suggestions=[])

    config = config or get_ai_config()
    call_config = config if config.timeout >= SUGGESTION_TIMEOUT_FLOOR else replace(config, timeout=SUGGESTION_TIMEOUT_FLOOR)
    try:
        raw_text = await ai_client.chat_completion(call_config, _build_checks_prompt(candidates), temperature=0.1)
        parsed = extract_json_object(raw_text)
        raw_items = parsed.get("checks", []) if isinstance(parsed, dict) else []
    except (ai_client.AIClientError, ValueError) as exc:
        logger.info("quality_contract: check suggestion AI unavailable for dataset %s: %s", dataset.id, exc)
        return CheckSuggestOut(suggestions=[])

    suggestions: list[CheckSuggestion] = []
    for item in raw_items[:8]:
        try:
            raw = _RawCheckSuggestion.model_validate(item)
        except ValidationError as exc:
            logger.info("quality_contract: malformed AI check suggestion for dataset %s: %s", dataset.id, exc)
            continue
        target_column = raw.target_column or ""
        errors = validate_check_parameters(raw.check_type, target_column, raw.parameters, real_columns)
        if errors:
            logger.info("quality_contract: rejected AI check suggestion (%s/%s) for dataset %s: %s", raw.check_type.value, target_column, dataset.id, errors)
            continue
        suggestions.append(CheckSuggestion(check_type=raw.check_type, target_column=raw.target_column, parameters=raw.parameters, rationale=raw.rationale[:300]))

    return CheckSuggestOut(suggestions=suggestions)


# ---------------------------------------------------------------------------------------------
# §8.3 — Étape 5: the AI's third and last bounded role (§0) — explain a measured defect,
# strictly downstream, never upstream. Optional; the caller decides whether to cache the result.
# ---------------------------------------------------------------------------------------------


async def explain_alert(alert: DataQualityAlert, config: AIConfig | None = None) -> str:
    """The ONLY input is the alert's own deterministically-built `message` (already carries the
    real measured rate and the configured threshold, evaluate_coherence in quality_rules.py) —
    nothing else, so there is nothing for the AI to invent a number, table, or cause from. On
    any AI failure, degrades to the raw message itself rather than a broken/empty explanation
    (never worse than what the engineer already sees, same pattern as the heuristic fallbacks
    above)."""
    system = (
        "You explain a data-quality alert in one or two short, plain sentences for a data "
        "engineer, in French. You are given the alert's type, severity, and its already-"
        "computed message (containing the real measured rate and threshold) — reformulate it "
        "into a clearer diagnostic. NEVER invent a number, a table name, or a cause not present "
        "in the message. Reply with the explanation text only, no JSON, no markdown, no preamble."
    )
    user = f"type: {alert.type.value}\nseverity: {alert.severity.value}\nmessage: {alert.message}"
    config = config or get_ai_config()
    call_config = config if config.timeout >= SUGGESTION_TIMEOUT_FLOOR else replace(config, timeout=SUGGESTION_TIMEOUT_FLOOR)
    try:
        explanation = await ai_client.chat_completion(call_config, [{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=0.2)
        explanation = explanation.strip()
        return explanation[:1000] if explanation else alert.message
    except ai_client.AIClientError as exc:
        logger.info("quality_contract: explain_alert AI unavailable for alert %s: %s", alert.id, exc)
        return alert.message
