"""Module 6 extension (payload & structuration) — §4 (profiling & contract) and §5 (rendering).

Structuration is a **deterministic compiler**: contract x payload -> SQL, never SQL from an
LLM. Two dbt models per payload bronze dataset, staged (§5 rewrite — unpacked/typed
convention):

- `01_unpacked_<name>`: pure extraction (`payload->>'key'`) + generic string hygiene
  (`clean_string`), every field the same way. No casting here.
- `02_typed_<name>`: casts `01_unpacked`'s cleaned text into real types, defensively, via
  `safe_cast` — a guarded dispatch to `dp_try_cast_*` (a tiny set of PL/pgSQL helpers deployed
  once per project via `on-run-start` in `public`; Postgres's own `::type`/`to_date` casts
  raise a hard error on a bad value, which would abort the whole model — these helpers catch
  that and return NULL instead). Every row from bronze reaches `02_typed` — nothing is ever
  excluded — diagnosed instead via a per-row `cast_issues` text[] (one `"field:absent"` or
  `"field:invalid"` tag per problem field, via the `cast_issue` macro), so a bad row is always
  inspectable and never silently dropped or gated behind a separate relation.

The field list itself (which payload keys to extract, their type, whether they're required)
is externalized as a dbt var (`<name>_fields`, written into `dbt_project.yml`) — the SQL is
pure logic ("clean and cast every field the same way"), the var is pure config ("here's the
list"), read by both stages via `var()`.
"""
import hashlib
import json
import logging
import re

import jinja2
import psycopg
from psycopg import sql as pgsql
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource, DataSourceType
from app.models.file_import import FileImport, FileImportStatus, ImportMode
from app.models.medallion import MedallionDataset, MedallionLayer
from app.models.payload_structuration import PayloadStructuration
from app.services import connections, schema_infer

logger = logging.getLogger("app.payload_structure")

IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ALLOWED_TARGET_TYPES = {"text", "integer", "bigint", "numeric", "boolean", "date", "timestamp", "jsonb"}

PROFILE_SAMPLE_SIZE = schema_infer.SAMPLE_SIZE  # 1000, same bound as M6's own inference

# schema_infer._THOUSANDS_CHARS is space / no-break space / narrow no-break space — the same
# separators M6's own inference already treats as thousands grouping (French convention).
_THOUSANDS_CHARS = schema_infer._THOUSANDS_CHARS

# Postgres to_char/to_date format tokens are all digit-only fields here, so a straight token
# substitution from the M6 contract's Python strptime-style format is unambiguous.
_PY_TO_PG_DATE_TOKENS = [
    ("%Y", "YYYY"), ("%y", "YY"), ("%m", "MM"), ("%d", "DD"),
    ("%H", "HH24"), ("%M", "MI"), ("%S", "SS"),
]
_DEFAULT_DATE_FORMAT = "%d/%m/%Y"
_DEFAULT_TIMESTAMP_FORMAT = "%d/%m/%Y %H:%M:%S"


class PayloadStructureError(Exception):
    pass


# ---------------------------------------------------------------------------
# Locating the payload landing table behind a bronze dataset
# ---------------------------------------------------------------------------

def resolve_import(db: Session, dataset: MedallionDataset) -> FileImport | None:
    """The FileImport that landed `dataset`'s payload table, or None if this bronze dataset
    isn't backed by a payload-mode import at all (wrong layer, no source_object, or a typed
    import) — callers treat None as "structuration doesn't apply here", never an error."""
    if dataset.layer != MedallionLayer.bronze or not dataset.source_id or not dataset.source_object:
        return None
    if "." not in dataset.source_object:
        return None
    schema_name, table = dataset.source_object.split(".", 1)
    fi = (
        db.query(FileImport)
        .filter(
            FileImport.target_source_id == dataset.source_id,
            FileImport.target_schema == schema_name,
            FileImport.target_table == table,
        )
        .order_by(FileImport.id.desc())
        .first()
    )
    if fi is None or fi.import_mode != ImportMode.payload:
        return None
    return fi


def bulk_payload_backed(db: Session, datasets: list[MedallionDataset]) -> dict[int, bool]:
    """Which bronze datasets in this list are backed by a payload-mode import — cheap,
    control-plane-only (no warehouse touch), one batched query rather than resolve_import()
    called per dataset. Same (target_source_id, target_schema, target_table) join
    build_lineage_graph already does for origin nodes, kept independent of it here since a
    caller (list_datasets) may not have loaded DataSource rows at all."""
    bronze = [
        d for d in datasets
        if d.layer == MedallionLayer.bronze and d.source_id and d.source_object and "." in d.source_object
    ]
    if not bronze:
        return {}
    source_ids = {d.source_id for d in bronze}
    imports = (
        db.query(FileImport)
        .filter(
            FileImport.target_source_id.in_(source_ids),
            FileImport.status == FileImportStatus.imported,
            FileImport.import_mode == ImportMode.payload,
        )
        .all()
    )
    payload_keys = {(fi.target_source_id, fi.target_schema, fi.target_table) for fi in imports}
    result: dict[int, bool] = {}
    for d in bronze:
        schema_name, table = d.source_object.split(".", 1)
        result[d.id] = (d.source_id, schema_name, table) in payload_keys
    return result


def bulk_structured(db: Session, datasets: list[MedallionDataset]) -> dict[int, bool]:
    """Which of these datasets have an actually-saved (not just profiled) structuration
    contract — same batched-query shape as bulk_payload_backed, driving the canvas's decision
    to show the 01..05 chain / instant unpacked+typed preview at all (§7 UX: nothing shows
    until a contract exists, not merely because the bronze happens to be payload-backed)."""
    ids = [d.id for d in datasets]
    if not ids:
        return {}
    rows = (
        db.query(PayloadStructuration.dataset_id)
        .filter(PayloadStructuration.dataset_id.in_(ids), PayloadStructuration.contract_hash.isnot(None))
        .all()
    )
    structured_ids = {r[0] for r in rows}
    return {d.id: d.id in structured_ids for d in datasets}


