import json
import logging
import re
from dataclasses import replace

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.models.data_source import DataSource, DataSourceType
from app.services import ai_client, join_builder
from app.services.ai_config import AIConfig
from app.services.ai_json import extract_json_object
from app.services.gold_builder import (
    DATE_DIFF_UNITS, DERIVED_OPS, FILTER_OPS, WHITELISTED_TIME_GRAINS,
    GoldBuilderError, build_gold_sql, gold_output_columns,
)
from app.services.grain_check import check_count_distinct, check_gold_coverage, check_gold_count, check_gold_grain, check_ratio_coverage, gold_grain
from app.services.sql_validator import dialect_for, validate_silver_sql
from app.services.superset_templates import WHITELISTED_AGGREGATIONS
from app.services import silver_preview

logger = logging.getLogger(__name__)

# On the original CPU-bound self-hosted Mistral, even a single-table plan (bronze+silver+
# gold) never completed within 900s. On the GPU-backed replacement, the same request
# completes in ~20-30s — kept well above that observed figure for headroom on larger,
# multi-table plans, but far below the old CPU-era ceiling.
PLAN_TIMEOUT_FLOOR = 300.0

_BRONZE_MODES = ("full", "incremental")
_TEST_TYPES = ("not_null", "unique", "accepted_values", "relationships")

_SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"]bronze['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_REF_RE = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")


class PlanAIError(Exception):
    pass


class _RawBronze(BaseModel):
    table: str
    mode: str = "full"
    incremental_key: str | None = None


_JOIN_TYPES = ("inner", "left")


class _RawLogicalJoin(BaseModel):
    left: str = ""
    right: str = ""
    type: str = "inner"


class _RawLogicalPlan(BaseModel):
    """Module 14 §6.2 — the AI's intent, produced BEFORE the sql field: what it means to join,
    filter and normalize, in a closed vocabulary an engineer reads without parsing SQL."""
    joins: list[_RawLogicalJoin] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    normalizations: list[str] = Field(default_factory=list)
    output: list[str] = Field(default_factory=list)


class _RawSilver(BaseModel):
    name: str = ""
    upstreams: list[str] = Field(default_factory=list)
    sql: str = ""
    rationale: str = ""
    logical_plan: _RawLogicalPlan | None = None


_FilterValue = str | int | float | bool


class _RawFilter(BaseModel):
    """Module 14 extension "primitives gold" §3.1 — a deterministic WHERE predicate. `value`
    stays untyped-lenient (the AI can emit a bare number/bool for a non-string column) since a
    single unexpected type here shouldn't fail the whole plan's JSON parsing — pipeline_plan.py
    normalizes/validates it against the real profile before it ever reaches gold_builder."""
    column: str = ""
    op: str = ""
    value: _FilterValue | list[_FilterValue] | None = None


class _RawMetric(BaseModel):
    """§3.3 — a derived measure, replacing metric_column. op="date_diff" is the only derived
    op so far; op="column" would be the (unused) explicit spelling of the flat metric_column
    case."""
    op: str = "column"
    start: str | None = None
    end: str | None = None
    unit: str = "day"


class _RawAggregateSpec(BaseModel):
    """§3.2 — one side (numerator/denominator) of a RATIO: its own closed aggregate, with its
    own optional filter(s)."""
    aggregation: str = ""
    metric_column: str = ""
    metric: _RawMetric | None = None
    filter: _RawFilter | None = None
    filters: list[_RawFilter] = Field(default_factory=list)


class _RawGold(BaseModel):
    name: str = ""
    upstreams: list[str] = Field(default_factory=list)
    metric_column: str = ""
    aggregation: str = ""
    # Module 14 extension "primitives gold" — three additive, optional slots (§3): a gold using
    # none of them compiles exactly as before (rétrocompat absolue).
    metric: _RawMetric | None = None
    filter: _RawFilter | None = None
    filters: list[_RawFilter] = Field(default_factory=list)
    numerator: _RawAggregateSpec | None = None
    denominator: _RawAggregateSpec | None = None
    scale: float | None = None
    dimension_columns: list[str] = Field(default_factory=list)
    time_column: str | None = None
    time_grain: str | None = None


class _RawTest(BaseModel):
    dataset: str = ""
    column: str = ""
    test: str = ""
    values: list[str] = Field(default_factory=list)
    to: str | None = None
    field: str | None = None


class _RawPlan(BaseModel):
    bronze: list[_RawBronze] = Field(default_factory=list)
    silver: list[_RawSilver] = Field(default_factory=list)
    gold: list[_RawGold] = Field(default_factory=list)
    tests: list[_RawTest] = Field(default_factory=list)


def _bronze_names(mapping_tables: list[dict]) -> dict[str, str]:
    """table ("schema.table") -> short, deduplicated bronze dataset name — the identifier
    the AI is told to use in {{ source('bronze', '<name>') }}, and the same one Étape 4 will
    use as the real MedallionDataset.name when it creates the bronze dataset."""
    used: set[str] = set()
    names: dict[str, str] = {}
    for t in mapping_tables:
        base = t["table"].split(".")[-1]
        name = base
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)
        names[t["table"]] = name
    return names


def _table_columns(columns: dict) -> list[str]:
    seen: list[str] = []
    for role in ("dimension", "temporal", "measure", "join_keys"):
        for c in columns.get(role, []):
            if c not in seen:
                seen.append(c)
    return seen


def _oracle_bronze_names(db: Session, mapping_tables: list[dict], bronze_names: dict[str, str]) -> set[str]:
    """bronze_names of every table whose source is Oracle — those land in the Postgres
    warehouse with quoted-UPPERCASE identifiers (confirmed manually on an earlier project:
    an unquoted lowercase reference fails with "column does not exist", since Postgres folds
    unquoted identifiers to lowercase but the real column is a distinct, case-sensitive
    quoted one). Every other supported source type (Postgres, MySQL) preserves the casing
    dbt/Postgres already expect, so this only ever needs to special-case Oracle."""
    source_ids = {t["source_id"] for t in mapping_tables}
    oracle_source_ids = {s.id for s in db.query(DataSource).filter(DataSource.id.in_(source_ids), DataSource.type == DataSourceType.oracle).all()}
    return {bronze_names[t["table"]] for t in mapping_tables if t["source_id"] in oracle_source_ids}


