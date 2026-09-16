import csv
import hashlib
import io
import json
import logging
import re
from datetime import datetime, timezone

import pandas as pd
import psycopg
from lxml import etree
from psycopg import sql

from app.core.security import decrypt_secret
from app.db.session import SessionLocal
from app.models.data_source import DataSource, DataSourceType
from app.models.file_import import FileImport, FileImportStatus, FileImportWriteMode, ImportMode
from app.services import connections, schema_infer

logger = logging.getLogger("app.file_import")

CHUNK_SIZE = 5000
EXCEL_MAX_BYTES = 50 * 1024 * 1024
JSON_ARRAY_MAX_BYTES = 50 * 1024 * 1024
XML_CANDIDATE_SAMPLE_ELEMENTS = 200

IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
ALLOWED_TYPES = {
    "text": "TEXT", "integer": "INTEGER", "bigint": "BIGINT", "numeric": "NUMERIC",
    "boolean": "BOOLEAN", "date": "DATE", "timestamp": "TIMESTAMP", "jsonb": "JSONB",
}


class FileImportError(Exception):
    pass


def _validate_identifier(name: str, what: str) -> str:
    if not name or not IDENTIFIER_RE.match(name):
        raise FileImportError(f"{what} invalide : « {name} » — attendu : lettres minuscules, chiffres, underscore, ne commence pas par un chiffre.")
    return name


def _validate_type(type_name: str) -> str:
    if type_name not in ALLOWED_TYPES:
        raise FileImportError(f"Type cible non supporté : « {type_name} ». Types autorisés : {', '.join(sorted(ALLOWED_TYPES))}.")
    return type_name


def detect_encoding(file_bytes: bytes) -> str:
    if file_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        file_bytes[:200_000].decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(file_bytes[:200_000]).best()
        if best is not None:
            return best.encoding
    except Exception:
        pass
    return "cp1252"


