"""Module 16 extension §4 — deterministic renderer: DataQualityCheck (opted in via
`materialize_as_dbt_test`) → dbt test forms. Conceptual twin of dbt_project.py's own model
generation: the AI proposed the check upstream (quality_contract.suggest_checks, M16 §7.3),
the engineer validated it, this module only PLACES the already-approved form — it never
generates or re-validates SQL (predicate/condition already passed
sql_validator.validate_predicate_sql at check-write time, quality_contract.py).

Only 3 of the 5 CheckType values are Tier A (a dbt-expectations macro, attached under the
target dataset's own column in schema.yml/sources.yml/ml_sources.yml): type_conformity,
format_validity, plausibility. The other 2 are Tier C (a standalone singular test .sql):
intra_row_consistency, conditional_completeness. See expectation_catalog.py for why Tier B
(referential_integrity/grain_uniqueness/null_rate) and the ⟨baseline⟩ indicators have no
per-check materialization toggle in this extension."""
from sqlalchemy.orm import Session

from app.models.data_quality import CheckStatus, CheckType, DataQualityCheck
from app.models.medallion import MedallionDataset, MedallionLayer, TransformType
from app.services import expectation_catalog, quality_intrinsic

_TIER_A_MACRO = {
    CheckType.type_conformity: "dbt_expectations.expect_column_values_to_match_regex",
    CheckType.format_validity: "dbt_expectations.expect_column_values_to_match_regex",
    CheckType.plausibility: "dbt_expectations.expect_column_values_to_be_between",
}
_TIER_C_TYPES = {CheckType.intra_row_consistency, CheckType.conditional_completeness}


def stable_name(check: DataQualityCheck) -> str:
    """§2 idempotence/diff lisible — every materialized test carries an identity derived from
    DataQualityCheck.id, stable across re-renders regardless of parameter edits."""
    return f"dqc_{check.id}"


def _tier_a_args(check: DataQualityCheck) -> dict | None:
    """Returns the macro's own args (never name/severity — added by the caller), or None if
    the check's already-validated parameters don't resolve to anything renderable (defensive;
    never happens for a check that passed quality_contract.validate_check_parameters)."""
    params = check.parameters or {}
    if check.check_type == CheckType.type_conformity:
        # Reuses TYPE_CONFORMITY_PATTERNS (quality_intrinsic.py) — the SAME regex the
        # observation path already measures against, never a second source of truth (§0 "non-
        # duplication de sens"). (?i): the observation path lower()s the column before
        # matching — an inline case-insensitive flag mirrors that exactly in Postgres regex.
        pattern = quality_intrinsic.TYPE_CONFORMITY_PATTERNS.get(params.get("target_type"))
        return {"regex": f"(?i){pattern}"} if pattern else None
    if check.check_type == CheckType.format_validity:
        pattern = params.get("pattern")
        return {"regex": pattern} if pattern else None
    if check.check_type == CheckType.plausibility:
        args = {}
        if params.get("min") is not None:
            args["min_value"] = params["min"]
        if params.get("max") is not None:
            args["max_value"] = params["max"]
        return args or None
    return None


def schema_test_entry(check: DataQualityCheck, severity: str | None = None) -> dict | None:
    """Tier A — one `data_tests:` list item for this check: the macro + its args + a stable
    name + a severity (§5 double chemin). `severity` defaults to the check's own live setting;
    the export path (§6) overrides it to always emit a test, `warn` for anything not
    live-enforced — "rien n'est perdu" without ever silently promoting a check to `error`."""
    macro = _TIER_A_MACRO.get(check.check_type)
    if macro is None:
        return None
    args = _tier_a_args(check)
    if args is None:
        return None
    args = dict(args)
    args["name"] = stable_name(check)
    args["severity"] = severity if severity is not None else check.dbt_test_severity.value
    return {macro: args}


def _relation_ref(dataset: MedallionDataset) -> str:
    if dataset.layer == MedallionLayer.bronze:
        return f"{{{{ source('bronze', '{dataset.name}') }}}}"
    if dataset.transform_type == TransformType.python:
        return f"{{{{ source('gold_ml', '{dataset.output_table or dataset.name}') }}}}"
    return f"{{{{ ref('{dataset.dbt_model_name}') }}}}"