def _connect_warehouse(warehouse: DataSource) -> "psycopg.Connection":
    if warehouse.type != DataSourceType.postgresql:
        raise PayloadStructureError("La structuration exige un warehouse PostgreSQL.")
    secret = decrypt_secret(warehouse.secret_encrypted)
    return psycopg.connect(host=warehouse.host, port=warehouse.port, dbname=warehouse.database_name, user=warehouse.username, password=secret, connect_timeout=10)


def _split_source_object(dataset: MedallionDataset) -> tuple[str, str]:
    schema_name, table = dataset.source_object.split(".", 1)
    if not IDENTIFIER_RE.match(schema_name) or not IDENTIFIER_RE.match(table):
        raise PayloadStructureError("Emplacement du payload invalide.")
    return schema_name, table


# ---------------------------------------------------------------------------
# Étape 2 — profiling (reuses schema_infer.infer_schema verbatim on the union of payload keys)
# ---------------------------------------------------------------------------

def profile_payload(db: Session, dataset: MedallionDataset) -> list[dict]:
    """Samples the first PROFILE_SAMPLE_SIZE rows of the payload table (ordered by the file's
    own row_number, not an arbitrary DB order), unions every key ever seen (captures shape
    drift across reimports), and runs each key through schema_infer — the exact same cascade,
    confidence, and francophone-pitfall table CSV/Excel typed imports already use."""
    fi = resolve_import(db, dataset)
    if fi is None:
        raise PayloadStructureError("Ce dataset bronze n'est pas adossé à un import en mode payload.")
    warehouse = db.get(DataSource, dataset.source_id)
    if warehouse is None:
        raise PayloadStructureError("Source introuvable.")
    schema_name, table = _split_source_object(dataset)

    conn = _connect_warehouse(warehouse)
    try:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL("SELECT payload FROM {}.{} ORDER BY row_number LIMIT {}").format(
                    pgsql.Identifier(schema_name), pgsql.Identifier(table), pgsql.Literal(PROFILE_SAMPLE_SIZE),
                )
            )
            payload_rows = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()

    if not payload_rows:
        raise PayloadStructureError("Aucune ligne dans la table payload — importez d'abord un fichier.")

    keys: list[str] = []
    seen: set[str] = set()
    for row in payload_rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)

    # Carries the source_pk chosen at import time (§3.6) forward into the contract: those
    # keys pre-fill as the primary key of the structured (02_typed) table — badge only, never
    # a hard SQL constraint — and, since a PK can't be null, nullable=False so it's flagged
    # "required" in the rendered var (cast_issue tags a missing value). Still plain editable
    # defaults, same "pré-remplissage effaçable" rule as every other inferred field.
    pk_keys = {k.strip() for k in ((fi.format_options or {}).get("source_pk") or "").split(",") if k.strip()}

    mapping = schema_infer.infer_schema(payload_rows, keys)
    for entry in mapping:
        if entry["source_name"] in pk_keys:
            entry["is_primary_key"] = True
            entry["nullable"] = False
    return mapping


# ---------------------------------------------------------------------------
# Contract validation (Étape 2, §4.4) — identifiers only; the cast itself is a closed,
# type-dispatched macro call (safe_cast), not contract-built SQL text, so there's no
# injection surface left for an AST safety net to guard.
# ---------------------------------------------------------------------------

def _pg_date_format(py_format: str) -> str:
    fmt = py_format
    for py_tok, pg_tok in _PY_TO_PG_DATE_TOKENS:
        fmt = fmt.replace(py_tok, pg_tok)
    return fmt


def validate_column_mapping(column_mapping: list[dict]) -> None:
    """§4.4 — every included field's source_name/target_name/target_type validated."""
    seen_names: set[str] = set()
    for field in column_mapping:
        if not field.get("include", True):
            continue
        source_name = field.get("source_name")
        target_name = field.get("target_name")
        target_type = field.get("target_type")
        if not source_name:
            raise PayloadStructureError("Chaque champ inclus doit référencer une clé du payload.")
        if not target_name or not IDENTIFIER_RE.match(target_name):
            raise PayloadStructureError(f"Nom de colonne invalide : « {target_name} ».")
        if target_name in seen_names:
            raise PayloadStructureError(f"Colonne cible en double : « {target_name} ».")
        seen_names.add(target_name)
        if target_type not in ALLOWED_TARGET_TYPES:
            raise PayloadStructureError(f"Type cible non supporté : « {target_type} ».")

    if not seen_names:
        raise PayloadStructureError("Au moins un champ doit être inclus.")


# ---------------------------------------------------------------------------
# Étape 3 — rendering (§5 rewrite): two staged dbt models per bronze payload dataset, both
# landing in the `bronze` schema next to `<name>` itself — `01_unpacked_<name>` (table, clean
# text extraction) and `02_typed_<name>` (table, cast + cast_issues[], what silver/gold ref()).
# ---------------------------------------------------------------------------

# Deployed once per project via dbt's on-run-start (dbt_project.py), in `public` — always on
# Postgres's default search_path, so the generated model SQL below can call them unqualified.
# Each catches every exception and returns NULL instead — the one thing a naked `::type` or
# to_date()/to_timestamp() cast in Postgres will never do (empirically confirmed: both raise
# hard errors on a bad value, aborting the whole statement — see module docstring).
TRY_CAST_FUNCTIONS_SQL = """
CREATE OR REPLACE FUNCTION dp_try_cast_integer(v text) RETURNS integer AS $$
BEGIN
    RETURN v::integer;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_bigint(v text) RETURNS bigint AS $$
BEGIN
    RETURN v::bigint;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_numeric(v text) RETURNS numeric AS $$
BEGIN
    RETURN v::numeric;
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_boolean(v text) RETURNS boolean AS $$
BEGIN
    RETURN CASE lower(btrim(v))
        WHEN 'true' THEN true WHEN '1' THEN true WHEN 'oui' THEN true WHEN 'vrai' THEN true WHEN 'o' THEN true WHEN 'yes' THEN true WHEN 'y' THEN true
        WHEN 'false' THEN false WHEN '0' THEN false WHEN 'non' THEN false WHEN 'faux' THEN false WHEN 'n' THEN false WHEN 'no' THEN false
        ELSE NULL
    END;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_date(v text, fmt text) RETURNS date AS $$
BEGIN
    RETURN to_date(v, fmt);
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_timestamp(v text, fmt text) RETURNS timestamp AS $$
BEGIN
    RETURN to_timestamp(v, fmt);
EXCEPTION WHEN OTHERS THEN RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION dp_try_cast_jsonb(v text) RETURNS jsonb AS $$
BEGIN
    IF v IS NULL THEN RETURN NULL; END IF;
    RETURN v::jsonb;
EXCEPTION WHEN OTHERS THEN RETURN to_jsonb(v);
END;
$$ LANGUAGE plpgsql;
""".strip()


