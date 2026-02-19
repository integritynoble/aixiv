"""Red Team agent — adversarial analysis to find flaws in papers."""
from .base_agent import call_llm, parse_json_from_response, STRONG_MODEL

REDTEAM_SYSTEM = """You are a Red Team Analyst for scientific papers. Your job is adversarial:
find every flaw, gap, unsupported claim, and vulnerability in a paper.

You think like an attacker trying to invalidate the paper's conclusions. You check:

## 1. Logical Integrity
- Are there logical fallacies or circular reasoning?
- Do conclusions follow from the evidence presented?
- Are there unstated assumptions that could be wrong?

## 2. Statistical Validity
- Is there evidence of p-hacking or cherry-picking results?
- Are sample sizes adequate?
- Are error bars / confidence intervals reported?
- Is the comparison with baselines fair?

## 3. Reproducibility Gaps
- Are all hyperparameters specified?
- Is the code / data available?
- Could an independent team replicate this?
- Are there hidden dependencies on proprietary tools?

## 4. Adversarial Scenarios
- What if the main assumption fails?
- What are the edge cases where the method breaks?
- How sensitive is the method to its parameters?
- What distribution shifts would degrade performance?

## 5. Overclaiming
- Do the claims match the evidence?
- Are limitations honestly addressed?
- Is the novelty claim overstated?
- Are there hidden caveats in the results?

## Output Format (JSON)
{
  "findings": [
    {
      "id": "RT-001",
      "severity": "critical/major/minor/suggestion",
      "category": "logical/statistical/reproducibility/adversarial/overclaiming",
      "title": "Short description",
      "description": "Detailed explanation of the issue",
      "evidence": "What in the paper supports this finding",
      "recommendation": "How to fix it"
    }
  ],
  "overall_risk": "high/medium/low",
  "confidence_in_conclusions": 0.0-1.0,
  "summary": "2-3 sentence executive summary",
  "attack_scenarios": [
    {
      "scenario": "Description of attack/failure mode",
      "likelihood": "high/medium/low",
      "impact": "What would happen"
    }
  ]
}

Be thorough and adversarial but honest. Only flag real issues, not speculative ones.
Every finding must cite specific evidence from the paper."""


def redteam_paper(title, abstract, full_text="", model=None):
    """Run red team analysis on a paper.

    Returns (findings_dict, raw_response).
    """
    prompt = f"""Perform a red team analysis on the following paper. Find every flaw and vulnerability.

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
Be thorough. Check for logical issues, statistical problems, reproducibility gaps,
adversarial scenarios, and overclaiming. Output as JSON."""

    use_model = model or STRONG_MODEL
    response = call_llm(REDTEAM_SYSTEM, [{"role": "user", "content": prompt}],
                        model=use_model, max_tokens=6144, temperature=0.3)

    parsed = parse_json_from_response(response)
    if parsed:
        return parsed, response

    # Fallback
    return {
        "findings": [],
        "overall_risk": "medium",
        "confidence_in_conclusions": 0.5,
        "summary": response[:500],
        "attack_scenarios": [],
    }, response


def severity_counts(findings_dict):
    """Count findings by severity."""
    counts = {"critical": 0, "major": 0, "minor": 0, "suggestion": 0}
    for f in findings_dict.get("findings", []):
        sev = f.get("severity", "minor")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def format_redteam_report(findings_dict):
    """Format red team findings into a human-readable report."""
    lines = ["# Red Team Analysis Report\n"]
    lines.append(f"**Overall Risk:** {findings_dict.get('overall_risk', 'N/A')}")
    lines.append(f"**Confidence in Conclusions:** {findings_dict.get('confidence_in_conclusions', 'N/A')}")
    lines.append(f"\n**Summary:** {findings_dict.get('summary', '')}\n")

    counts = severity_counts(findings_dict)
    lines.append(f"**Findings:** {counts['critical']} Critical, {counts['major']} Major, "
                 f"{counts['minor']} Minor, {counts['suggestion']} Suggestions\n")

    lines.append("---\n")

    for f in findings_dict.get("findings", []):
        sev = f.get("severity", "minor").upper()
        lines.append(f"### [{sev}] {f.get('id', 'RT-???')}: {f.get('title', 'Untitled')}")
        lines.append(f"**Category:** {f.get('category', 'N/A')}")
        lines.append(f"\n{f.get('description', '')}")
        if f.get("evidence"):
            lines.append(f"\n**Evidence:** {f['evidence']}")
        if f.get("recommendation"):
            lines.append(f"\n**Recommendation:** {f['recommendation']}")
        lines.append("")

    if findings_dict.get("attack_scenarios"):
        lines.append("---\n## Attack Scenarios\n")
        for s in findings_dict["attack_scenarios"]:
            lines.append(f"- **{s.get('scenario', '')}** (Likelihood: {s.get('likelihood', '?')}, "
                         f"Impact: {s.get('impact', '?')})")

    return "\n".join(lines)
