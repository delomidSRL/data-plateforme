from app.models.medallion import MedallionVersion


def diff(base: MedallionVersion, target: MedallionVersion) -> dict:
    """Diff from `base` (usually the older, selected version) to `target` (usually the
    active one) — computed purely from the two datasets_snapshot JSON blobs, no live DB
    query needed. Matched by dataset `name` (stable key), not id."""
    base_by_name = {d["name"]: d for d in base.datasets_snapshot}
    target_by_name = {d["name"]: d for d in target.datasets_snapshot}

    added = sorted(set(target_by_name) - set(base_by_name))
    removed = sorted(set(base_by_name) - set(target_by_name))
    common = set(base_by_name) & set(target_by_name)

    sql_changed = []
    tests_changed = []
    for name in sorted(common):
        b, t = base_by_name[name], target_by_name[name]
        if (b.get("sql") or "") != (t.get("sql") or ""):
            sql_changed.append({"name": name, "before": b.get("sql"), "after": t.get("sql")})
        if (b.get("tests") or []) != (t.get("tests") or []):
            tests_changed.append({"name": name, "before": b.get("tests") or [], "after": t.get("tests") or []})

    return {
        "datasets_added": added,
        "datasets_removed": removed,
        "sql_changed": sql_changed,
        "tests_changed": tests_changed,
    }