# ---------------------------------------------------------------------------
# dbt macros (static, written once per project alongside generate_schema_name.sql) — the
# unpacked/typed convention's building blocks. safe_cast dispatches to the dp_try_cast_*
# functions above; cast_issue is the "does this look valid" side of the same per-type
# dispatch, used only to tag a problem, never to gate anything — it takes an already-computed
# pattern (see _cast_pattern below) rather than deriving one itself, since a date/timestamp's
# pattern depends on that field's own format, not just its type.
# ---------------------------------------------------------------------------

CLEAN_STRING_MACRO_SQL = """{% macro clean_string(expr) -%}
NULLIF(btrim(btrim(btrim({{ expr }}), '"' || chr(39))), '')
{%- endmacro %}
"""

SAFE_CAST_MACRO_SQL = ("""{% macro safe_cast(type, col, format=none) -%}
{%- if type == 'integer' -%}
dp_try_cast_integer(regexp_replace(replace({{ col }}, ',', '.'), '[__THOUSANDS__]', '', 'g'))
{%- elif type == 'bigint' -%}
dp_try_cast_bigint(regexp_replace(replace({{ col }}, ',', '.'), '[__THOUSANDS__]', '', 'g'))
{%- elif type == 'numeric' -%}
dp_try_cast_numeric(regexp_replace(replace({{ col }}, ',', '.'), '[__THOUSANDS__]', '', 'g'))
{%- elif type == 'boolean' -%}
dp_try_cast_boolean({{ col }})
{%- elif type == 'date' -%}
dp_try_cast_date({{ col }}, '{{ format or "DD/MM/YYYY" }}')
{%- elif type == 'timestamp' -%}
dp_try_cast_timestamp({{ col }}, '{{ format or "DD/MM/YYYY HH24:MI:SS" }}')
{%- elif type == 'jsonb' -%}
dp_try_cast_jsonb({{ col }})
{%- else -%}
{{ col }}
{%- endif -%}
{%- endmacro %}
""").replace("__THOUSANDS__", _THOUSANDS_CHARS)

CAST_ISSUE_MACRO_SQL = """{% macro cast_issue(col, field_name, pattern, required) -%}
CASE
    WHEN {{ col }} IS NULL THEN {{ ("'" ~ field_name ~ ":absent'") if required else "NULL" }}
    WHEN {{ col }} !~* '{{ pattern }}' THEN '{{ field_name }}:invalid'
    ELSE NULL
END
{%- endmacro %}
"""

STRUCTURATION_MACROS = {
    "macros/clean_string.sql": CLEAN_STRING_MACRO_SQL,
    "macros/safe_cast.sql": SAFE_CAST_MACRO_SQL,
    "macros/cast_issue.sql": CAST_ISSUE_MACRO_SQL,
}

# Passed through untouched from bronze on every unpacked/typed model — the platform's actual
# payload+audit shape (file_import.py's _prepare_payload_table / _prepare_table), not a
# per-field concern.
_TRACEABILITY_COLUMNS = ["load_id", "source_file", "source_pk", "source_system", "row_number"]

_DATE_TOKEN_DIGITS = [("YYYY", "[0-9]{4}"), ("HH24", "[0-9]{2}"), ("MI", "[0-9]{2}"), ("SS", "[0-9]{2}"), ("MM", "[0-9]{2}"), ("DD", "[0-9]{2}"), ("YY", "[0-9]{2}")]


def _cast_pattern(target_type: str, pg_format: str | None) -> str:
    """The regex cast_issue checks a field's cleaned text against — computed here in Python,
    not as a type-only dbt macro, because a date/timestamp's pattern depends on that field's
    own configured format (DD/MM/YYYY vs YYYY-MM-DD aren't interchangeable), not just its
    type. Longest tokens replaced first (YYYY before YY) so a 4-digit year never gets doubly
    substituted into two 2-digit ones."""
    if target_type in ("integer", "bigint"):
        return r"^-?[0-9]+$"
    if target_type == "numeric":
        return r"^-?[0-9]+([.,][0-9]+)?$"
    if target_type == "boolean":
        return r"^(true|false|1|0|oui|non|vrai|faux|o|n|yes|no|y)$"
    if target_type in ("date", "timestamp"):
        fmt = pg_format or ("DD/MM/YYYY" if target_type == "date" else "DD/MM/YYYY HH24:MI:SS")
        for token, digits in _DATE_TOKEN_DIGITS:
            fmt = fmt.replace(token, digits)
        return f"^{fmt}$"
    return ".*"


