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

    upload_storage_dir: str = "./data/uploads"

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
