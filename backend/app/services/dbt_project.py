import yaml
from sqlalchemy.orm import Session

from app.models.dbt_macro import DbtMacro
from app.models.medallion import MedallionDataset, MedallionLayer, MedallionProject, TransformType
from app.models.payload_structuration import PayloadStructuration
from app.services import dbt_macros, dbt_test_renderer, payload_structure

# Module 16 extension §1/§4 — pinned exact (never "latest", §1: reproductibilité de
# déploiement multi-tenant), validated live on dbt-core 1.8.8 / dbt-postgres 1.8.2 / Postgres
# (see expectation_catalog.json's `_meta.validated_on`).
DBT_EXPECTATIONS_VERSION = "0.10.3"
DBT_UTILS_VERSION = "1.3.0"

GENERATE_SCHEMA_MACRO = """{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
"""


def _test_entries(tests: list[dict], extra_by_column: dict[str, list] | None = None) -> dict[str, list]:
    """Group a dataset's flat tests list by column name, dbt schema-test format. `extra_by_column`
    (Module 16 extension §4) merges in materialized contract-check tests (Tier A) alongside the
    pre-existing M14 structural ones (Tier B) — same column, same list, one `data_tests:`."""
    by_column: dict[str, list] = {}
    for t in tests or []:
        column = t.get("column")
        test_name = t.get("test")
        if not column or not test_name:
            continue
        if test_name == "accepted_values":
            entry = {"accepted_values": {"values": t.get("values", [])}}
        elif test_name == "relationships":
            entry = {"relationships": {"to": t.get("to"), "field": t.get("field")}}
        else:
            entry = test_name
        by_column.setdefault(column, []).append(entry)
    for column, entries in (extra_by_column or {}).items():
        by_column.setdefault(column, []).extend(entries)
    return by_column


def _schema_yml(datasets: list[MedallionDataset], extra_tests_by_dataset: dict[int, dict[str, list]] | None = None) -> dict:
    extra_tests_by_dataset = extra_tests_by_dataset or {}
    models = []
    for ds in datasets:
        by_column = _test_entries(ds.tests, extra_tests_by_dataset.get(ds.id))
        entry: dict = {"name": ds.dbt_model_name}
        if ds.description:
            entry["description"] = ds.description
        if by_column:
            # Module 16 extension §4 — `data_tests:` (dbt >=1.8 syntax) rather than the
            # deprecated `tests:` key, uniformly for BOTH the pre-existing M14 native tests and
            # the new materialized ones (validated live: no behavior change, drops the
            # deprecation warning, and a column may need one of each).
            entry["columns"] = [{"name": col, "data_tests": t} for col, t in by_column.items()]
        models.append(entry)
    return {"version": 2, "models": models}


def _dbt_project_yml(project: MedallionProject, needs_try_cast: bool, structuration_vars: dict | None = None) -> dict:
    config = {
        "name": project.dbt_project_name,
        "version": "1.0.0",
        "config-version": 2,
        "profile": project.dbt_project_name,
        "model-paths": ["models"],
        "target-path": "target",
        "clean-targets": ["target", "dbt_packages"],
        "models": {
            project.dbt_project_name: {
                "silver": {"+schema": "silver", "+materialized": "view"},
                "gold": {"+schema": "gold", "+materialized": "view"},
            }
        },
    }
    if needs_try_cast:
        # Module 6 extension (payload & structuration) §11.7 — the try_cast-style helpers
        # every structuration model's guarded cast calls (deployed once per run, in `public` —
        # always on the default search_path, idempotent CREATE OR REPLACE).
        config["on-run-start"] = [payload_structure.TRY_CAST_FUNCTIONS_SQL]
        # Module 18 §8 — dq_flag_registry.csv can legitimately have zero data rows (no quality
        # flag defined anywhere in the project yet). Left to dbt's default seed type inference
        # (agate), an all-empty column has nothing to disprove "numeric", so `category` gets
        # created as integer — and 05's `category = 'informative'` comparison then fails with
        # "invalid input syntax for type integer" the moment it runs. Pin every column to text
        # explicitly so the seed's shape never depends on how much sample data it happens to hold.
        config["seeds"] = {
            project.dbt_project_name: {
                "dq_flag_registry": {
                    "+column_types": {"flag_name": "text", "category": "text", "source_rule": "text", "issue_type": "text"},
                },
            },
        }
    if structuration_vars:
        # §5 rewrite — the field list each bronze payload dataset's 01_unpacked/02_typed pair
        # reads via var(<name>_fields); one key per structured dataset, merged here.
        config["vars"] = structuration_vars
    return config