def _build_prompt(db: Session, instruction: str, mapping: dict) -> tuple[list[dict], dict[str, str]]:
    bronze_names = _bronze_names(mapping["tables"])
    oracle_bronze_names = _oracle_bronze_names(db, mapping["tables"], bronze_names)
    tables_sheet = [
        {
            "bronze_name": bronze_names[t["table"]],
            "table": t["table"],
            "role": t["role"],
            "columns": _table_columns(t["columns"]),
            "measure_columns": t["columns"].get("measure", []),
            "temporal_columns": t["columns"].get("temporal", []),
            "dimension_columns": t["columns"].get("dimension", []),
            "join_key_columns": t["columns"].get("join_keys", []),
            "column_casing": "quoted_uppercase" if bronze_names[t["table"]] in oracle_bronze_names else "natural",
            # Module 14 extension "primitives gold" §4.1 — real, complete distinct values for
            # whichever columns have them (never sent when empty, same "omit rather than empty"
            # rule as intent_mapping._profile_sheet), so the AI picks a filter's "value" from
            # what actually exists instead of guessing ("Deceased" vs "Died" vs "deceased"...).
            **({"enum_values": t["filter_enum_values"]} if t.get("filter_enum_values") else {}),
        }
        for t in mapping["tables"]
    ]
    example_table = mapping["tables"][0]
    example_bronze_name = bronze_names[example_table["table"]]
    example_cols = _table_columns(example_table["columns"])
    example_col = example_cols[0] if example_cols else "id"
    example_measure = (example_table["columns"].get("measure") or example_cols or ["value"])[0]
    example_json = json.dumps({
        "bronze": [{"table": example_table["table"], "mode": "full", "incremental_key": None}],
        "silver": [{
            "name": f"stg_{example_bronze_name}",
            "upstreams": [example_bronze_name],
            "logical_plan": {"joins": [], "filters": [], "normalizations": [], "output": [example_col]},
            "sql": f"SELECT {example_col} FROM {{{{ source('bronze', '{example_bronze_name}') }}}}",
            "rationale": "Selects the useful columns.",
        }],
        "gold": [{
            "name": f"mart_{example_bronze_name}",
            "upstreams": [f"stg_{example_bronze_name}"],
            "metric_column": example_measure, "aggregation": "SUM",
            "dimension_columns": [], "time_column": None, "time_grain": None,
        }],
        "tests": [{"dataset": f"stg_{example_bronze_name}", "column": example_col, "test": "not_null"}],
    }, ensure_ascii=False)

    # Star-schema joins (a fact table + reference table(s) sharing a join key) are built
    # deterministically, not by the AI — confirmed empirically that even a worked JOIN
    # example in the prompt didn't stop the model from producing one staging silver per
    # bronze table instead of joining them. The AI is told these already exist and to use
    # them by name for any gold that needs columns from more than one table.
    join_groups = join_builder.detect_join_groups(mapping["tables"])
    prebuilt_silver = []
    for g in join_groups:
        fact_bname = bronze_names[g["fact"]["table"]]
        fact_cols = _table_columns(g["fact"]["columns"])
        refs = [
            {
                "bronze_name": bronze_names[r["ref"]["table"]],
                "column_names": _table_columns(r["ref"]["columns"]),
                "join_key": r["join_key"],
                "via": r["via"],
                "quoted": bronze_names[r["ref"]["table"]] in oracle_bronze_names,
            }
            for r in g["references"]
        ]
        name = f"stg_{fact_bname}_joined"
        sql = join_builder.build_join_sql(fact_bname, fact_cols, refs, fact_quoted=fact_bname in oracle_bronze_names)
        exposed_columns = sorted({*fact_cols, *(c for r in refs for c in r["column_names"])})

        # Module 14 §6.2 — derived mechanically from the same join structure join_builder just
        # compiled, never asked of the AI: this silver isn't AI-authored SQL to begin with.
        alias_to_bname = {"f": fact_bname}
        logical_joins = []
        for i, r in enumerate(refs):
            logical_joins.append({"left": f"{alias_to_bname[r['via']]}.{r['join_key']}", "right": f"{r['bronze_name']}.{r['join_key']}", "type": "inner"})
            alias_to_bname[f"r{i}"] = r["bronze_name"]

        prebuilt_silver.append({
            "name": name, "sql": sql, "exposed_columns": exposed_columns,
            "fact_table": g["fact"]["table"], "reference_tables": [r["ref"]["table"] for r in g["references"]],
            "logical_plan": {"joins": logical_joins, "filters": [], "normalizations": [], "output": exposed_columns},
        })

    join_guidance = ""
    if prebuilt_silver:
        listing = "\n".join(
            f"- \"{p['name']}\" (join {p['fact_table']} + {', '.join(p['reference_tables'])}) — "
            f"available columns: {', '.join(p['exposed_columns'])}"
            for p in prebuilt_silver
        )
        main_join = prebuilt_silver[0]
        join_guidance = (
            "\n\nJOIN SILVER MODELS ARE ALREADY PREPARED FOR YOU (do not recreate them, do not "
            f"rewrite their SQL, do not put them back in \"silver\"):\n{listing}\n"
            "YOU MUST produce at least one object in \"gold\" whose \"upstreams\" contains "
            f"EXACTLY the name \"{main_join['name']}\" (not another name, not a silver you would "
            "invent yourself). Choose \"metric_column\" and \"dimension_columns\" only among its "
            f"available columns listed above ({', '.join(main_join['exposed_columns'])})."
        )

    # Annexe "élargir le scope de l'assistant IA" — the prompt used to implicitly frame toward
    # a single gold ("the expected main gold"), forcing any multi-need instruction (e.g. "top
    # 10 products, top 10 customers, top 10 countries...") into one generalist mart with every
    # dimension combined. A plan (`_RawPlan.gold`) already accepts a list — this guidance just
    # lifts the friction that kept the AI from using it.
    multi_gold_guidance = (
        "\n\nIf the business instruction expresses SEVERAL distinct analytical needs (e.g. "
        "\"top 10 products\", \"top 10 customers\", \"top channels\" are different needs), "
        "DO NOT GROUP THEM into a single generalist gold: produce ONE separate \"gold\" object "
        "per need, each with a \"name\" descriptive of the need it serves (e.g. "
        "\"mart_top_products\", \"mart_top_customers\", \"mart_channel_performance\"), and "
        "\"dimension_columns\" limited to what THAT precise need requires — never the union of "
        "every possible dimension in each gold. Each gold can reuse the same \"upstreams\" (the "
        "same source silver). If the instruction expresses only one need, a single gold remains "
        "the right answer — do not multiply golds without reason. Never exceed 10 golds total, "
        "even if the instruction evokes more needs. If you fill \"time_column\" for a gold, you "
        "MUST also fill \"time_grain\" with one of these values: \"year\", \"month\" or \"day\" "
        "— never one without the other."
    )

    # Module 14 extension "primitives gold" (§4.1/§4.2/§4.3), raffinage §4.2 — three optional
    # primitives on top of the existing flat contract, used ONLY when the need genuinely
    # requires it (a gold needing none of them stays a plain metric_column/aggregation,
    # unchanged). Confirmed empirically (language A/B test, project 41's occupancy-rate
    # objective): the English phrasing below reliably produces correct COUNT_DISTINCT-based
    # RATIO golds where the equivalent French prompt kept producing SUM(bed_id) — same model,
    # same data, same underlying constraints, only the prompt's language differed.
    primitives_guidance = (
        "\n\nTHREE ADDITIONAL PRIMITIVES are available on a gold, on top of the existing flat "
        "contract (metric_column/aggregation) — use them only when the need genuinely requires "
        "it, never by default:\n"
        "1) \"filter\" (single object) or \"filters\" (list, combined with AND) — for a need "
        "like \"count of <filtered event>\" (deaths, discharges, collected/paid amount...). "
        "Format: {\"column\": \"...\", \"op\": \"=\"|\"!=\"|\"IN\"|\"IS_NULL\"|\"IS_NOT_NULL\", "
        "\"value\": ...}. Choose \"value\" ONLY among the values listed in \"enum_values\" of the "
        "column concerned (provided below when available) — NEVER invent or guess a value absent "
        "from that list.\n"
        "2) \"aggregation\": \"RATIO\" with \"numerator\" and \"denominator\" (each an ISOLATED "
        "sub-aggregation object: {\"aggregation\", \"metric_column\", \"filter\"?}) and optional "
        "\"scale\" (100 for a percentage) — for a need like a rate/ratio/percentage. The filter "
        "that concerns ONLY the numerator (e.g. payment_status='Paid') goes INSIDE "
        "\"numerator.filter\", NEVER in \"filter\" at the gold level — a gold-level \"filter\" "
        "would also apply to the denominator and make the rate wrong. Bad: a gold with "
        "\"filter\": {\"column\": \"payment_status\", \"op\": \"=\", \"value\": \"Paid\"} and "
        "\"aggregation\": \"RATIO\" (the denominator would be filtered too). Good: "
        "\"numerator\": {\"aggregation\": \"SUM\", \"metric_column\": \"total_amount\", "
        "\"filter\": {\"column\": \"payment_status\", \"op\": \"=\", \"value\": \"Paid\"}}, "
        "\"denominator\": {\"aggregation\": \"SUM\", \"metric_column\": \"total_amount\"}, "
        "\"scale\": 100. For a count-based rate (e.g. occupancy rate), use \"aggregation\": "
        "\"COUNT\" or \"COUNT_DISTINCT\" on BOTH sides instead of SUM — e.g. numerator = "
        "COUNT(bed_id) filtered bed_status='Occupied', denominator = COUNT(bed_id) with no "
        "filter.\n"
        "3) \"metric\": {\"op\": \"date_diff\", \"start\": \"<start_date_column>\", \"end\": "
        "\"<end_date_column>\", \"unit\": \"day\"|\"month\"|\"year\"} INSTEAD OF "
        "\"metric_column\" — for a duration/delay-between-two-dates need (e.g. average length "
        "of stay = aggregation \"AVG\" + metric date_diff(admission_date, discharge_date, "
        "\"day\")). \"metric\" and \"metric_column\" are EXCLUSIVE: if you use \"metric\", omit "
        "\"metric_column\" (or leave it empty) — never provide both together.\n"
        "Never reference a column in these primitives that is not listed for this table below.\n\n"
        "NEGATIVE CONSTRAINT for column roles: 'measure' means ONLY summable/cumulative numeric "
        "quantities (amounts, quantities, durations). NEVER classify as 'measure': an identifier "
        "or key (*_id, *_key columns), a number/rank (floor number, room number), a year or a "
        "tenure duration expressed in years (e.g. experience_years, age), a numeric code or "
        "status. When in doubt, classify as 'dimension'. For a 'count of <entities>' or 'rate' "
        "need on a dimension/join_key column (e.g. counting beds, departments), use COUNT or "
        "COUNT_DISTINCT — NEVER SUM on a non-measure column."
    )

    system = (
        "You propose a medallion project plan (bronze/silver/gold/tests) from an already "
        "validated mapping of real tables. Respond ONLY with a valid JSON object, no text or "
        "markdown around it.\n\n"
        f"EXAMPLE TO FOLLOW — exact structure, with REAL values from this specific project:\n{example_json}"
        f"{join_guidance}"
        f"{multi_gold_guidance}"
        f"{primitives_guidance}\n\n"
        "Adapt this example to the tables/columns listed below in the user message: reproduce "
        "exactly the same JSON structure, and especially the same SQL writing style. Every "
        "reference to a bronze table is written literally as "
        "{{ source('bronze', '<real_bronze_name>') }} — replace <real_bronze_name> with the real "
        "value of the bronze_name field of the table concerned (above it is "
        f"'{example_bronze_name}', for another table it will be a different value — never the "
        "literal text 'bronze_name'). Every reference to another silver model is written "
        "literally as {{ ref('<name_given_in_name>') }}, where <name_given_in_name> is the real "
        "name you yourself chose in the \"name\" field of that silver. Never put a raw "
        "schema.table name (e.g. 'sh.sales') nor a bare name (e.g. 'stg_sales') without these "
        "double braces in a FROM/JOIN clause.\n\n"
        "Other rules: use ONLY the tables/columns provided below, never invent anything. "
        "mode='incremental' only if incremental_key is a column listed in temporal_columns. Each "
        "gold's upstreams must contain the real name (\"name\" field) of one of the silvers you "
        "produced. Each test's \"dataset\" must be the real name of a bronze_name, a silver or a "
        "gold you produced — never a generic word like 'bronze_name' or 'silver_name'.\n\n"
        "For each silver, fill \"logical_plan\" BEFORE writing \"sql\" — it is your intent, in a "
        "closed vocabulary, that \"sql\" must then faithfully translate: \"joins\" (list of "
        "{\"left\": \"<bronze_name_or_silver>.<column>\", \"right\": "
        "\"<bronze_name_or_silver>.<column>\", \"type\": \"inner\"|\"left\"} — empty if no join), "
        "\"filters\" (WHERE conditions in free text, e.g. \"date_disp IS NOT NULL\"), "
        "\"normalizations\" (transformations in free text, e.g. \"UPPER(TRIM(dci))\"), \"output\" "
        "(list of columns actually present in the final SELECT). Never invent a table/column "
        "name in \"logical_plan\" that isn't in the tables provided below.\n\n"
        "Pay attention to column casing: each table in the list carries a \"column_casing\" "
        "field. If its value is \"quoted_uppercase\", write THAT column preceded by its alias in "
        "double quotes and UPPERCASE in your silver SQL (e.g. for column prod_id on a table "
        "aliased b: b.\"PROD_ID\"), otherwise the query fails with « column does not exist ». "
        "Always alias the result with the original lowercase name (e.g. b.\"PROD_ID\" AS "
        "prod_id) so anything reading this silver afterwards keeps using the normal lowercase "
        "name. If \"column_casing\" is \"natural\", write the column normally, without quotes."
    )
    user = json.dumps({"instruction": instruction, "tables": tables_sheet}, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], bronze_names, prebuilt_silver


