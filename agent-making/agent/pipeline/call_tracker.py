"""Tracks every real Anthropic API call a script makes, at the actual
call boundary — not at the outer "run" boundary. Built after discovering
that a single logical "run" of the pipeline can silently be 1-3 real API
calls (judge.py's evidence_supports_result retry + integrity.py's missing-
rule_id retry both loop on the SAME underlying call), which made a "5 run"
consistency probe actually make ~10+ real calls with no visibility into it
until the bill arrived.

Cost is computed from each call's actual response.usage (real token counts),
not guessed — see PRICING_PER_MTOK below, which must be kept in sync with
the model in judge.py if that ever changes.
"""
import os

# claude-sonnet-5 intro pricing (through 2026-08-31); update if judge.MODEL
# changes or the intro window lapses (see shared model pricing table).
INPUT_COST_PER_MTOK = 2.00
OUTPUT_COST_PER_MTOK = 10.00

# claude-haiku-4-5 (model_provider.py's ANTHROPIC_FALLBACK_MODEL) — a
# DIFFERENT, cheaper rate card from Sonnet above; using the Sonnet
# constants for a Haiku call would overstate its real cost. FLAG: this is
# a best-available published-rate estimate, not independently re-verified
# against Anthropic's live pricing page this round — confirm before
# treating it as gospel for a real budget decision, same caveat as the
# Sonnet numbers above.
HAIKU_INPUT_COST_PER_MTOK = 1.00
HAIKU_OUTPUT_COST_PER_MTOK = 5.00

# Never silently absent — read once with an explicit default (4.00) rather
# than "if set" logic that could leave the cap undefined.
DEFAULT_MAX_SPEND_USD = 4.00

# Used only to project the FIRST real call's cost, before any real usage
# data exists this session to average from — see _project_next_call_cost's
# own docstring. Matches judge.MAX_TOKENS, the largest real call this
# pipeline makes (the production Anthropic path); a caller expecting a
# smaller call (e.g. judge.OPENROUTER_MAX_TOKENS) should pass its own
# estimated_max_tokens to check_before_call for a tighter, less
# conservative projection.
_CONSERVATIVE_MAX_TOKENS_ESTIMATE = 32000


class ApiCallCapExceeded(Exception):
    """Raised the instant a script would make a real API call beyond its
    configured call-COUNT cap — before the call happens, not after."""


class ApiSpendCapExceeded(Exception):
    """Raised the instant a script would make a real API call whose
    PROJECTED cost would push cumulative session spend past the dollar
    ceiling — before the call happens, not after. A separate check from
    ApiCallCapExceeded: a run can have plenty of calls left under
    max_calls and still be refused here because those calls would simply
    cost too much, and vice versa."""


class ApiCallTracker:
    """Pass one shared instance through run_full_pipeline -> integrity.py ->
    judge.py so every real API call — initial or retry, for whatever reason
    — increments the same counter and is checked against the same caps.
    """

    def __init__(self, max_calls: int | None = None, max_spend_usd: float | None = None):
        self.max_calls = max_calls
        self.max_spend_usd = (
            max_spend_usd if max_spend_usd is not None
            else float(os.environ.get("MAX_REAL_SPEND_USD", str(DEFAULT_MAX_SPEND_USD)))
        )
        self.count = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def _project_next_call_cost(self, estimated_max_tokens: int) -> float:
        """Estimates the NEXT real call's likely cost, so check_before_call
        can refuse BEFORE that call goes out, not after the bill arrives.

        Once this session has made at least one real call, its own real,
        measured average cost-per-call is the best available estimate for
        the next one (same shape of call, same rule batch, repeatedly).
        Before that — the very first call of the session, with no real
        data yet to average — falls back to a deliberately conservative
        worst case: as if this call used its full estimated_max_tokens as
        real OUTPUT (input cost is comparatively small and, unlike output,
        bounded by the prompt this call already built, so it's excluded
        from this worst-case floor). Overestimating here is the safe
        direction; underestimating would let a call through that could
        blow past the cap.
        """
        if self.count > 0:
            return self.estimated_cost() / self.count
        return (estimated_max_tokens / 1_000_000) * OUTPUT_COST_PER_MTOK

    def check_before_call(self, *, estimated_max_tokens: int | None = None) -> None:
        """Call this immediately before making a real API call. Raises
        before the call happens if it would exceed either cap — call-count
        or dollar spend — never after.
        """
        if self.max_calls is not None and self.count >= self.max_calls:
            raise ApiCallCapExceeded(
                f"Refusing real API call #{self.count + 1}: cap is {self.max_calls}. "
                f"Stopped before making the call, not after."
            )
        projected_call_cost = self._project_next_call_cost(
            estimated_max_tokens if estimated_max_tokens is not None else _CONSERVATIVE_MAX_TOKENS_ESTIMATE
        )
        projected_total = self.estimated_cost() + projected_call_cost
        if projected_total > self.max_spend_usd:
            raise ApiSpendCapExceeded(
                f"Refusing real API call #{self.count + 1}: projected total spend "
                f"${projected_total:.4f} (${self.estimated_cost():.4f} already spent + "
                f"${projected_call_cost:.4f} estimated for this call) would exceed the "
                f"${self.max_spend_usd:.2f} session cap. Stopped before making the call, not after."
            )

    def record(self, reason: str, rule_ids: list[str], usage) -> None:
        """Call this immediately after a real API call completes."""
        self.count += 1
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        print(
            f"[API call #{self.count}] {reason} — {len(rule_ids)} rule_id(s): {rule_ids}. "
            f"tokens this call: in={input_tokens} out={output_tokens}. "
            f"Running total: {self.count} call(s)"
            f"{f'/{self.max_calls}' if self.max_calls else ''}, "
            f"~{self.total_input_tokens} in / {self.total_output_tokens} out tokens total, "
            f"est. cost so far: ${self.estimated_cost():.4f}"
        )
        print(f"[spend-cap] ${self.estimated_cost():.2f} / ${self.max_spend_usd:.2f} real spend this session")

    def estimated_cost(self) -> float:
        return (
            self.total_input_tokens / 1_000_000 * INPUT_COST_PER_MTOK
            + self.total_output_tokens / 1_000_000 * OUTPUT_COST_PER_MTOK
        )