def _profiles_yml(project: MedallionProject, warehouse: dict) -> dict:
    output = {
        "type": "postgres",
        "host": warehouse["host"],
        "port": warehouse["port"],
        "user": warehouse["username"],
        "password": warehouse["password"],
        "dbname": warehouse["database_name"],
        "schema": "silver",
        "threads": 4,
    }
    return {
        project.dbt_project_name: {
            "target": project.target.value,
            "outputs": {"dev": output, "prod": output},
        }
    }


def _sources_yml(bronze_datasets: list[MedallionDataset], extra_tests_by_dataset: dict[int, dict[str, list]] | None = None) -> dict:
    extra_tests_by_dataset = extra_tests_by_dataset or {}
    tables = []
    for ds in bronze_datasets:
        entry: dict = {"name": ds.name}
        # Module 16 extension §4 — bronze had no test rendering at all before this extension
        # (type_conformity is the only materializable check_type that ever targets bronze);
        # additive only, nothing here for a project that materializes nothing.
        by_column = extra_tests_by_dataset.get(ds.id) or {}
        if by_column:
            entry["columns"] = [{"name": col, "data_tests": t} for col, t in by_column.items()]
        tables.append(entry)
    return {
        "version": 2,
        "sources": [
            {
                "name": "bronze",
                "schema": "bronze",
                "tables": tables,
            }
        ],
    }


def _ml_sources_yml(ml_datasets: list[MedallionDataset], extra_tests_by_dataset: dict[int, dict[str, list]] | None = None) -> dict:
    """Python/ML nodes write their gold table themselves (no dbt model) — declaring the
    table as a dbt source instead of a model lets `dbt test` still validate it, exactly
    like tests on bronze sources."""
    extra_tests_by_dataset = extra_tests_by_dataset or {}
    tables = []
    for ds in ml_datasets:
        entry: dict = {"name": ds.output_table or ds.name}
        by_column = _test_entries(ds.tests, extra_tests_by_dataset.get(ds.id))
        if by_column:
            entry["columns"] = [{"name": col, "data_tests": t} for col, t in by_column.items()]
        tables.append(entry)
    return {
        "version": 2,
        "sources": [
            {
                "name": "gold_ml",
                "schema": "gold",
                "tables": tables,
            }
        ],
    }


def _packages_yml() -> str:
    """Module 16 extension §4/§6 — emitted only when at least one Tier A check is
    materialized (§4). Exact pin, never "latest" (§1)."""
    return (
        "packages:\n"
        f"  - package: metaplane/dbt_expectations\n"
        f"    version: {DBT_EXPECTATIONS_VERSION}\n"
        f"  - package: dbt-labs/dbt_utils\n"
        f"    version: {DBT_UTILS_VERSION}\n"
    )


def _model_sql(ds: MedallionDataset) -> str:
    return f"{{{{ config(materialized='{ds.materialization.value}') }}}}\n\n{ds.sql.strip()}\n"


