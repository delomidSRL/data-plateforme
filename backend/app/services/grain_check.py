"""Module 14 §5.3, extended for multi-gold plans — deterministic, plan-level consistency
checks for the gold layer. No LLM involved anywhere in this file: the per-gold grain is
derived mechanically from gold_builder's own GROUP BY construction (never asked of the AI),
and every "inference" here is a closed lexicon matching explicit French wording in the
instruction, never a guess — same discipline for the plan-wide checks (gold count, business
need coverage) added alongside the original grain check."""
import re

_GRANULARITY_LEXICON: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bannuel(?:les?|s)?\b|\bpar an(?:n[ée]e)?s?\b|\bchaque ann[ée]e\b", re.IGNORECASE), "year"),
    (re.compile(r"\bmensuel(?:les?|s)?\b|\bpar mois\b|\bchaque mois\b", re.IGNORECASE), "month"),
    (re.compile(r"\bquotidien(?:nes?|s)?\b|\bjournali[eè]re?s?\b|\bpar jour\b|\bchaque jour\b", re.IGNORECASE), "day"),
]

_GRAIN_LABELS = {"year": "annuelle", "month": "mensuelle", "day": "journalière"}

# Module 14 extension "primitives gold" §4.4 — a gold's own name/metric/dimensions are real
# identifiers (unlike free-form instruction prose); splitting them into words and matching
# instruction LINES against that vocabulary scopes the granularity check to the ONE need this
# specific gold actually serves, instead of the whole instruction blob. Fixes a confirmed false
# positive: "comparaison année N/N-1" (legitimately yearly) was flagged as wrongly-monthly
# purely because "mensuelle" appeared elsewhere in the same instruction, for a different need.
_IDENTIFIER_WORD_RE = re.compile(r"[a-zA-Z]+")
_RATIO_KEYWORD_RE = re.compile(r"\btaux\b|\bratio\b|\bpourcentage\b|%", re.IGNORECASE)

# Annexe "élargir le scope de l'assistant IA" — a plan can now legitimately propose several
# golds (one per distinct analytical need), so two new plan-wide advisory checks join the
# original per-gold grain check: has the AI produced an unreasonable number of golds, and does
# every business need explicitly named in the instruction have at least one matching column
# somewhere across all proposed golds. Both are warnings, never a rejection — same "the
# engineer decides, nothing is ever silently dropped for a judgment call" philosophy as
# grain_warning/logical_plan_warning elsewhere in this module/pipeline_plan.py.
MAX_RECOMMENDED_GOLDS = 10

_NEED_LEXICON: list[tuple[re.Pattern, str, tuple[str, ...]]] = [
    (re.compile(r"\bproduits?\b", re.IGNORECASE), "produits", ("prod",)),
    (re.compile(r"\bclients?\b", re.IGNORECASE), "clients", ("cust", "client")),
    (re.compile(r"\bpays\b", re.IGNORECASE), "pays", ("country", "pays")),
    (re.compile(r"\br[ée]gions?\b", re.IGNORECASE), "régions", ("region",)),
    (re.compile(r"\bcana(?:l|ux)\b", re.IGNORECASE), "canaux", ("channel", "canal")),
    (re.compile(r"\bpromotions?\b", re.IGNORECASE), "promotions", ("promo",)),
    (re.compile(r"\bp[ée]riodes?\b|\btemporel(?:les?)?\b", re.IGNORECASE), "périodes", ("time", "date", "year", "month", "jour", "period")),
]


def gold_grain(dimension_columns: list[str], time_column: str | None, time_grain: str | None) -> list[str]:
    """The columns that define row-uniqueness in a gold dataset — exactly gold_builder's own
    GROUP BY clause (dimension_columns, plus the aliased time-bucket column when there's a
    time_column), so a plan's declared grain is never a second, driftable source of truth."""
    grain = list(dimension_columns)
    if time_column:
        grain.append(f"{time_grain}_{time_column}")
    return sorted(grain)


def instruction_time_grain(instruction: str) -> str | None:
    """Closed heuristic (§5.3/§12): "annuel/annuelle" -> year, "mensuel" -> month,
    "quotidien/journalier" -> day. None when the instruction doesn't explicitly express a
    granularity — deliberately not inferred further: an uncertain guess would produce a warning
    the engineer can't act on, worse than no warning at all."""
    for pattern, grain in _GRANULARITY_LEXICON:
        if pattern.search(instruction):
            return grain
    return None


def _local_instruction_text(instruction: str, tokens: set[str]) -> str:
    """The instruction line whose words best overlap this gold's own identifiers — never the
    whole instruction (§4.4's fix). Empty if no line has any overlap at all: silence is safer
    than falling back to the whole blob, which is exactly the bug being fixed."""
    lines = [ln.strip() for ln in instruction.splitlines() if ln.strip()]
    if not lines or not tokens:
        return ""
    def score(line: str) -> int:
        low = line.lower()
        return sum(1 for tok in tokens if tok in low)
    best = max(lines, key=score)
    return best if score(best) > 0 else ""


