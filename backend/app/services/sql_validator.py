"""Module 14 §6.3 — deterministic SQL AST validator for silver SQL (sqlglot). Runs BEFORE any
preview/display: SELECT-only (no write/DDL anywhere in the tree), every referenced table stays
within the declared lineage, every referenced column exists in the real upstream schema, no
accidental cross join, and only whitelisted functions. Replaces the M13 §5.2 "parsing léger"
(a bare regex over source()/ref() calls) with a real syntactic pass — the same source()/ref()
extraction is kept (dbt SQL is Jinja, not raw SQL; sqlglot can't parse Jinja), just substituted
into placeholder identifiers first so the rest of the SQL gets genuine AST scrutiny."""
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.models.data_source import DataSourceType
from app.services.superset_templates import WHITELISTED_AGGREGATIONS

_DIALECT_BY_SOURCE_TYPE = {
    DataSourceType.postgresql: "postgres",
    DataSourceType.mysql: "mysql",
    DataSourceType.oracle: "oracle",
}

_WRITE_EXPR_TYPES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Alter,
    exp.Create, exp.TruncateTable, exp.Grant,
)

_SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"]bronze['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
_REF_RE = re.compile(r"\{\{\s*ref\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")

_BRONZE_PREFIX = "__bronze__"
_REF_PREFIX = "__ref__"

# Named sqlglot expression classes a normalization/filter is allowed to use — covers standard
# string/numeric/date functions plus the aggregations gold_builder already whitelists
# (superset_templates.WHITELISTED_AGGREGATIONS), reused rather than duplicated.
_ALLOWED_FUNC_CLASSES = {
    "Upper", "Lower", "Trim", "Coalesce", "Cast", "Extract", "TimestampTrunc", "Round",
    "Substring", "Concat", "Nullif", "Abs", "Length", "Replace", "TimeToStr", "StrToTime",
    "CurrentDate", "CurrentTimestamp", "Case", "If",
    "Sum", "Count", "Avg", "Min", "Max",
    # Module 6 extension (payload & structuration) §5.3 — `payload->>'key'` (JSONExtractScalar)
    # and the numeric-cleaning regexp_replace() its guarded casts are built from.
    "JSONExtractScalar", "RegexpReplace",
}
# Function names sqlglot doesn't model as a dedicated class (parsed as exp.Anonymous) but that
# are legitimate on at least one supported dialect (mostly Oracle).
_ALLOWED_ANONYMOUS_FUNCS = {
    "NVL", "NVL2", "TO_CHAR", "TO_DATE", "TO_NUMBER", "DECODE", "REGEXP_REPLACE", "LPAD", "RPAD",
    # Module 6 extension — the try_cast-style PL/pgSQL helpers structuration casts go through
    # (never a naked ::type / to_date that could raise and abort the whole model, §11.7).
    "DP_TRY_CAST_INTEGER", "DP_TRY_CAST_BIGINT", "DP_TRY_CAST_NUMERIC", "DP_TRY_CAST_BOOLEAN",
    "DP_TRY_CAST_DATE", "DP_TRY_CAST_TIMESTAMP", "DP_TRY_CAST_JSONB",
}

assert WHITELISTED_AGGREGATIONS  # reused, not duplicated — see _ALLOWED_FUNC_CLASSES above


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    referenced_bronze: set[str] = field(default_factory=set)
    referenced_silver: set[str] = field(default_factory=set)
    referenced_columns: set[str] = field(default_factory=set)
    # Annexe "élargir le scope de l'assistant IA" §fiabilité — the SELECT list's own output
    # names (alias if aliased, bare column name otherwise), i.e. what THIS silver itself
    # exposes to whatever reads it next. Was previously only known for the deterministic
    # prebuilt-join silvers; an AI-authored silver's own output was invisible to
    # _resolve_gold's column-existence check, letting a hallucinated gold dimension_column
    # (referencing a column its own upstream silver never selected) through unvalidated —
    # confirmed on a real project where two gold models referenced columns their silver
    # never selected, only failing later at real dbt execution in Postgres.
    output_columns: set[str] = field(default_factory=set)


def dialect_for(source_type: DataSourceType | None) -> str:
    return _DIALECT_BY_SOURCE_TYPE.get(source_type, "postgres")