async def generate_plan_ai(db: Session, config: AIConfig, instruction: str, mapping: dict) -> tuple[_RawPlan, dict[str, str], list[dict]]:
    """Raises PlanAIError on any failure — same reasoning as intent_mapping.map_intent_ai."""
    messages, bronze_names, prebuilt_silver = _build_prompt(db, instruction, mapping)
    call_config = config if config.timeout >= PLAN_TIMEOUT_FLOOR else replace(config, timeout=PLAN_TIMEOUT_FLOOR)
    try:
        raw = await ai_client.chat_completion(call_config, messages, temperature=0.1)
        return _RawPlan.model_validate(extract_json_object(raw)), bronze_names, prebuilt_silver
    except (ai_client.AIClientError, ValueError, ValidationError) as exc:
        raise PlanAIError(str(exc)) from exc


def _resolve_bronze(raw_bronze: list[_RawBronze], mapping_tables: list[dict], bronze_names: dict[str, str]) -> list[dict]:
    raw_by_table = {b.table: b for b in raw_bronze if b.table in bronze_names}
    result = []
    for t in mapping_tables:
        table = t["table"]
        raw = raw_by_table.get(table)
        mode, incremental_key = "full", None
        if raw and raw.mode in _BRONZE_MODES:
            temporal_cols = t["columns"].get("temporal", [])
            if raw.mode == "incremental" and raw.incremental_key in temporal_cols:
                mode, incremental_key = "incremental", raw.incremental_key
        result.append({"name": bronze_names[table], "table": table, "source_id": t["source_id"], "mode": mode, "incremental_key": incremental_key})
    return result


