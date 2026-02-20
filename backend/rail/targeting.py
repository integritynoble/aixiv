"""Targeting System — structured evaluation criteria for L0-L5 maturity assessment."""
from agents.base_agent import call_llm, parse_json_from_response, DEFAULT_MODEL

# L0-L5 maturity checklist criteria
MATURITY_CRITERIA = {
    "L0": {
        "name": "Ill-Posed",
        "description": "No agreement on objectives; data messy; decisions anecdotal",
        "checklist": [
            "Problem statement exists",
            "Research question is stated",
            "Some data or observations referenced",
        ],
        "threshold": "Any research attempt, however informal",
    },
    "L1": {
        "name": "Measurable",
        "description": "Agreed-upon metrics; basic leaderboards; AI acts as scorekeeper",
        "checklist": [
            "Clear quantitative metrics defined",
            "Baseline comparisons provided",
            "Evaluation protocol specified",
            "Results reported with numbers (not just qualitative)",
            "Dataset or benchmark identified",
        ],
        "threshold": "At least 3 of 5 checklist items satisfied",
    },
    "L2": {
        "name": "Repeatable",
        "description": "Standard operating procedures; consistent manual processes",
        "checklist": [
            "All hyperparameters specified",
            "Code availability stated or provided",
            "Data availability stated or provided",
            "Experimental setup fully described",
            "Results include error bars or confidence intervals",
            "Ablation study included",
        ],
        "threshold": "At least 4 of 6 checklist items satisfied",
    },
    "L3": {
        "name": "Automated",
        "description": "Checklists become code; AI executes majority of tasks",
        "checklist": [
            "Method is end-to-end automated",
            "No manual intervention required at inference",
            "Scalability demonstrated or analyzed",
            "Multiple datasets or domains tested",
            "Comparison with automated alternatives",
            "Runtime/efficiency analysis provided",
        ],
        "threshold": "At least 4 of 6 checklist items satisfied",
    },
    "L4": {
        "name": "Industrialized",
        "description": "Market buys outcomes not effort; AI permanently beats human methods",
        "checklist": [
            "Solution deployed or deployable in production",
            "Robustness to distribution shift demonstrated",
            "Failure modes characterized",
            "Cost/benefit analysis provided",
            "Comparison with commercial/industrial alternatives",
            "API or tool released for community use",
        ],
        "threshold": "At least 4 of 6 checklist items satisfied",
    },
    "L5": {
        "name": "Solved",
        "description": "Compute-bound; multiple providers compete on price",
        "checklist": [
            "Problem fully characterized mathematically",
            "Optimality proven or demonstrated empirically",
            "No known failure cases remain",
            "Multiple independent implementations exist",
            "Community consensus on solution",
            "Remaining improvements are purely computational",
        ],
        "threshold": "At least 5 of 6 checklist items satisfied",
    },
}

TARGETING_SYSTEM = """You are a Targeting System Evaluator. You assess papers against structured criteria
to determine their maturity level (L0-L5) according to the SolveEverything.org framework.

For each maturity level, evaluate the paper against a specific checklist. A paper achieves a level
if it satisfies the threshold number of checklist items for that level AND all lower levels.

Output JSON:
{
  "maturity_assessment": {
    "L0": {"satisfied": ["items satisfied"], "missing": ["items missing"], "passes": true/false},
    "L1": {"satisfied": ["items satisfied"], "missing": ["items missing"], "passes": true/false},
    "L2": {"satisfied": ["items satisfied"], "missing": ["items missing"], "passes": true/false},
    "L3": {"satisfied": ["items satisfied"], "missing": ["items missing"], "passes": true/false},
    "L4": {"satisfied": ["items satisfied"], "missing": ["items missing"], "passes": true/false},
    "L5": {"satisfied": ["items satisfied"], "missing": ["items missing"], "passes": true/false}
  },
  "current_level": "L0-L5",
  "next_level": "L1-L5 or null",
  "advancement_requirements": ["specific actions to reach next level"],
  "targeting_score": 0-100,
  "summary": "Assessment summary"
}"""


def assess_maturity(title, abstract, full_text="", model=None, api_key=None, api_provider=None):
    """Assess the maturity level of a paper using the targeting system.

    Returns (assessment_dict, raw_response).
    """
    criteria_text = ""
    for level, info in MATURITY_CRITERIA.items():
        criteria_text += f"\n### {level} — {info['name']}\n"
        criteria_text += f"{info['description']}\n"
        criteria_text += f"Threshold: {info['threshold']}\n"
        criteria_text += "Checklist:\n"
        for item in info["checklist"]:
            criteria_text += f"  - {item}\n"

    prompt = f"""Assess this paper against the L0-L5 maturity criteria.

## Paper
Title: {title}
Abstract: {abstract}
"""
    if full_text:
        prompt += f"\nFull Content:\n{full_text[:6000]}\n"

    prompt += f"""
## Maturity Criteria
{criteria_text}

Evaluate each checklist item. A paper's level is the highest level where it passes the threshold
AND passes all lower levels. Be specific about which items are satisfied and which are missing."""

    response = call_llm(TARGETING_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096, temperature=0.3,
                        api_key=api_key, api_provider=api_provider)

    parsed = parse_json_from_response(response)
    if parsed:
        return parsed, response

    return {
        "current_level": "L1",
        "targeting_score": 50,
        "summary": response[:500],
    }, response


def get_criteria():
    """Return the maturity criteria for display."""
    return MATURITY_CRITERIA
