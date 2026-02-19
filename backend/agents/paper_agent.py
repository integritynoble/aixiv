"""Paper composition agent — section-by-section paper writing."""
from .base_agent import call_llm, DEFAULT_MODEL

COMPOSER_SYSTEM = """You are a Scientific Paper Composer — you write high-quality scientific papers section by section.

You follow the SolveEverything.org framework:
- Claims must be precise, measurable, and supported by evidence
- Methods must be reproducible (target L2+ maturity)
- Results must include proper baselines and statistical comparisons
- Discussion must honestly address limitations

Write in clear, concise academic prose. Use LaTeX notation for math when appropriate.
When writing a specific section, you have access to all prior sections for context and consistency.

Always maintain a coherent narrative thread throughout the paper."""

SECTION_PROMPTS = {
    "abstract": """Write the Abstract for this paper.

The abstract should be 150-250 words and include:
1. Problem statement (1-2 sentences)
2. Key insight or approach (1-2 sentences)
3. Main results with specific numbers (2-3 sentences)
4. Significance/impact (1 sentence)""",

    "introduction": """Write the Introduction for this paper.

The introduction should:
1. Establish the problem and its importance (1-2 paragraphs)
2. Describe limitations of existing approaches (1 paragraph)
3. State the paper's contribution clearly (1 paragraph, use bullet points for multiple contributions)
4. Outline the paper structure (1 sentence)

Include citations to related work where appropriate (use [Author, Year] format as placeholders).""",

    "related_work": """Write the Related Work section for this paper.

Organize related work into logical categories. For each category:
1. Summarize the main approaches
2. Identify the gap that this work addresses
3. Clearly differentiate this paper from prior art

Use [Author, Year] format for citation placeholders.""",

    "methods": """Write the Methods section for this paper.

The methods section should:
1. Present the problem formalization with mathematical notation
2. Describe the proposed approach step by step
3. Include algorithm pseudocode if applicable
4. Explain design choices and their justification
5. Specify all hyperparameters and settings for reproducibility""",

    "experiments": """Write the Experiments section for this paper.

Include:
1. Experimental setup (datasets, baselines, metrics, hardware)
2. Main results with tables/figures described in text
3. Comparison with baselines (quantitative)
4. Ablation studies
5. Statistical significance or error bars where applicable

Describe tables and figures in text even if they don't exist yet.""",

    "discussion": """Write the Discussion section for this paper.

Address:
1. Key findings and their implications
2. Why the proposed method works (analysis)
3. Limitations and failure cases (be honest)
4. Broader impact and future directions""",

    "conclusion": """Write the Conclusion for this paper.

The conclusion should:
1. Restate the main contribution (1-2 sentences)
2. Summarize key results (2-3 sentences)
3. State the maturity level achieved and path forward (1-2 sentences)
4. End with future work directions (1-2 sentences)""",
}

SECTION_ORDER = ["abstract", "introduction", "related_work", "methods",
                 "experiments", "discussion", "conclusion"]


def compose_section(section_name, idea, methodology, prior_sections=None,
                    related_papers=None, model=None):
    """Write a single paper section.

    Args:
        section_name: one of SECTION_ORDER
        idea: research idea dict or text
        methodology: methodology text
        prior_sections: dict of {section_name: content} already written
        related_papers: list of related paper dicts
        model: LLM model to use

    Returns:
        section_text
    """
    # Build context
    context = "## Research Idea\n"
    if isinstance(idea, dict):
        context += f"Title: {idea.get('title', '')}\n"
        context += f"Description: {idea.get('description', '')}\n"
        context += f"Contribution: {idea.get('key_contribution', '')}\n"
    else:
        context += f"{idea}\n"

    context += f"\n## Methodology\n{methodology}\n"

    if related_papers:
        context += "\n## Related Papers\n"
        for p in related_papers[:5]:
            context += f"- {p.get('title', '')}\n"

    if prior_sections:
        context += "\n## Previously Written Sections\n"
        for name in SECTION_ORDER:
            if name in prior_sections and name != section_name:
                context += f"\n### {name.replace('_', ' ').title()}\n"
                context += prior_sections[name] + "\n"

    section_prompt = SECTION_PROMPTS.get(section_name, f"Write the {section_name} section.")

    prompt = f"""{context}

## Task
{section_prompt}

Write only the {section_name.replace('_', ' ').title()} section. Do not include section headers like "# Abstract" — just the content."""

    response = call_llm(COMPOSER_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096)
    return response


def revise_section(section_name, current_content, feedback, context="", model=None):
    """Revise a specific section based on feedback.

    Returns revised section text.
    """
    prompt = f"""Revise the following {section_name.replace('_', ' ').title()} section based on the feedback.

## Current Content
{current_content}

## Feedback
{feedback}

## Paper Context
{context}

Produce a revised version that addresses all the feedback. Output only the revised section content."""

    response = call_llm(COMPOSER_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096)
    return response


def compose_full_paper(idea, methodology, related_papers=None, model=None):
    """Compose a full paper section by section.

    Returns (sections_dict, log_entries).
    """
    sections = {}
    log = []

    for section_name in SECTION_ORDER:
        content = compose_section(
            section_name, idea, methodology,
            prior_sections=sections,
            related_papers=related_papers,
            model=model,
        )
        sections[section_name] = content
        log.append({"step": f"compose_{section_name}", "length": len(content)})

    return sections, log


def format_paper_markdown(title, authors, sections):
    """Format paper sections into a complete Markdown document."""
    lines = [f"# {title}\n"]
    if authors:
        lines.append(f"**Authors:** {authors}\n")
    lines.append("---\n")

    section_titles = {
        "abstract": "Abstract",
        "introduction": "1. Introduction",
        "related_work": "2. Related Work",
        "methods": "3. Methods",
        "experiments": "4. Experiments",
        "discussion": "5. Discussion",
        "conclusion": "6. Conclusion",
    }

    for key in SECTION_ORDER:
        if key in sections:
            lines.append(f"## {section_titles.get(key, key.title())}\n")
            lines.append(sections[key])
            lines.append("")

    return "\n".join(lines)