def _resolve_logical_plan(
    raw_lp: _RawLogicalPlan | None, declared_names: set[str], available_columns: dict[tuple[str, str], set[str]], name_kind: dict[str, str],
) -> dict | None:
    """Module 14 §6.2 — structural validation only: joins.left/right reference real columns of
    datasets ∈ lignée; type ∈ {inner,left}; output ⊆ known columns. Anything invalid is
    dropped — same "never trust, never crash" philosophy as every other resolver here. None if
    the AI didn't produce a usable logical_plan at all: graceful degradation (§0) — the SQL
    itself, independently AST-validated, remains the real, executable artifact regardless."""
    if raw_lp is None:
        return None

    def _dataset_columns(ds_name: str) -> set[str] | None:
        kind = name_kind.get(ds_name)
        return available_columns.get((kind, ds_name)) if kind else None

    joins = []
    for j in raw_lp.joins:
        if j.type not in _JOIN_TYPES or "." not in j.left or "." not in j.right:
            continue
        left_ds, left_col = j.left.rsplit(".", 1)
        right_ds, right_col = j.right.rsplit(".", 1)
        if left_ds not in declared_names or right_ds not in declared_names:
            continue
        left_cols, right_cols = _dataset_columns(left_ds), _dataset_columns(right_ds)
        if (left_cols is not None and left_col not in left_cols) or (right_cols is not None and right_col not in right_cols):
            continue
        joins.append({"left": j.left, "right": j.right, "type": j.type})

    all_known: set[str] = set()
    any_known = False
    for ds_name in declared_names:
        cols = _dataset_columns(ds_name)
        if cols is not None:
            any_known, all_known = True, all_known | cols
    output = [c for c in raw_lp.output if not any_known or c in all_known]

    if not joins and not raw_lp.filters and not raw_lp.normalizations and not output:
        return None
    return {"joins": joins, "filters": list(raw_lp.filters)[:10], "normalizations": list(raw_lp.normalizations)[:10], "output": output}


def _relationship_pairs_by_bronze(relationships: list[dict], bronze_names: dict[str, str]) -> dict[frozenset, set[tuple[str, str]]]:
    """Module 14 §6.2 — translates relationship_detect's real schema.table.column naming into
    the short bronze_name vocabulary logical_plan.joins actually uses, so a proposed join can
    be checked against what was actually detected on the real data."""
    out: dict[frozenset, set[tuple[str, str]]] = {}
    for r in relationships:
        b_child, b_parent = bronze_names.get(r["child_table"]), bronze_names.get(r["parent_table"])
        if not b_child or not b_parent:
            continue
        pairs = out.setdefault(frozenset({b_child, b_parent}), set())
        pairs.add((r["child_column"], r["parent_column"]))
        pairs.add((r["parent_column"], r["child_column"]))
    return out


def _logical_plan_warning(logical_plan: dict | None, rel_pairs: dict[frozenset, set[tuple[str, str]]]) -> str | None:
    """None if consistent (or nothing to compare against). A join whose column pair doesn't
    match any high-confidence relationship detected between those same two tables is flagged —
    never rejected: the engineer may know something the sampler didn't (§6.2/§12)."""
    if not logical_plan:
        return None
    for j in logical_plan["joins"]:
        if "." not in j["left"] or "." not in j["right"]:
            continue
        left_ds, left_col = j["left"].rsplit(".", 1)
        right_ds, right_col = j["right"].rsplit(".", 1)
        known = rel_pairs.get(frozenset({left_ds, right_ds}))
        if known and (left_col, right_col) not in known:
            return f"la jointure {j['left']} = {j['right']} ne correspond à aucune relation détectée entre ces deux tables."
    return None


def _resolve_silver(
    raw_silver: list[_RawSilver], bronze_name_set: set[str], dialect: str,
    available_columns: dict[tuple[str, str], set[str]], rel_pairs: dict[frozenset, set[tuple[str, str]]],
) -> tuple[list[dict], dict[str, list[str]]]:
    result = []
    accepted_names: set[str] = set()
    # Annexe "élargir le scope de l'assistant IA" §fiabilité — an AI-authored silver's own
    # output columns (from validate_silver_sql's AST-parsed SELECT list), so a gold built on
    # top of it can be checked for hallucinated dimension_columns/metric_column exactly like a
    # prebuilt-join silver already is, instead of only failing at real dbt execution time.
    output_columns_by_silver: dict[str, list[str]] = {}
    for s in raw_silver:
        if not s.name or not s.sql or s.name in accepted_names:
            continue
        # Module 14 §6.3 — replaces the old bare source()/ref() regex with a real AST pass:
        # SELECT-only, lineage, real-column existence, no accidental cross join.
        validation = validate_silver_sql(s.sql, dialect, bronze_name_set, accepted_names, available_columns)
        if not validation.valid:
            logger.info("Module 14 sql_validator: silver %s rejeté : %s", s.name, "; ".join(validation.errors))
            continue

        declared_names = bronze_name_set | accepted_names
        name_kind = {n: "bronze" for n in bronze_name_set} | {n: "silver" for n in accepted_names}
        logical_plan = _resolve_logical_plan(s.logical_plan, declared_names, available_columns, name_kind)
        logical_plan_warning = _logical_plan_warning(logical_plan, rel_pairs)

        declared = set(s.upstreams) | validation.referenced_bronze | validation.referenced_silver
        result.append({
            "name": s.name, "upstreams": sorted(declared), "sql": s.sql, "rationale": s.rationale,
            "status": "draft", "approved_by": None, "approved_at": None,
            "logical_plan": logical_plan, "logical_plan_warning": logical_plan_warning,
        })
        accepted_names.add(s.name)
        if validation.output_columns:
            output_columns_by_silver[s.name] = sorted(validation.output_columns)
    return result, output_columns_by_silver


# Real production failure: an AI-proposed gold aggregated `AVG(discharge_date)` — a genuine,
# existing column, but a raw DATE, not a number — Postgres has no AVG(date) overload
# ("function avg(date) does not exist"), so the failure only ever surfaced at real dbt
# execution time. SUM/AVG are the only WHITELISTED_AGGREGATIONS that require a genuinely
# numeric operand; COUNT/COUNT_DISTINCT/MIN/MAX are valid on any column type (COUNT of
# non-nulls, MIN/MAX of a date is a real, meaningful date).
_NUMERIC_ONLY_AGGREGATIONS = ("SUM", "AVG")

# Module 14 extension "primitives gold" §2 — "hygiène données" : a filter value is only ever
# checked against values already gathered by profiling (filter_enum_values, §3.1), never a new
# scan at validation time. A temporal column without a captured enum list still gets a light
# type check (a real date-shaped string), everything else is accepted as-is — no per-column
# min/max range is threaded this far yet, so range validation is simply not attempted rather
# than faked.
_DATE_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _resolve_filters(
    raw_filters: list[_RawFilter], exposed: set[str] | None, enum_values: dict[str, list[str]], column_roles: dict[str, str],
) -> tuple[list[dict], str | None]:
    """Returns (resolved filters, rejection reason). §6 — a bad filter REJECTS the whole gold
    (never silently dropped/degraded): unlike a hallucinated dimension_column, losing a filter
    silently changes what the KPI actually means (e.g. "nombre de décès" quietly becoming
    "nombre total d'admissions"), so this follows §3.1's explicit "rejet, pas warning" rule."""
    result: list[dict] = []
    for f in raw_filters:
        if not f.column or f.op not in FILTER_OPS:
            return [], f"filtre invalide sur « {f.column or '?'} » — op « {f.op} » non autorisé (attendu : {', '.join(FILTER_OPS)})."
        if exposed is not None and f.column not in exposed:
            return [], f"filtre sur une colonne inexistante : « {f.column} »."
        if f.op in ("IS_NULL", "IS_NOT_NULL"):
            result.append({"column": f.column, "op": f.op, "value": None})
            continue
        if f.value is None:
            return [], f"filtre « {f.op} » sur « {f.column} » sans valeur."
        values = f.value if isinstance(f.value, list) else [f.value]
        if f.op != "IN" and len(values) != 1:
            return [], f"filtre « {f.op} » sur « {f.column} » attend une seule valeur."
        known = enum_values.get(f.column)
        if known is not None:
            invalid = [v for v in values if str(v) not in known]
            if invalid:
                return [], f"valeur(s) {invalid} absente(s) des valeurs réelles de « {f.column} » (valeurs admises : {known})."
        elif column_roles.get(f.column) == "temporal":
            invalid = [v for v in values if not _DATE_LIKE_RE.match(str(v))]
            if invalid:
                return [], f"valeur(s) {invalid} non reconnue(s) comme date pour « {f.column} »."
        result.append({"column": f.column, "op": f.op, "value": f.value if f.op == "IN" else values[0]})
    return result, None


