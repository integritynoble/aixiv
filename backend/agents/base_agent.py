"""Base agent with shared LLM calling, streaming, error handling, and retry logic.

Supports two backends:
  1. CompareGPT (preferred) — OpenAI-compatible API gateway at comparegpt.io/api
     Set COMPAREGPT_API_KEY and optionally COMPAREGPT_BASE_URL
  2. Anthropic (fallback) — Direct Claude API
     Set ANTHROPIC_API_KEY

Per-user API keys:
  All LLM-calling functions accept optional api_key/api_provider params.
  When provided, a per-request client is created instead of using the global singleton.
"""
import os
import json
import time
import hashlib
import logging

logger = logging.getLogger("ai_scientist")

_client = None
_backend = None  # "comparegpt" or "anthropic"

# CompareGPT defaults (Gemini via CompareGPT gateway)
COMPAREGPT_BASE_URL = "https://comparegpt.io/api"
CG_DEFAULT_MODEL = "gemini-2.5-flash"
CG_STRONG_MODEL = "gemini-2.5-pro"

# Anthropic defaults (direct)
ANTHRO_DEFAULT_MODEL = "claude-sonnet-4-20250514"
ANTHRO_STRONG_MODEL = "claude-opus-4-20250514"

# OpenAI defaults
OAI_DEFAULT_MODEL = "gpt-4o"
OAI_STRONG_MODEL = "gpt-4o"

DEFAULT_MODEL = None  # set by get_client()
STRONG_MODEL = None

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds
RETRY_MAX_DELAY = 30  # seconds
LLM_TIMEOUT = 120  # seconds


def get_client():
    """Initialize the global LLM client. Prefers CompareGPT, falls back to Anthropic."""
    global _client, _backend, DEFAULT_MODEL, STRONG_MODEL

    if _client is not None:
        return _client

    cg_key = os.environ.get("COMPAREGPT_API_KEY", "")
    anthro_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if cg_key:
        from openai import OpenAI
        base_url = os.environ.get("COMPAREGPT_BASE_URL", COMPAREGPT_BASE_URL)
        _client = OpenAI(api_key=cg_key, base_url=base_url, timeout=LLM_TIMEOUT)
        _backend = "comparegpt"
        DEFAULT_MODEL = os.environ.get("LLM_DEFAULT_MODEL", CG_DEFAULT_MODEL)
        STRONG_MODEL = os.environ.get("LLM_STRONG_MODEL", CG_STRONG_MODEL)
        logger.info(f"Using CompareGPT backend ({base_url}), default model: {DEFAULT_MODEL}")
    elif anthro_key:
        from anthropic import Anthropic
        _client = Anthropic(api_key=anthro_key, timeout=LLM_TIMEOUT)
        _backend = "anthropic"
        DEFAULT_MODEL = os.environ.get("LLM_DEFAULT_MODEL", ANTHRO_DEFAULT_MODEL)
        STRONG_MODEL = os.environ.get("LLM_STRONG_MODEL", ANTHRO_STRONG_MODEL)
        logger.info(f"Using Anthropic backend, default model: {DEFAULT_MODEL}")
    else:
        raise ValueError(
            "No LLM API key found. Set COMPAREGPT_API_KEY or ANTHROPIC_API_KEY."
        )

    return _client


def _make_client(api_key, provider):
    """Create a per-user LLM client for a given API key and provider.

    Returns (client, backend_name, default_model, strong_model).
    """
    if not api_key:
        # Fall back to global
        c = get_client()
        return c, _backend, DEFAULT_MODEL, STRONG_MODEL

    provider = (provider or "comparegpt").lower()

    if provider == "anthropic":
        from anthropic import Anthropic
        c = Anthropic(api_key=api_key, timeout=LLM_TIMEOUT)
        return c, "anthropic", ANTHRO_DEFAULT_MODEL, ANTHRO_STRONG_MODEL
    elif provider == "openai":
        from openai import OpenAI
        c = OpenAI(api_key=api_key, timeout=LLM_TIMEOUT)
        return c, "openai", OAI_DEFAULT_MODEL, OAI_STRONG_MODEL
    else:
        # comparegpt — OpenAI-compatible
        from openai import OpenAI
        base_url = os.environ.get("COMPAREGPT_BASE_URL", COMPAREGPT_BASE_URL)
        c = OpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT)
        return c, "comparegpt", CG_DEFAULT_MODEL, CG_STRONG_MODEL


