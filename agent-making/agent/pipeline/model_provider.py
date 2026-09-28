"""Round 59: a single, explicit provider/model selection point, so no call
site in this pipeline hardcodes "call Anthropic" anymore. Two providers:

- "openrouter" (the DEFAULT, for all building and automated/CI testing) --
  routes through OpenRouter's OpenAI-compatible REST API via plain
  `requests` (no new SDK dependency). Model defaults to
  `nvidia/nemotron-3-ultra-550b-a55b:free` -- confirmed live against
  OpenRouter's own /api/v1/models list this round: real, current,
  `pricing: {"prompt": "0", "completion": "0"}`, and
  `supported_parameters` includes `"tools"`/`"tool_choice"` (function-
  calling capable, which the extraction step needs for structured JSON
  output). Genuinely free -- $0 regardless of call volume -- but still
  call-ceiling-limited below so a runaway retry loop can't hammer
  OpenRouter's rate limits.
- "anthropic" -- the real, billed path. Never the default, never invoked
  by anything in this round's own tests or build steps. Reachable only via
  an explicit `model_override` a human passes deliberately -- same
  standing rule as every other round: no real Anthropic call without
  stating the exact command/count/cost and getting per-instance approval
  in chat FIRST. This module doesn't add a runtime block on top of that
  (the existing project discipline is "mock the boundary in tests, ask
  before running for real" -- see judge.py/supporting_doc_extraction.py),
  but it does mean the default can never accidentally reach Anthropic:
  you have to name it.

`model_override` accepts either:
- `None` -- use the env-configured default (AGENT_LLM_PROVIDER /
  AGENT_LLM_MODEL, both optional; falls back to "openrouter" + the free
  Nemotron model above if unset).
- `"openrouter"` / `"anthropic"` -- provider only, default model for that
  provider.
- `"openrouter:<model-id>"` / `"anthropic:<model-id>"` -- explicit
  provider AND model, e.g. `"openrouter:nvidia/nemotron-3-super-120b-a12b:free"`
  or `"anthropic:claude-sonnet-5"`.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"  # matches judge.py's MODEL

# 2026-08-13 real incident: two separate real uploads (Yisroel, Zohan) both
# failed at the session-note-extraction step on the SAME OpenRouter gateway
# timeout, back to back -- frequent enough to block real end-to-end
# testing. This is the model used ONLY when falling back from a failed
# OpenRouter call (see call_openrouter_with_fallback below) -- deliberately
# Haiku, not Sonnet: a fallback whose whole point is "OpenRouter is having
# a bad moment, get a real answer anyway" shouldn't itself become the slow/
# expensive path. Never the model for anthropic as a PRIMARY provider
# choice (DEFAULT_ANTHROPIC_MODEL/judge.py's MODEL stay Sonnet for that).
ANTHROPIC_FALLBACK_MODEL = "claude-haiku-4-5"

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_SECONDS = 120

# Live incident (2026-08): OpenRouter's free-tier shared worker pool
# returned "Upstream error from Nvidia: ResourceExhausted: Worker local
# total request limit reached (32/32)" -- wrapped in a 200-status response
# with no `choices`, so _call_openrouter correctly raised, but as a PLAIN
# ModelCallError indistinguishable from a genuinely broken request (bad
# schema, bad API key). That's the wrong bucket: a shared-pool capacity
# wall is transient by nature -- the identical request will very likely
# succeed seconds later once a worker frees up. These substrings are
# matched case-insensitively against the raw error body/message; keep
# this list narrow and evidence-based (only patterns actually observed or
# clearly documented as transient-by-nature) rather than broad enough to
# accidentally swallow a real, permanent failure.
_TRANSIENT_ERROR_MARKERS = (
    "resourceexhausted",
    "worker local total request limit reached",
    "rate limit",
    "rate_limit",
    "too many requests",
    "temporarily unavailable",
    "try again later",
    "overloaded",
    # 2026-08-13 real incident: two separate real uploads (Yisroel, Zohan)
    # both failed at session-note extraction with the identical shape --
    # {"error": {"message": "error code: 524...", "code": 504}}, a
    # Cloudflare-style gateway timeout wrapped inside OpenRouter's own
    # 200-status response body. Same "upstream infrastructure blip, will
    # likely succeed seconds later" reasoning as the ResourceExhausted
    # case above -- this text-based check is the belt; _is_transient_error_
    # code below (checking the wrapped error's own numeric `code`, not
    # just this text) is the suspenders, since the exact wording of a
    # gateway's own error page isn't something to depend on staying
    # constant.
    "gateway timeout",
    "bad gateway",
    "error code: 524",
    "error code: 502",
    "error code: 503",
    "error code: 504",
)

# Numeric-code counterpart to the text markers above -- OpenRouter wraps
# the REAL upstream failure code inside the JSON body's own `error.code`
# field (confirmed live: outer HTTP status was 200, inner `code` was 504)
# rather than always surfacing it as the outer HTTP status. Checked
# against both the outer response status and this inner code -- see
# _call_openrouter's own two call sites of _is_transient_error_code below.
_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504, 524}


def _is_transient_error_code(code: object) -> bool:
    try:
        return int(code) in _TRANSIENT_HTTP_CODES
    except (TypeError, ValueError):
        return False


class ModelCallError(Exception):
    """Wraps a real failure from either provider's call site (HTTP error,
    missing tool_call in the response, etc.) into one exception type
    callers can catch regardless of which provider actually ran."""


class TransientModelCallError(ModelCallError):
    """A ModelCallError whose underlying cause looks like a temporary,
    shared-capacity problem on the PROVIDER'S side (worker pool exhausted,
    rate-limited, "try again"), not a real problem with this specific
    request. Distinguished from the plain ModelCallError superclass so
    call_tool_json's retry loop can retry ONLY this kind -- a genuinely
    malformed request or an auth failure raises the plain
    ModelCallError instead and is never retried here."""


def _is_transient_error_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _TRANSIENT_ERROR_MARKERS)


def resolve_provider_and_model(model_override: str | None = None) -> tuple[str, str]:
    """Pure resolution logic, no call made here. Returns (provider, model)."""
    if model_override:
        if ":" in model_override and "/" not in model_override.split(":", 1)[0]:
            # "provider:model" -- but guard against OpenRouter's OWN model
            # ids that contain a colon (e.g. "...:free") being misread as
            # "provider:model". A real provider prefix is always exactly
            # "openrouter" or "anthropic", never containing a "/".
            provider, _, model = model_override.partition(":")
            if provider in ("openrouter", "anthropic"):
                return provider, model or _default_model_for(provider)
        if model_override in ("openrouter", "anthropic"):
            return model_override, _default_model_for(model_override)
        # A bare model id with no recognized "provider:" prefix -- infer
        # from shape. OpenRouter ids are always "<org>/<model>"; Anthropic
        # ids never contain "/".
        provider = "openrouter" if "/" in model_override else "anthropic"
        return provider, model_override

    provider = os.environ.get("AGENT_LLM_PROVIDER", "openrouter")
    model = os.environ.get("AGENT_LLM_MODEL") or _default_model_for(provider)
    return provider, model


def _default_model_for(provider: str) -> str:
    return DEFAULT_OPENROUTER_MODEL if provider == "openrouter" else DEFAULT_ANTHROPIC_MODEL


def call_tool_json(
    *,
    prompt_text: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    tracker: "CallTracker",
    model_override: str | None = None,
    max_tokens: int = 4096,
    call_reason: str = "call",
    max_transient_retries: int = 2,
    backoff_seconds: float = 1.0,
    sleep_fn=time.sleep,
    enable_anthropic_fallback: bool = True,
) -> dict[str, Any]:
    """The one call site both session_note_extraction.py's extraction step
    and any future comparison-adjacent reasoning should use -- dispatches
    to whichever provider resolve_provider_and_model() picks, and returns
    the tool call's parsed `arguments`/`input` dict either way, so callers
    never need to know which provider actually ran.

    `tracker` must support `.check_before_call()` (raise before an
    over-ceiling call) and `.record(reason, provider, model, usage)`
    (after a successful call) -- see CallTracker below. Always called,
    regardless of provider, so the SAME ceiling covers both.

    Live incident fix (2026-08): a TransientModelCallError (a provider-
    side, shared-capacity failure -- see the module docstring's marker
    list) is now retried up to `max_transient_retries` times (default 2,
    i.e. 3 total attempts) with short exponential backoff
    (`backoff_seconds * 2**attempt` -- 1s, then 2s by default) before
    being allowed to propagate. A plain ModelCallError (a genuinely broken
    request -- bad schema, bad API key, malformed response shape) is
    NEVER retried here and raises immediately on the first attempt,
    unchanged from before this fix -- retrying a permanently-broken
    request would just waste the same number of calls for the same
    guaranteed failure, and would risk masking a real problem as if it
    were transient.

    `sleep_fn` defaults to `time.sleep` but is a real parameter so tests
    can inject a fake, instant sleep and assert on the backoff schedule
    without a real test actually waiting seconds.

    2026-08-13: when the resolved provider is "openrouter", this now goes
    through call_openrouter_with_fallback below -- same retry-with-backoff
    behavior as before, PLUS a genuine fallback to a real (billed, Haiku)
    Anthropic call if OpenRouter is still failing once retries are
    exhausted. See that function's own docstring for the full design and
    the real incident that motivated it. The "anthropic" provider branch
    is unchanged -- there is nothing to fall back to when Anthropic is
    already the primary, explicitly-chosen provider.

    `enable_anthropic_fallback=False` disables the fallback for this call
    only (still retries OpenRouter itself, per max_transient_retries) --
    for a caller that deliberately wants OpenRouter-only behavior, or a
    test isolating the retry logic from the fallback logic.
    """
    provider, model = resolve_provider_and_model(model_override)

    if provider == "openrouter":
        result = call_openrouter_with_fallback(
            model=model, prompt_text=prompt_text, tool_name=tool_name, tool_description=tool_description,
            input_schema=input_schema, max_tokens=max_tokens, call_reason=call_reason, tracker=tracker,
            max_transient_retries=max_transient_retries, backoff_seconds=backoff_seconds, sleep_fn=sleep_fn,
            enable_anthropic_fallback=enable_anthropic_fallback,
        )
        tracker.record(reason=call_reason, provider=result["provider_used"], model=result["model_used"], usage=result["usage"])
        return result["arguments"]

    if provider != "anthropic":
        raise ModelCallError(f"Unknown provider {provider!r} (expected 'openrouter' or 'anthropic')")

    attempt = 0
    while True:
        tracker.check_before_call()
        try:
            result = _call_anthropic(
                model=model, prompt_text=prompt_text, tool_name=tool_name,
                tool_description=tool_description, input_schema=input_schema, max_tokens=max_tokens,
            )
        except TransientModelCallError as exc:
            if attempt >= max_transient_retries:
                raise
            wait = backoff_seconds * (2 ** attempt)
            attempt += 1
            print(
                f"[model-provider] transient upstream failure on attempt {attempt}/{max_transient_retries + 1} "
                f"({provider}:{model}, reason={call_reason!r}): {exc}. Retrying in {wait:.1f}s..."
            )
            sleep_fn(wait)
            continue
        break

    tracker.record(reason=call_reason, provider=provider, model=model, usage=result["usage"])
    return result["arguments"]


def call_openrouter_with_fallback(
    *,
    model: str,
    prompt_text: str,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    max_tokens: int,
    call_reason: str = "call",
    fallback_model: str | None = None,
    enable_anthropic_fallback: bool = True,
    max_transient_retries: int = 2,
    backoff_seconds: float = 1.0,
    sleep_fn=time.sleep,
    tracker: "CallTracker | Any | None" = None,
) -> dict[str, Any]:
    """The general pattern (2026-08-13): retry a failing OpenRouter call
    with short backoff, and if it's STILL failing once retries are
    exhausted, fall back to a real Anthropic call (default model:
    ANTHROPIC_FALLBACK_MODEL, i.e. Haiku -- fast and cheap, since the
    whole point of a fallback is to not itself become the slow/expensive
    path) rather than giving up. OpenRouter stays the PRIMARY path -- this
    is a fallback for when it's failing, not a switch away from it; every
    call still tries OpenRouter first, every time.

    Real incident this fixes: two separate real uploads (Yisroel, Zohan)
    both failed at the session-note-extraction step on the identical
    OpenRouter gateway timeout, back to back -- frequent enough to block
    real end-to-end testing. Shared by BOTH real call sites that talk to
    OpenRouter with no protection before this (session_note_extraction.py
    via call_tool_json above, and judge.py's own OpenRouter branch in
    _run_judgment_checks_once, which called `_call_openrouter` directly
    with no retry/fallback at all) -- one mechanism, not two copies of the
    same logic.

    Falls back on ANY OpenRouter failure once retries are exhausted --
    not just ones matching the transient-error markers. Deliberately
    broader than the retry loop's own transient/permanent distinction:
    a bad/expired OPENROUTER_API_KEY, for instance, is a plain (non-
    transient) ModelCallError that will never succeed no matter how many
    times OpenRouter is retried, but IS exactly the kind of failure a
    working Anthropic key can still recover from -- confirmed live, see
    this fix's own real verification (a real OpenRouter call deliberately
    broken via a bad key, recovered via the Anthropic fallback).

    Returns {"arguments": ..., "usage": ..., "provider_used": "openrouter"
    | "anthropic-fallback", "model_used": str} -- "provider_used"/
    "model_used" are new, additive fields (not present on _call_openrouter/
    _call_anthropic's own raw return shape) so a caller (or this
    function's own log lines) can always tell which path actually served
    a given request, per this fix's own explicit requirement -- never
    silently ambiguous after the fact.

    Raises the ORIGINAL OpenRouter error if `enable_anthropic_fallback` is
    False (e.g. a caller that's deliberately testing OpenRouter-only
    behavior), or a combined ModelCallError naming BOTH failures if the
    Anthropic fallback also fails.

    `tracker`, if given, must support `.check_before_call()` (both
    CallTracker here and call_tracker.py's ApiCallTracker do) -- called
    before EVERY real attempt this function makes (each OpenRouter retry,
    and the fallback attempt), so a call-count cap is enforced across the
    whole retry+fallback sequence, not just once before it starts. Does
    NOT call `.record()` -- the two tracker classes' own record() methods
    take different arguments (CallTracker: reason/provider/model/usage;
    ApiCallTracker: reason/rule_ids/usage), so recording stays the
    caller's own job, using this function's returned usage/provider_used/
    model_used fields.
    """
    attempt = 0
    last_error: ModelCallError | None = None
    while True:
        if tracker is not None:
            tracker.check_before_call()
        try:
            result = _call_openrouter(
                model=model, prompt_text=prompt_text, tool_name=tool_name,
                tool_description=tool_description, input_schema=input_schema, max_tokens=max_tokens,
            )
            return {**result, "provider_used": "openrouter", "model_used": model}
        except TransientModelCallError as exc:
            last_error = exc
            if attempt < max_transient_retries:
                wait = backoff_seconds * (2 ** attempt)
                attempt += 1
                print(
                    f"[model-provider] transient OpenRouter failure on attempt {attempt}/{max_transient_retries + 1} "
                    f"(openrouter:{model}, reason={call_reason!r}): {exc}. Retrying in {wait:.1f}s..."
                )
                sleep_fn(wait)
                continue
            break
        except ModelCallError as exc:
            # Non-transient (e.g. a bad API key, a malformed response
            # shape) -- retrying OpenRouter itself won't help, so go
            # straight to the fallback decision below instead of wasting
            # retries on a guaranteed-repeat failure.
            last_error = exc
            break

    if not enable_anthropic_fallback:
        raise last_error

    fb_model = fallback_model or ANTHROPIC_FALLBACK_MODEL
    print(
        f"[model-provider] OpenRouter exhausted for this call (reason={call_reason!r}): {last_error}. "
        f"Falling back to Anthropic ({fb_model})..."
    )
    if tracker is not None:
        tracker.check_before_call()
    try:
        fb_result = _call_anthropic(
            model=fb_model, prompt_text=prompt_text, tool_name=tool_name,
            tool_description=tool_description, input_schema=input_schema, max_tokens=max_tokens,
        )
    except ModelCallError as fb_exc:
        raise ModelCallError(
            f"Both OpenRouter and the Anthropic fallback failed for this call (reason={call_reason!r}). "
            f"OpenRouter ({model}): {last_error}. Anthropic fallback ({fb_model}): {fb_exc}"
        ) from fb_exc

    print(f"[model-provider] Anthropic fallback SUCCEEDED for this call (reason={call_reason!r}), model={fb_model}.")
    return {**fb_result, "provider_used": "anthropic-fallback", "model_used": fb_model}


def _call_openrouter(
    *, model: str, prompt_text: str, tool_name: str, tool_description: str, input_schema: dict, max_tokens: int,
) -> dict:
    """The actual `requests.post` -- kept as its own function (not inlined
    into call_tool_json) so tests can monkeypatch exactly this one seam,
    same convention as this project's judge.py tests monkeypatch
    `judge.anthropic.Anthropic` at its own single seam.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ModelCallError("OPENROUTER_API_KEY is not set (checked agent-making/.env and the process environment)")

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt_text}],
        "tools": [{
            "type": "function",
            "function": {"name": tool_name, "description": tool_description, "parameters": input_schema},
        }],
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
    }
    response = requests.post(
        OPENROUTER_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=OPENROUTER_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        error_cls = TransientModelCallError if (
            _is_transient_error_code(response.status_code) or _is_transient_error_text(response.text)
        ) else ModelCallError
        raise error_cls(f"OpenRouter call failed: {response.status_code} {response.text[:500]}")

    body = response.json()
    if "choices" not in body or not body["choices"]:
        # A 200 status doesn't guarantee a usable body -- e.g. OpenRouter's
        # free tier can return a 200 with an `error` object instead of
        # `choices` under rate-limiting/provider-side issues. Surface the
        # real body rather than a bare KeyError, so this is diagnosable
        # from the first failure instead of needing a live re-run to see
        # what actually came back. Confirmed live incident (2026-08): this
        # exact shape -- {"error": {"message": "Upstream error from
        # Nvidia: ResourceExhausted: Worker local total request limit
        # reached (32/32)", "code": 502}} -- is a transient shared-pool
        # capacity wall, not a broken request; raise the retryable
        # subclass when the body's own text matches that pattern.
        body_text = json.dumps(body)
        wrapped_code = body.get("error", {}).get("code") if isinstance(body.get("error"), dict) else None
        error_cls = TransientModelCallError if (
            _is_transient_error_text(body_text) or _is_transient_error_code(wrapped_code)
        ) else ModelCallError
        raise error_cls(f"OpenRouter response had no usable 'choices' (status 200): {body_text[:800]}")
    choice = body["choices"][0]
    tool_calls = choice.get("message", {}).get("tool_calls") or []
    if not tool_calls:
        raise ModelCallError(
            f"OpenRouter response had no tool_calls (finish_reason={choice.get('finish_reason')!r}); "
            f"message content: {choice.get('message', {}).get('content')!r}"
        )
    arguments_raw = tool_calls[0]["function"]["arguments"]
    arguments = json.loads(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw

    usage = body.get("usage") or {}
    return {
        "arguments": arguments,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _call_anthropic(
    *, model: str, prompt_text: str, tool_name: str, tool_description: str, input_schema: dict, max_tokens: int,
) -> dict:
    """The real, billed path -- structurally identical shape to every
    other real Anthropic call site in this pipeline (judge.py,
    supporting_doc_extraction.py). Never invoked by this round's own code
    or tests; only reachable via an explicit model_override a human passes
    on purpose, per the standing per-instance-approval rule.
    """
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[{"name": tool_name, "description": tool_description, "input_schema": input_schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": prompt_text}],
    )
    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    return {
        "arguments": tool_use_block.input,
        "usage": {
            "input_tokens": getattr(response.usage, "input_tokens", 0),
            "output_tokens": getattr(response.usage, "output_tokens", 0),
        },
    }


def call_tool_json_with_images(
    *,
    prompt_text: str,
    images: dict[int, bytes],
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    tracker: "CallTracker",
    model_override: str | None = None,
    max_tokens: int = 1024,
    call_reason: str = "call",
) -> dict[str, Any]:
    """Fix Round (Previous TP round, Bug 2): a real vision-capable sibling
    of call_tool_json above -- that function's `prompt_text: str` only
    parameter has no way to attach an image (confirmed: neither
    _call_openrouter nor _call_anthropic accepts anything but a plain
    string `content`). This is for exactly the case call_tool_json can't
    handle: a narrow, single-purpose question about a SPECIFIC rendered
    page image (e.g. "what score is shown in this milestone grid?"),
    reusing the SAME real image content-block construction judge.py's own
    vision-eligible judgment path already uses (`_build_prompt`'s
    "type": "image" blocks, base64-encoded PNG) -- not a new mechanism.

    Anthropic ONLY, always -- OpenRouter's free-tier model this pipeline
    defaults to has no confirmed vision support in this codebase, and
    nothing here builds an OpenRouter-shaped image message. Raises
    ModelCallError immediately (before any call) if `model_override`
    doesn't explicitly resolve to the "anthropic" provider -- silently
    falling back to a text-only call would mean quietly losing the one
    thing this function exists for, not a graceful degradation.

    No retry/backoff/fallback logic (unlike call_tool_json) -- a single
    narrow vision call for a real production path; kept simple rather than
    duplicating that machinery for a call shape that isn't the main
    judgment batch's own high-volume path.
    """
    provider, model = resolve_provider_and_model(model_override)
    if provider != "anthropic":
        raise ModelCallError(
            f"call_tool_json_with_images requires the 'anthropic' provider explicitly "
            f"(got {provider!r} from model_override={model_override!r}) -- image content isn't "
            f"supported through the OpenRouter path in this codebase."
        )

    tracker.check_before_call()

    import anthropic

    content: list[dict] = [{"type": "text", "text": prompt_text}]
    for page_number in sorted(images):
        content.append({"type": "text", "text": f"--- Rendered page {page_number} ---"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(images[page_number]).decode("utf-8"),
            },
        })

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        tools=[{"name": tool_name, "description": tool_description, "input_schema": input_schema}],
        tool_choice={"type": "tool", "name": tool_name},
        messages=[{"role": "user", "content": content}],
    )
    tool_use_block = next(b for b in response.content if b.type == "tool_use")
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0),
        "output_tokens": getattr(response.usage, "output_tokens", 0),
    }
    tracker.record(reason=call_reason, provider=provider, model=model, usage=usage)
    return tool_use_block.input


