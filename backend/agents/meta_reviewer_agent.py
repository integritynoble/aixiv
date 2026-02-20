"""Meta-reviewer agent — synthesizes reviews and makes final recommendation."""
from .base_agent import call_llm, parse_json_from_response, STRONG_MODEL

META_REVIEWER_SYSTEM = """You are a Meta-Reviewer (Area Chair) for a scientific publication venue.

You synthesize multiple reviews of a paper and make a final recommendation. You have access to:
1. The peer review (Layer 1-3 evaluation)
2. The red team analysis (adversarial findings)

Your job is to:
1. Weigh all reviews fairly, considering the strength of each argument
2. Identify consensus points and disagreements
3. Produce a consolidated set of required and suggested changes
4. Make a final recommendation with clear justification
5. Assign an overall maturity level (L0-L5)

## Output Format (JSON)
{
  "final_recommendation": "accept/minor_revision/major_revision/reject",
  "confidence": 0.0-1.0,
  "justification": "Detailed explanation of the recommendation",
  "maturity_level": "L0-L5",
  "required_changes": [
    {
      "id": "RC-001",
      "description": "What must be changed",
      "priority": "high/medium",
      "source": "peer_review/redteam"
    }
  ],
  "suggested_changes": [
    {
      "id": "SC-001",
      "description": "What could be improved",
      "priority": "medium/low",
      "source": "peer_review/redteam"
    }
  ],
  "strengths_consensus": ["agreed strengths across reviews"],
  "concerns_consensus": ["agreed concerns across reviews"],
  "summary_for_authors": "Paragraph summarizing what authors should do next",
  "arena_eligible": true/false,
  "arena_eligibility_reason": "Why/why not eligible for Arena promotion"
}"""


def meta_review(title, abstract, peer_review, redteam_report, model=None, api_key=None, api_provider=None):
    """Synthesize peer review and red team report into a meta-review.

    Returns (meta_review_dict, raw_response).
    """
    # Format peer review
    if isinstance(peer_review, dict):
        import json
        peer_text = json.dumps(peer_review, indent=2)
    else:
        peer_text = str(peer_review)

    # Format red team
    if isinstance(redteam_report, dict):
        import json
        redteam_text = json.dumps(redteam_report, indent=2)
    else:
        redteam_text = str(redteam_report)

    prompt = f"""Synthesize the following reviews and make a final recommendation for this paper.

## Paper
Title: {title}
Abstract: {abstract}

## Peer Review (Layer 1-3)
{peer_text}

## Red Team Analysis
{redteam_text}

Weigh all evidence, resolve any contradictions, and produce your final meta-review as JSON."""

    use_model = model or STRONG_MODEL
    response = call_llm(META_REVIEWER_SYSTEM, [{"role": "user", "content": prompt}],
                        model=use_model, max_tokens=4096, temperature=0.3,
                        api_key=api_key, api_provider=api_provider)

    parsed = parse_json_from_response(response)
    if parsed:
        return parsed, response

    return {
        "final_recommendation": "minor_revision",
        "confidence": 0.5,
        "justification": response[:500],
        "maturity_level": "L1",
        "required_changes": [],
        "suggested_changes": [],
        "strengths_consensus": [],
        "concerns_consensus": [],
        "summary_for_authors": response,
        "arena_eligible": False,
        "arena_eligibility_reason": "Needs review completion",
    }, response


def format_meta_review(meta_dict):
    """Format meta-review into human-readable text."""
    lines = ["# Meta-Review Decision\n"]
    lines.append(f"**Recommendation:** {meta_dict.get('final_recommendation', 'N/A').replace('_', ' ').title()}")
    lines.append(f"**Confidence:** {meta_dict.get('confidence', 'N/A')}")
    lines.append(f"**Maturity Level:** {meta_dict.get('maturity_level', 'N/A')}")
    lines.append(f"**Arena Eligible:** {'Yes' if meta_dict.get('arena_eligible') else 'No'}")
    if meta_dict.get("arena_eligibility_reason"):
        lines.append(f"  *Reason:* {meta_dict['arena_eligibility_reason']}")
    lines.append(f"\n**Justification:**\n{meta_dict.get('justification', '')}\n")

    if meta_dict.get("strengths_consensus"):
        lines.append("## Agreed Strengths")
        for s in meta_dict["strengths_consensus"]:
            lines.append(f"- {s}")
        lines.append("")

    if meta_dict.get("concerns_consensus"):
        lines.append("## Agreed Concerns")
        for c in meta_dict["concerns_consensus"]:
            lines.append(f"- {c}")
        lines.append("")

    if meta_dict.get("required_changes"):
        lines.append("## Required Changes")
        for rc in meta_dict["required_changes"]:
            lines.append(f"- **[{rc.get('id', '?')}]** ({rc.get('priority', '?')}): {rc.get('description', '')}")
        lines.append("")

    if meta_dict.get("suggested_changes"):
        lines.append("## Suggested Changes")
        for sc in meta_dict["suggested_changes"]:
            lines.append(f"- **[{sc.get('id', '?')}]** ({sc.get('priority', '?')}): {sc.get('description', '')}")
        lines.append("")

    if meta_dict.get("summary_for_authors"):
        lines.append(f"## Summary for Authors\n{meta_dict['summary_for_authors']}")

    return "\n".join(lines)
