import json
import re


def extract_json_object(raw: str) -> dict:
    """Strips markdown code fences if present and parses the first {...} object found —
    shared by every Module 13 AI call (mapping, plan) that demands strict-JSON output from a
    model that doesn't always comply with "no markdown" instructions."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.MULTILINE).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Réponse IA sans objet JSON exploitable.")
    return json.loads(stripped[start : end + 1])