def render_unpacked_typed_models(column_mapping: list[dict], bronze_name: str) -> dict:
    """Contract -> dbt files (§5 rewrite), deterministic (an unchanged contract renders
    byte-identical SQL): `01_unpacked_<name>` (extraction + clean_string) feeding
    `02_typed_<name>` (safe_cast + cast_issues[]), plus the `<name>_fields` var both read via
    `var()`. Both models read `{{ source('bronze', bronze_name) }}` / `{{ ref(...) }}` — the
    existing, unchanged bronze ingestion (§11.1), never `imports.<table>` directly.

    Returns {"vars_key", "vars_entries", "unpacked_sql", "typed_sql"}."""
    included = [f for f in column_mapping if f.get("include", True)]
    if not included:
        raise PayloadStructureError("Le contrat de structuration n'a aucun champ inclus.")

    vars_key = f"{bronze_name}_fields"
    vars_entries = []
    for f in included:
        entry = {"name": f["source_name"], "type": f["target_type"], "required": not f.get("nullable", True)}
        pg_format = None
        if f["target_type"] in ("date", "timestamp"):
            default_fmt = _DEFAULT_DATE_FORMAT if f["target_type"] == "date" else _DEFAULT_TIMESTAMP_FORMAT
            pg_format = _pg_date_format(f.get("format") or default_fmt)
            entry["format"] = pg_format
        entry["pattern"] = _cast_pattern(f["target_type"], pg_format)
        vars_entries.append(entry)

    # last traceability column has no trailing comma
    traceability = "\n".join(f"    {c}" + ("," if i < len(_TRACEABILITY_COLUMNS) - 1 else "") for i, c in enumerate(_TRACEABILITY_COLUMNS))

    unpacked_sql = (
        "{{ config(materialized='table', schema='bronze') }}\n\n"
        f"{{% set fields = var('{vars_key}') %}}\n\n"
        "with source as (\n\n"
        "    select *\n"
        f"    from {{{{ source('bronze', '{bronze_name}') }}}}\n\n"
        ")\n\n"
        "select\n\n"
        "    {% for f in fields %}\n"
        "    {{ clean_string(\"payload->>'\" ~ f.name ~ \"'\") }} as {{ f.name.lower() }},\n"
        "    {% endfor %}\n\n"
        "    -- traceability, passed through from bronze\n"
        f"{traceability}\n\n"
        "from source\n"
    )

    typed_sql = (
        "{{ config(materialized='table', schema='bronze') }}\n\n"
        f"{{% set fields = var('{vars_key}') %}}\n\n"
        "with unpacked as (\n\n"
        "    select *\n"
        f"    from {{{{ ref('01_unpacked_{bronze_name}') }}}}\n\n"
        ")\n\n"
        "select\n\n"
        "    {% for f in fields %}\n"
        "    {%- set col = f.name.lower() %}\n"
        "    {{ safe_cast(f.type, col, f.get('format')) }} as {{ col }},\n"
        "    {% endfor %}\n\n"
        "    -- cast issues raised on the fields above — every row still reaches this table,\n"
        "    -- diagnosed, never excluded\n"
        "    array_remove(array[\n"
        "        {% for f in fields %}\n"
        "        {%- set col = f.name.lower() %}\n"
        "        {{ cast_issue(col, f.name, f.pattern, f.required) }}{{ \",\" if not loop.last }}\n"
        "        {% endfor %}\n"
        "    ], null) as cast_issues,\n\n"
        "    -- traceability\n"
        f"{traceability}\n\n"
        "from unpacked\n"
    )

    return {"vars_key": vars_key, "vars_entries": vars_entries, "unpacked_sql": unpacked_sql, "typed_sql": typed_sql}


# ---------------------------------------------------------------------------
# Module 18 — no-code raffinage silver: 03 (standardization), 04 (annotation), 05 (routing),
# and the dq_flag_registry seed. Same "one stage, one question" discipline as 01/02, generic
# where the platform can be (05, the registry mechanism, every macro below) and per-dataset
# only where real business knowledge lives (which standardize op applies to which field, which
# quality-flag rules exist) — configured through the structuration UI, never hand-written SQL.
# ---------------------------------------------------------------------------

NORMALIZE_FOR_MATCHING_MACRO_SQL = """{% macro normalize_for_matching(col) -%}
btrim(regexp_replace(lower(translate({{ col }}, 'àâäáãåÀÂÄÁÃÅèéêëÈÉÊËìíîïÌÍÎÏòóôöõÒÓÔÖÕùúûüÙÚÛÜçÇñÑ', 'aaaaaaAAAAAAeeeeEEEEiiiiIIIIooooOOOOOuuuuUUUUcCnN')), '[^a-z0-9]+', ' ', 'g'))
{%- endmacro %}
"""

CLEAN_VAT_MACRO_SQL = """{% macro clean_vat(col) -%}
upper(regexp_replace({{ col }}, '[\\s.\\-]', '', 'g'))
{%- endmacro %}
"""

CLEAN_PHONE_MACRO_SQL = """{% macro clean_phone(col) -%}
concat(case when {{ col }} like '+%' then '+' else '' end, regexp_replace({{ col }}, '[^0-9]', '', 'g'))
{%- endmacro %}
"""

FORMAT_FLAG_MACRO_SQL = """{% macro format_flag(col, flag, regex) -%}
CASE WHEN {{ col }} IS NOT NULL AND {{ col }} !~ '{{ regex }}' THEN '{{ flag }}' END
{%- endmacro %}
"""

PLACEHOLDER_NAME_FLAG_MACRO_SQL = """{% macro placeholder_name_flag(col, flag) -%}
CASE WHEN lower(btrim({{ col }})) IN ('test', 'n/a', 'na', 'none', 'unknown', 'todo', 'tbd', 'xxx', 'xxxx', '-', '--', '???') THEN '{{ flag }}' END
{%- endmacro %}
"""

GARBAGE_FLAG_MACRO_SQL = r"""{% macro garbage_flag(col, flag) -%}
CASE WHEN {{ col }} IS NOT NULL AND {{ col }} ~ '^(.)\1+$' THEN '{{ flag }}' END
{%- endmacro %}
"""

DATE_RANGE_FLAG_MACRO_SQL = """{% macro date_range_flag(col, flag, min_date='1900-01-01', max_date=none) -%}
CASE WHEN {{ col }} IS NOT NULL AND ({{ col }} < DATE '{{ min_date }}' OR {{ col }} > {{ ("DATE '" ~ max_date ~ "'") if max_date else "current_date" }}) THEN '{{ flag }}' END
{%- endmacro %}
"""

UNMAPPED_BOOLEAN_FLAG_MACRO_SQL = """{% macro unmapped_boolean_flag(cast_issues_col, boolean_fields, flag) -%}
CASE WHEN {{ cast_issues_col }} && ARRAY[{% for f in boolean_fields %}'{{ f }}:invalid'{{ "," if not loop.last }}{% endfor %}]::text[] THEN '{{ flag }}' END
{%- endmacro %}
"""