def _resolve_metric(
    metric_column: str, raw_metric: _RawMetric | None, aggregation: str, exposed: set[str] | None, column_roles: dict[str, str],
) -> tuple[str, dict | None, str | None]:
    """Returns (metric_column, metric_dict, rejection_reason) — mutually exclusive, mirroring
    §3.3's flat metric_column (rétrocompat) vs. derived metric={"op": "date_diff", ...}. The
    _NUMERIC_ONLY_AGGREGATIONS role gate only ever applies to the flat metric_column case: a
    date_diff's OWN result is numeric regardless of start/end being classified "temporal", not
    "measure" — that classification is exactly what date_diff is for."""
    if raw_metric is not None and raw_metric.op == "date_diff":
        # Raffinage §4.2 — "metric" et "metric_column" sont exclusifs (contrat §3.3). Rejeté
        # explicitement plutôt que de silencieusement préférer "metric" : une ambiguïté visible
        # est plus sûre qu'une résolution devinée.
        if metric_column:
            return "", None, "« metric » (date_diff) et « metric_column » sont exclusifs — fournis l'un ou l'autre, jamais les deux."
        start, end, unit = raw_metric.start, raw_metric.end, raw_metric.unit
        if not start or not end:
            return "", None, "« metric »: date_diff requiert « start » et « end »."
        if unit not in DATE_DIFF_UNITS:
            return "", None, f"« metric »: unité « {unit} » non autorisée (attendu : {', '.join(DATE_DIFF_UNITS)})."
        if exposed is not None and (start not in exposed or end not in exposed):
            return "", None, f"« metric »: date_diff référence une colonne absente de l'upstream : {start}/{end}."
        return "", {"op": "date_diff", "start": start, "end": end, "unit": unit}, None
    if raw_metric is not None and raw_metric.op not in DERIVED_OPS:
        return "", None, f"« metric.op » non autorisé : « {raw_metric.op} » (attendu : {', '.join(DERIVED_OPS)})."
    if not metric_column:
        return "", None, "« metric_column » ou « metric » requis."
    if exposed is not None and metric_column not in exposed:
        return "", None, f"la colonne mesure « {metric_column} » n'existe pas dans le silver source."
    metric_role = column_roles.get(metric_column)
    if aggregation in _NUMERIC_ONLY_AGGREGATIONS and metric_role is not None and metric_role != "measure":
        return "", None, f"{aggregation}({metric_column}) impossible — « {metric_column} » n'est pas une mesure numérique (classée {metric_role})."
    return metric_column, None, None


def _resolve_aggregate_spec(
    spec: _RawAggregateSpec | None, exposed: set[str] | None, enum_values: dict[str, list[str]], column_roles: dict[str, str],
) -> tuple[dict | None, str | None]:
    """§3.2 — one side (numerator/denominator) of a RATIO, validated with the exact same gates
    a flat gold gets (lineage, numeric-role, filter-against-profile)."""
    if spec is None or not spec.aggregation:
        return None, "spécification d'agrégat manquante (« aggregation » requis)."
    if spec.aggregation == "RATIO":
        return None, "un RATIO ne peut pas être imbriqué dans un autre RATIO."
    if spec.aggregation not in WHITELISTED_AGGREGATIONS:
        return None, f"agrégation non autorisée dans un ratio : « {spec.aggregation} »."
    metric_column, metric, err = _resolve_metric(spec.metric_column, spec.metric, spec.aggregation, exposed, column_roles)
    if err:
        return None, err
    raw_filters = ([spec.filter] if spec.filter else []) + list(spec.filters)
    filters, filter_err = _resolve_filters(raw_filters, exposed, enum_values, column_roles)
    if filter_err:
        return None, filter_err
    return {"aggregation": spec.aggregation, "metric_column": metric_column, "metric": metric, "filters": filters}, None