def _retry_with_backoff(fn, max_retries=MAX_RETRIES):
    """Execute fn with exponential backoff retry on transient errors."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            # Determine if retryable
            retryable = False
            err_name = type(e).__name__

            # OpenAI SDK errors
            if err_name in ("RateLimitError", "APITimeoutError", "APIConnectionError"):
                retryable = True
            elif err_name == "APIStatusError" and hasattr(e, "status_code") and e.status_code >= 500:
                retryable = True
            # httpx / network errors
            elif err_name in ("ConnectError", "ReadTimeout", "ConnectTimeout"):
                retryable = True

            if retryable:
                last_error = e
                delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                logger.warning(f"{err_name} (attempt {attempt+1}/{max_retries+1}), retrying in {delay}s")
                time.sleep(delay)
            else:
                raise
    raise last_error


def call_llm(system_prompt, messages, model=None, max_tokens=4096, temperature=0.7,
             api_key=None, api_provider=None):
    """Call LLM with retry logic and return the text response.

    When api_key/api_provider are provided, uses a per-user client instead of the global one.
    """
    if api_key:
        c, backend, def_model, _ = _make_client(api_key, api_provider)
    else:
        c = get_client()
        backend = _backend
        def_model = DEFAULT_MODEL

    use_model = model or def_model

    if backend in ("comparegpt", "openai"):
        def _call():
            oai_messages = [{"role": "system", "content": system_prompt}]
            oai_messages.extend(messages)
            response = c.chat.completions.create(
                model=use_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=oai_messages,
            )
            return response.choices[0].message.content
        return _retry_with_backoff(_call)
    else:
        # Anthropic backend
        def _call():
            response = c.messages.create(
                model=use_model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=messages,
            )
            return response.content[0].text
        return _retry_with_backoff(_call)


def call_llm_stream(system_prompt, messages, model=None, max_tokens=4096, temperature=0.7,
                    api_key=None, api_provider=None):
    """Call LLM with streaming and retry on initial connection, yielding text chunks."""
    if api_key:
        c, backend, def_model, _ = _make_client(api_key, api_provider)
    else:
        c = get_client()
        backend = _backend
        def_model = DEFAULT_MODEL

    use_model = model or def_model

    if backend in ("comparegpt", "openai"):
        def _create_stream():
            oai_messages = [{"role": "system", "content": system_prompt}]
            oai_messages.extend(messages)
            return c.chat.completions.create(
                model=use_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=oai_messages,
                stream=True,
            )
        stream = _retry_with_backoff(_create_stream)
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    else:
        # Anthropic backend
        def _create_stream():
            return c.messages.stream(
                model=use_model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=messages,
            )
        stream_ctx = _retry_with_backoff(_create_stream)
        with stream_ctx as stream:
            for text in stream.text_stream:
                yield text


def multi_turn(system_prompt, turns, model=None, max_tokens=4096, temperature=0.7,
               api_key=None, api_provider=None):
    """Run a multi-turn conversation. `turns` is a list of user messages.
    Returns the list of all messages and the final assistant reply."""
    messages = []
    reply = ""
    for user_msg in turns:
        messages.append({"role": "user", "content": user_msg})
        reply = call_llm(system_prompt, messages, model=model,
                         max_tokens=max_tokens, temperature=temperature,
                         api_key=api_key, api_provider=api_provider)
        messages.append({"role": "assistant", "content": reply})
    return messages, reply


def parse_json_from_response(text):
    """Extract JSON from an LLM response that may contain markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # skip ```json
        end = next((i for i, l in enumerate(lines) if l.strip() == "```"), len(lines))
        text = "\n".join(lines[:end])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    return None


def make_decision_record(paper_id, action_type, model, prompt_text, input_text, output_text):
    """Create a decision record dict for DR-AIS audit logging."""
    return {
        "paper_id": paper_id,
        "action_type": action_type,
        "model_used": model or DEFAULT_MODEL,
        "prompt_hash": hashlib.sha256(prompt_text.encode()).hexdigest()[:16],
        "input_summary": input_text[:500] if input_text else "",
        "output_summary": output_text[:500] if output_text else "",
        "timestamp": time.time(),
    }