STRUCTURATION_MACROS.update({
    "macros/normalize_for_matching.sql": NORMALIZE_FOR_MATCHING_MACRO_SQL,
    "macros/clean_vat.sql": CLEAN_VAT_MACRO_SQL,
    "macros/clean_phone.sql": CLEAN_PHONE_MACRO_SQL,
    "macros/format_flag.sql": FORMAT_FLAG_MACRO_SQL,
    "macros/placeholder_name_flag.sql": PLACEHOLDER_NAME_FLAG_MACRO_SQL,
    "macros/garbage_flag.sql": GARBAGE_FLAG_MACRO_SQL,
    "macros/date_range_flag.sql": DATE_RANGE_FLAG_MACRO_SQL,
    "macros/unmapped_boolean_flag.sql": UNMAPPED_BOOLEAN_FLAG_MACRO_SQL,
})

# Closed catalog (schemas.StandardizeOp) -> the macro/function call each op renders to. Only
# ever applied to text fields (validated below) — a passthrough (no key here, or None) leaves
# the field exactly as 02_typed produced it.
_STANDARDIZE_EXPR = {
    "upper": lambda col: f"upper({col})",
    "lower": lambda col: f"lower({col})",
    "title_case": lambda col: f"initcap({col})",
    # These four route through a Jinja macro (see STRUCTURATION_MACROS), so `col` must be
    # quoted as a Jinja string literal — an unquoted `{{ clean_string(fax) }}` makes Jinja
    # treat `fax` as an undefined *variable* (not the column name), which renders as an
    # empty string and produces invalid SQL like `btrim()` (matches _flag_expr's `f'"{field}"'`).
    "trim_collapse": lambda col: f'{{{{ clean_string("{col}") }}}}',
    "normalize_matching": lambda col: f'{{{{ normalize_for_matching("{col}") }}}}',
    "clean_vat": lambda col: f'{{{{ clean_vat("{col}") }}}}',
    "clean_phone": lambda col: f'{{{{ clean_phone("{col}") }}}}',
    "url_prefix": lambda col: f"(CASE WHEN {col} IS NOT NULL AND {col} !~* '^https?://' THEN 'https://' || {col} ELSE {col} END)",
}


def _jinja_arg(value: str) -> str:
    """A user-supplied string (a regex, a flag name) about to be passed as a macro-call
    argument, safe against BOTH layers it crosses: the macro substitutes it verbatim into a
    SQL '...' literal (so embedded single quotes are SQL-doubled first), then this whole call
    is itself Jinja source the dbt compiler parses (so it's wrapped in a double-quoted Jinja
    string literal, backslash-escaping \\ and " — never single-quote-delimited, precisely
    because the SQL-escaping step above just filled it with doubled single quotes)."""
    sql_escaped = value.replace("'", "''")
    jinja_escaped = sql_escaped.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + jinja_escaped + '"'


def _standardize_expr(op: str, col: str) -> str:
    builder = _STANDARDIZE_EXPR.get(op)
    if builder is None:
        raise PayloadStructureError(f"Opération de standardisation non supportée : « {op} ».")
    return builder(col)


def validate_standardize_ops(column_mapping: list[dict]) -> None:
    """A standardize op only ever makes sense on text — 02_typed already cast everything else
    to its real type, and calling e.g. upper() on an integer is a modeling mistake to reject
    at contract-save time, not a build-time surprise."""
    for field in column_mapping:
        op = field.get("standardize")
        if op and field.get("target_type") != "text":
            raise PayloadStructureError(
                f"« {field.get('target_name')} » : la standardisation ne s'applique qu'aux champs texte (type actuel : {field.get('target_type')})."
            )
        if op and op not in _STANDARDIZE_EXPR:
            raise PayloadStructureError(f"Opération de standardisation non supportée : « {op} ».")


def render_standardized_model(column_mapping: list[dict], bronze_name: str) -> str:
    """03 (§5) — the only question: what's this field's canonical form? Never touches
    validity (04) or type (02). Every column listed explicitly (Postgres has no "select *
    except this one"): a field with a standardize op gets that expression in place, every
    other field (and cast_issues, and the audit columns) passes through unchanged."""
    included = [f for f in column_mapping if f.get("include", True)]
    if not included:
        raise PayloadStructureError("Le contrat de structuration n'a aucun champ inclus.")
    validate_standardize_ops(included)

    cols = []
    for f in included:
        target = f["target_name"]
        op = f.get("standardize")
        cols.append(f'{_standardize_expr(op, target)} as "{target}"' if op else f'"{target}"')
    cols.append("cast_issues")
    cols.extend(_TRACEABILITY_COLUMNS)

    select_list = ",\n    ".join(cols)
    return (
        "{{ config(materialized='table', schema='bronze') }}\n\n"
        "select\n    " + select_list + "\n"
        f"from {{{{ ref('02_typed_{bronze_name}') }}}}\n"
    )


# ---------------------------------------------------------------------------
# UX ask — the "+" on a payload-backed bronze and "pick this bronze as a new silver's
# upstream" popups shouldn't just save config and leave the canvas empty until the engineer
# remembers to build + run the project's DAG. Every save immediately creates the *shape* of
# `silver.01_unpacked_<name>` and `silver.02_typed_<name>` — real tables, right columns/types/
# constraints, zero rows (materialize_unpacked_typed_sync, by compiling the same 01/02
# templates a dbt build would eventually run through a standalone Jinja pass instead of the dbt
# compiler, then executing the result directly against the warehouse with `WITH NO DATA`). The
# rows themselves land later, the normal way: render_silver_unpacked_typed_passthrough renders
# two thin dbt models (schema='silver', aliased to the exact same names) that dbt_run_silver —
# already a DAG task — builds for real once the engineer actually runs the project's DAG.
# ---------------------------------------------------------------------------

