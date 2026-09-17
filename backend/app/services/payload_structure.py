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

import psycopg
from psycopg import sql as pgsql
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.models.data_source import DataSource, DataSourceType
from app.models.file_import import FileImport, FileImportStatus, ImportMode
from app.models.medallion import MedallionDataset, MedallionLayer
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
# Étape 4 — anomaly inspection: no separate quarantine relation anymore (every row from bronze
# reaches `02_typed_<name>`, diagnosed, never excluded) — this reads the rows that carry at
# least one cast issue straight from that table. No control-plane copy of the data ever
# exists, same relay-not-store principle as the M10 preview / M11 export.
# ---------------------------------------------------------------------------

def _typed_table_exists(conn, bronze_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'bronze' AND table_name = %s",
            (f"02_typed_{bronze_name}",),
        )
        return cur.fetchone() is not None


def list_quarantine(warehouse: DataSource, bronze_name: str, column: str | None, limit: int, offset: int) -> list[dict]:
    if not IDENTIFIER_RE.match(bronze_name):
        raise PayloadStructureError("Nom de dataset invalide.")
    table = f"02_typed_{bronze_name}"
    conn = _connect_warehouse(warehouse)
    try:
        if not _typed_table_exists(conn, bronze_name):
            return []
        with conn.cursor() as cur:
            if column:
                cur.execute(
                    pgsql.SQL(
                        "SELECT row_number, source_file, cast_issues FROM bronze.{} "
                        "WHERE EXISTS (SELECT 1 FROM unnest(cast_issues) i WHERE split_part(i, ':', 1) = %s) "
                        "ORDER BY row_number LIMIT %s OFFSET %s"
                    ).format(pgsql.Identifier(table)),
                    (column, limit, offset),
                )
            else:
                cur.execute(
                    pgsql.SQL(
                        "SELECT row_number, source_file, cast_issues FROM bronze.{} "
                        "WHERE cardinality(cast_issues) > 0 ORDER BY row_number LIMIT %s OFFSET %s"
                    ).format(pgsql.Identifier(table)),
                    (limit, offset),
                )
            rows = cur.fetchall()
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()
    return [{"row_number": r[0], "source_file": r[1], "issues": r[2] or []} for r in rows]


def quarantine_summary(warehouse: DataSource, bronze_name: str) -> dict:
    """§6.2 — per-column issue counts, to point the engineer at *which* field to fix first."""
    if not IDENTIFIER_RE.match(bronze_name):
        raise PayloadStructureError("Nom de dataset invalide.")
    table = f"02_typed_{bronze_name}"
    conn = _connect_warehouse(warehouse)
    try:
        if not _typed_table_exists(conn, bronze_name):
            return {"total": 0, "by_column": []}
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL("SELECT count(*) FROM bronze.{} WHERE cardinality(cast_issues) > 0").format(pgsql.Identifier(table))
            )
            total = cur.fetchone()[0]
            cur.execute(
                pgsql.SQL(
                    "SELECT split_part(i, ':', 1) AS col, count(*) "
                    "FROM bronze.{} t, unnest(t.cast_issues) i "
                    "GROUP BY col ORDER BY count(*) DESC"
                ).format(pgsql.Identifier(table))
            )
            by_column = [{"column": r[0], "count": r[1], "sample_values": []} for r in cur.fetchall()]
    except Exception as exc:
        raise PayloadStructureError(connections.clean_error(exc)) from exc
    finally:
        conn.close()
    return {"total": total, "by_column": by_column}


def canonical_contract(column_mapping: list[dict]) -> str:
    minimal = {
        "fields": [
            {"source_name": c["source_name"], "target_name": c["target_name"], "target_type": c["target_type"],
             "format": c.get("format"), "include": c.get("include", True), "nullable": c.get("nullable", True)}
            for c in column_mapping
        ],
    }
    return hashlib.sha256(json.dumps(minimal, sort_keys=True).encode()).hexdigest()