def compute_checksum(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def derive_table_name(name: str) -> str:
    """Payload mode has no mapping step to ask the user for a target table name (§3.5) — it's
    derived from the import's own name/filename, through the same slugifier column names go
    through (lowercase, transliterated, reserved-word-safe)."""
    return schema_infer.normalize_column_name(name, set())


def canonical_contract(column_mapping: list[dict]) -> str:
    minimal = [
        {"source_name": c["source_name"], "target_name": c["target_name"], "target_type": c["target_type"], "include": c.get("include", True)}
        for c in column_mapping
    ]
    return hashlib.sha256(json.dumps(minimal, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Archive (MinIO) — always first, before any parsing.
# ---------------------------------------------------------------------------

def archive_raw(archive_source: DataSource, import_id: int, file_name: str, file_bytes: bytes) -> str:
    secret = decrypt_secret(archive_source.secret_encrypted)
    client = connections._minio_client(archive_source.host, archive_source.port, archive_source.username, secret, archive_source.options)
    bucket = (archive_source.options or {}).get("bucket") or "dataplateforme-bronze"
    if not client.bucket_exists(bucket):
        try:
            client.make_bucket(bucket)
        except Exception as exc:
            raise RuntimeError(
                f"Le bucket '{bucket}' n'existe pas et ces identifiants ne sont pas autorisés à le créer "
                f"(s3:CreateBucket refusé). Créez ce bucket manuellement, ou renseignez le nom d'un bucket "
                f"existant dans les options de la source objet, puis réessayez. Erreur d'origine : {exc}"
            ) from exc
    object_name = f"imports/{import_id}/{file_name}"
    client.put_object(bucket, object_name, io.BytesIO(file_bytes), length=len(file_bytes))
    return f"{bucket}/{object_name}"


def fetch_archived(archive_source: DataSource, archive_path: str) -> bytes:
    secret = decrypt_secret(archive_source.secret_encrypted)
    client = connections._minio_client(archive_source.host, archive_source.port, archive_source.username, secret, archive_source.options)
    bucket, object_name = archive_path.split("/", 1)
    response = client.get_object(bucket, object_name)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


# ---------------------------------------------------------------------------
# Reading — CSV / Excel
# ---------------------------------------------------------------------------

def _csv_read_kwargs(file_bytes: bytes, format_options: dict) -> dict:
    encoding = format_options.get("encoding") or detect_encoding(file_bytes)
    delimiter = format_options.get("delimiter")
    if not delimiter:
        sample = file_bytes[:8192].decode(encoding, errors="replace")
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    return {
        "encoding": encoding,
        "sep": delimiter,
        "header": format_options.get("header_row", 0),
        "skiprows": format_options.get("skiprows") or None,
        "quotechar": format_options.get("quotechar", '"'),
        "na_values": format_options.get("na_values") or [],
        "keep_default_na": False,
        "dtype": str,
    }


# ---------------------------------------------------------------------------
# Reading — JSON (NDJSON streamable, or a fully-in-memory array like Excel)
# ---------------------------------------------------------------------------

def detect_json_mode(file_bytes: bytes) -> str:
    """'ndjson' (one object per line) or 'array' ([{...}, {...}] / a single {...})."""
    text = file_bytes.decode("utf-8", errors="replace").strip()
    if not text:
        return "array"
    try:
        json.loads(text)
        return "array"  # a single valid JSON document — array or bare object, either way not NDJSON
    except json.JSONDecodeError:
        pass
    lines = [l for l in text.splitlines() if l.strip()]
    try:
        for l in lines[:50]:
            json.loads(l)
        if lines:
            return "ndjson"
    except json.JSONDecodeError:
        pass
    return "array"


def _resolve_root_path(obj, root_path: str | None):
    if not root_path:
        return obj
    for key in root_path.split("."):
        obj = obj[key]
    return obj


def _read_json_array(file_bytes: bytes, format_options: dict) -> list[dict]:
    if len(file_bytes) > JSON_ARRAY_MAX_BYTES:
        raise FileImportError("Fichier JSON (tableau) trop volumineux (> 50 Mo) — utilisez un NDJSON ou un CSV.")
    obj = json.loads(file_bytes.decode("utf-8"))
    obj = _resolve_root_path(obj, format_options.get("root_path"))
    if isinstance(obj, dict):
        obj = [obj]
    return obj


def _iter_ndjson(file_bytes: bytes):
    for line in io.BytesIO(file_bytes):
        line = line.strip()
        if not line:
            continue
        yield json.loads(line)


# ---------------------------------------------------------------------------
# Reading — XML (iterative, never loads the full document)
# ---------------------------------------------------------------------------

def _xml_element_to_dict(elem) -> dict:
    d = {}
    for k, v in elem.attrib.items():
        d[f"@{k}"] = v
    children = list(elem)
    if not children:
        text = (elem.text or "").strip()
        if text:
            d["#text"] = text
    else:
        for child in children:
            tag = etree.QName(child).localname
            child_dict = _xml_element_to_dict(child)
            if tag in d:
                if not isinstance(d[tag], list):
                    d[tag] = [d[tag]]
                d[tag].append(child_dict)
            else:
                d[tag] = child_dict
    return d


def _record_xpath_tag(record_xpath: str, namespaces: dict | None) -> str:
    """Only the common '//tagname' (or '/a/b/tagname') shape is supported — iterparse
    matches by tag as records stream in, not a full XPath engine mid-parse."""
    tag = record_xpath.strip().rstrip("/").split("/")[-1]
    if ":" in tag and namespaces:
        prefix, local = tag.split(":", 1)
        uri = namespaces.get(prefix)
        if uri:
            return f"{{{uri}}}{local}"
    return tag


def _iter_xml_records(file_bytes: bytes, record_xpath: str, namespaces: dict | None):
    if not record_xpath:
        raise FileImportError("record_xpath est obligatoire pour un import XML.")
    tag = _record_xpath_tag(record_xpath, namespaces)
    context = etree.iterparse(io.BytesIO(file_bytes), events=("end",), tag=tag)
    for _event, elem in context:
        yield _xml_element_to_dict(elem)
        # release memory as we go — never hold the whole document in the tree
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    del context


def suggest_record_xpaths(file_bytes: bytes, max_elements: int = XML_CANDIDATE_SAMPLE_ELEMENTS) -> list[dict]:
    """Candidate record tags from the first N elements, with occurrence counts — lets the
    user pick from a list instead of typing an XPath blind. When several tags repeat the
    same number of times (e.g. a record and each of its leaf fields), the one that actually
    has child elements is the more likely "record" container, so it's ranked first."""
    counts: dict[str, int] = {}
    has_children: dict[str, bool] = {}
    context = etree.iterparse(io.BytesIO(file_bytes), events=("end",))
    seen = 0
    for _event, elem in context:
        tag = etree.QName(elem).localname
        counts[tag] = counts.get(tag, 0) + 1
        has_children[tag] = has_children.get(tag, False) or len(elem) > 0
        seen += 1
        if seen >= max_elements:
            break
    del context
    candidates = [{"xpath": f"//{tag}", "count": c} for tag, c in counts.items() if c > 1]
    candidates.sort(key=lambda x: (-x["count"], not has_children[x["xpath"][2:]]))
    return candidates


# ---------------------------------------------------------------------------
# Format-agnostic entry points used by the API layer
# ---------------------------------------------------------------------------

def read_columns_and_sample(format_: str, file_bytes: bytes, format_options: dict) -> tuple[list[str], list[dict]]:
    """CSV/Excel only — returns (source_columns, sample_rows) of raw strings. JSON/XML use
    infer_column_mapping() directly since their shape (flat vs nested) decides the whole
    column_mapping, not just per-column types."""
    if format_ == "csv":
        kwargs = _csv_read_kwargs(file_bytes, format_options)
        df = pd.read_csv(io.BytesIO(file_bytes), nrows=schema_infer.SAMPLE_SIZE, **kwargs)
    elif format_ == "excel":
        if len(file_bytes) > EXCEL_MAX_BYTES:
            raise FileImportError("Fichier Excel trop volumineux (> 50 Mo) — exportez-le en CSV.")
        df = pd.read_excel(
            io.BytesIO(file_bytes), sheet_name=format_options.get("sheet", 0),
            header=format_options.get("header_row", 0), skiprows=format_options.get("skiprows") or None,
            dtype=str, nrows=schema_infer.SAMPLE_SIZE,
        )
    else:
        raise FileImportError(f"Format non pris en charge ici : {format_}")

    df = df.where(pd.notnull(df), None)
    columns = [str(c) for c in df.columns]
    rows = df.to_dict("records")
    return columns, rows


def infer_column_mapping(format_: str, file_bytes: bytes, format_options: dict) -> list[dict]:
    """The one function the API layer calls to go from an uploaded file to a proposed
    column_mapping, for every format."""
    if format_ in ("csv", "excel"):
        columns, rows = read_columns_and_sample(format_, file_bytes, format_options)
        return schema_infer.infer_schema(rows, columns)

    if format_ == "json":
        mode = format_options.get("mode") or detect_json_mode(file_bytes)
        if mode == "ndjson":
            rows = []
            for i, rec in enumerate(_iter_ndjson(file_bytes)):
                if i >= schema_infer.SAMPLE_SIZE:
                    break
                rows.append(rec)
        else:
            rows = _read_json_array(file_bytes, format_options)[: schema_infer.SAMPLE_SIZE]
        if not rows:
            raise FileImportError("Aucun enregistrement JSON trouvé.")
        if schema_infer.is_flat_records(rows):
            columns = list(rows[0].keys())
            return schema_infer.infer_schema(rows, columns)
        return schema_infer.infer_nested_schema(rows)

    if format_ == "xml":
        record_xpath = format_options.get("record_xpath")
        namespaces = format_options.get("namespaces")
        rows = []
        for i, rec in enumerate(_iter_xml_records(file_bytes, record_xpath, namespaces)):
            if i >= schema_infer.SAMPLE_SIZE:
                break
            rows.append(rec)
        if not rows:
            raise FileImportError(f"Aucun enregistrement trouvé pour « {record_xpath} ».")
        if schema_infer.is_flat_records(rows):
            columns = list(rows[0].keys())
            return schema_infer.infer_schema(rows, columns)
        return schema_infer.infer_nested_schema(rows)

    raise FileImportError(f"Format non pris en charge : {format_}")


def _iter_chunks(format_: str, file_bytes: bytes, format_options: dict):
    """Yields list[dict] chunks of raw values (strings for CSV/Excel, native JSON/XML
    scalars/dicts otherwise), keyed by source column name."""
    if format_ == "csv":
        kwargs = _csv_read_kwargs(file_bytes, format_options)
        reader = pd.read_csv(io.BytesIO(file_bytes), chunksize=CHUNK_SIZE, **kwargs)
        for chunk in reader:
            chunk = chunk.where(pd.notnull(chunk), None)
            yield chunk.to_dict("records")
    elif format_ == "excel":
        # openpyxl loads the whole workbook in memory regardless of chunking below — the same
        # hard 50 Mo limit as read_columns_and_sample (§3.4 3.bis), which the payload path
        # never calls (it skips inference entirely), so it must be re-asserted here too.
        if len(file_bytes) > EXCEL_MAX_BYTES:
            raise FileImportError("Fichier Excel trop volumineux (> 50 Mo) — exportez-le en CSV.")
        df = pd.read_excel(
            io.BytesIO(file_bytes), sheet_name=format_options.get("sheet", 0),
            header=format_options.get("header_row", 0), skiprows=format_options.get("skiprows") or None,
            dtype=str,
        )
        df = df.where(pd.notnull(df), None)
        for start in range(0, len(df), CHUNK_SIZE):
            yield df.iloc[start:start + CHUNK_SIZE].to_dict("records")
    elif format_ == "json":
        mode = format_options.get("mode") or detect_json_mode(file_bytes)
        if mode == "ndjson":
            buffer = []
            for rec in _iter_ndjson(file_bytes):
                buffer.append(rec)
                if len(buffer) >= CHUNK_SIZE:
                    yield buffer
                    buffer = []
            if buffer:
                yield buffer
        else:
            records = _read_json_array(file_bytes, format_options)
            for start in range(0, len(records), CHUNK_SIZE):
                yield records[start:start + CHUNK_SIZE]
    elif format_ == "xml":
        record_xpath = format_options.get("record_xpath")
        namespaces = format_options.get("namespaces")
        buffer = []
        for rec in _iter_xml_records(file_bytes, record_xpath, namespaces):
            buffer.append(rec)
            if len(buffer) >= CHUNK_SIZE:
                yield buffer
                buffer = []
        if buffer:
            yield buffer
    else:
        raise FileImportError(f"Format non pris en charge à cette étape : {format_}")


# ---------------------------------------------------------------------------
# Casting a raw cell to its validated target type
# ---------------------------------------------------------------------------

def cast_value(raw, target_type: str, date_format: str | None):
    """Returns (value, ok). ok=False means the cell could not be cast — caller writes
    NULL and increments a cast-error counter; a bad cell never aborts the import."""
    if target_type == "jsonb":
        # a nested JSON/XML record arrives as an actual dict/list, not text to re-parse
        if isinstance(raw, (dict, list)):
            return json.dumps(raw), True
        if raw is None or str(raw).strip() == "":
            return None, True
        try:
            return json.dumps(json.loads(str(raw).strip())), True
        except Exception:
            return None, False

    if raw is None or str(raw).strip() == "":
        return None, True
    text = str(raw).strip()
    try:
        if target_type == "text":
            return text, True
        if target_type in ("integer", "bigint"):
            n = schema_infer._parse_number(text)
            if n is None or n != n.to_integral_value():
                return None, False
            return int(n), True
        if target_type == "numeric":
            n = schema_infer._parse_number(text)
            return (n, True) if n is not None else (None, False)
        if target_type == "boolean":
            low = text.lower()
            if low in schema_infer._BOOL_TRUE:
                return True, True
            if low in schema_infer._BOOL_FALSE:
                return False, True
            return None, False
        if target_type in ("date", "timestamp"):
            fmt = date_format or ("%d/%m/%Y" if target_type == "date" else "%d/%m/%Y %H:%M:%S")
            dt = datetime.strptime(text, fmt)
            return (dt.date() if target_type == "date" else dt), True
    except Exception:
        return None, False
    return None, False


# ---------------------------------------------------------------------------
# Warehouse write — DDL (identifiers validated + quoted, never f-string) + chunked insert
# ---------------------------------------------------------------------------

def _pg_connect(target: DataSource) -> "psycopg.Connection":
    if target.type != DataSourceType.postgresql:
        raise FileImportError("La cible d'un import doit être une source PostgreSQL.")
    secret = decrypt_secret(target.secret_encrypted)
    return psycopg.connect(host=target.host, port=target.port, dbname=target.database_name, user=target.username, password=secret, connect_timeout=10)


def _table_exists(cur, schema_name: str, table_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        (schema_name, table_name),
    )
    return cur.fetchone() is not None


def _prepare_table(cur, schema_name: str, table_name: str, included_columns: list[dict], write_mode: FileImportWriteMode) -> None:
    schema_ident = sql.Identifier(_validate_identifier(schema_name, "Schéma cible"))
    table_ident = sql.Identifier(_validate_identifier(table_name, "Table cible"))

    cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema_ident))

    exists = _table_exists(cur, schema_name, table_name)
    if write_mode == FileImportWriteMode.create and exists:
        raise FileImportError(f"La table {schema_name}.{table_name} existe déjà (mode « create »).")
    if write_mode == FileImportWriteMode.append and not exists:
        raise FileImportError(f"La table {schema_name}.{table_name} n'existe pas (mode « append »).")

    if write_mode == FileImportWriteMode.replace and exists:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(schema_ident, table_ident))
        exists = False

    if not exists:
        col_defs = [
            sql.SQL("{} {}").format(
                sql.Identifier(_validate_identifier(c["target_name"], "Nom de colonne")),
                sql.SQL(ALLOWED_TYPES[_validate_type(c["target_type"])]),
            )
            for c in included_columns
        ]
        cur.execute(sql.SQL("CREATE TABLE {}.{} ({})").format(schema_ident, table_ident, sql.SQL(", ").join(col_defs)))