def check_gold_grain(
    instruction: str, time_column: str | None, time_grain: str | None,
    *, gold_name: str = "", metric_column: str = "", dimension_columns: list[str] | None = None,
) -> str | None:
    """None if consistent (or nothing explicit to check against). The only divergence this
    checks (§5.3): the gold's own time_grain vs. a granularity explicitly named in the
    instruction — scoped (§4.4) to the instruction line matching THIS gold's own name/columns,
    not the whole instruction (a multi-need instruction mentions several granularities at once,
    one per need — matching the whole text against any one gold conflates them). Falls back to
    the whole instruction when no gold identifiers are given (legacy callers). Structural
    consistency between `grain` and `dimension_columns`/`time_column` can't drift in the first
    place — gold_grain() derives one from the exact same inputs as the other, so there's
    nothing left to compare there."""
    if not time_column or not time_grain:
        return None
    tokens = {w.lower() for ident in (gold_name, metric_column, *(dimension_columns or [])) for w in _IDENTIFIER_WORD_RE.findall(ident or "") if len(w) > 2}
    local_text = _local_instruction_text(instruction, tokens) if tokens else instruction
    wanted = instruction_time_grain(local_text) if local_text else None
    if wanted and wanted != time_grain:
        return f"l'intention exprime une granularité {_GRAIN_LABELS[wanted]} pour ce besoin, mais ce gold agrège en {_GRAIN_LABELS[time_grain]}."
    return None


def check_gold_count(gold: list[dict]) -> str | None:
    """None if within the recommended ceiling. Never truncates the list itself — an
    instruction can legitimately need more than MAX_RECOMMENDED_GOLDS distinct marts; this
    only flags it for a human look, same as every other check here."""
    if len(gold) > MAX_RECOMMENDED_GOLDS:
        return f"{len(gold)} golds proposés, au-delà du seuil recommandé ({MAX_RECOMMENDED_GOLDS}) — vérifie qu'ils répondent tous à un besoin réel avant de valider."
    return None


def instruction_needs(instruction: str) -> set[str]:
    """Closed lexicon (mirrors instruction_time_grain's own discipline): which business needs
    ("produits", "clients", "pays"...) the instruction explicitly names. Never inferred beyond
    this fixed vocabulary — an uncertain guess would produce a warning nobody can act on."""
    return {label for pattern, label, _ in _NEED_LEXICON if pattern.search(instruction)}


def _covered_needs(gold: list[dict]) -> set[str]:
    """Which of the same needs have at least one matching column name somewhere across ALL
    proposed golds — plan-wide, not per-gold: a need answered by a DIFFERENT gold than the one
    a human happens to be looking at still counts as covered."""
    all_columns = {c.lower() for g in gold for c in (*g["dimension_columns"], g["metric_column"], g["time_column"] or "") if c}
    return {label for _, label, substrings in _NEED_LEXICON if any(sub in col for col in all_columns for sub in substrings)}


def check_gold_coverage(instruction: str, gold: list[dict]) -> str | None:
    """None if consistent (or the instruction names no need from the closed lexicon). Flags —
    never blocks — a business need explicitly named in the instruction that no proposed gold's
    columns actually cover, e.g. "canaux" asked for but no gold carries a channel column."""
    wanted = instruction_needs(instruction)
    if not wanted:
        return None
    missing = sorted(wanted - _covered_needs(gold))
    if missing:
        return f"l'intention mentionne {', '.join(missing)}, mais aucun gold ne contient de colonne correspondante."
    return None


def check_count_distinct(gold: list[dict], column_roles: dict[str, str]) -> str | None:
    """Module 14 extension "primitives gold" §4.4 — advisory only (the engineer decides, same
    discipline as _NUMERIC_ONLY_AGGREGATIONS in pipeline_plan.py): a plain COUNT on a column
    classified dimension/join_key is almost always a sign COUNT_DISTINCT was the real intent
    (confirmed on a real gold: "nombre de patients" counted every admission row instead of
    every unique patient, because COUNT(patient_id) counts rows, not distinct patients)."""
    flagged = []
    for g in gold:
        if g.get("aggregation") != "COUNT" or g.get("metric") or not g.get("metric_column"):
            continue
        role = column_roles.get(g["metric_column"])
        if role in ("dimension", "join_keys"):
            kind = "de clé de jointure" if role == "join_keys" else "de dimension"
            flagged.append(f"{g['name']} : COUNT({g['metric_column']}) sur une colonne {kind} — un COUNT_DISTINCT est probablement voulu si le besoin compte des valeurs uniques.")
    return " · ".join(flagged) if flagged else None


def check_ratio_coverage(instruction: str, gold: list[dict]) -> str | None:
    """Module 14 extension "primitives gold" §4.4 — same "advisory, never a rejection"
    philosophy as check_gold_coverage: the instruction names a taux/ratio/pourcentage need but
    no proposed gold actually uses the RATIO primitive to express it."""
    if not _RATIO_KEYWORD_RE.search(instruction):
        return None
    if any(g.get("aggregation") == "RATIO" for g in gold):
        return None
    return "l'intention mentionne un taux/ratio/pourcentage, mais aucun gold de type RATIO n'a été proposé."