def _resolve_gold(
    raw_gold: list[_RawGold], silver_name_set: set[str], exposed_columns_by_silver: dict[str, list[str]] | None = None,
    instruction: str = "", column_roles: dict[str, str] | None = None, filter_enum_values: dict[str, list[str]] | None = None,
) -> tuple[list[dict], list[str]]:
    """Returns (resolved golds, human-readable reasons for every AI-proposed gold this
    dropped). Annexe "élargir le scope de l'assistant IA" §fiabilité : a dropped gold used to
    be visible only in the server log (logger.info) — invisible to the engineer validating the
    plan, so a fully-collapsed AI proposal (everything dropped, silent fallback to one generic
    gold) looked identical to "the AI genuinely only proposed one gold". Every drop below now
    also appends a short reason here; resolve_plan() surfaces the list via gold_warnings,
    exactly where check_gold_count/check_gold_coverage already do."""
    exposed_columns_by_silver = exposed_columns_by_silver or {}
    column_roles = column_roles or {}
    filter_enum_values = filter_enum_values or {}
    result: list[dict] = []
    rejected: list[str] = []
    for g in raw_gold:
        if not g.name:
            continue
        is_ratio = g.aggregation == "RATIO"
        if not g.aggregation:
            rejected.append(f"{g.name} : champ obligatoire manquant (aggregation).")
            continue
        if not is_ratio and not g.metric_column and not g.metric:
            rejected.append(f"{g.name} : champ obligatoire manquant (metric_column ou metric).")
            continue
        upstreams = [u for u in g.upstreams if u in silver_name_set]
        if not upstreams:
            logger.info("Module 13 plan: gold %s sans upstream silver valide — écarté", g.name)
            rejected.append(f"{g.name} : aucun silver source valide dans \"upstreams\".")
            continue

        time_column = g.time_column
        # De-duplicated, order-preserving: confirmed empirically that the AI can list the same
        # real column twice in dimension_columns (e.g. ["country_id", ..., "country_id", ...]).
        # Each individually passes the exposed-columns membership check below, so without this
        # the built SQL selects the same column name twice — harmless in a plain SELECT, but
        # gold materializes as a `table` (CREATE TABLE AS SELECT), where Postgres rejects a
        # duplicate column name outright ("column \"x\" specified more than once").
        dimension_columns = list(dict.fromkeys(g.dimension_columns))
        # Only silvers we built ourselves (the deterministic joins) have a known exact output
        # schema — AI-authored silver SQL can rename/alias columns freely, so there's no
        # reliable list to check gold against there. For a prebuilt join, though, we know
        # precisely what it exposes: silently drop hallucinated dimension_columns — same
        # "invalid entries never reach the build" philosophy as everywhere else in this
        # validator, just finally backed by real column knowledge here.
        exposed = exposed_columns_by_silver.get(upstreams[0])
        exposed_set = set(exposed) if exposed is not None else None
        if exposed is not None:
            dropped = [c for c in dimension_columns if c not in exposed]
            if dropped:
                logger.info("Module 13 plan: gold %s — dimension_columns inventées écartées : %s", g.name, dropped)
            dimension_columns = [c for c in dimension_columns if c in exposed]
            if time_column not in exposed:
                time_column = None

        raw_filters = ([g.filter] if g.filter else []) + list(g.filters)
        filters, filter_err = _resolve_filters(raw_filters, exposed_set, filter_enum_values, column_roles)
        if filter_err:
            rejected.append(f"{g.name} : {filter_err}")
            continue

        metric_column, metric, numerator, denominator, scale = "", None, None, None, None
        if is_ratio:
            numerator, num_err = _resolve_aggregate_spec(g.numerator, exposed_set, filter_enum_values, column_roles)
            if num_err:
                rejected.append(f"{g.name} : numerator — {num_err}")
                continue
            denominator, den_err = _resolve_aggregate_spec(g.denominator, exposed_set, filter_enum_values, column_roles)
            if den_err:
                rejected.append(f"{g.name} : denominator — {den_err}")
                continue
            scale = g.scale
        else:
            metric_column, metric, metric_err = _resolve_metric(g.metric_column, g.metric, g.aggregation, exposed_set, column_roles)
            if metric_err:
                rejected.append(f"{g.name} : {metric_err}")
                continue

        # Confirmed empirically (multi-gold plans): the AI can fill a real time_column while
        # leaving time_grain empty/invalid — especially once it's describing several golds at
        # once rather than the single worked example. That's still a perfectly good, well-
        # targeted gold with one missing field, not a reason to drop it entirely: default to
        # "year", the same fallback _heuristic_gold_from_join already uses.
        time_grain = g.time_grain if (time_column and g.time_grain in WHITELISTED_TIME_GRAINS) else ("year" if time_column else None)

        try:
            sql = build_gold_sql(
                upstreams[0], metric_column, g.aggregation, dimension_columns, time_column, time_grain,
                metric=metric, filters=filters, numerator=numerator, denominator=denominator, scale=scale,
            )
        except GoldBuilderError as exc:
            logger.info("Module 13 plan: gold %s rejeté par le builder déterministe : %s", g.name, exc)
            rejected.append(f"{g.name} : {exc}")
            continue
        result.append({
            "name": g.name, "upstreams": upstreams, "metric_column": metric_column, "aggregation": g.aggregation,
            "metric": metric, "filters": filters, "numerator": numerator, "denominator": denominator, "scale": scale,
            "dimension_columns": dimension_columns, "time_column": time_column,
            "time_grain": time_grain, "sql": sql,
            # Module 14 §5.3 — derived from the exact same inputs gold_builder used for the
            # GROUP BY, never a second AI-declared value that could drift from the real SQL.
            "grain": gold_grain(dimension_columns, time_column, time_grain),
            "grain_warning": check_gold_grain(instruction, time_column, time_grain, gold_name=g.name, metric_column=metric_column, dimension_columns=dimension_columns),
        })
    return result, rejected


def _resolve_tests(
    raw_tests: list[_RawTest],
    valid_dataset_names: set[str],
    exposed_columns_by_dataset: dict[str, list[str]] | None = None,
    column_renames_by_dataset: dict[str, dict[str, str]] | None = None,
) -> list[dict]:
    exposed_columns_by_dataset = exposed_columns_by_dataset or {}
    column_renames_by_dataset = column_renames_by_dataset or {}
    result = []
    for t in raw_tests:
        if t.dataset not in valid_dataset_names or not t.column or t.test not in _TEST_TYPES:
            continue
        # Gold columns are deterministically aliased by gold_builder (e.g. metric_column ->
        # total_<metric_column>) — the AI is prompted with the raw metric/time column names,
        # so a test it writes against those needs remapping to what the mart actually exposes,
        # not just validation against it.
        column = column_renames_by_dataset.get(t.dataset, {}).get(t.column, t.column)
        # Same known-schema check as gold's dimension_columns: a prebuilt join's or a gold
        # mart's real output columns are known exactly, so a test the AI invented against a
        # column that isn't actually there is caught here instead of failing at dbt compile time.
        exposed = exposed_columns_by_dataset.get(t.dataset)
        if exposed is not None and column not in exposed:
            logger.info("Module 13 plan: test sur %s.%s écarté — colonne absente du dataset", t.dataset, t.column)
            continue
        entry: dict = {"dataset": t.dataset, "column": column, "test": t.test, "include": True}
        if t.test == "accepted_values":
            if not t.values:
                continue
            entry["values"] = t.values
        elif t.test == "relationships":
            if not t.to or not t.field or t.to not in valid_dataset_names:
                continue
            entry["to"] = t.to
            entry["field"] = t.field
        result.append(entry)
    return result


def validate_silver_lineage(sql: str, bronze_name_set: set[str], other_silver_name_set: set[str]):
    """AST-validated (Module 14 §6.3) — the same rule `_resolve_silver` applies to AI output,
    reused when a human edits a silver SQL via PATCH /plan (§5.6.2's "SQL citant une table hors
    lignée" rule isn't AI-specific), now backed by a real syntactic pass instead of a bare
    regex: also catches a disguised write statement or an accidental cross join in a manual
    edit. Dialect fixed to Postgres — dbt always materializes silver against the warehouse
    (dbt_project.py's profiles.yml), never against a bronze table's own original source
    dialect. No column-schema check here (available_columns=None): PATCH /plan doesn't carry
    the full column universe at this call site, and a human edit is trusted more than AI output."""
    return validate_silver_sql(sql, dialect_for(DataSourceType.postgresql), bronze_name_set, other_silver_name_set)


def _heuristic_gold_from_join(join_group: dict, joined_silver_name: str, instruction: str = "") -> dict | None:
    """Deterministic fallback gold built off a prebuilt join when the AI's own gold ends up
    empty — same "heuristic when the AI comes up short" pattern as Module 12's
    indicator_suggest.py falling back to suggest_indicators_heuristic(). None if the fact
    table has no measure column to aggregate (nothing sensible to fall back to)."""
    fact_columns = join_group["fact"]["columns"]
    measure_candidates = fact_columns.get("measure") or []
    if not measure_candidates:
        return None
    metric_column = measure_candidates[0]
    # De-duplicated (see _resolve_gold's identical fix): two different reference tables could
    # each expose a same-named dimension column.
    dimension_columns = list(dict.fromkeys(c for ref in join_group["references"] for c in ref["ref"]["columns"].get("dimension", [])))
    temporal_candidates = fact_columns.get("temporal") or []
    time_column = temporal_candidates[0] if temporal_candidates else None
    time_grain = "year" if time_column else None
    name = f"mart_{joined_silver_name.removeprefix('stg_')}"
    try:
        sql = build_gold_sql(joined_silver_name, metric_column, "SUM", dimension_columns, time_column, time_grain)
    except GoldBuilderError:
        return None
    return {
        "name": name, "upstreams": [joined_silver_name],
        "metric_column": metric_column, "aggregation": "SUM", "metric": None, "filters": [],
        "numerator": None, "denominator": None, "scale": None, "dimension_columns": dimension_columns,
        "time_column": time_column, "time_grain": time_grain, "sql": sql,
        "grain": gold_grain(dimension_columns, time_column, time_grain),
        "grain_warning": check_gold_grain(instruction, time_column, time_grain, gold_name=name, metric_column=metric_column, dimension_columns=dimension_columns),
    }