def _substitute_jinja(sql: str) -> str:
    sql = _SOURCE_RE.sub(lambda m: f"{_BRONZE_PREFIX}{m.group(1)}", sql)
    sql = _REF_RE.sub(lambda m: f"{_REF_PREFIX}{m.group(1)}", sql)
    return sql


def _invalid(errors: list[str]) -> ValidationResult:
    return ValidationResult(valid=False, errors=errors)


def validate_silver_sql(
    sql: str,
    dialect: str,
    bronze_name_set: set[str],
    other_silver_name_set: set[str],
    available_columns: dict[tuple[str, str], set[str]] | None = None,
) -> ValidationResult:
    """`available_columns`: {("bronze"|"silver", name): {real_column, ...}} — only checked for
    tables present in the map (a schema-unknown upstream, e.g. an AI-authored silver whose own
    output columns we can't derive without parsing ITS sql too, is skipped rather than falsely
    rejected — same "only reject what we can actually verify" rule _resolve_gold already
    applies to AI-authored silvers elsewhere in this pipeline)."""
    available_columns = available_columns or {}

    if not sql or not sql.strip():
        return _invalid(["SQL vide."])
    if not _SOURCE_RE.search(sql) and not _REF_RE.search(sql):
        return _invalid(["aucune référence source()/ref() — un modèle silver doit lire depuis la lignée déclarée."])

    substituted = _substitute_jinja(sql)
    try:
        tree = sqlglot.parse_one(substituted, read=dialect)
    except ParseError as exc:
        return _invalid([f"SQL invalide : {str(exc).splitlines()[0]}"])

    if not isinstance(tree, exp.Select):
        return _invalid([f"seul un SELECT est autorisé (reçu : {type(tree).__name__})."])

    errors: list[str] = []
    for node in tree.walk():
        if isinstance(node, _WRITE_EXPR_TYPES):
            errors.append("instruction d'écriture détectée dans le SQL (INSERT/UPDATE/DELETE/DDL) — un modèle silver est lecture seule.")
            break

    cte_names = {cte.alias for cte in tree.find_all(exp.CTE)}

    referenced_bronze: set[str] = set()
    referenced_silver: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name
        if name in cte_names:
            continue
        if name.startswith(_BRONZE_PREFIX):
            referenced_bronze.add(name[len(_BRONZE_PREFIX):])
        elif name.startswith(_REF_PREFIX):
            referenced_silver.add(name[len(_REF_PREFIX):])
        else:
            errors.append(f"table « {name} » référencée directement — utilise {{{{ source('bronze', '...') }}}} ou {{{{ ref('...') }}}}.")

    if not referenced_bronze.issubset(bronze_name_set):
        errors.append(f"référence des sources bronze hors lignée : {sorted(referenced_bronze - bronze_name_set)}.")
    if not referenced_silver.issubset(other_silver_name_set):
        errors.append(f"référence des modèles silver inconnus/hors lignée : {sorted(referenced_silver - other_silver_name_set)}.")

    for join in tree.find_all(exp.Join):
        if not join.args.get("on") and not join.args.get("using"):
            errors.append("jointure sans condition ON/USING détectée (produit cartésien potentiel).")

    referenced_columns: set[str] = set()
    all_known: set[str] = set()
    for name in referenced_bronze:
        cols = available_columns.get(("bronze", name))
        if cols is not None:
            all_known |= cols
    for name in referenced_silver:
        cols = available_columns.get(("silver", name))
        if cols is not None:
            all_known |= cols
    schema_known = bool(all_known) or any(
        (("bronze", n) in available_columns) or (("silver", n) in available_columns)
        for n in (referenced_bronze | referenced_silver)
    )
    for col in tree.find_all(exp.Column):
        referenced_columns.add(col.name)
        if schema_known and col.name not in all_known:
            errors.append(f"colonne « {col.name} » absente du schéma des tables référencées.")

    # `SELECT *` can't be statically resolved to real names here — leave output_columns empty
    # (the existing "unknown schema, don't reject" rule downstream treats that the same as
    # never having this information at all, same as before this field existed).
    output_columns: set[str] = set()
    if not any(isinstance(e, exp.Star) for e in tree.expressions):
        output_columns = {e.alias_or_name for e in tree.expressions if e.alias_or_name}

    for func in tree.find_all((exp.Func, exp.AggFunc)):
        cls_name = type(func).__name__
        if isinstance(func, exp.Anonymous):
            fname = (func.name or "").upper()
            if fname not in _ALLOWED_ANONYMOUS_FUNCS:
                errors.append(f"fonction « {fname} » non autorisée.")
        elif cls_name not in _ALLOWED_FUNC_CLASSES:
            errors.append(f"fonction « {cls_name} » non autorisée.")

    group_by = tree.args.get("group")
    if group_by:
        group_exprs = {g.sql(dialect=dialect) for g in group_by.expressions}
        for select_expr in tree.expressions:
            inner = select_expr.this if isinstance(select_expr, exp.Alias) else select_expr
            if inner.find(exp.AggFunc) is not None:
                continue
            if inner.sql(dialect=dialect) in group_exprs:
                continue
            # A deterministic expression built only from already-grouped columns (e.g.
            # UPPER(TRIM(x)) with GROUP BY x) is valid Postgres/standard SQL — grouping by the
            # underlying column is enough, the exact expression doesn't need to match verbatim.
            inner_cols = {c.sql(dialect=dialect) for c in inner.find_all(exp.Column)}
            if inner_cols and inner_cols.issubset(group_exprs):
                continue
            errors.append(f"colonne « {inner.sql(dialect=dialect)} » absente du GROUP BY.")

    if errors:
        return ValidationResult(valid=False, errors=errors, referenced_bronze=referenced_bronze, referenced_silver=referenced_silver, referenced_columns=referenced_columns, output_columns=output_columns)
    return ValidationResult(valid=True, referenced_bronze=referenced_bronze, referenced_silver=referenced_silver, referenced_columns=referenced_columns, output_columns=output_columns)


