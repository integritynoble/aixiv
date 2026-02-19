"""Revision agent — AI-assisted paper revision based on review feedback."""
from .base_agent import call_llm, parse_json_from_response, DEFAULT_MODEL

REVISION_SYSTEM = """You are a Revision Assistant — you help authors revise their papers based on reviewer feedback.

Given a paper (or section) and reviewer feedback, you:
1. Analyze each reviewer concern systematically
2. Propose specific text changes to address each concern
3. Draft a revision letter explaining how each point was addressed
4. Improve the paper while maintaining the authors' voice and intent

For each revision suggestion, be specific:
- Quote the original text
- Provide the revised text
- Explain why this change addresses the reviewer's concern

Output JSON:
{
  "revision_suggestions": [
    {
      "id": "REV-001",
      "reviewer_concern": "What the reviewer flagged",
      "section": "Which section to modify",
      "original_text": "Current text (key phrase, not entire section)",
      "revised_text": "Proposed replacement text",
      "explanation": "How this addresses the concern",
      "priority": "required/suggested"
    }
  ],
  "new_content": [
    {
      "id": "NEW-001",
      "section": "Where to add",
      "content": "New text to insert",
      "reason": "Why this is needed"
    }
  ],
  "revision_letter": "Dear Reviewers,\\n\\nWe thank the reviewers for...\\n\\n[point-by-point response]"
}"""

REVISION_APPLY_SYSTEM = """You are a Paper Editor. Given a paper section and a list of revisions to apply,
produce the revised section text incorporating all changes seamlessly.

Maintain the academic style and voice of the original. Ensure the revised text flows naturally.
Output only the revised section text, nothing else."""


def generate_revisions(paper_text, review_feedback, redteam_feedback="",
                       meta_review="", model=None):
    """Generate revision suggestions based on all review feedback.

    Returns (revisions_dict, raw_response).
    """
    prompt = f"""Analyze the reviewer feedback and generate specific revision suggestions for this paper.

## Paper
{paper_text[:8000]}

## Peer Review Feedback
{review_feedback[:3000]}
"""
    if redteam_feedback:
        prompt += f"""
## Red Team Findings
{redteam_feedback[:3000]}
"""
    if meta_review:
        prompt += f"""
## Meta-Review Decision
{meta_review[:2000]}
"""
    prompt += """
Generate specific, actionable revision suggestions. Prioritize required changes over suggested ones.
Also draft a revision letter responding to each reviewer point."""

    response = call_llm(REVISION_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=6144)
    parsed = parse_json_from_response(response)
    if parsed:
        return parsed, response

    return {
        "revision_suggestions": [],
        "new_content": [],
        "revision_letter": response,
    }, response


def apply_revisions(section_text, revisions, model=None):
    """Apply a list of revisions to a paper section.

    Returns revised section text.
    """
    revisions_text = ""
    for rev in revisions:
        if isinstance(rev, dict):
            revisions_text += f"\n- Change: \"{rev.get('original_text', '')}\" → \"{rev.get('revised_text', '')}\"\n"
            revisions_text += f"  Reason: {rev.get('explanation', '')}\n"
        else:
            revisions_text += f"\n- {rev}\n"

    prompt = f"""Apply the following revisions to this paper section.

## Original Section
{section_text}

## Revisions to Apply
{revisions_text}

Produce the complete revised section, incorporating all changes smoothly."""

    response = call_llm(REVISION_APPLY_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096)
    return response


def generate_revision_letter(paper_title, review_feedback, changes_made, model=None):
    """Generate a formal revision letter (response to reviewers).

    Returns the revision letter text.
    """
    prompt = f"""Write a formal revision letter for the paper "{paper_title}".

## Reviewer Feedback Received
{review_feedback[:4000]}

## Changes Made
{changes_made[:4000]}

Write a professional, point-by-point response letter addressing each reviewer concern.
Format as:
- Reviewer concern (quoted or paraphrased)
- Our response and what was changed
- Where in the paper the change can be found"""

    response = call_llm(
        "You are a scientific writing assistant helping authors write revision letters.",
        [{"role": "user", "content": prompt}],
        model=model, max_tokens=4096,
    )
    return response
