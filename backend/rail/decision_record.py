"""Decision Records for AI Systems (DR-AIS) — immutable audit logging."""
import json
import time
import hashlib
import os
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "decision_logs"


def _ensure_log_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def record_decision(paper_id, action_type, model_used, prompt_text,
                    input_summary, output_summary, metadata=None):
    """Record an AI decision to the append-only audit log.

    Args:
        paper_id: The paper this decision relates to.
        action_type: Type of action (review, redteam, meta_review, revision, idea, etc.)
        model_used: LLM model identifier.
        prompt_text: The system prompt used.
        input_summary: Summary of input (truncated).
        output_summary: Summary of output (truncated).
        metadata: Optional dict of additional metadata.

    Returns:
        The decision record dict.
    """
    _ensure_log_dir()

    record = {
        "id": hashlib.sha256(f"{paper_id}{action_type}{time.time()}".encode()).hexdigest()[:16],
        "paper_id": paper_id,
        "action_type": action_type,
        "model_used": model_used,
        "prompt_hash": hashlib.sha256(prompt_text.encode()).hexdigest()[:16],
        "input_summary": input_summary[:1000],
        "output_summary": output_summary[:1000],
        "metadata": metadata or {},
        "timestamp": time.time(),
        "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Append to log file (one file per paper)
    safe_id = paper_id.replace(":", "_").replace(".", "_")
    log_file = LOG_DIR / f"{safe_id}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(record) + "\n")

    return record


def get_decisions(paper_id):
    """Retrieve all decision records for a paper.

    Returns list of decision record dicts.
    """
    _ensure_log_dir()
    safe_id = paper_id.replace(":", "_").replace(".", "_")
    log_file = LOG_DIR / f"{safe_id}.jsonl"

    if not log_file.exists():
        return []

    records = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def get_all_decisions(limit=100):
    """Retrieve recent decision records across all papers.

    Returns list of decision record dicts, most recent first.
    """
    _ensure_log_dir()
    all_records = []
    for log_file in LOG_DIR.glob("*.jsonl"):
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    all_records.sort(key=lambda r: r.get("timestamp", 0), reverse=True)
    return all_records[:limit]


def format_decision_log(records):
    """Format decision records into a human-readable audit log."""
    lines = ["# DR-AIS Decision Audit Log\n"]
    for r in records:
        lines.append(f"## [{r.get('id', '?')}] {r.get('action_type', '?')}")
        lines.append(f"**Paper:** {r.get('paper_id', '?')}")
        lines.append(f"**Model:** {r.get('model_used', '?')}")
        lines.append(f"**Time:** {r.get('iso_time', '?')}")
        lines.append(f"**Prompt Hash:** {r.get('prompt_hash', '?')}")
        lines.append(f"\n**Input:** {r.get('input_summary', '')[:200]}...")
        lines.append(f"**Output:** {r.get('output_summary', '')[:200]}...")
        lines.append("")
    return "\n".join(lines)
