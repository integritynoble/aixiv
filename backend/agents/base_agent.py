"""Base agent with shared LLM calling, streaming, and error handling."""
import os
import json
import time
import hashlib
from anthropic import Anthropic

_client = None

DEFAULT_MODEL = "claude-sonnet-4-20250514"
STRONG_MODEL = "claude-opus-4-20250514"


def get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        _client = Anthropic(api_key=api_key)
    return _client


def call_llm(system_prompt, messages, model=None, max_tokens=4096, temperature=0.7):
    """Call Claude API and return the text response."""
    c = get_client()
    response = c.messages.create(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=messages,
    )
    return response.content[0].text


def call_llm_stream(system_prompt, messages, model=None, max_tokens=4096, temperature=0.7):
    """Call Claude API with streaming, yielding text chunks."""
    c = get_client()
    with c.messages.stream(
        model=model or DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def multi_turn(system_prompt, turns, model=None, max_tokens=4096, temperature=0.7):
    """Run a multi-turn conversation. `turns` is a list of user messages.
    Returns the list of all messages and the final assistant reply."""
    messages = []
    reply = ""
    for user_msg in turns:
        messages.append({"role": "user", "content": user_msg})
        reply = call_llm(system_prompt, messages, model=model,
                         max_tokens=max_tokens, temperature=temperature)
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
