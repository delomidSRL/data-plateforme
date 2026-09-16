"""Deterministic star-schema join detection/compiler (Module 13) — when a fact table and one
or more reference tables share a join key (both already known from the validated mapping),
this builds the joining silver SQL directly instead of asking the AI to write it. Same
reasoning as gold_builder.py: a structural pattern this common and this mechanical is safer
compiled than generated. Confirmed empirically that the self-hosted Mistral, even prompted
with a worked JOIN example, reliably produces one staging model per bronze table instead of
joining them — removing the join itself from the AI's job sidesteps that failure mode."""


def _all_columns(table: dict) -> set[str]:
    return {c for cols in table["columns"].values() for c in cols}


def detect_join_groups(mapping_tables: list[dict]) -> list[dict]:
    """Returns one entry per fact table that transitively shares a join key with >=1
    reference table: [{"fact": table, "references": [{"ref": table, "join_key": str,
    "via": "f" | "r{i}"}, ...]}, ...].

    Builds a join *chain*, not just direct fact->reference edges: starting from the fact
    table, a reference is matched if it shares a column with the fact OR with a reference
    already matched in an earlier pass (e.g. sales has no country_id at all, but customers
    does — customers.country_id -> countries.country_id joins countries in via customers,
    a second hop the original direct-to-fact-only version couldn't reach). Each match's "via"
    records which alias it actually joins against, since different hops legitimately join on
    different keys — build_join_sql needs that to emit the right ON clause per hop.

    Matching pool for each anchor (fact or an already-matched reference) is that anchor's own
    declared "join_keys", checked against the candidate's columns anywhere (not just the
    candidate's own "join_keys") — confirmed empirically that Étape 2's mapping reliably tags
    a table's *own* foreign keys as join_keys, but not reliably a table's role as *someone
    else's* target key (that often only shows up in "dimension"). Requiring both sides to
    have it under "join_keys" specifically was needlessly brittle."""
    facts = [t for t in mapping_tables if t.get("role") == "fact"]
    others = [t for t in mapping_tables if t.get("role") != "fact"]
    groups = []
    for fact in facts:
        fact_keys = set(fact["columns"].get("join_keys", []))
        if not fact_keys:
            continue
        anchor_keys: dict[str, set[str]] = {"f": fact_keys}
        remaining = list(others)
        matches: list[dict] = []
        progress = True
        while progress and remaining:
            progress = False
            still_remaining = []
            for t in remaining:
                t_cols = _all_columns(t)
                found = None
                for alias, keys in anchor_keys.items():
                    common = keys & t_cols
                    if common:
                        found = (alias, next(iter(common)))
                        break
                if found is None:
                    still_remaining.append(t)
                    continue
                via, key = found
                matches.append({"ref": t, "join_key": key, "via": via})
                anchor_keys[f"r{len(matches) - 1}"] = set(t["columns"].get("join_keys", []))
                progress = True
            remaining = still_remaining
        if matches:
            groups.append({"fact": fact, "references": matches})
    return groups


def _col_expr(alias: str, column: str, quoted: bool) -> str:
    # Oracle-sourced bronze tables land in the Postgres warehouse with quoted-UPPERCASE
    # identifiers (confirmed earlier, manually, on the "Ventes annuelles" demo project) — an
    # unquoted lowercase reference silently fails to match ("column does not exist"), since
    # Postgres folds unquoted identifiers to lowercase but the real column is a distinct,
    # case-sensitive quoted one. `quoted` is per-table, driven by that bronze table's real
    # DataSource.type (see pipeline_plan._oracle_bronze_names).
    return f'{alias}."{column.upper()}"' if quoted else f"{alias}.{column}"


def build_join_sql(fact_bronze_name: str, fact_column_names: list[str], references: list[dict], fact_quoted: bool = False) -> str:
    """`references`: [{"bronze_name": str, "column_names": list[str], "join_key": str,
    "via": "f" | "r{i}", "quoted": bool}, ...] — the shape `detect_join_groups` produces
    (bronze_name/quoted resolved by the caller, which knows the bronze-name mapping and each
    table's source type). Every table is read straight off bronze via {{ source('bronze', ...)
    }} — this always joins bronze tables, never other silver models. Every selected column is
    aliased to its plain lowercase name regardless of `quoted`, so anything reading this
    model's *output* (gold, tests, another silver) never needs to know or care which source
    table used quoted-uppercase identifiers.

    Each reference's `via` names the alias it actually joins against — "f" for the fact, or an
    earlier reference's "r{i}" for a chained (2+ hop) join — since a join key detected several
    hops away from the fact is legitimately different per hop (e.g. customers joins the fact on
    cust_id, but countries joins *customers* on country_id, a column the fact never has)."""
    seen = set(fact_column_names)
    select_parts = [f"{_col_expr('f', c, fact_quoted)} AS {c}" for c in fact_column_names]
    lines = [f"FROM {{{{ source('bronze', '{fact_bronze_name}') }}}} f"]
    quoted_by_alias = {"f": fact_quoted}
    for i, ref in enumerate(references):
        alias = f"r{i}"
        ref_cols = [c for c in ref["column_names"] if c not in seen]
        seen.update(ref_cols)
        select_parts += [f"{_col_expr(alias, c, ref['quoted'])} AS {c}" for c in ref_cols]
        via_quoted = quoted_by_alias[ref["via"]]
        lines.append(
            f"JOIN {{{{ source('bronze', '{ref['bronze_name']}') }}}} {alias} "
            f"ON {_col_expr(ref['via'], ref['join_key'], via_quoted)} = {_col_expr(alias, ref['join_key'], ref['quoted'])}"
        )
        quoted_by_alias[alias] = ref["quoted"]
    select_clause = "SELECT " + select_parts[0] + "".join(f"\n    , {c}" for c in select_parts[1:])
    return select_clause + "\n" + "\n".join(lines)