def _insert_chunk(cur, schema_name: str, table_name: str, included_columns: list[dict], records: list[dict], cast_errors: dict) -> int:
    schema_ident = sql.Identifier(schema_name)
    table_ident = sql.Identifier(table_name)
    col_idents = sql.SQL(", ").join(sql.Identifier(c["target_name"]) for c in included_columns)
    placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in included_columns)
    stmt = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(schema_ident, table_ident, col_idents, placeholders)

    rows_to_insert = []
    for record in records:
        row_values = []
        for c in included_columns:
            # "__root__" is the sentinel for a nested record's whole-payload column —
            # the record itself is the value, not a lookup within it
            raw = record if c["source_name"] == "__root__" else record.get(c["source_name"])
            value, ok = cast_value(raw, c["target_type"], c.get("format"))
            if not ok:
                cast_errors[c["target_name"]] = cast_errors.get(c["target_name"], 0) + 1
            row_values.append(value)
        rows_to_insert.append(row_values)

    cur.executemany(stmt, rows_to_insert)
    return len(rows_to_insert)


# ---------------------------------------------------------------------------
# Payload landing (Module 6 extension §3) — CSV/Excel only; JSON/XML already have their own
# nested-payload path (M6 étape 3, via column_mapping's "__root__" entry) and are untouched.
# ---------------------------------------------------------------------------

