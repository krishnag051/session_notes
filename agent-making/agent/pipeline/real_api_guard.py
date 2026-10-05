"""Structural gate — the code-level half of the fix for a real incident
(2026-10-05): a bare debug script run outside pytest has NONE of agent-
making/agent/tests/conftest.py's autouse guardrail fixtures applied (those
are pytest fixtures — they only activate inside a pytest test collection),
so it had zero protection against making a real, billed call. Confirmed:
this happened for real (~$0.43 unintended spend, see this round's own
report) while debugging a failing test with a bare script instead of a
pytest test. A WRITTEN incident note alone already failed once to prevent
a repeat of the exact same gap (an earlier, similar incident predates this
one) — this is a second, independent, code-level gate that applies to
EVERY process, pytest or not, rather than relying on anyone remembering to
use pytest for debugging.

Every real network call site in this pipeline (every `anthropic.Anthropic()`
construction, the OpenRouter `requests.post`) calls `ensure_real_api_calls_
allowed()` first, before constructing a client or sending a request. It
refuses outright unless `ALLOW_REAL_API_CALLS=1` is explicitly set in the
process environment. Who sets it, deliberately:

- The real, deployed app (staging/production) sets this once in its own
  environment — see docker-compose.yml / docker-compose.staging.yml and
  agent-making/.env.example. That is a genuine, intentional "this process
  serves real reviews" declaration, not a loophole — a deployed backend is
  SUPPOSED to make real calls.
- agent-making's own tests/conftest.py (and backend/tests/conftest.py)
  sets this ONLY for the duration of a test explicitly marked
  `@pytest.mark.real_api`, and only there — the SAME human, per-instance
  approval that marker already requires; the marker itself never
  self-grants permission, same as before this round.
- Anything else — a bare debug script, a fresh shell, an unconfigured
  environment, a stray `python -c "..."` — has this unset by default, and
  is refused immediately and loudly, before any client is even
  constructed, let alone a request sent over the network.
"""
import os


class RealApiCallsNotAllowed(RuntimeError):
    """Raised instead of making a real network call when ALLOW_REAL_API_CALLS
    is not explicitly set to '1' in this process's environment."""


def ensure_real_api_calls_allowed(seam_name: str) -> None:
    if os.environ.get("ALLOW_REAL_API_CALLS") != "1":
        raise RealApiCallsNotAllowed(
            f"BLOCKED by real_api_guard.ensure_real_api_calls_allowed: refusing to make a real, billed API call "
            f"from {seam_name!r} — ALLOW_REAL_API_CALLS is not set to "
            f"'1' in this process's environment. This is a structural gate that applies to EVERY process, "
            f"pytest or not — including a bare debug script run outside pytest, the exact gap that caused a "
            f"real, unintended ~$0.43 spend on 2026-10-05. The real, deployed app sets this once in its own "
            f"environment (see docker-compose.yml / docker-compose.staging.yml); a pytest test sets it only for "
            f"the duration of a test explicitly marked @pytest.mark.real_api, with the user's own per-instance "
            f"approval already granted beforehand. If you are seeing this from an ad hoc script, you almost "
            f"certainly do NOT want to set this yourself — get explicit, per-instance approval first, per "
            f"CLAUDE.md's hard rule, same as any other real API call."
        )
