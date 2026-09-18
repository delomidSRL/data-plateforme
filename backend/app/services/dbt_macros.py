"""New module — platform-wide, admin-authored dbt macros (complements the fixed built-in
catalogue in payload_structure.STRUCTURATION_MACROS). Offered in every project's
03_standardized SQL editor alongside built-ins and columns (same click-to-insert logic,
DatasetPanel.jsx), and written unconditionally into every generated dbt project's macros/
folder (dbt_project.generate_project_files) — a macro a dataset doesn't call is simply unused,
never a build error, exactly like an unused built-in."""
import re

from app.models.dbt_macro import DbtMacro
from app.services.payload_structure import STRUCTURATION_MACROS

# Derived from the built-ins' own file names (macros/<name>.sql) rather than hardcoded a
# second time — stays correct automatically if payload_structure ever adds/renames one.
BUILTIN_MACRO_NAMES = {path.rsplit("/", 1)[-1].removesuffix(".sql") for path in STRUCTURATION_MACROS}

# UX ask — an admin writes the whole `{% macro name(...) -%} ... {%- endmacro %}` block by
# hand (no separate structured "parameters" form), so the only thing the route layer needs to
# extract from it is the declared macro name, to check it matches DbtMacro.name.
_MACRO_DECL_RE = re.compile(r"\{%-?\s*macro\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")


def extract_macro_name(definition: str) -> str | None:
    """The name from a `{% macro <name>(...) %}` declaration inside a hand-written definition,
    or None if it doesn't contain one at all (an empty/malformed paste)."""
    match = _MACRO_DECL_RE.search(definition)
    return match.group(1) if match else None


def render_macro(macro: DbtMacro) -> str:
    """A DbtMacro row -> the exact text written into macros/<name>.sql — `definition` already
    IS the complete `{% macro %}...{% endmacro %}` block, written by hand."""
    return macro.definition.strip("\n") + "\n"


def render_macro_files(macros: list[DbtMacro]) -> dict[str, str]:
    """{relative_path: content} for every macro in the platform library — merged into
    generate_project_files' output alongside macros/generate_schema_name.sql and the built-ins."""
    return {f"macros/{macro.name}.sql": render_macro(macro) for macro in macros}


def macros_source_for_validation(macros: list[DbtMacro]) -> str:
    """Joined macro source for compile_adhoc_sql's standalone Jinja environment — so "Valider
    la syntaxe" resolves the platform's own macros exactly like the built-ins, without a real
    dbt build."""
    return "".join(render_macro(macro) for macro in macros)