PAYLOAD_FORMATS = {"csv", "excel"}


def _prepare_payload_table(cur, schema_name: str, table_name: str, write_mode: FileImportWriteMode) -> None:
    """DDL for the payload+audit landing shape (§3.3): `payload JSONB NOT NULL` plus
    `load_id`/`source_file`/`row_number` audit columns — never a cast, never a column per
    source key. Same create/replace/append semantics as the typed-mode `_prepare_table`."""
    schema_ident = sql.Identifier(_validate_identifier(schema_name, "Schéma cible"))
    table_ident = sql.Identifier(_validate_identifier(table_name, "Table cible"))

    cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema_ident))

    exists = _table_exists(cur, schema_name, table_name)
    if write_mode == FileImportWriteMode.create and exists:
        raise FileImportError(f"La table {schema_name}.{table_name} existe déjà (mode « create »).")
    if write_mode == FileImportWriteMode.append and not exists:
        raise FileImportError(f"La table {schema_name}.{table_name} n'existe pas (mode « append »).")

    if write_mode == FileImportWriteMode.replace and exists:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(schema_ident, table_ident))
        exists = False

    if not exists:
        cur.execute(
            sql.SQL("CREATE TABLE {}.{} (payload JSONB NOT NULL, load_id INTEGER, source_file TEXT, row_number BIGINT)")
            .format(schema_ident, table_ident)
        )


