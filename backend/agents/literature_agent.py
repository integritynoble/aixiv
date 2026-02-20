"""Literature search and novelty checking agent using arXiv API."""
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from .base_agent import call_llm, parse_json_from_response, DEFAULT_MODEL

ARXIV_API = "http://export.arxiv.org/api/query"
MAX_RESULTS = 10
MAX_SEARCH_ROUNDS = 5

NOVELTY_SYSTEM = """You are a Novelty Assessor — you evaluate whether a research idea is novel given existing literature.

Given a research idea and a list of related papers found in literature, you must determine:
1. Does any existing paper already propose the same core idea?
2. How much overlap exists with existing work?
3. What is the unique contribution beyond prior art?

Output your assessment as JSON:
{
  "decision": "novel" | "not_novel" | "needs_more_search",
  "confidence": 0.0-1.0,
  "overlap_papers": [
    {
      "arxiv_id": "paper id",
      "title": "paper title",
      "overlap_description": "what overlaps",
      "overlap_degree": "high/medium/low"
    }
  ],
  "unique_aspects": ["what makes this idea different"],
  "suggested_queries": ["additional search queries if needs_more_search"],
  "summary": "2-3 sentence assessment"
}"""

QUERY_GEN_SYSTEM = """You are a search query generator for academic literature.

Given a research idea, generate effective search queries for arXiv that would find related work.
Consider synonyms, related concepts, and different phrasings.

Output JSON:
{
  "queries": ["query1", "query2", "query3"]
}"""


def search_arxiv(query, max_results=MAX_RESULTS):
    """Search arXiv API and return list of paper dicts."""
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aiXiv/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
    except Exception as e:
        return [], str(e)

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(data)
    papers = []
    for entry in root.findall("atom:entry", ns):
        title_el = entry.find("atom:title", ns)
        summary_el = entry.find("atom:summary", ns)
        id_el = entry.find("atom:id", ns)
        published_el = entry.find("atom:published", ns)
        authors = [a.find("atom:name", ns).text
                   for a in entry.findall("atom:author", ns)
                   if a.find("atom:name", ns) is not None]

        arxiv_id = ""
        if id_el is not None and id_el.text:
            arxiv_id = id_el.text.split("/abs/")[-1]

        papers.append({
            "arxiv_id": arxiv_id,
            "title": title_el.text.strip().replace("\n", " ") if title_el is not None else "",
            "abstract": summary_el.text.strip().replace("\n", " ") if summary_el is not None else "",
            "authors": authors,
            "published": published_el.text if published_el is not None else "",
        })
    return papers, None


def generate_search_queries(idea_text, model=None, api_key=None, api_provider=None):
    """Generate search queries for a research idea."""
    prompt = f"""Generate 3 effective arXiv search queries to find related work for this research idea:

{idea_text}

Generate queries that would find the most relevant prior art."""

    response = call_llm(QUERY_GEN_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=1024, temperature=0.5,
                        api_key=api_key, api_provider=api_provider)
    parsed = parse_json_from_response(response)
    if parsed and "queries" in parsed:
        return parsed["queries"]
    # Fallback: extract key phrases
    words = idea_text.split()[:10]
    return [" ".join(words)]


def assess_novelty(idea_text, found_papers, model=None, api_key=None, api_provider=None):
    """Assess novelty of an idea against found papers."""
    papers_text = ""
    for i, p in enumerate(found_papers, 1):
        papers_text += f"\n### Paper {i}: {p['title']}\n"
        papers_text += f"arXiv ID: {p['arxiv_id']}\n"
        papers_text += f"Authors: {', '.join(p.get('authors', []))}\n"
        papers_text += f"Abstract: {p['abstract'][:300]}...\n"

    prompt = f"""Assess the novelty of this research idea given the related papers found.

## Research Idea
{idea_text}

## Related Papers Found
{papers_text if papers_text else "No related papers found."}

Determine if this idea is novel, not novel (already done), or if more search is needed."""

    response = call_llm(NOVELTY_SYSTEM, [{"role": "user", "content": prompt}],
                        model=model, max_tokens=2048, temperature=0.3,
                        api_key=api_key, api_provider=api_provider)
    parsed = parse_json_from_response(response)
    return parsed if parsed else {"decision": "novel", "confidence": 0.5, "summary": response}, response


def run_novelty_check(idea_text, model=None, api_key=None, api_provider=None):
    """Run full novelty check pipeline with iterative search.

    Returns (assessment_dict, all_papers_found, log_entries).
    """
    log = []
    all_papers = []
    seen_ids = set()

    # Generate initial queries
    queries = generate_search_queries(idea_text, model=model, api_key=api_key, api_provider=api_provider)
    log.append({"step": "generate_queries", "queries": queries})

    for round_num in range(MAX_SEARCH_ROUNDS):
        # Search with each query
        for query in queries:
            papers, error = search_arxiv(query)
            if error:
                log.append({"step": "search_error", "query": query, "error": error})
                continue
            for p in papers:
                if p["arxiv_id"] not in seen_ids:
                    seen_ids.add(p["arxiv_id"])
                    all_papers.append(p)
            log.append({"step": "search", "round": round_num + 1, "query": query, "found": len(papers)})

        # Assess novelty
        assessment, raw = assess_novelty(idea_text, all_papers, model=model, api_key=api_key, api_provider=api_provider)
        log.append({"step": "assess", "round": round_num + 1, "decision": assessment.get("decision", "unknown")})

        if assessment.get("decision") != "needs_more_search":
            return assessment, all_papers, log

        # Generate new queries if more search needed
        new_queries = assessment.get("suggested_queries", [])
        if not new_queries:
            break
        queries = new_queries[:3]

    return assessment, all_papers, log