def generate_project_files(
    db: Session,
    project: MedallionProject, datasets: list[MedallionDataset], warehouse: dict | None = None,
    structurations: dict[int, PayloadStructuration] | None = None,
    for_export: bool = False,
) -> dict[str, str]:
    """Returns {relative_path: file_content} for the whole dbt project.

    `structurations` — Module 6 extension (payload & structuration), keyed by bronze dataset
    id: for each one, two staged models are rendered (`01_unpacked_<name>`, `02_typed_<name>`,
    §5 rewrite / §7 UX), both landing in the `silver` schema (never bronze — a directly-usable
    table, PK/NOT NULL enforced, not bronze's own "never exclude a row" surface), the first
    reading `{{ source('bronze', name) }}` (the same, unchanged bronze ingestion — §11.1), the
    second `ref()`-ing the first. 03_standardized onward stay in `bronze`, `ref()`-ing 02_typed
    across that schema boundary transparently — unless a real, hand-written silver dataset
    already claims the `03_standardized_<name>` model name (the "+" on 02_typed's custom SQL
    editor), in which case that one wins and the no-code bronze version is skipped entirely
    (two dbt models can't share a name). A dataset absent from this map (no contract yet, or
    not payload-backed) gets nothing extra — additive only, zero regression for every project
    that doesn't use this feature.

    Module 16 extension §4 — `dbt_test_renderer.render()` is replayed on EVERY generation
    (build, preview, export alike), never a separate write path: a project that materializes
    no check gets no extra file at all (additive, zero regression); dismissing/un-flagging a
    check simply makes it absent from the next regeneration (§2 réversibilité — the whole tree
    is always regenerated fresh from current DB state, never patched in place)."""
    structurations = structurations or {}
    bronze = [d for d in datasets if d.layer == MedallionLayer.bronze]
    silver = [d for d in datasets if d.layer == MedallionLayer.silver]
    gold = [d for d in datasets if d.layer == MedallionLayer.gold]
    gold_dbt = [d for d in gold if d.transform_type == TransformType.dbt]
    gold_ml = [d for d in gold if d.transform_type == TransformType.python]

    # Module 16 extension §6 — the export renders EVERY active check as a test (rendered_tests
    # via render_for_export), not just the ones opted into live enforcement (render); the live
    # build/preview path is unchanged (render(), materialize_as_dbt_test-gated).
    rendered_tests = (dbt_test_renderer.render_for_export if for_export else dbt_test_renderer.render)(db, project.id, datasets)

    # §5 rewrite — rendered first so their vars can go straight into dbt_project.yml below,
    # one <name>_fields key per structured bronze dataset, merged into a single vars block.
    # Module 18 — 03/04/05 are rendered right alongside 01/02 for every structured dataset,
    # unconditionally (not opt-in per stage): an empty standardize/quality_flags list is a
    # no-op passthrough at each stage, so this is additive for every contract that predates
    # Module 18, zero regression for 01/02-only projects.
    # UX ask — the "+" on a 02_typed canvas node (DatasetPanel) lets an engineer author their
    # own 03_standardized_<name> as a real, hand-written silver dataset instead of the no-code,
    # per-field "standardize" ops. When one exists, it must WIN outright: dbt rejects two
    # models sharing one name ("change the name of one of these resources") — a real bug an
    # engineer hit as soon as they used the custom-SQL path on a bronze that still had its
    # auto-rendered bronze version too. Checked by dbt_model_name (silver/gold_dbt's own
    # uniqueness key), not by MedallionDataset.name, since dbt_model_name is what actually
    # determines the generated file/model name.
    custom_model_names = {d.dbt_model_name for d in (silver + gold_dbt) if d.dbt_model_name}

    structuration_vars: dict[str, list] = {}
    structuration_models: dict[str, str] = {}
    all_quality_flags: list[dict] = []
    for ds in bronze:
        structuration = structurations.get(ds.id)
        if structuration is None:
            continue
        standardized_model_name = f"03_standardized_{ds.name}"
        has_custom_standardized = standardized_model_name in custom_model_names
        try:
            rendered = payload_structure.render_unpacked_typed_models(structuration.column_mapping, ds.name)
            annotated_sql = payload_structure.render_annotated_model(structuration.quality_flags, ds.name)
            validated_quarantine = payload_structure.render_validated_quarantine_models(ds.name)
            if not has_custom_standardized:
                standardized_sql = payload_structure.render_standardized_model(structuration.column_mapping, ds.name)
        except payload_structure.PayloadStructureError as exc:
            # A contract that fails to re-render at build time (e.g. a field removed from the
            # payload since it was written) must not silently skip structuration nor crash the
            # whole project's build — surfaced as a normal build error instead (§2 "messages
            # lisibles, jamais de stack trace").
            raise ValueError(f"Structuration invalide pour le dataset « {ds.name} » : {exc}") from exc
        structuration_vars[rendered["vars_key"]] = rendered["vars_entries"]
        # §7 UX — 01/02 land in models/silver/ (schema='silver', see render_unpacked_typed_models),
        # never models/bronze/: 03 onward stay bronze-schema and keep reading
        # {{ ref('02_typed_<name>') }} unchanged — ref() resolves by model name regardless of
        # which schema/folder the referenced model is actually in. dbt_run_bronze_structuration's
        # selector (dag.py.j2) is `+bronze` (not a bare `bronze`) specifically so building 03
        # pulls its now-silver upstream (01/02) in first, same task, correct order.
        structuration_models[f"models/silver/01_unpacked_{ds.name}.sql"] = rendered["unpacked_sql"]
        structuration_models[f"models/silver/02_typed_{ds.name}.sql"] = rendered["typed_sql"]
        if not has_custom_standardized:
            structuration_models[f"models/bronze/03_standardized_{ds.name}.sql"] = standardized_sql
        # 04_annotated keeps reading {{ ref('03_standardized_<name>') }} regardless of which one
        # exists — ref() resolves by model name, so it transparently picks up the custom silver
        # version when there is one, the auto-rendered bronze version otherwise.
        structuration_models[f"models/bronze/04_annotated_{ds.name}.sql"] = annotated_sql
        structuration_models[f"models/bronze/05_validated_{ds.name}.sql"] = validated_quarantine["validated_sql"]
        structuration_models[f"models/bronze/05_quarantine_{ds.name}.sql"] = validated_quarantine["quarantine_sql"]
        structuration_models[f"tests/dq_reconciliation_{ds.name}.sql"] = payload_structure.render_reconciliation_test(ds.name)
        all_quality_flags.extend(structuration.quality_flags)

    files: dict[str, str] = {
        "dbt_project.yml": yaml.safe_dump(
            _dbt_project_yml(project, needs_try_cast=bool(structurations), structuration_vars=structuration_vars),
            sort_keys=False,
        ),
        "macros/generate_schema_name.sql": GENERATE_SCHEMA_MACRO,
        "models/bronze/sources.yml": yaml.safe_dump(_sources_yml(bronze, rendered_tests.schema_by_dataset), sort_keys=False),
    }
    if structurations:
        files.update(payload_structure.STRUCTURATION_MACROS)
        # §8 — one registry seed for the whole project, read by every dataset's 05 (never
        # per-dataset: a flag name is a project-wide identifier, §8's "quelle que soit la
        # table"). Present even when no dataset has defined a single flag yet (05's routing
        # still needs the seed to exist — an empty registry just means every flag routes to
        # quarantine, the documented safe default for anything undeclared).
        files["seeds/dq_flag_registry.csv"] = payload_structure.render_registry_seed(all_quality_flags)
    # New module — the platform's admin-managed macro library (DbtMacro), unconditional (not
    # gated on `structurations`, not scoped to this project): every project gets every macro,
    # any dbt SQL dataset can call one, not just a 03_standardized. A macro nothing calls is
    # simply unused, never a build error — same additive contract as the built-ins above.
    custom_macros = db.query(DbtMacro).all()
    if custom_macros:
        files.update(dbt_macros.render_macro_files(custom_macros))
    if for_export or rendered_tests.has_tier_a:
        # §6 — the exported bundle always pins dbt-expectations/dbt-utils, even for a project
        # with zero Tier A checks today: a standalone artifact ready to extend. The live build
        # only pins it when actually needed (unchanged behavior).
        files["packages.yml"] = _packages_yml()
    if not for_export:
        # profiles.yml carries the live warehouse credential — deliberately NEVER part of the
        # export (§8 "aucun secret nouveau"); the README documents the expected profile shape
        # instead (§6).
        files["profiles.yml"] = yaml.safe_dump(_profiles_yml(project, warehouse), sort_keys=False)
    files.update(rendered_tests.singular_files)
    files.update(structuration_models)

    if silver:
        files["models/silver/schema.yml"] = yaml.safe_dump(_schema_yml(silver, rendered_tests.schema_by_dataset), sort_keys=False)
        for ds in silver:
            files[f"models/silver/{ds.dbt_model_name}.sql"] = _model_sql(ds)

    if gold_dbt:
        files["models/gold/schema.yml"] = yaml.safe_dump(_schema_yml(gold_dbt, rendered_tests.schema_by_dataset), sort_keys=False)
        for ds in gold_dbt:
            files[f"models/gold/{ds.dbt_model_name}.sql"] = _model_sql(ds)

    if gold_ml:
        files["models/gold/ml_sources.yml"] = yaml.safe_dump(_ml_sources_yml(gold_ml, rendered_tests.schema_by_dataset), sort_keys=False)

    return files