def _insert_payload_chunk(cur, schema_name: str, table_name: str, fi_id: int, source_file: str, records: list[dict], row_offset: int) -> int:
    """Every cell lands as text in the payload, keyed by the file's own header — no cast, no
    row ever rejected here. `row_number` is the file's true 1-based rank, tracked by the
    caller across chunks (§3.3's traceability line for the future quarantine relation)."""
    schema_ident = sql.Identifier(schema_name)
    table_ident = sql.Identifier(table_name)
    stmt = sql.SQL("INSERT INTO {}.{} (payload, load_id, source_file, row_number) VALUES ({}, {}, {}, {})").format(
        schema_ident, table_ident, sql.Placeholder(), sql.Placeholder(), sql.Placeholder(), sql.Placeholder(),
    )

    rows_to_insert = []
    for i, record in enumerate(records):
        payload = {k: (None if v is None else str(v)) for k, v in record.items()}
        rows_to_insert.append((json.dumps(payload), fi_id, source_file, row_offset + i + 1))

    cur.executemany(stmt, rows_to_insert)
    return len(rows_to_insert)


def run_import_payload(file_import_id: int) -> None:
    """Payload-mode counterpart of run_import() — same self-contained-session contract (own
    DB session, never raises: every failure lands in FileImport.status=error/last_error)."""
    db = SessionLocal()
    try:
        fi = db.get(FileImport, file_import_id)
        if fi is None:
            return
        try:
            _do_run_import_payload(db, fi)
        except Exception as exc:
            logger.warning("payload import %s failed: %s", file_import_id, exc)
            fi.status = FileImportStatus.error
            fi.last_error = str(exc)[:2000]
            db.commit()
    finally:
        db.close()