def _compile_stage_sql(body: str, *, var_entries: list[dict], ref_map: dict[str, str]) -> str:
    """Resolves a dbt model body's config()/var()/source()/ref() calls (plus our own
    clean_string/safe_cast/... macros) to plain, directly-executable Postgres SQL, entirely
    outside dbt. `ref_map` supplies the literal FROM text for every {{ ref(...) }} this
    particular body calls — a real, already-materialized table, or a derived subquery for a
    stage compiled inline instead of persisted on its own."""
    def _ref(model_name: str) -> str:
        if model_name not in ref_map:
            raise PayloadStructureError(f"Référence dbt non résolue pour l'exécution directe : « {model_name} ».")
        return ref_map[model_name]

    macros_src = "".join(STRUCTURATION_MACROS.values())
    template = jinja2.Environment().from_string(macros_src + "\n" + body)
    return template.render(
        config=lambda **_kwargs: "",
        var=lambda _key: var_entries,
        source=lambda schema_name, table_name: f'"{schema_name}"."{table_name}"',
        ref=_ref,
    ).strip()


def materialize_unpacked_typed_sync(warehouse: DataSource, column_mapping: list[dict], bronze_name: str) -> None:
    """Creates `silver.01_unpacked_<name>` and, reading that real (still empty) table,
    `silver.02_typed_<name>` — the right columns/types/constraints, zero rows (`WITH NO DATA`):
    a shape, not a copy of the data. Both DROP+CREATE (never incremental), same "always fresh,
    no accumulation" convention as file_import.py's _sync_bronze_mirror. dp_try_cast_* must
    exist before 02_typed's safe_cast calls can run (even against zero rows — it's still part
    of the compiled SELECT) — normally deployed once per project via dbt's on-run-start,
    (re)created here too since a first save can land before any dbt build ever has. The actual
    rows land later, via render_silver_unpacked_typed_passthrough + a real dbt build (§7 UX).

    Unlike the real 02_typed (bronze deliberately never excludes a row — cast_issues tags a
    problem instead), `silver.02_typed_<name>` is meant to be a directly-usable preview: the
    contract's own is_primary_key/nullable are applied as real PRIMARY KEY / NOT NULL
    constraints right after it's built — trivially satisfied here since the table is empty, but
    the same constraints are reapplied by the passthrough model's post-hook once real rows
    exist, where they matter and can actually fail (see that function)."""
    included = [f for f in column_mapping if f.get("include", True)]
    pk_columns = [f["target_name"] for f in included if f.get("is_primary_key")]
    # A primary key column is implicitly NOT NULL in Postgres regardless of what `nullable`
    # says — included here so the ALTER TABLE order below (NOT NULL, then the PK constraint)
    # never fails on that account alone.
    not_null_columns = sorted({f["target_name"] for f in included if not f.get("nullable", True)} | set(pk_columns))

    rendered = render_unpacked_typed_models(column_mapping, bronze_name)

    unpacked_select = _compile_stage_sql(rendered["unpacked_sql"], var_entries=rendered["vars_entries"], ref_map={})
    unpacked_table = f"01_unpacked_{bronze_name}"
    typed_select = _compile_stage_sql(
        rendered["typed_sql"], var_entries=rendered["vars_entries"],
        ref_map={f"01_unpacked_{bronze_name}": f'"silver"."{unpacked_table}"'},
    )
    typed_table = f"02_typed_{bronze_name}"

    conn = _connect_warehouse(warehouse)
    try:
        with conn.cursor() as cur:
            for statement in TRY_CAST_FUNCTIONS_SQL.strip().split("\n\n"):
                cur.execute(statement)
            cur.execute(pgsql.SQL("CREATE SCHEMA IF NOT EXISTS silver"))
            cur.execute(pgsql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(pgsql.Identifier("silver", unpacked_table)))
            cur.execute(pgsql.SQL("CREATE TABLE {} AS {} WITH NO DATA").format(pgsql.Identifier("silver", unpacked_table), pgsql.SQL(unpacked_select)))
            cur.execute(pgsql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(pgsql.Identifier("silver", typed_table)))
            cur.execute(pgsql.SQL("CREATE TABLE {} AS {} WITH NO DATA").format(pgsql.Identifier("silver", typed_table), pgsql.SQL(typed_select)))
            for col in not_null_columns:
                cur.execute(
                    pgsql.SQL("ALTER TABLE {} ALTER COLUMN {} SET NOT NULL")
                    .format(pgsql.Identifier("silver", typed_table), pgsql.Identifier(col))
                )
            if pk_columns:
                cur.execute(
                    pgsql.SQL("ALTER TABLE {} ADD CONSTRAINT {} PRIMARY KEY ({})")
                    .format(
                        pgsql.Identifier("silver", typed_table),
                        pgsql.Identifier(f"{typed_table}_pkey"),
                        pgsql.SQL(", ").join(pgsql.Identifier(c) for c in pk_columns),
                    )
                )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()


def render_silver_unpacked_typed_passthrough(column_mapping: list[dict], bronze_name: str) -> dict:
    """§7 UX — the two thin dbt models that actually fill `silver.01_unpacked_<name>`/
    `silver.02_typed_<name>` with real rows: a plain `select *` from the real bronze
    `01_unpacked_<name>`/`02_typed_<name>` models, aliased so the built table lands under the
    exact same name materialize_unpacked_typed_sync already used for the empty shell. Rendered
    into `models/silver/` (dbt_project.py) so dbt_run_silver — already a DAG task — builds them
    on every run, no new task needed; `ref()` gives dbt the correct build order automatically
    (bronze's 01/02 before these). A `materialized='table'` build is a fresh CREATE each time,
    so 02's PRIMARY KEY/NOT NULL constraints (materialize_unpacked_typed_sync's docstring) are
    reapplied here via post_hook — this is the version where they can actually fail, if real
    data doesn't honor what the contract declares."""
    included = [f for f in column_mapping if f.get("include", True)]
    pk_columns = [f["target_name"] for f in included if f.get("is_primary_key")]
    not_null_columns = sorted({f["target_name"] for f in included if not f.get("nullable", True)} | set(pk_columns))

    unpacked_alias = f"01_unpacked_{bronze_name}"
    typed_alias = f"02_typed_{bronze_name}"
    typed_qualified = f'"silver"."{typed_alias}"'

    post_hooks = [f'ALTER TABLE {typed_qualified} ALTER COLUMN "{c}" SET NOT NULL' for c in not_null_columns]
    if pk_columns:
        pk_cols_sql = ", ".join(f'"{c}"' for c in pk_columns)
        post_hooks.append(f'ALTER TABLE {typed_qualified} ADD CONSTRAINT "{typed_alias}_pkey" PRIMARY KEY ({pk_cols_sql})')

    unpacked_sql = (
        f"{{{{ config(materialized='table', schema='silver', alias='{unpacked_alias}') }}}}\n\n"
        f"select * from {{{{ ref('01_unpacked_{bronze_name}') }}}}\n"
    )
    typed_config = f"materialized='table', schema='silver', alias='{typed_alias}'"
    if post_hooks:
        post_hook_literal = "[" + ", ".join(f"'{h}'" for h in post_hooks) + "]"
        typed_config += f", post_hook={post_hook_literal}"
    typed_sql = (
        f"{{{{ config({typed_config}) }}}}\n\n"
        f"select * from {{{{ ref('02_typed_{bronze_name}') }}}}\n"
    )
    return {"unpacked_sql": unpacked_sql, "typed_sql": typed_sql}


