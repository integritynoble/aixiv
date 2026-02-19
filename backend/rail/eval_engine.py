"""Evaluation Engine — 4-scenario evaluation protocol for the Rail."""
from agents.base_agent import call_llm, parse_json_from_response, DEFAULT_MODEL

EVAL_SYSTEM = """You are a Scientific Evaluation Engine. You assess papers under four scenarios
as defined by the Rail evaluation protocol.

For each scenario, evaluate whether the paper's claims and methods hold up:

## Scenario 1: Ideal Conditions
Does the method work under the paper's own assumptions?
- Are the theoretical claims valid?
- Do the experiments match the stated conditions?
- Are results consistent with the methodology?

## Scenario 2: Real-World Noise
Does the method degrade gracefully with realistic noise?
- Is noise modeling realistic?
- Are robustness experiments included?
- How sensitive is performance to noise levels?

## Scenario 3: Operator Mismatch
Does the method handle imperfect forward models?
- Is model mismatch acknowledged?
- Are calibration errors considered?
- How does performance change with model inaccuracy?

## Scenario 4: Adversarial Perturbation
Does the method survive adversarial attacks?
- Is adversarial robustness tested?
- Are failure modes under attack characterized?
- Are worst-case scenarios analyzed?

Output JSON:
{
  "scenarios": [
    {
      "name": "ideal",
      "score": 0-10,
      "assessment": "Detailed assessment",
      "evidence": "Evidence from paper",
      "gaps": ["What's missing"]
    },
    {
      "name": "noisy",
      "score": 0-10,
      "assessment": "...",
      "evidence": "...",
      "gaps": ["..."]
    },
    {
      "name": "mismatch",
      "score": 0-10,
      "assessment": "...",
      "evidence": "...",
      "gaps": ["..."]
    },
    {
      "name": "adversarial",
      "score": 0-10,
      "assessment": "...",
      "evidence": "...",
      "gaps": ["..."]
    }
  ],
  "overall_robustness": 0-10,
  "rail_compliant": true/false,
  "summary": "Overall assessment"
}"""


def evaluate_paper(title, abstract, full_text="", model=None):
    """Run 4-scenario evaluation on a paper.

    Returns (eval_dict, raw_response).
    """
    prompt = f"""Evaluate this paper under all four Rail evaluation scenarios.

## Paper
Title: {title}
Abstract: {abstract}
"""
    if full_text:
        prompt += f"\nFull Content:\n{full_text[:6000]}\n"

    prompt += """
Assess each scenario carefully. Score each 0-10 based on how well the paper addresses that scenario.
A paper is "Rail-compliant" if it scores >= 5 on all four scenarios."""

    response = call_llm(EVAL_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096, temperature=0.3)

    parsed = parse_json_from_response(response)
    if parsed:
        return parsed, response

    return {
        "scenarios": [],
        "overall_robustness": 5,
        "rail_compliant": False,
        "summary": response[:500],
    }, response


def format_eval_report(eval_dict):
    """Format evaluation results into a report."""
    lines = ["# Rail Evaluation Report\n"]
    lines.append(f"**Overall Robustness:** {eval_dict.get('overall_robustness', 'N/A')}/10")
    lines.append(f"**Rail Compliant:** {'Yes' if eval_dict.get('rail_compliant') else 'No'}")
    lines.append(f"\n{eval_dict.get('summary', '')}\n")

    scenario_names = {
        "ideal": "Scenario 1: Ideal Conditions",
        "noisy": "Scenario 2: Real-World Noise",
        "mismatch": "Scenario 3: Operator Mismatch",
        "adversarial": "Scenario 4: Adversarial Perturbation",
    }

    for s in eval_dict.get("scenarios", []):
        name = scenario_names.get(s.get("name", ""), s.get("name", "Unknown"))
        lines.append(f"## {name}")
        lines.append(f"**Score:** {s.get('score', '?')}/10\n")
        lines.append(f"{s.get('assessment', '')}\n")
        if s.get("gaps"):
            lines.append("**Gaps:**")
            for g in s["gaps"]:
                lines.append(f"- {g}")
            lines.append("")

    return "\n".join(lines)
