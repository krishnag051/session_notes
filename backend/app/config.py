from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://snuser:snpass@localhost:5432/session_notes"

    # --- rule-checking agent wiring ---
    # app/agent_client.py imports agent-making/agent/pipeline/api.py directly
    # (it's not an installed package) by inserting this path onto sys.path
    # at import time. Defaults to the standard sibling-directory repo layout
    # (../agent-making relative to this backend/ directory) — override if
    # agent-making ever lives somewhere else.
    agent_making_path: str = "../agent-making"
    # agent-making's own model_provider.py already loads agent-making/.env
    # via a relative-to-itself path the moment it's imported, so this is
    # belt-and-suspenders, not strictly load-bearing on its own — but it
    # makes the dependency visible from the backend's own config, for a
    # deploy that ships agent-making's code without its own .env.
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None

    # Hard caps forwarded to every real review_person_document call — see
    # agent-making/agent/pipeline/call_tracker.py for how these are
    # enforced (before the call, not after).
    rule_engine_max_calls: int = 50
    rule_engine_max_spend_usd: float = 4.00

    # A SEPARATE, batch-cumulative cap — distinct from rule_engine_max_spend_usd
    # above (which bounds a single document's own review call). This one
    # bounds the running total across an ENTIRE batch's auto-triggered
    # reviews (see app/services/batch_reviews.py): once already-completed
    # documents in a batch have spent this much, remaining pending
    # documents are marked status="skipped_spend_cap" rather than started —
    # same $4 default as the per-document cap today, but a real, separate
    # number an operator may want to size differently once real batches
    # (not just single test uploads) are actually running live.
    batch_max_spend_usd: float = 4.00

    # session_note_extraction.py's own call tracker (agent-making's
    # CallTracker) now DOES estimate real cost, per-provider (Haiku
    # fallback priced at Haiku's own rate, never Sonnet's — see
    # model_provider.CallTracker.estimated_cost_usd) — folded into
    # SessionNoteReview.spend_usd alongside review_person_document's own
    # cost, so batch_max_spend_usd's running total now reflects BOTH real
    # calls a document's review makes, not just the rule-checking one.
    # (Previously a known gap where this call's cost was invisible to the
    # cap — closed this phase.)
    session_note_extraction_max_calls: int = 3

    # The post-review humanization pass (agent-making's humanize.py::
    # humanize_findings_batch, via app/agent_client.py) -- one real Haiku
    # call per finding, run AFTER rule-checking completes, replacing the
    # old client-side string-truncation heuristic. A real document today
    # has ~15-40 findings; 60 is comfortable headroom without being
    # effectively unbounded.
    humanize_max_calls: int = 60

    # HARD, ENFORCED ceiling on ONE document's total real spend, across
    # ALL THREE real call stages combined (extraction + review + humanize)
    # — urgent production ask after a review-time regression: "no single
    # session note's review may spend more than $2 in real API cost...
    # an actual enforced limit, not just a monitoring/alert". Each stage
    # in run_review gets only its own REMAINING headroom under this
    # ceiling as ITS OWN max_spend_usd (never the full $2, and never the
    # flat rule_engine_max_spend_usd/session_note extraction caps above,
    # which this can only ever tighten, never loosen) — so the three
    # stages combined structurally cannot exceed this number.
    per_document_hard_cap_usd: float = 2.00

    upload_storage_dir: str = "./data/uploads"

    # A document's review failing with an UNEXPECTED exception (not
    # review_person_document's own structured status="error" reporting,
    # e.g. a spend-cap hit — that's left alone, retrying it wouldn't help)
    # gets this many automatic retries before landing in a permanent
    # "failed" state — a transient failure (a flaky real-API response, a
    # momentary network blip) shouldn't require a human to notice and
    # manually re-trigger it. See run_review's own retry loop.
    review_retry_attempts: int = 2
    review_retry_backoff_seconds: float = 2.0

    # Audit Flags (PASSED/FAILED) is a threshold on SessionNoteReview.score,
    # not "any single fail = fail" — real Brellium screenshots show 76%
    # FAILED and 88%/90%/95% PASSED. FLAG FOR KRISHNA: 80 is our own default
    # guess, not a confirmed real Brellium number — confirm or correct it.
    audit_pass_threshold: float = 80.0

    # Comma-separated, not a real list field — pydantic-settings expects
    # JSON-array syntax for a List[str] env var, an awkward thing to
    # hand-type into a docker-compose.yml/.env file. Plain comma-split
    # (see app/main.py) is the simpler contract for an operator setting this.
    cors_allow_origins: str = "http://localhost:5173,http://localhost:3000"


settings = Settings()