def _do_run_import_payload(db, fi: FileImport) -> None:
    target = db.get(DataSource, fi.target_source_id)
    archive_source = db.get(DataSource, fi.archive_source_id)
    if target is None or archive_source is None:
        raise FileImportError("Source cible ou d'archive introuvable.")

    fi.status = FileImportStatus.importing
    db.commit()

    file_bytes = fetch_archived(archive_source, fi.archive_path)
    _validate_identifier(fi.target_schema, "Schéma cible")
    _validate_identifier(fi.target_table, "Table cible")

    conn = _pg_connect(target)
    row_count = 0
    try:
        with conn.cursor() as cur:
            _prepare_payload_table(cur, fi.target_schema, fi.target_table, fi.write_mode)
        conn.commit()

        for chunk in _iter_chunks(fi.format.value, file_bytes, fi.format_options or {}):
            with conn.cursor() as cur:
                row_count += _insert_payload_chunk(cur, fi.target_schema, fi.target_table, fi.id, fi.source_file_name, chunk, row_count)
            conn.commit()
    finally:
        conn.close()

    fi.status = FileImportStatus.imported
    fi.row_count = row_count
    fi.cast_errors = {}
    fi.imported_at = datetime.now(timezone.utc)
    db.commit()


def run_import(file_import_id: int) -> None:
    """Self-contained background entrypoint — opens its own DB session, never raises
    (all failures land in FileImport.status=error / last_error)."""
    db = SessionLocal()
    try:
        fi = db.get(FileImport, file_import_id)
        if fi is None:
            return
        try:
            _do_run_import(db, fi)
        except Exception as exc:
            logger.warning("file import %s failed: %s", file_import_id, exc)
            fi.status = FileImportStatus.error
            fi.last_error = str(exc)[:2000]
            db.commit()
    finally:
        db.close()


def _do_run_import(db, fi: FileImport) -> None:
    target = db.get(DataSource, fi.target_source_id)
    archive_source = db.get(DataSource, fi.archive_source_id)
    if target is None or archive_source is None:
        raise FileImportError("Source cible ou d'archive introuvable.")

    fi.status = FileImportStatus.importing
    db.commit()

    file_bytes = fetch_archived(archive_source, fi.archive_path)

    included_columns = [c for c in fi.column_mapping if c.get("include", True)]
    if not included_columns:
        raise FileImportError("Aucune colonne incluse dans le contrat de mapping.")
    for c in included_columns:
        _validate_identifier(c["target_name"], "Nom de colonne")
        _validate_type(c["target_type"])
    _validate_identifier(fi.target_schema, "Schéma cible")
    _validate_identifier(fi.target_table, "Table cible")

    conn = _pg_connect(target)
    cast_errors: dict[str, int] = {}
    row_count = 0
    try:
        with conn.cursor() as cur:
            _prepare_table(cur, fi.target_schema, fi.target_table, included_columns, fi.write_mode)
        conn.commit()

        for chunk in _iter_chunks(fi.format.value, file_bytes, fi.format_options or {}):
            with conn.cursor() as cur:
                row_count += _insert_chunk(cur, fi.target_schema, fi.target_table, included_columns, chunk, cast_errors)
            conn.commit()
    finally:
        conn.close()

    fi.status = FileImportStatus.imported
    fi.row_count = row_count
    fi.cast_errors = cast_errors
    fi.imported_at = datetime.now(timezone.utc)
    fi.contract_hash = canonical_contract(fi.column_mapping)
    db.commit()


