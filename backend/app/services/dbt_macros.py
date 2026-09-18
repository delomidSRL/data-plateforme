"""New module — project-scoped, user-authored dbt macros (complements the fixed built-in
catalogue in payload_structure.STRUCTURATION_MACROS). A project's macros are offered in the
03_standardized SQL editor alongside built-ins and columns (same click-to-insert logic,
DatasetPanel.jsx), and written unconditionally into every generated dbt project's macros/
folder (dbt_project.generate_project_files) — a macro a dataset doesn't call is simply unused,
never a build error, exactly like an unused built-in."""
from app.models.dbt_macro import DbtMacro
from app.services.payload_structure import STRUCTURATION_MACROS

# Derived from the built-ins' own file names (macros/<name>.sql) rather than hardcoded a
# second time — stays correct automatically if payload_structure ever adds/renames one.
BUILTIN_MACRO_NAMES = {path.rsplit("/", 1)[-1].removesuffix(".sql") for path in STRUCTURATION_MACROS}


def render_macro(macro: DbtMacro) -> str:
    """A DbtMacro row -> a real `{% macro %}` block. A parameter's `default`, when set, is
    spliced in as raw Jinja source (not auto-quoted): an author writes `flag='DEFAULT'` or
    `max_date=none` exactly as dbt itself expects, same freedom a hand-written macro has."""
    args = []
    for p in macro.parameters:
        name = p["name"]
        default = p.get("default")
        args.append(f"{name}={default}" if default not in (None, "") else name)
    signature = ", ".join(args)
    body = macro.sql_body.strip("\n")
    return f"{{% macro {macro.name}({signature}) -%}}\n{body}\n{{%- endmacro %}}\n"


def render_macro_files(macros: list[DbtMacro]) -> dict[str, str]:
    """{relative_path: content} for every one of a project's custom macros — merged into
    generate_project_files' output alongside macros/generate_schema_name.sql and the built-ins."""
    return {f"macros/{macro.name}.sql": render_macro(macro) for macro in macros}


def macros_source_for_validation(macros: list[DbtMacro]) -> str:
    """Joined macro source for compile_adhoc_sql's standalone Jinja environment — so "Valider
    la syntaxe" resolves a project's own custom macros exactly like the built-ins, without a
    real dbt build."""
    return "".join(render_macro(macro) for macro in macros)