def singular_test_sql(check: DataQualityCheck, dataset: MedallionDataset, severity: str | None = None) -> str | None:
    """Tier C — a standalone tests/*.sql file. Must return 0 rows to pass (dbt singular test
    convention). The predicate/condition is placed VERBATIM, exactly as approved at check-
    write time — the renderer never regenerates or re-validates SQL (§4). `severity` defaults
    to the check's own live setting; see schema_test_entry for the export override."""
    params = check.parameters or {}
    if check.check_type == CheckType.intra_row_consistency:
        predicate = params.get("predicate") or ""
        if not predicate:
            return None
        where = f"NOT ({predicate})"
    elif check.check_type == CheckType.conditional_completeness:
        condition, target = params.get("condition") or "", params.get("target") or ""
        if not condition or not target:
            return None
        where = f"({condition}) AND ({target} IS NULL)"
    else:
        return None
    relation = _relation_ref(dataset)
    effective_severity = severity if severity is not None else check.dbt_test_severity.value
    return (
        f"{{{{ config(severity='{effective_severity}') }}}}\n"
        f"-- Matérialisé depuis le contrat de qualité (check #{check.id}, {check.check_type.value}) "
        f"— ne pas éditer à la main, écrasé au prochain build.\n"
        f"SELECT *\nFROM {relation}\nWHERE {where}\n"
    )


def describe(check: DataQualityCheck) -> str | None:
    """§5 — the concrete dbt form shown read-only under a check in the « Contrôles » screen
    (JetBrains Mono), computed from the SAME functions the renderer itself uses so display and
    reality can never drift apart. None for a check the catalogue can't materialize."""
    entry = schema_test_entry(check)
    if entry is not None:
        (macro, args), = entry.items()
        inner = ", ".join(f"{k}={v!r}" for k, v in args.items() if k not in ("name", "severity"))
        return f"{macro}({inner})"
    if check.check_type in _TIER_C_TYPES:
        params = check.parameters or {}
        if check.check_type == CheckType.intra_row_consistency:
            return f"test singulier : WHERE NOT ({params.get('predicate', '')})"
        return f"test singulier : WHERE ({params.get('condition', '')}) AND ({params.get('target', '')} IS NULL)"
    return None


class RenderedTests:
    __slots__ = ("schema_by_dataset", "singular_files", "has_tier_a")

    def __init__(self) -> None:
        self.schema_by_dataset: dict[int, dict[str, list]] = {}
        self.singular_files: dict[str, str] = {}
        self.has_tier_a = False


def _render(checks: list[DataQualityCheck], dataset_by_id: dict[int, MedallionDataset], severity_of) -> RenderedTests:
    result = RenderedTests()
    for check in checks:
        # A project-wide check (dataset_id is None) has no single dataset to attach a test to
        # — mirrors quality_intrinsic.collect()'s own current limitation exactly (loaded but
        # never resolved to a table until a real use case asks for it), never a crash here.
        if check.dataset_id is None:
            continue
        dataset = dataset_by_id.get(check.dataset_id)
        if dataset is None or not expectation_catalog.is_materializable(check.check_type):
            continue
        severity = severity_of(check)

        entry = schema_test_entry(check, severity=severity)
        if entry is not None:
            result.schema_by_dataset.setdefault(dataset.id, {}).setdefault(check.target_column, []).append(entry)
            result.has_tier_a = True
            continue

        sql = singular_test_sql(check, dataset, severity=severity)
        if sql is not None:
            result.singular_files[f"tests/{stable_name(check)}.sql"] = sql

    return result


def render(db: Session, project_id: int, datasets: list[MedallionDataset]) -> RenderedTests:
    """§4 orchestration, replayed on every LIVE dbt-project generation (build/preview) — never
    a separate incremental write path. Reads only `active` + `materialize_as_dbt_test` checks,
    at THEIR OWN severity; a dismissed/un-flagged check simply produces nothing on the NEXT
    regeneration (§2 réversibilité — the whole tree is always rebuilt fresh from current DB
    state)."""
    dataset_by_id = {d.id: d for d in datasets}
    checks = (
        db.query(DataQualityCheck)
        .filter(
            DataQualityCheck.project_id == project_id,
            DataQualityCheck.status == CheckStatus.active,
            DataQualityCheck.materialize_as_dbt_test.is_(True),
        )
        .all()
    )
    return _render(checks, dataset_by_id, lambda c: c.dbt_test_severity.value)


def render_for_export(db: Session, project_id: int, datasets: list[MedallionDataset]) -> RenderedTests:
    """§6 — every `active` check becomes a test in the exported bundle, not just the ones
    materialized live: "le client repart avec l'intégralité du contrat validé exprimé en
    tests, rien n'est perdu". A check not opted into live enforcement is exported at `warn`
    (never silently promoted to `error`); one already opted in keeps its own live severity."""
    dataset_by_id = {d.id: d for d in datasets}
    checks = (
        db.query(DataQualityCheck)
        .filter(DataQualityCheck.project_id == project_id, DataQualityCheck.status == CheckStatus.active)
        .all()
    )
    return _render(checks, dataset_by_id, lambda c: c.dbt_test_severity.value if c.materialize_as_dbt_test else "warn")
