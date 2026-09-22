"""Platform-wide, admin-managed dq_flag_registry (revives Module 18 §8's seed, decoupled from
the no-code quality_flags contract — see app.models.dq_flag_registry). Written unconditionally
into every generated dbt project's seeds/dq_flag_registry.csv, exactly like DbtMacro's
macros/*.sql: a flag no hand-written SQL ever looks up is simply unused, never a build error."""
import csv
import io

from app.models.dq_flag_registry import DqFlagRegistryEntry


def render_registry_seed(entries: list[DqFlagRegistryEntry]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["flag_name", "category", "source_rule", "issue_type"])
    for e in entries:
        writer.writerow([e.flag_name, e.category.value, e.source_rule or "", e.issue_type or ""])
    return buf.getvalue()