def watch_reimport(db, fi: FileImport, file_name: str, file_bytes: bytes) -> dict:
    """Module 6 extension §5.2 — the file watcher's internal equivalent of POST /{id}/
    reimport, called from a background scan (never HTTP), so it runs synchronously — no
    BackgroundTasks, the caller is already off the request thread.

    Unlike the interactive endpoint, a shape mismatch here NEVER mutates fi.column_mapping:
    an unsupervised path rejects cleanly (Decision E — "il ne s'adapte pas en silence") rather
    than silently prepping the contract for a review that isn't happening. Never raises —
    every failure mode (read, shape mismatch, write) comes back as a `{"outcome": ...}` dict
    for the caller (services/file_watch.py) to journal; there's no dedicated "write failed"
    WatchOutcome value (§4.2's enum only has fetch_error for every non-drift ingestion
    failure), so a DB/cast-level failure is reported the same way as a read failure — the
    `error` text always carries the real, specific reason regardless.

    Returns one of:
      {"outcome": "imported", "row_count": int, "cast_errors": dict, "imported_at": iso str}
      {"outcome": "drift", "drift_message": str}
      {"outcome": "error", "error": str}
    """
    try:
        archive_source = db.get(DataSource, fi.archive_source_id)
        if archive_source is None:
            return {"outcome": "error", "error": "Source d'archive introuvable."}

        archive_path = archive_raw(archive_source, fi.id, file_name, file_bytes)

        if fi.import_mode == ImportMode.payload:
            # Module 6 extension §3.5 — payload is shape-agnostic: no drift detection, straight
            # re-load under the existing write_mode/target_table (mirrors POST /reimport above).
            fi.source_file_name = file_name
            fi.file_size = len(file_bytes)
            fi.checksum = compute_checksum(file_bytes)
            fi.archive_path = archive_path
            fi.uploaded_at = datetime.now(timezone.utc)
            db.commit()

            run_import_payload(fi.id)
            db.refresh(fi)

            if fi.status == FileImportStatus.error:
                return {"outcome": "error", "error": fi.last_error or "Import échoué."}
            return {
                "outcome": "imported", "row_count": fi.row_count, "cast_errors": {},
                "imported_at": fi.imported_at.isoformat() if fi.imported_at else None,
            }

        try:
            new_mapping = infer_column_mapping(fi.format.value, file_bytes, fi.format_options or {})
        except Exception as exc:
            return {"outcome": "error", "error": f"Lecture du fichier impossible : {exc}"}

        existing_names = {c["source_name"] for c in fi.column_mapping}
        new_names = {c["source_name"] for c in new_mapping}
        if new_names != existing_names:
            added = new_names - existing_names
            removed = existing_names - new_names
            parts = []
            if added:
                parts.append(f"colonne(s) apparue(s) : {', '.join(sorted(added))}")
            if removed:
                parts.append(f"colonne(s) disparue(s) : {', '.join(sorted(removed))}")
            return {"outcome": "drift", "drift_message": " ; ".join(parts) or "forme différente du contrat de mapping."}

        fi.source_file_name = file_name
        fi.file_size = len(file_bytes)
        fi.checksum = compute_checksum(file_bytes)
        fi.archive_path = archive_path
        fi.uploaded_at = datetime.now(timezone.utc)
        db.commit()

        # run_import() is self-contained (own DB session, never raises — Module 6's own
        # invariant) — refresh here to pick up what that other session just committed
        # (status/row_count/cast_errors/imported_at) onto THIS session's copy of fi.
        run_import(fi.id)
        db.refresh(fi)

        if fi.status == FileImportStatus.error:
            return {"outcome": "error", "error": fi.last_error or "Import échoué."}
        return {
            "outcome": "imported", "row_count": fi.row_count, "cast_errors": fi.cast_errors,
            "imported_at": fi.imported_at.isoformat() if fi.imported_at else None,
        }
    except Exception as exc:
        logger.warning("watch_reimport failed for file_import %s: %s", fi.id, exc)
        return {"outcome": "error", "error": str(exc)[:2000]}
