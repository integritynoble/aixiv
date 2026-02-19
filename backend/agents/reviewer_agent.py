"""Three-layer peer review agent grounded in SolveEverything.org framework."""
from .base_agent import call_llm, parse_json_from_response, STRONG_MODEL, DEFAULT_MODEL

REVIEWER_SYSTEM = """You are an AI Scientist Reviewer — a rigorous, fair, and constructive peer reviewer.

You perform a THREE-LAYER evaluation following the SolveEverything.org framework.

## Layer 1: Standard Peer Review
Score each dimension 1-5:
- Soundness: Are claims supported by evidence?
- Novelty: Does this contribute new knowledge?
- Clarity: Is the writing clear and well-organized?
- Significance: How important is this work?
- Reproducibility: Could someone replicate this?

## Layer 2: L0-L5 Maturity Assessment (SolveEverything.org)
Determine which maturity level the paper achieves:
- L0 (Ill-Posed): Objectives undefined, no metrics, vague claims
- L1 (Measurable): Clear metrics and baselines established
- L2 (Repeatable): Results reproducible by independent teams
- L3 (Automated): Approach is systematic, scalable, automatable
- L4 (Industrialized): Advances field toward commodity solutions
- L5 (Solved): Definitively resolves the problem, compute-bound only

For each level, explain what evidence the paper provides (or lacks).

## Layer 3: Domain-Specific Gate Analysis
For computational imaging papers, apply the Triad Law Gates:
- Gate 1 (Recoverability): Is the measurement information-sufficient?
- Gate 2 (Carrier Budget): Is the SNR adequate?
- Gate 3 (Operator Mismatch): Is the forward model accurate?

For other domains, assess domain-specific technical validity.

## Output Format (JSON)
{
  "layer1_peer_review": {
    "soundness": {"score": 1-5, "justification": "..."},
    "novelty": {"score": 1-5, "justification": "..."},
    "clarity": {"score": 1-5, "justification": "..."},
    "significance": {"score": 1-5, "justification": "..."},
    "reproducibility": {"score": 1-5, "justification": "..."},
    "strengths": ["..."],
    "weaknesses": ["..."],
    "questions": ["..."]
  },
  "layer2_maturity": {
    "current_level": "L0-L5",
    "level_justification": "...",
    "evidence_by_level": {
      "L0": "what's defined/undefined",
      "L1": "what metrics exist/missing",
      "L2": "reproducibility assessment",
      "L3": "automation/scalability assessment",
      "L4": "industrialization assessment",
      "L5": "completeness assessment"
    },
    "next_level_requirements": ["what's needed to advance"]
  },
  "layer3_domain_gates": {
    "applicable": true/false,
    "domain": "computational_imaging/other",
    "gates": [
      {"name": "Gate name", "status": "pass/fail/partial", "assessment": "..."}
    ]
  },
  "overall_score": 1-10,
  "recommendation": "accept/minor_revision/major_revision/reject",
  "detailed_feedback": "paragraph of constructive feedback",
  "summary": "2-3 sentence summary"
}

Be rigorous but constructive. The goal is to help authors improve their work."""


def review_paper(title, abstract, full_text="", model=None):
    """Run full three-layer review on a paper.

    Returns (parsed_review_dict, raw_response).
    """
    prompt = f"""Please review the following scientific paper submission using your three-layer evaluation framework.

## Title
{title}

## Abstract
{abstract}
"""
    if full_text:
        prompt += f"""
## Full Paper Content
{full_text}
"""
    prompt += """
Provide a comprehensive three-layer review:
1. Standard peer review with scores
2. L0-L5 maturity assessment
3. Domain-specific gate analysis (if applicable)

Output your review as a JSON object following your output format specification."""

    use_model = model or STRONG_MODEL
    response = call_llm(REVIEWER_SYSTEM, [{"role": "user", "content": prompt}],
                        model=use_model, max_tokens=6144, temperature=0.3)

    parsed = parse_json_from_response(response)
    if parsed:
        return parsed, response

    # Fallback: return structured dict from text parsing
    return _parse_review_fallback(response), response


def _parse_review_fallback(text):
    """Parse review from free-text if JSON parsing fails."""
    result = {
        "layer1_peer_review": {
            "soundness": {"score": 3, "justification": ""},
            "novelty": {"score": 3, "justification": ""},
            "clarity": {"score": 3, "justification": ""},
            "significance": {"score": 3, "justification": ""},
            "reproducibility": {"score": 3, "justification": ""},
            "strengths": [],
            "weaknesses": [],
            "questions": [],
        },
        "layer2_maturity": {
            "current_level": "L1",
            "level_justification": "",
            "next_level_requirements": [],
        },
        "layer3_domain_gates": {"applicable": False, "gates": []},
        "overall_score": 6,
        "recommendation": "minor_revision",
        "detailed_feedback": text,
        "summary": text[:300],
    }

    # Try to extract scores from text
    import re
    for dim in ["soundness", "novelty", "clarity", "significance", "reproducibility"]:
        pattern = rf'{dim}[:\s]*(\d)[/\s]*5'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            result["layer1_peer_review"][dim]["score"] = int(match.group(1))

    # Extract recommendation
    lower = text.lower()
    if "reject" in lower:
        result["recommendation"] = "reject"
    elif "major revision" in lower:
        result["recommendation"] = "major_revision"
    elif "minor revision" in lower:
        result["recommendation"] = "minor_revision"
    elif "accept" in lower:
        result["recommendation"] = "accept"

    # Extract maturity level
    for level in ["L5", "L4", "L3", "L2", "L1", "L0"]:
        if level in text:
            result["layer2_maturity"]["current_level"] = level
            break

    # Calculate overall score
    scores = [result["layer1_peer_review"][d]["score"]
              for d in ["soundness", "novelty", "clarity", "significance", "reproducibility"]]
    result["overall_score"] = round(sum(scores) / len(scores) * 2)

    return result


def extract_flat_scores(review_dict):
    """Extract flat score dict from nested review for database storage."""
    l1 = review_dict.get("layer1_peer_review", {})
    return {
        "soundness": l1.get("soundness", {}).get("score", 3) if isinstance(l1.get("soundness"), dict) else l1.get("soundness", 3),
        "novelty": l1.get("novelty", {}).get("score", 3) if isinstance(l1.get("novelty"), dict) else l1.get("novelty", 3),
        "clarity": l1.get("clarity", {}).get("score", 3) if isinstance(l1.get("clarity"), dict) else l1.get("clarity", 3),
        "significance": l1.get("significance", {}).get("score", 3) if isinstance(l1.get("significance"), dict) else l1.get("significance", 3),
        "reproducibility": l1.get("reproducibility", {}).get("score", 3) if isinstance(l1.get("reproducibility"), dict) else l1.get("reproducibility", 3),
        "overall_score": review_dict.get("overall_score", 6),
        "recommendation": review_dict.get("recommendation", "minor_revision"),
        "summary": review_dict.get("summary", ""),
        "strengths": "\n".join(l1.get("strengths", [])) if isinstance(l1.get("strengths"), list) else l1.get("strengths", ""),
        "weaknesses": "\n".join(l1.get("weaknesses", [])) if isinstance(l1.get("weaknesses"), list) else l1.get("weaknesses", ""),
        "questions": "\n".join(l1.get("questions", [])) if isinstance(l1.get("questions"), list) else l1.get("questions", ""),
        "detailed_feedback": review_dict.get("detailed_feedback", ""),
        "maturity_level": review_dict.get("layer2_maturity", {}).get("current_level", "L0"),
        "gate_analysis": str(review_dict.get("layer3_domain_gates", {})),
    }
