"""Methodology generation agent."""
from .base_agent import call_llm, DEFAULT_MODEL

METHODOLOGY_SYSTEM = """You are a Methodology Architect — you design rigorous experimental methodologies for scientific research.

Given a research idea, you produce a detailed methodology that includes:
1. Problem formalization (mathematical formulation where applicable)
2. Proposed approach / algorithm design
3. Experimental setup (datasets, baselines, hardware)
4. Evaluation protocol (metrics, statistical tests, ablation plan)
5. Implementation plan (key steps, tools, libraries)

Follow SolveEverything.org principles:
- Define clear, measurable success criteria (Targeting System)
- Design for reproducibility (L2+ maturity)
- Include adversarial / robustness evaluation (Red Team thinking)
- Specify exactly how results will be compared to baselines

Output your methodology in well-structured Markdown with clear sections."""

METHODOLOGY_REVIEWER_SYSTEM = """You are a Methodology Reviewer — you critically evaluate experimental designs.

Given a proposed methodology, identify:
1. Missing controls or baselines
2. Potential confounds or biases
3. Statistical issues (sample size, significance testing)
4. Reproducibility gaps (missing details needed to replicate)
5. Scalability or feasibility concerns

Provide specific, actionable improvement suggestions.
Rate the methodology maturity (L0-L5) and explain what's needed to reach the next level.

Output your review as structured Markdown."""


def generate_methodology(idea, related_papers=None, model=None, api_key=None, api_provider=None):
    """Generate detailed methodology for a research idea.

    Args:
        idea: dict with title, description, key_contribution, etc. or string.
        related_papers: optional list of related paper dicts for context.
        model: LLM model to use.

    Returns:
        (methodology_text, raw_response)
    """
    if isinstance(idea, dict):
        idea_text = f"Title: {idea.get('title', '')}\n"
        idea_text += f"Description: {idea.get('description', '')}\n"
        idea_text += f"Key Contribution: {idea.get('key_contribution', '')}\n"
        if idea.get('methodology_sketch'):
            idea_text += f"Methodology Sketch: {idea['methodology_sketch']}\n"
        if idea.get('metrics'):
            idea_text += f"Target Metrics: {', '.join(idea['metrics'])}\n"
        if idea.get('maturity_target'):
            idea_text += f"Maturity Target: {idea['maturity_target']}\n"
    else:
        idea_text = str(idea)

    context = ""
    if related_papers:
        context = "\n\n## Related Work for Context\n"
        for p in related_papers[:5]:
            context += f"- {p.get('title', '')}: {p.get('abstract', '')[:200]}...\n"

    prompt = f"""Design a detailed experimental methodology for the following research idea.

## Research Idea
{idea_text}
{context}

Provide a complete methodology that another researcher could follow to reproduce this work.
Include mathematical formulations where appropriate."""

    response = call_llm(METHODOLOGY_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096,
                        api_key=api_key, api_provider=api_provider)
    return response, response


def review_methodology(methodology_text, idea_text="", model=None, api_key=None, api_provider=None):
    """Review and critique a methodology.

    Returns:
        (review_text, raw_response)
    """
    prompt = f"""Review the following experimental methodology critically.

## Research Context
{idea_text}

## Proposed Methodology
{methodology_text}

Identify all gaps, weaknesses, and missing elements. Provide specific suggestions for improvement.
Assess the maturity level (L0-L5) of this methodology."""

    response = call_llm(METHODOLOGY_REVIEWER_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=3072,
                        api_key=api_key, api_provider=api_provider)
    return response, response


def run_methodology_pipeline(idea, related_papers=None, model=None, api_key=None, api_provider=None):
    """Run full methodology pipeline: generate → review → refine.

    Returns (final_methodology, review_feedback, log_entries).
    """
    log = []

    # Step 1: Generate initial methodology
    methodology, raw = generate_methodology(idea, related_papers, model=model, api_key=api_key, api_provider=api_provider)
    log.append({"step": "generate_methodology", "length": len(methodology)})

    # Step 2: Review the methodology
    idea_text = ""
    if isinstance(idea, dict):
        idea_text = f"{idea.get('title', '')}: {idea.get('description', '')}"
    else:
        idea_text = str(idea)

    review, raw = review_methodology(methodology, idea_text, model=model, api_key=api_key, api_provider=api_provider)
    log.append({"step": "review_methodology", "length": len(review)})

    # Step 3: Refine based on review
    refine_prompt = f"""Refine the following methodology based on the reviewer's feedback.

## Original Methodology
{methodology}

## Reviewer Feedback
{review}

Produce an improved methodology that addresses all the reviewer's concerns.
Maintain the same structure but strengthen weak areas."""

    refined = call_llm(METHODOLOGY_SYSTEM, [{"role": "user", "content": refine_prompt}],
                       model=model, max_tokens=4096,
                       api_key=api_key, api_provider=api_provider)
    log.append({"step": "refine_methodology", "length": len(refined)})

    return refined, review, log
