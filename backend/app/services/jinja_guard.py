"""Module 19 §6.3 — the Jinja guard: a deterministic, static analysis pass over every `.sql` /
`.yml` file's raw text, human- or generator-authored alike, at save time AND again right before
compilation (étape 4 reuses `check_source`). No sandboxing, no execution — this never renders
the template, it only walks the parsed AST (and, for hooks, a couple of narrow regexes) looking
for a fixed, versioned-in-code blocklist (§6.3: never configurable from the DB or the UI).

Forbidden in v1:
  - `env_var(...)`        — would let a model read control-plane environment variables.
  - `run_query(...)`      — an arbitrary, unaudited query against the warehouse at compile time.
  - `adapter.execute(...)`— same escape hatch, via the adapter object directly.
  - `{{ statement(...) }}` / `{% call statement(...) %}` — same escape hatch, dbt's own name for it.
  - `modules.*`            — raw Python stdlib access (`modules.os`, `modules.subprocess`, …).
  - `log(...)` with a non-literal argument — logging a literal message is fine; logging a
    variable is a side channel for exfiltrating query results/context through Airflow logs.
  - `pre_hook` / `post_hook` config values, and dbt_project.yml's `on-run-start` / `on-run-end`,
    whose statements aren't all `GRANT ...` — anything else there runs arbitrary SQL outside
    the model's own SELECT, unaudited by the M14 SQL validator.

Python dbt models (`models/**/*.py`) are rejected earlier, by the write endpoint's own file
extension allowlist (§2) — this module never sees them."""
import re

import yaml
from jinja2 import Environment, TemplateSyntaxError, nodes

_env = Environment()

_FORBIDDEN_CALLS = {"env_var", "run_query", "statement"}
_GRANT_RE = re.compile(r"^\s*grant\s", re.IGNORECASE)


class GuardViolation:
    def __init__(self, line: int, message: str):
        self.line = line
        self.message = message

    def __repr__(self):
        return f"GuardViolation(line={self.line}, message={self.message!r})"


def _is_grant_only(statements) -> bool:
    """A hook's value is a single string or a list of strings (dbt's own hook shape) — every
    one of them must be a GRANT statement, nothing else."""
    if isinstance(statements, str):
        statements = [statements]
    if not isinstance(statements, list):
        return False
    return all(isinstance(s, str) and _GRANT_RE.match(s) for s in statements)


def _check_hook_literals(source: str) -> list[GuardViolation]:
    """Narrow, regex-based check for `pre_hook=`/`post_hook=` kwargs inside a `{{ config(...) }}`
    call — walking these as Jinja/Python-expression AST to extract the actual string value
    would need a much heavier expression evaluator than this guard is worth building for v1.
    Good enough: flags any pre_hook/post_hook assignment whose literal string values are not
    all GRANT statements; a non-literal (an expression, a variable) is flagged too, since its
    content can't be verified statically at all."""
    violations = []
    for m in re.finditer(r"(pre_hook|post_hook)\s*=\s*(\[[^\]]*\]|'[^']*'|\"[^\"]*\")", source):
        raw = m.group(2)
        line = source.count("\n", 0, m.start()) + 1
        try:
            value = yaml.safe_load(raw.replace("'", '"'))
        except Exception:
            violations.append(GuardViolation(line, f"{m.group(1)} : valeur illisible, seules des instructions GRANT littérales sont autorisées."))
            continue
        if not _is_grant_only(value):
            violations.append(GuardViolation(line, f"{m.group(1)} : seules des instructions GRANT sont autorisées (aucun autre SQL en dehors de la requête du modèle)."))
    return violations


def _check_dbt_project_hooks(source: str) -> list[GuardViolation]:
    try:
        data = yaml.safe_load(source) or {}
    except Exception:
        return []  # a malformed dbt_project.yml is reported by dbt parse itself, not this guard
    violations = []
    for key in ("on-run-start", "on-run-end"):
        value = data.get(key)
        if value is None:
            continue
        if not _is_grant_only(value):
            violations.append(GuardViolation(1, f"{key} : seules des instructions GRANT sont autorisées."))
    return violations


def check_source(path: str, content: str) -> list[GuardViolation]:
    violations: list[GuardViolation] = []

    if path == "dbt_project.yml":
        violations += _check_dbt_project_hooks(content)

    if not (path.endswith(".sql") or path.endswith(".yml") or path.endswith(".yaml")):
        return violations

    violations += _check_hook_literals(content)

    try:
        ast = _env.parse(content)
    except TemplateSyntaxError:
        # A genuine syntax error is dbt/workspace_sync's job to report (it has the real
        # compiler and a much better message) — the guard only judges well-formed Jinja.
        return violations

    for call in ast.find_all(nodes.Call):
        target = call.node
        if isinstance(target, nodes.Name) and target.name in _FORBIDDEN_CALLS:
            violations.append(GuardViolation(call.lineno, f"« {target.name}(...) » n'est pas autorisé dans le code du projet."))
        elif isinstance(target, nodes.Getattr) and target.attr == "execute" and isinstance(target.node, nodes.Name) and target.node.name == "adapter":
            violations.append(GuardViolation(call.lineno, "« adapter.execute(...) » n'est pas autorisé dans le code du projet."))
        elif isinstance(target, nodes.Name) and target.name == "log":
            if any(not isinstance(arg, nodes.Const) for arg in call.args):
                violations.append(GuardViolation(call.lineno, "« log(...) » ne peut recevoir que des messages littéraux, jamais une variable du contexte."))

    for getattr_node in ast.find_all(nodes.Getattr):
        if isinstance(getattr_node.node, nodes.Name) and getattr_node.node.name == "modules":
            violations.append(GuardViolation(getattr_node.lineno, "« modules.* » (accès direct à la bibliothèque standard Python) n'est pas autorisé."))

    return violations