def resolve_plan(raw: _RawPlan, mapping: dict, bronze_names: dict[str, str], prebuilt_silver: list[dict] | None = None, instruction: str = "") -> dict:
    """Whitelists the AI's raw plan against the validated mapping, the bronze lineage silver
    SQL actually declares, and gold_builder's own closed vocabulary — mirrors
    intent_mapping.resolve_mapping's philosophy: anything invalid is dropped, never trusted.

    `prebuilt_silver` (from _build_prompt, via join_builder) is injected unconditionally,
    regardless of whether the AI actually used it — the deterministic join must exist so a
    gold referencing it by name (as instructed in the prompt) resolves correctly, even if the
    AI ignored the instruction and tried to redefine it under the same name itself."""
    bronze = _resolve_bronze(raw.bronze, mapping["tables"], bronze_names)
    bronze_name_set = {b["name"] for b in bronze}

    # Module 14 §6.3 — the real column universe the AST validator checks silver SQL/output
    # against: every mapped table's own classified columns (bronze), plus each prebuilt join's
    # exact known output (silver) — same "only reject what we can actually verify" rule as
    # elsewhere, an AI-authored silver's own output schema stays unknown to later resolvers.
    #
    # Real production failure (Module 14 correctif follow-up) — the mapping step already
    # classifies every real column's role (measure/temporal/dimension/join_keys) via
    # connections._infer_column_role; _resolve_gold reuses it here to catch a numeric
    # aggregation (SUM/AVG) proposed on a column that was never a measure to begin with (a
    # real gold once proposed AVG(discharge_date) — a genuine column, just a raw DATE, which
    # Postgres has no AVG() overload for) — instead of only failing at real dbt execution.
    # Global by column name (not scoped per table): good enough in practice, and a gold's
    # metric_column is already separately checked against its own upstream silver's real
    # exposed columns just below.
    available_columns: dict[tuple[str, str], set[str]] = {}
    column_roles: dict[str, str] = {}
    # Module 14 extension "primitives gold" §3.1 — same "global by column name" simplification
    # as column_roles just above: a gold filter's column is already separately checked against
    # its own upstream silver's real exposed columns, this only supplies the real distinct
    # values to validate the literal against.
    filter_enum_values: dict[str, list[str]] = {}
    for t in mapping["tables"]:
        bname = bronze_names.get(t["table"])
        if bname:
            available_columns[("bronze", bname)] = set(_table_columns(t["columns"]))
        for role in ("measure", "temporal", "dimension", "join_keys"):
            for col in t["columns"].get(role, []):
                column_roles[col] = role
        for col, values in (t.get("filter_enum_values") or {}).items():
            filter_enum_values.setdefault(col, values)
    for p in prebuilt_silver or []:
        available_columns[("silver", p["name"])] = set(p["exposed_columns"])
    # Module 14 §6.2 — detected relationships (Étape 1), translated to the short bronze_name
    # vocabulary logical_plan.joins uses, so a proposed AI join can be checked against them.
    rel_pairs = _relationship_pairs_by_bronze(mapping.get("relationships") or [], bronze_names)
    dialect = dialect_for(DataSourceType.postgresql)  # dbt always targets the Postgres warehouse

    silver, output_columns_by_silver = _resolve_silver(raw.silver, bronze_name_set, dialect, available_columns, rel_pairs)

    prebuilt_names = {p["name"] for p in (prebuilt_silver or [])}
    silver = [s for s in silver if s["name"] not in prebuilt_names]
    for p in prebuilt_silver or []:
        silver.append({
            "name": p["name"], "upstreams": sorted(bronze_name_set), "sql": p["sql"],
            "rationale": "Jointure générée automatiquement (clé de jointure détectée dans le mapping).",
            "status": "draft", "approved_by": None, "approved_at": None,
            "logical_plan": p.get("logical_plan"), "logical_plan_warning": None,
        })

    silver_name_set = {s["name"] for s in silver}
    # Prebuilt-join silvers' exact known output, PLUS every AI-authored silver's own
    # AST-parsed SELECT output (annexe "élargir le scope de l'assistant IA" §fiabilité) — no
    # name collision possible (prebuilt names are excluded from output_columns_by_silver above).
    exposed_columns_by_silver = {p["name"]: p["exposed_columns"] for p in (prebuilt_silver or [])} | output_columns_by_silver
    gold, gold_rejections = _resolve_gold(raw.gold, silver_name_set, exposed_columns_by_silver, instruction, column_roles, filter_enum_values)

    if not gold and prebuilt_silver:
        join_groups = join_builder.detect_join_groups(mapping["tables"])
        for join_group, p in zip(join_groups, prebuilt_silver):
            fallback = _heuristic_gold_from_join(join_group, p["name"], instruction)
            if fallback:
                gold.append(fallback)
                logger.info("Module 13 plan: gold IA absent — repli heuristique déterministe sur %s", p["name"])
        if gold:
            gold_rejections.append(
                "aucun des golds proposés par l'IA n'a survécu à la validation — repli sur un "
                "gold générique unique, qui peut ne pas correspondre à l'intention exprimée."
            )

    # Annexe "élargir le scope de l'assistant IA" §fiabilité — a dropped-gold reason always
    # comes first: it explains WHY the plan looks the way it does (e.g. only the fallback gold
    # survived), which the count/coverage checks below can't on their own.
    gold_warnings = []
    if gold_rejections:
        gold_warnings.append(
            f"{len(gold_rejections)} gold(s) proposé(s) par l'IA écarté(s) à la validation : " + " · ".join(gold_rejections[:5])
            + (" · …" if len(gold_rejections) > 5 else "")
        )
    gold_warnings += [
        w for w in [
            check_gold_count(gold), check_gold_coverage(instruction, gold),
            check_count_distinct(gold, column_roles), check_ratio_coverage(instruction, gold),
        ] if w
    ]

    gold_name_set = {g["name"] for g in gold}
    exposed_columns_by_gold = {
        g["name"]: gold_output_columns(
            g["metric_column"], g["dimension_columns"], g["time_column"], g["time_grain"],
            aggregation=g["aggregation"], metric=g.get("metric"),
        )
        for g in gold
    }
    column_renames_by_gold = {}
    for g in gold:
        renames = {}
        # RATIO has no single input column to rename from (its output is the fixed "ratio"
        # alias); a derived metric (date_diff) likewise has no bare metric_column to rename —
        # only the flat, rétrocompat case renames metric_column -> total_<metric_column>.
        if g["aggregation"] != "RATIO" and not g.get("metric") and g["metric_column"]:
            renames[g["metric_column"]] = f"total_{g['metric_column'].lower()}"
        if g["time_column"]:
            renames[g["time_column"]] = f"{g['time_grain']}_{g['time_column']}"
        column_renames_by_gold[g["name"]] = renames
    tests = _resolve_tests(
        raw.tests,
        bronze_name_set | silver_name_set | gold_name_set,
        {**exposed_columns_by_silver, **exposed_columns_by_gold},
        column_renames_by_gold,
    )
    return {"bronze": bronze, "silver": silver, "gold": gold, "tests": tests, "gold_warnings": gold_warnings}


def is_plan_valid(plan: dict) -> bool:
    return bool(plan["bronze"]) and bool(plan["gold"])


class _RawRepair(BaseModel):
    sql: str = ""