_FLAG_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_quality_flags(quality_flags: list[dict], column_mapping: list[dict]) -> None:
    """§6/§8 — a flag name is a literal tag matched verbatim by 05's routing (never a SQL
    identifier), but it still can't contain ':' — that's the delimiter cast_issue tags use
    ('FIELD:absent'/'FIELD:invalid'), and 05 treats *any* ':'-bearing flag as bloquant by
    construction (§8): a user-chosen name containing one would silently misroute regardless
    of its declared category."""
    target_names = {f["target_name"] for f in column_mapping}
    seen: set[str] = set()
    for rule in quality_flags:
        name = rule.get("name", "")
        if not _FLAG_NAME_RE.match(name):
            raise PayloadStructureError(f"Nom de flag invalide : « {name} » (minuscules, chiffres, underscore, ne commence pas par un chiffre).")
        if ":" in name:
            raise PayloadStructureError(f"Nom de flag invalide : « {name} » — ':' est réservé aux tags de cast.")
        if name in seen:
            raise PayloadStructureError(f"Flag en double : « {name} ».")
        seen.add(name)
        if rule.get("field") not in target_names:
            raise PayloadStructureError(f"Le flag « {name} » référence un champ inconnu : « {rule.get('field')} ».")
        rule_type = rule.get("rule_type")
        if rule_type == "format" and not rule.get("regex"):
            raise PayloadStructureError(f"Le flag « {name} » (format) requiert une expression régulière.")
        if rule_type not in ("format", "placeholder", "garbage", "date_range"):
            raise PayloadStructureError(f"Type de règle qualité non supporté : « {rule_type} ».")


def _flag_expr(rule: dict) -> str:
    field = rule["field"]
    name = rule["name"]
    rule_type = rule["rule_type"]
    col = f'"{field}"'
    if rule_type == "format":
        return f"{{{{ format_flag({col}, {_jinja_arg(name)}, {_jinja_arg(rule['regex'])}) }}}}"
    if rule_type == "placeholder":
        return f"{{{{ placeholder_name_flag({col}, {_jinja_arg(name)}) }}}}"
    if rule_type == "garbage":
        return f"{{{{ garbage_flag({col}, {_jinja_arg(name)}) }}}}"
    if rule_type == "date_range":
        args = [col, _jinja_arg(name)]
        if rule.get("min_date"):
            args.append(f"min_date={_jinja_arg(rule['min_date'])}")
        if rule.get("max_date"):
            args.append(f"max_date={_jinja_arg(rule['max_date'])}")
        return f"{{{{ date_range_flag({', '.join(args)}) }}}}"
    raise PayloadStructureError(f"Type de règle qualité non supporté : « {rule_type} ».")


def render_annotated_model(quality_flags: list[dict], bronze_name: str) -> str:
    """04 (§6) — the only question: what problems does this row carry? Stacks every flag,
    informative and blocking alike, into one `data_quality_flags` array — 04 has no authority
    over which are blocking (that's 05, via the registry). `cast_issues` (from 02) is prefixed,
    never replaced, never lost."""
    flag_exprs = [_flag_expr(rule) for rule in quality_flags]
    flags_array = ("array_remove(array[\n        " + ",\n        ".join(flag_exprs) + "\n    ], null)") if flag_exprs else "'{}'::text[]"
    return (
        "{{ config(materialized='table', schema='bronze') }}\n\n"
        "select *,\n"
        f"    cast_issues || {flags_array} as data_quality_flags\n"
        f"from {{{{ ref('03_standardized_{bronze_name}') }}}}\n"
    )


def render_validated_quarantine_models(bronze_name: str) -> dict:
    """05 (§7) — partitions 04_annotated into two complementary, disjoint views, with zero
    routing logic hard-coded here: a row is quarantined iff it carries a cast tag ('%:%') or a
    flag absent from dq_flag_registry's 'informative' rows (§8's "undeclared = blocking, safe
    default"). validated is the exact mirror (`not exists` of the same condition) — never a
    second, independently-written query that could drift out of sync."""
    condition = (
        "exists (\n"
        "        select 1\n"
        "        from unnest(t.data_quality_flags) as flag\n"
        "        where flag like '%:%'\n"
        "           or flag not in (select flag_name from {{ ref('dq_flag_registry') }} where category = 'informative')\n"
        "    )"
    )
    ref = f"{{{{ ref('04_annotated_{bronze_name}') }}}}"
    quarantine_sql = (
        "{{ config(materialized='view', schema='bronze') }}\n\n"
        f"select t.*\nfrom {ref} t\nwhere {condition}\n"
    )
    validated_sql = (
        "{{ config(materialized='view', schema='bronze') }}\n\n"
        f"select t.*\nfrom {ref} t\nwhere not {condition}\n"
    )
    return {"validated_sql": validated_sql, "quarantine_sql": quarantine_sql}


_ISSUE_TYPE_BY_RULE = {
    "format": "FORMAT_MISMATCH",
    "placeholder": "PROXY_VALUE",
    "garbage": "SUSPECT_VALUE",
    "date_range": "SUSPECT_VALUE",
}


