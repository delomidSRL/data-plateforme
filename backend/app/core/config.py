from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"

    database_url: str = "postgresql+psycopg://dataplateforme:dataplateforme@localhost:5432/dataplateforme"

    app_secret_key: str = ""

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    reset_token_expire_minutes: int = 30

    frontend_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@delomid.io"
    smtp_tls: bool = True

    admin_name: str = "Admin"
    admin_email: str = "admin@delomid.io"
    admin_password: str = "changeme123"

    # Module 12 — self-hosted Mistral (OpenAI-compatible), on a VM external to any Module 1
    # stack: reached via .env, not tied to a stack's lifecycle. The key lives only here —
    # never persisted to the app DB, so no Fernet involved (that's for DB-at-rest secrets).
    mistral_base_url: str = ""
    mistral_api_key: str = ""
    mistral_model: str = ""
    mistral_verify_tls: bool = False
    mistral_timeout: int = 30

    # Module 13 — pipeline-generation agent: below this confidence, a mapped table is
    # demoted to `unresolved` rather than trusted as-is.
    pipeline_agent_min_confidence: float = 0.7
    # The GPU-backed Mistral instance fixed generation *speed*, but not context-size
    # reliability. Confirmed empirically: at 10 real tables (~17K prompt chars, still under
    # the old 15 ceiling — meaning the lexical pre-filter in source_profile.py never actually
    # engaged), the model abandoned the JSON format entirely and wrote a prose description of
    # the schema instead. A 3-table prompt reliably worked. Kept low enough that the pre-filter
    # (services/source_profile.py) actually trims a real multi-table source down to something
    # this model can follow — this is a model-capability limit, not a speed one, so raise only
    # after re-testing at scale.
    pipeline_agent_max_tables: int = 5
    pipeline_agent_max_remap_iterations: int = 3

    # Module 14 — relation detection (services/relationship_detect.py): a candidate relation
    # (child column's sampled distinct values found in the parent column) is kept above this
    # overlap ratio.
    rel_match_threshold: float = 0.90
    # Hard cap on directed column-pair overlap queries per detection run — cost containment on
    # a source with many candidate tables/columns; pairs are prioritized by name similarity
    # before this cutoff, so the cap trims the least plausible candidates first.
    rel_max_pairs: int = 40
    # LIMIT on the child column's distinct-value sample — the overlap check never scans the
    # full (potentially huge) child/fact table, only this bounded sample.
    rel_sample_size: int = 500
    # Hard per-query timeout (seconds) for the overlap check, same role as connections.py's
    # PROFILE_TIMEOUT/SAMPLE_TIMEOUT.
    rel_query_timeout: int = 8

    # Module 14 correctif — fiabilisation détection de relations (§3.6). match_rate alone used
    # to be sufficient to promote a candidate to a "verified fact" injected into the mapping
    # prompt — a small-integer measure column (stock level, threshold...) trivially falls
    # within a wide surrogate ID's range, producing match_rate≈1.0 between two tables with no
    # real business relationship. Four deterministic gates (role exclusion, parent PK-likeness,
    # name affinity, direction) now gate promotion; each has a safe default below.
    #
    # Garde B — the parent side must be quasi-unique (a real PK/UNIQUE constraint always wins
    # outright; this ratio is the fallback when no such constraint is introspectable).
    rel_parent_unique_ratio: float = 0.98
    # Garde C — minimum name-affinity score (child column's stem vs. parent table/column
    # stem, token-overlap based, see relationship_detect._name_affinity) required to promote a
    # candidate absent a real introspected FK constraint. 0.0 = any non-null stem overlap
    # qualifies (the "radical non nul" default from the spec) — raise only if a real schema
    # still lets too much noise through with a token match.
    rel_name_affinity_min: float = 0.0
    # Garde D — diagnostic only (never a rejection on its own, see garde D's own direction/
    # cardinality check for the actual gate): more than this many RAW candidate pairs (before
    # any gate) between the same (child_table, parent_table) logs a "recouvrement de plage
    # surrogate probable" warning, surfacing the failure mode the spec observed even when the
    # gates already filter it out.
    rel_max_per_pair: int = 2

    # Module 14 §7 — bounded repair loop: a silver SQL that fails AST/preview validation is
    # sent back to the model for a fix, at most this many times, before it's dropped with a
    # readable repair_log entry instead of ever being presented as valid.
    repair_max_attempts: int = 3

    # Module 6 extension — File Watcher (§1/§2.1). The in-process asyncio scrutation loop
    # (Décision B): how often it wakes up, and the Postgres advisory-lock key that guarantees
    # a single active scruteur across multiple uvicorn workers (arbitrary constant, unique in
    # this codebase — no other pg_try_advisory_lock call exists to collide with).
    watch_tick_seconds: int = 30
    watch_lock_key: int = 727001
    # §2.1 — every `location.path` for transport=local must resolve strictly under this base;
    # empty (default) means local transport is not configured on this deployment, and any
    # attempt to create one is rejected at validation time rather than silently allowing an
    # unbounded filesystem path.
    watch_local_base: str = ""
    # §2.1 — anti-ReDoS: a regex pattern is probe-matched against a worst-case string, in a
    # genuinely killable spawned subprocess (see watch_transport.validate_pattern — Python's
    # `re` engine holds the GIL for a whole match call, so only a real OS process can be
    # terminated out from under a catastrophic backtrack). The budget has to cover Python
    # interpreter spawn overhead on top of the match itself (confirmed live on Windows:
    # ~0.3-0.6s just to start), not only the regex — a genuinely catastrophic pattern still
    # takes orders of magnitude longer than this either way.
    watch_regex_timeout_ms: int = 2000
    # §5.3 — consecutive scan/import failures on one watch before it self-pauses (status=error)
    # rather than continuing to hammer a broken location/credential unsupervised.
    watch_max_consecutive_failures: int = 3

    # Module 19 — workspace (éditeur de code). Local `dbt parse`/`compile` runs in a throwaway
    # temp dir per call, never against the tenant's own Airflow/warehouse — see
    # services/workspace_sync.py. A project whose packages.yml needs dbt-utils/dbt-expectations
    # gets them copied from this cache instead of `dbt deps` hitting the network (§1 "aucun
    # accès à dbt Hub au runtime"); the cache itself is seeded once, lazily, the first time any
    # project actually needs it (see _ensure_packages_cache).
    workspace_max_file_bytes: int = 512_000
    dbt_runner_timeout_s: int = 60
    dbt_packages_cache_dir: str = "./.dbt_packages_cache"

    @field_validator("smtp_host", "smtp_user", "smtp_from", mode="before")
    @classmethod
    def _strip_whitespace(cls, v: str) -> str:
        # .env values pasted from elsewhere commonly carry a trailing space, which turns
        # a valid hostname into one that fails DNS resolution outright.
        return v.strip() if isinstance(v, str) else v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