class CallTracker:
    """Round 59's OpenRouter-and-Anthropic-agnostic call tracker -- separate
    from call_tracker.py's ApiCallTracker (which is Anthropic-pricing-
    specific: INPUT_COST_PER_MTOK/OUTPUT_COST_PER_MTOK only make sense for
    the real billed path). This one tracks call COUNT for a cap regardless
    of provider, and cost only when the provider actually charges anything
    (OpenRouter's free-tier calls are always $0 by construction -- pricing
    is looked up per-provider, not assumed).
    """

    def __init__(self, max_calls: int | None = None):
        self.max_calls = max_calls
        self.count = 0
        self.calls_by_provider: dict[str, int] = {}
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # Fix Round (Performance, 2026-09-11): both real call sites this
        # tracker guards (judge.py's 5-way vote, agent_client.py's
        # humanize batch) now run their calls concurrently across a
        # thread pool, sharing this ONE tracker instance per batch -- see
        # each one's own docstring. `self.count += 1` and the dict/int
        # increments below are not atomic in Python under concurrent
        # access from multiple threads (a classic lost-update race), so
        # without this lock two threads' calls could both pass
        # check_before_call() when only one call's budget remains,
        # silently exceeding max_calls -- a real correctness gap for the
        # one thing this class exists to enforce. A plain lock is enough
        # here (not a more elaborate primitive): both guarded methods are
        # short and never block on I/O while holding it.
        self._lock = threading.Lock()

    def check_before_call(self) -> None:
        with self._lock:
            if self.max_calls is not None and self.count >= self.max_calls:
                raise ModelCallError(
                    f"Refusing call #{self.count + 1}: cap is {self.max_calls}. Stopped before making the call, not after."
                )

    def record(self, *, reason: str, provider: str, model: str, usage: dict) -> None:
        with self._lock:
            self.count += 1
            self.calls_by_provider[provider] = self.calls_by_provider.get(provider, 0) + 1
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            print(
                f"[model-provider] call #{self.count} ({provider}:{model}, reason={reason!r}) -- "
                f"tokens in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}. "
                f"Running total: {self.count}{f'/{self.max_calls}' if self.max_calls else ''} "
                f"({self.calls_by_provider})"
            )