def render_registry_seed(all_quality_flags: list[dict]) -> str:
    """§8 — seeds/dq_flag_registry.csv, the sole source of truth 05's routing reads (only
    `category` — `issue_type` is descriptive-only, per §8). One row per unique flag_name
    project-wide (§8: "quelle que soit la table"); a name repeated across datasets keeps its
    first definition — a naming collision is the engineer's mistake to avoid, not something to
    silently resolve differently per caller."""
    import csv
    import io

    seen: set[str] = set()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["flag_name", "category", "source_rule", "issue_type"])
    for rule in all_quality_flags:
        name = rule["name"]
        if name in seen:
            continue
        seen.add(name)
        writer.writerow([name, rule.get("category", "elimination"), rule.get("source_rule") or "", _ISSUE_TYPE_BY_RULE.get(rule["rule_type"], "")])
    return buf.getvalue()


def render_reconciliation_test(bronze_name: str) -> str:
    """§7.4 — a livrable, not an option: fails the build if 05_validated + 05_quarantine's row
    count ever drifts from 04_annotated's. (Disjointness itself needs no separate check: the
    two views are exact boolean complements of the same `exists(...)` predicate over the same
    `t` — a row structurally cannot satisfy both at once.)"""
    return (
        "with counts as (\n"
        f"    select (select count(*) from {{{{ ref('04_annotated_{bronze_name}') }}}}) as annotated_n,\n"
        f"           (select count(*) from {{{{ ref('05_validated_{bronze_name}') }}}}) as validated_n,\n"
        f"           (select count(*) from {{{{ ref('05_quarantine_{bronze_name}') }}}}) as quarantine_n\n"
        ")\n"
        "select * from counts where annotated_n != validated_n + quarantine_n\n"
    )


# ---------------------------------------------------------------------------
# Étape 4 — anomaly inspection. Module 18 §7.5/§10 — reused, not recreated: this now reads
# `05_quarantine_<name>` (every row already routed as bloquant, whether by a cast tag or a
# quality flag), not the old `02_typed_<name>`-with-cast_issues interim surface. Every row here
# IS quarantined by construction (05's own WHERE clause), so no extra filter is needed. No
# control-plane copy of the data ever exists, same relay-not-store principle as the M10
# preview / M11 export.
# ---------------------------------------------------------------------------

def _quarantine_view_exists(conn, bronze_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'bronze' AND table_name = %s",
            (f"05_quarantine_{bronze_name}",),
        )
        return cur.fetchone() is not None


def list_quarantine(warehouse: DataSource, bronze_name: str, column: str | None, limit: int, offset: int) -> list[dict]:
    if not IDENTIFIER_RE.match(bronze_name):
        raise PayloadStructureError("Nom de dataset invalide.")
    table = f"05_quarantine_{bronze_name}"
    conn = _connect_warehouse(warehouse)
    try:
        if not _quarantine_view_exists(conn, bronze_name):
            return []
        with conn.cursor() as cur:
            if column:
                cur.execute(
                    pgsql.SQL(
                        "SELECT row_number, source_file, data_quality_flags FROM bronze.{} "
                        "WHERE EXISTS (SELECT 1 FROM unnest(data_quality_flags) i WHERE split_part(i, ':', 1) = %s) "
                        "ORDER BY row_number LIMIT %s OFFSET %s"
                    ).format(pgsql.Identifier(table)),
                    (column, limit, offset),
                )
            else:
                cur.execute(
                    pgsql.SQL("SELECT row_number, source_file, data_quality_flags FROM bronze.{} ORDER BY row_number LIMIT %s OFFSET %s")
                    .format(pgsql.Identifier(table)),
                    (limit, offset),
                )
            rows = cur.fetchall()
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()
    return [{"row_number": r[0], "source_file": r[1], "issues": r[2] or []} for r in rows]


def quarantine_summary(warehouse: DataSource, bronze_name: str) -> dict:
    """§6.2 — per-column issue counts, to point the engineer at *which* field to fix first.
    A bucket is either a field name (from a 'FIELD:absent'/'FIELD:invalid' cast tag) or a
    quality-flag name itself (flag names are validated ':'-free, §6/§8) — split_part on a
    colon-less string is a no-op, so both group correctly, just under one shared axis."""
    if not IDENTIFIER_RE.match(bronze_name):
        raise PayloadStructureError("Nom de dataset invalide.")
    table = f"05_quarantine_{bronze_name}"
    conn = _connect_warehouse(warehouse)
    try:
        if not _quarantine_view_exists(conn, bronze_name):
            return {"total": 0, "by_column": []}
        with conn.cursor() as cur:
            cur.execute(pgsql.SQL("SELECT count(*) FROM bronze.{}").format(pgsql.Identifier(table)))
            total = cur.fetchone()[0]
            cur.execute(
                pgsql.SQL(
                    "SELECT split_part(i, ':', 1) AS col, count(*) "
                    "FROM bronze.{} t, unnest(t.data_quality_flags) i "
                    "GROUP BY col ORDER BY count(*) DESC"
                ).format(pgsql.Identifier(table))
            )
            by_column = [{"column": r[0], "count": r[1], "sample_values": []} for r in cur.fetchall()]
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()
    return {"total": total, "by_column": by_column}


def canonical_contract(column_mapping: list[dict], quality_flags: list[dict] | None = None) -> str:
    minimal = {
        "fields": [
            {"source_name": c["source_name"], "target_name": c["target_name"], "target_type": c["target_type"],
             "format": c.get("format"), "include": c.get("include", True), "nullable": c.get("nullable", True),
             "standardize": c.get("standardize")}
            for c in column_mapping
        ],
        # Module 18 — a rule change (regex, category, bounds) alters 04/05's rendered SQL just
        # as much as a field edit alters 01/02/03's, so it belongs in the same hash: this is
        # what has_pending_changes/redeploy-staleness tracking keys off.
        "quality_flags": [
            {"name": f["name"], "field": f["field"], "rule_type": f["rule_type"], "category": f.get("category", "elimination"),
             "regex": f.get("regex"), "min_date": f.get("min_date"), "max_date": f.get("max_date")}
            for f in (quality_flags or [])
        ],
    }
    return hashlib.sha256(json.dumps(minimal, sort_keys=True).encode()).hexdigest()
