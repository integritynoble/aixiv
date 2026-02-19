"""Idea generation agent with maker/critic iterative loop."""
from .base_agent import call_llm, parse_json_from_response, DEFAULT_MODEL

IDEA_MAKER_SYSTEM = """You are an Idea Maker — a creative scientific researcher who generates novel research ideas.

Given a research topic or data description, generate research ideas that are:
- Novel and non-obvious
- Technically feasible
- Impactful if successful
- Clearly scoped with measurable outcomes

Always output your ideas in this JSON format:
{
  "ideas": [
    {
      "title": "Paper title",
      "description": "5-sentence description of the idea",
      "key_contribution": "One sentence stating the main contribution",
      "metrics": ["list of measurable success metrics"],
      "feasibility": "high/medium/low"
    }
  ]
}"""

IDEA_CRITIC_SYSTEM = """You are an Idea Critic — a harsh but fair scientific evaluator.

Your job is to ruthlessly critique research ideas to find flaws, gaps, and weaknesses.
For each idea, assess:
1. Is this truly novel, or has it been done before?
2. Are the claims realistic and achievable?
3. Are the proposed metrics actually meaningful?
4. What are the biggest risks and failure modes?
5. Would this advance the field if successful?

Rate each idea 1-10 and explain your reasoning. Be specific and constructive.
Output JSON:
{
  "critiques": [
    {
      "title": "Paper title being critiqued",
      "score": 7,
      "strengths": ["list of strengths"],
      "weaknesses": ["list of weaknesses"],
      "risks": ["list of risks"],
      "improvement_suggestions": ["how to make it better"],
      "verdict": "keep/revise/discard"
    }
  ]
}"""

IDEA_REFINER_SYSTEM = """You are an Idea Refiner — you take a research idea and its critique, then produce an improved version.

Given the original idea and the critic's feedback, produce a refined idea that:
- Addresses the weaknesses identified
- Incorporates the improvement suggestions
- Strengthens the novel contribution
- Sharpens the metrics and evaluation plan

Output JSON:
{
  "title": "Refined paper title",
  "description": "5-sentence refined description",
  "key_contribution": "One sentence main contribution",
  "methodology_sketch": "Brief methodology outline (3-5 sentences)",
  "metrics": ["refined measurable metrics"],
  "expected_results": "What results would demonstrate success",
  "maturity_target": "L0-L5 maturity level this work aims to achieve"
}"""


def generate_ideas(topic, num_ideas=5, model=None):
    """Generate initial research ideas for a topic."""
    prompt = f"""Generate {num_ideas} novel research project ideas for the following topic/description:

{topic}

Provide diverse ideas spanning different approaches and methodologies. Each idea should be distinct and address a different aspect of the problem."""

    response = call_llm(IDEA_MAKER_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096)
    parsed = parse_json_from_response(response)
    if parsed and "ideas" in parsed:
        return parsed["ideas"], response
    return [], response


def critique_ideas(ideas, topic, model=None):
    """Critique a list of research ideas."""
    ideas_text = ""
    for i, idea in enumerate(ideas, 1):
        if isinstance(idea, dict):
            ideas_text += f"\n### Idea {i}: {idea.get('title', 'Untitled')}\n"
            ideas_text += f"{idea.get('description', '')}\n"
            ideas_text += f"Key contribution: {idea.get('key_contribution', '')}\n"
            ideas_text += f"Metrics: {', '.join(idea.get('metrics', []))}\n"
        else:
            ideas_text += f"\n### Idea {i}\n{idea}\n"

    prompt = f"""Critique the following research ideas for the topic: {topic}

{ideas_text}

Be rigorous. Identify which ideas are truly novel, which are incremental, and which have fundamental flaws. Score each 1-10."""

    response = call_llm(IDEA_CRITIC_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096)
    parsed = parse_json_from_response(response)
    if parsed and "critiques" in parsed:
        return parsed["critiques"], response
    return [], response


def select_top_ideas(critiques, ideas, n=2):
    """Select top N ideas based on critique scores."""
    if not critiques:
        return ideas[:n]
    scored = []
    for i, c in enumerate(critiques):
        score = c.get("score", 5) if isinstance(c, dict) else 5
        verdict = c.get("verdict", "keep") if isinstance(c, dict) else "keep"
        if verdict != "discard":
            scored.append((score, i))
    scored.sort(reverse=True)
    selected = []
    for _, idx in scored[:n]:
        if idx < len(ideas):
            selected.append(ideas[idx])
    return selected if selected else ideas[:n]


def refine_idea(idea, critique, topic, model=None):
    """Refine a single idea based on critique feedback."""
    idea_text = ""
    if isinstance(idea, dict):
        idea_text = f"Title: {idea.get('title', '')}\nDescription: {idea.get('description', '')}\nContribution: {idea.get('key_contribution', '')}"
    else:
        idea_text = str(idea)

    critique_text = ""
    if isinstance(critique, dict):
        critique_text = f"Score: {critique.get('score', 'N/A')}\n"
        critique_text += f"Strengths: {', '.join(critique.get('strengths', []))}\n"
        critique_text += f"Weaknesses: {', '.join(critique.get('weaknesses', []))}\n"
        critique_text += f"Suggestions: {', '.join(critique.get('improvement_suggestions', []))}\n"
    else:
        critique_text = str(critique)

    prompt = f"""Refine this research idea based on the critique.

Topic: {topic}

Original Idea:
{idea_text}

Critique:
{critique_text}

Produce an improved version that addresses the weaknesses and incorporates the suggestions."""

    response = call_llm(IDEA_REFINER_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=4096)
    parsed = parse_json_from_response(response)
    return parsed if parsed else {"title": "Refined Idea", "description": response}, response


def run_idea_pipeline(topic, model=None):
    """Run the full idea generation pipeline:
    Generate 5 → Critique → Select 2 → Critique → Select 1 → Refine → Final idea.

    Returns (final_idea_dict, log_entries).
    """
    log = []

    # Step 1: Generate 5 ideas
    ideas, raw = generate_ideas(topic, num_ideas=5, model=model)
    log.append({"step": "generate", "raw": raw, "count": len(ideas)})

    if not ideas:
        return {"title": "Generation Failed", "description": raw}, log

    # Step 2: Critique all 5
    critiques, raw = critique_ideas(ideas, topic, model=model)
    log.append({"step": "critique_round1", "raw": raw, "count": len(critiques)})

    # Step 3: Select top 2
    top2 = select_top_ideas(critiques, ideas, n=2)
    log.append({"step": "select_top2", "count": len(top2)})

    # Step 4: Critique top 2 more deeply
    critiques2, raw = critique_ideas(top2, topic, model=model)
    log.append({"step": "critique_round2", "raw": raw})

    # Step 5: Select top 1
    top1 = select_top_ideas(critiques2, top2, n=1)
    log.append({"step": "select_top1"})

    # Step 6: Refine the winner
    best_idea = top1[0] if top1 else top2[0]
    best_critique = critiques2[0] if critiques2 else (critiques[0] if critiques else {})
    final_idea, raw = refine_idea(best_idea, best_critique, topic, model=model)
    log.append({"step": "refine", "raw": raw})

    return final_idea, log