def _repair_prompt(original_sql: str, logical_plan: dict | None, errors: list[str], tables_sheet: list[dict]) -> list[dict]:
    system = (
        "You are fixing a dbt silver model's SQL that failed validation. Respond ONLY with a "
        "valid JSON object, exactly: {\"sql\": \"...\"}. Fix ONLY what the errors listed below "
        "point out; keep the rest of the SQL and the intent of the provided logical plan "
        "unchanged. Follow the same writing rules: every bronze table as "
        "{{ source('bronze', '<name>') }}, every silver as {{ ref('<name>') }}, never a raw "
        "name; use only the columns actually listed in the tables provided."
    )
    user = json.dumps({
        "sql_to_fix": original_sql, "logical_plan": logical_plan,
        "errors_to_fix": errors, "available_tables": tables_sheet,
    }, ensure_ascii=False)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def _repair_silver_sql(config: AIConfig, original_sql: str, logical_plan: dict | None, errors: list[str], tables_sheet: list[dict]) -> str | None:
    """None means the AI attempt itself failed (unreachable/unparsable) — the caller treats
    that the same as an unproductive repair attempt: it still counts against the bound, just
    with no new SQL to re-validate."""
    messages = _repair_prompt(original_sql, logical_plan, errors, tables_sheet)
    call_config = config if config.timeout >= PLAN_TIMEOUT_FLOOR else replace(config, timeout=PLAN_TIMEOUT_FLOOR)
    try:
        raw = await ai_client.chat_completion(call_config, messages, temperature=0.1)
        parsed = _RawRepair.model_validate(extract_json_object(raw))
        return parsed.sql or None
    except (ai_client.AIClientError, ValueError, ValidationError):
        return None


async def repair_plan_silvers(
    db: Session, config: AIConfig, raw: _RawPlan, mapping: dict, bronze_names: dict[str, str],
    prebuilt_silver: list[dict] | None, max_attempts: int,
) -> list[dict]:
    """Module 14 §7 — bounded, read-only repair loop. Runs BEFORE resolve_plan(): validates
    each AI-authored silver (prebuilt joins never need this — they're compiler-built, not
    AI-authored) via the AST validator (sql_validator.py) then, when possible, a real execution
    probe against the original source (silver_preview.py); on failure, asks the model to fix
    ONLY that silver's sql, up to `max_attempts` times. Mutates `raw.silver[i].sql` in place
    when a fix is accepted — resolve_plan()'s own validation remains the final, unchanged
    authority regardless of what happens here (it re-validates everything on the way in,
    cheaply). Returns a repair log — one entry per silver that needed at least one attempt —
    for the UI's "corrigé automatiquement (n)" indicator. A silver that exhausts its attempts
    is simply left failing: resolve_plan() drops it exactly like any other invalid entry, so no
    SQL that never actually validated is ever presented as good (§7.5)."""
    bronze_name_set = set(bronze_names.values())
    prebuilt_names = {p["name"] for p in (prebuilt_silver or [])}

    available_columns: dict[tuple[str, str], set[str]] = {}
    for t in mapping["tables"]:
        bname = bronze_names.get(t["table"])
        if bname:
            available_columns[("bronze", bname)] = set(_table_columns(t["columns"]))
    for p in prebuilt_silver or []:
        available_columns[("silver", p["name"])] = set(p["exposed_columns"])
    dialect = dialect_for(DataSourceType.postgresql)
    tables_sheet = [{"bronze_name": bronze_names[t["table"]], "columns": _table_columns(t["columns"])} for t in mapping["tables"]]

    accepted_names: set[str] = set(prebuilt_names)
    log: list[dict] = []
    for s in raw.silver:
        if not s.name or not s.sql or s.name in prebuilt_names:
            continue
        attempts = 0
        while True:
            validation = validate_silver_sql(s.sql, dialect, bronze_name_set, accepted_names, available_columns)
            if not validation.valid:
                ok, errors = False, list(validation.errors)
            else:
                preview = silver_preview.check_silver_sql(db, mapping["tables"], bronze_names, s.sql)
                ok = preview.status != "error"
                errors = [] if ok else [f"exécution réelle : {preview.message}"]

            if ok:
                if attempts > 0:
                    log.append({"name": s.name, "attempts": attempts, "status": "repaired"})
                accepted_names.add(s.name)
                break

            attempts += 1
            if attempts > max_attempts:
                log.append({"name": s.name, "attempts": attempts - 1, "status": "failed", "error": errors[0] if errors else "échec non spécifié"})
                break

            logical_plan_dict = s.logical_plan.model_dump() if s.logical_plan else None
            fixed_sql = await _repair_silver_sql(config, s.sql, logical_plan_dict, errors, tables_sheet)
            if fixed_sql is None:
                log.append({"name": s.name, "attempts": attempts, "status": "failed", "error": "IA indisponible pour la réparation."})
                break
            s.sql = fixed_sql
    return log


async def repair_one_silver(db: Session, config: AIConfig, plan: dict, mapping: dict, bronze_names: dict[str, str], silver_name: str, max_attempts: int) -> dict:
    """Module 14 §7.4 — optional, on-demand counterpart to repair_plan_silvers(): re-validates
    and (bounded) repairs ONE already-resolved silver entry in place, e.g. after a manual SQL
    edit the engineer suspects broke it, without regenerating the whole plan. A prebuilt-join
    silver (never AI-authored) is a no-op — nothing to repair. Returns
    {"entry": <updated silver dict>, "log": <repair log entry or None if it was already valid>}."""
    entry = next((s for s in plan["silver"] if s["name"] == silver_name), None)
    if entry is None:
        raise ValueError(f"silver « {silver_name} » introuvable dans ce plan.")
    if entry.get("rationale", "").startswith("Jointure générée automatiquement"):
        return {"entry": entry, "log": None}

    bronze_name_set = {b["name"] for b in plan["bronze"]}
    other_silver_names = {s["name"] for s in plan["silver"] if s["name"] != silver_name}
    available_columns: dict[tuple[str, str], set[str]] = {}
    for t in mapping["tables"]:
        bname = bronze_names.get(t["table"])
        if bname:
            available_columns[("bronze", bname)] = set(_table_columns(t["columns"]))
    dialect = dialect_for(DataSourceType.postgresql)
    tables_sheet = [{"bronze_name": bronze_names[t["table"]], "columns": _table_columns(t["columns"])} for t in mapping["tables"]]

    sql = entry["sql"]
    attempts = 0
    while True:
        validation = validate_silver_sql(sql, dialect, bronze_name_set, other_silver_names, available_columns)
        if not validation.valid:
            ok, errors = False, list(validation.errors)
        else:
            preview = silver_preview.check_silver_sql(db, mapping["tables"], bronze_names, sql)
            ok = preview.status != "error"
            errors = [] if ok else [f"exécution réelle : {preview.message}"]

        entry["sql"] = sql
        if ok:
            return {"entry": entry, "log": {"name": silver_name, "attempts": attempts, "status": "repaired"} if attempts > 0 else None}

        attempts += 1
        if attempts > max_attempts:
            return {"entry": entry, "log": {"name": silver_name, "attempts": attempts - 1, "status": "failed", "error": errors[0] if errors else "échec non spécifié"}}

        fixed_sql = await _repair_silver_sql(config, sql, entry.get("logical_plan"), errors, tables_sheet)
        if fixed_sql is None:
            return {"entry": entry, "log": {"name": silver_name, "attempts": attempts, "status": "failed", "error": "IA indisponible pour la réparation."}}
        sql = fixed_sql
