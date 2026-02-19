"""AI Scientist: paper writing and reviewing agents powered by Claude."""
import os
import json
from anthropic import Anthropic

client = None

def get_client():
    global client
    if client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        client = Anthropic(api_key=api_key)
    return client


WRITER_SYSTEM = """You are an AI Scientist Writer — a world-class scientific writing assistant.

Your role is to help researchers write high-quality scientific papers. You follow the SolveEverything.org framework principles:
- L0→L5 maturity: help move research from ill-posed ideas to measurable, repeatable, industrialized results
- Targeting systems: ensure papers have clear metrics, reproducible results, and auditable claims
- Rail infrastructure: standardized evaluation protocols and benchmarks

When helping write a paper, you:
1. Help structure the paper (Introduction, Related Work, Methods, Experiments, Discussion, Conclusion)
2. Ensure claims are precise and supported by evidence
3. Suggest proper experimental design with clear baselines and metrics
4. Help articulate the contribution clearly
5. Write in clear, concise academic prose
6. Suggest relevant citations and comparisons
7. Identify potential weaknesses before reviewers do

Always output in well-structured sections. Use LaTeX notation for math when appropriate."""


REVIEWER_SYSTEM = """You are an AI Scientist Reviewer — a rigorous, fair, and constructive peer reviewer.

You follow the SolveEverything.org framework for evaluating scientific contributions:

## Evaluation Framework (L0-L5 Maturity Assessment)
- L0 (Ill-Posed): Are objectives clearly defined? Are metrics specified?
- L1 (Measurable): Are results quantified with proper baselines?
- L2 (Repeatable): Could someone reproduce this work?
- L3 (Automated): Is the approach systematic and scalable?
- L4 (Industrialized): Does it advance the field toward commodity solutions?
- L5 (Solved): Does it definitively resolve the problem?

## Triad Law Gate Analysis (for computational imaging papers)
- Gate 1 (Recoverability): Is the measurement information-sufficient?
- Gate 2 (Carrier Budget): Is the SNR adequate?
- Gate 3 (Operator Mismatch): Is the forward model accurate?

## Review Structure
Provide your review in this exact format:

### Summary
[2-3 sentence summary of the paper's main contribution]

### Soundness (1-5)
[Score and justification]

### Novelty (1-5)
[Score and justification]

### Clarity (1-5)
[Score and justification]

### Significance (1-5)
[Score and justification]

### Strengths
[Bulleted list of key strengths]

### Weaknesses
[Bulleted list of key weaknesses]

### Questions for Authors
[Specific questions that should be addressed]

### Maturity Assessment
[L0-L5 rating with justification]

### Recommendation
[One of: Accept, Minor Revision, Major Revision, Reject]

### Detailed Feedback
[Paragraph-level feedback for improving the paper]

Be rigorous but constructive. The goal is to help authors improve their work, not to gatekeep."""


def ai_write(messages, user_prompt):
    """AI writing assistant for paper composition."""
    c = get_client()
    messages = list(messages)
    messages.append({"role": "user", "content": user_prompt})
    response = c.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=WRITER_SYSTEM,
        messages=messages,
    )
    reply = response.content[0].text
    messages.append({"role": "assistant", "content": reply})
    return reply, messages


def ai_review(title, abstract, full_text=""):
    """AI reviewer for submitted papers."""
    c = get_client()
    review_prompt = f"""Please review the following scientific paper submission.

## Title
{title}

## Abstract
{abstract}
"""
    if full_text:
        review_prompt += f"""
## Full Paper Content
{full_text}
"""
    review_prompt += """
Please provide a comprehensive review following your review structure format.
Assess the maturity level (L0-L5) according to the SolveEverything.org framework.
If this is a computational imaging paper, also provide a Triad Law Gate Analysis."""

    response = c.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=REVIEWER_SYSTEM,
        messages=[{"role": "user", "content": review_prompt}],
    )
    return response.content[0].text


def parse_review(review_text):
    """Parse structured review text into fields."""
    sections = {}
    current_section = None
    current_content = []

    for line in review_text.split('\n'):
        if line.startswith('### '):
            if current_section:
                sections[current_section] = '\n'.join(current_content).strip()
            current_section = line[4:].strip()
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        sections[current_section] = '\n'.join(current_content).strip()

    def extract_score(text, max_val=5):
        for token in text.split():
            try:
                v = int(token.strip('():/'))
                if 1 <= v <= max_val:
                    return v
            except ValueError:
                continue
        return 3

    result = {
        'summary': sections.get('Summary', ''),
        'soundness': extract_score(sections.get('Soundness (1-5)', '3')),
        'novelty': extract_score(sections.get('Novelty (1-5)', '3')),
        'clarity': extract_score(sections.get('Clarity (1-5)', '3')),
        'significance': extract_score(sections.get('Significance (1-5)', '3')),
        'strengths': sections.get('Strengths', ''),
        'weaknesses': sections.get('Weaknesses', ''),
        'questions': sections.get('Questions for Authors', ''),
        'recommendation': 'minor_revision',
        'detailed_feedback': sections.get('Detailed Feedback', ''),
        'gate_analysis': sections.get('Maturity Assessment', ''),
    }

    rec_text = sections.get('Recommendation', '').lower()
    if 'accept' in rec_text and 'minor' not in rec_text and 'major' not in rec_text:
        result['recommendation'] = 'accept'
    elif 'minor' in rec_text:
        result['recommendation'] = 'minor_revision'
    elif 'major' in rec_text:
        result['recommendation'] = 'major_revision'
    elif 'reject' in rec_text:
        result['recommendation'] = 'reject'

    result['overall_score'] = round(
        (result['soundness'] + result['novelty'] +
         result['clarity'] + result['significance']) / 4 * 2
    )

    return result
