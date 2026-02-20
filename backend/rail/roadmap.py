"""Roadmap generator — produces LLM-generated step-by-step advancement plans."""
from agents.base_agent import call_llm

ROADMAP_SYSTEM = """You are a Research Maturity Advisor for the SolveEverything.org L0-L5 framework.
Your task is to produce a concrete, actionable roadmap to advance a research paper from its current
maturity level to a target level.

Output format: Markdown with numbered steps. Each step must:
1. Reference a specific gap item (quote it)
2. Explain what concrete action to take
3. Give a brief example or suggestion where helpful

Keep steps focused, practical, and ordered by priority. Use clear section headers."""


def generate_roadmap(title, abstract, current_level, target_level,
                     gap_items, api_key=None, api_provider=None) -> str:
    """Generate an LLM roadmap to advance a paper from current_level to target_level.

    Args:
        title: Paper title
        abstract: Paper abstract
        current_level: Current maturity level (e.g. "L1")
        target_level: Target maturity level (e.g. "L4")
        gap_items: List of missing checklist items between current and target levels
        api_key: Optional per-user API key
        api_provider: Optional per-user API provider

    Returns:
        Markdown-formatted roadmap string
    """
    if not gap_items:
        return f"## Roadmap: {current_level} → {target_level}\n\nNo gap items identified. The paper may already satisfy the requirements for {target_level}."

    gap_text = "\n".join(f"- {item}" for item in gap_items)

    prompt = f"""## Paper
**Title:** {title}
**Abstract:** {abstract[:1500]}

## Advancement Goal
Current level: **{current_level}**
Target level: **{target_level}**

## Gap Items to Address
These checklist items are currently missing and must be satisfied to reach {target_level}:
{gap_text}

## Instructions
Write a numbered step-by-step roadmap for the authors to advance this paper from {current_level} to {target_level}.
For each step:
1. Quote or reference the specific gap item it addresses
2. Explain the concrete action needed
3. Give a practical example or suggestion where helpful

Order steps from highest priority / lowest effort to lowest priority / highest effort.
Keep the total roadmap under 600 words."""

    roadmap = call_llm(ROADMAP_SYSTEM, [{"role": "user", "content": prompt}],
                       max_tokens=2048, temperature=0.4,
                       api_key=api_key, api_provider=api_provider)

    # Ensure it starts with a header
    if not roadmap.strip().startswith("#"):
        roadmap = f"## Roadmap: {current_level} → {target_level}\n\n" + roadmap

    return roadmap