_MAX_PREDICATE_LEN = 500


def validate_predicate_sql(expr_sql: str, dialect: str, available_columns: set[str] | None = None) -> ValidationResult:
    """Module 16 §7.3 — validates a single boolean EXPRESSION (a quality check's predicate/
    condition, e.g. `date_fin >= date_debut`), not a full model: unlike validate_silver_sql
    above, there is no FROM/source()/ref() here at all — a check runs `SELECT count(*) FILTER
    (WHERE <predicate>) ...` against the ONE dataset it targets, so anything resembling a table
    reference or a subquery is exactly the "cross-join" smuggling the spec (§7.6.2) requires
    rejecting, not a legitimate shape to parse around.
    `available_columns`: real column names of the check's target dataset — None/empty skips the
    existence check (schema unknown), same "never falsely reject what we can't verify" rule as
    validate_silver_sql; a non-empty set enforces every referenced column is real."""
    if not expr_sql or not expr_sql.strip():
        return _invalid(["expression vide."])
    if len(expr_sql) > _MAX_PREDICATE_LEN:
        return _invalid([f"expression trop longue (max {_MAX_PREDICATE_LEN} caractères)."])

    try:
        tree = sqlglot.parse_one(expr_sql, read=dialect, into=exp.Condition)
    except ParseError as exc:
        return _invalid([f"expression invalide : {str(exc).splitlines()[0]}"])
    if tree is None:
        return _invalid(["expression invalide."])

    errors: list[str] = []
    for node in tree.walk():
        if isinstance(node, _WRITE_EXPR_TYPES):
            errors.append("instruction d'écriture détectée — un prédicat de contrôle est lecture seule.")
            break
        if isinstance(node, (exp.Select, exp.Subquery, exp.Table)):
            errors.append("référence de table/sous-requête détectée — un prédicat de contrôle porte uniquement sur les colonnes de la table ciblée.")
            break

    referenced_columns: set[str] = {col.name for col in tree.find_all(exp.Column)}
    if available_columns:
        unknown = referenced_columns - available_columns
        if unknown:
            errors.append(f"colonne(s) absente(s) du schéma de la table ciblée : {sorted(unknown)}.")

    for func in tree.find_all((exp.Func, exp.AggFunc)):
        if isinstance(func, exp.Anonymous):
            fname = (func.name or "").upper()
            if fname not in _ALLOWED_ANONYMOUS_FUNCS:
                errors.append(f"fonction « {fname} » non autorisée.")
        elif type(func).__name__ not in _ALLOWED_FUNC_CLASSES:
            errors.append(f"fonction « {type(func).__name__} » non autorisée.")

    if errors:
        return ValidationResult(valid=False, errors=errors, referenced_columns=referenced_columns)
    return ValidationResult(valid=True, referenced_columns=referenced_columns)
