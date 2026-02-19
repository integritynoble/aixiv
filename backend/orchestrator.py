"""Orchestrator — manages the full AI Scientist workflow pipeline."""
import json
import time
from datetime import datetime
from database import get_db
from agents.idea_agent import run_idea_pipeline
from agents.literature_agent import run_novelty_check
from agents.method_agent import run_methodology_pipeline
from agents.paper_agent import compose_full_paper, format_paper_markdown
from agents.reviewer_agent import review_paper, extract_flat_scores
from agents.redteam_agent import redteam_paper, format_redteam_report
from agents.meta_reviewer_agent import meta_review, format_meta_review
from agents.revision_agent import generate_revisions
from rail.targeting import assess_maturity
from rail.eval_engine import evaluate_paper
from rail.decision_record import record_decision

# Paper lifecycle states
STATES = [
    "submitted",       # Published on aiXiv (Tier 1)
    "under_review",    # AI review in progress
    "revision",        # Authors revising
    "re_review",       # Revised paper under re-review
    "accepted",        # Passed review, ready for Arena
    "published_arena", # Promoted to Arena (Tier 2)
    "rejected",        # Did not pass (stays on aiXiv)
]

VALID_TRANSITIONS = {
    "submitted": ["under_review"],
    "under_review": ["revision", "accepted", "rejected"],
    "revision": ["re_review"],
    "re_review": ["revision", "accepted", "rejected"],
    "accepted": ["published_arena"],
    "published_arena": [],
    "rejected": ["revision"],  # authors can revise and resubmit
}


def transition_paper(paper_id, new_status):
    """Transition a paper to a new status, enforcing valid transitions."""
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise ValueError(f"Paper {paper_id} not found")

    current = paper["status"]
    if new_status not in VALID_TRANSITIONS.get(current, []):
        conn.close()
        raise ValueError(f"Invalid transition: {current} -> {new_status}")

    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE papers SET status = ?, updated_at = ? WHERE paper_id = ?",
                 (new_status, now, paper_id))
    conn.commit()
    conn.close()
    return new_status


def run_full_review(paper_id, model=None):
    """Run the complete review pipeline: peer review + red team + meta-review.

    Returns dict with all results.
    """
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise ValueError(f"Paper {paper_id} not found")

    title = paper["title"]
    abstract = paper["abstract"]
    full_text = paper["full_text"] or ""
    now = datetime.utcnow().isoformat()

    # Transition to under_review
    if paper["status"] == "submitted":
        conn.execute("UPDATE papers SET status = 'under_review', updated_at = ? WHERE paper_id = ?",
                     (now, paper_id))
        conn.commit()

    results = {}

    # Step 1: Peer Review (3-layer)
    peer_result, peer_raw = review_paper(title, abstract, full_text, model=model)
    flat = extract_flat_scores(peer_result)
    conn.execute("""
        INSERT INTO reviews (paper_id, reviewer_type, review_layer, overall_score,
                           soundness, novelty, clarity, significance, reproducibility,
                           summary, strengths, weaknesses, questions, recommendation,
                           detailed_feedback, maturity_level, gate_analysis, raw_review, created_at)
        VALUES (?, 'ai', 'full', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, flat["overall_score"], flat["soundness"], flat["novelty"],
          flat["clarity"], flat["significance"], flat.get("reproducibility", 3),
          flat["summary"], flat["strengths"], flat["weaknesses"],
          flat["questions"], flat["recommendation"], flat["detailed_feedback"],
          flat.get("maturity_level", "L0"), flat.get("gate_analysis", ""), peer_raw, now))
    conn.commit()
    results["peer_review"] = peer_result

    record_decision(paper_id, "peer_review", model or "claude-opus", "",
                    f"Title: {title}", flat["summary"][:500])

    # Step 2: Red Team
    redteam_result, redteam_raw = redteam_paper(title, abstract, full_text, model=model)
    conn.execute("""
        INSERT INTO redteam_reports (paper_id, overall_risk, confidence, findings,
                                    attack_scenarios, summary, raw_report, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, redteam_result.get("overall_risk", "medium"),
          redteam_result.get("confidence_in_conclusions", 0.5),
          json.dumps(redteam_result.get("findings", [])),
          json.dumps(redteam_result.get("attack_scenarios", [])),
          redteam_result.get("summary", ""), redteam_raw, now))
    conn.commit()
    results["redteam"] = redteam_result

    record_decision(paper_id, "redteam", model or "claude-opus", "",
                    f"Title: {title}", redteam_result.get("summary", "")[:500])

    # Step 3: Meta-Review
    meta_result, meta_raw = meta_review(title, abstract, peer_result, redteam_result, model=model)
    conn.execute("""
        INSERT INTO meta_reviews (paper_id, final_recommendation, confidence,
                                 justification, maturity_level, required_changes,
                                 suggested_changes, summary_for_authors,
                                 arena_eligible, arena_eligibility_reason,
                                 raw_review, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, meta_result.get("final_recommendation", ""),
          meta_result.get("confidence", 0.5),
          meta_result.get("justification", ""),
          meta_result.get("maturity_level", "L0"),
          json.dumps(meta_result.get("required_changes", [])),
          json.dumps(meta_result.get("suggested_changes", [])),
          meta_result.get("summary_for_authors", ""),
          1 if meta_result.get("arena_eligible") else 0,
          meta_result.get("arena_eligibility_reason", ""),
          meta_raw, now))
    conn.commit()
    results["meta_review"] = meta_result

    record_decision(paper_id, "meta_review", model or "claude-opus", "",
                    f"Title: {title}",
                    meta_result.get("final_recommendation", "")[:500])

    # Step 4: Update paper maturity level
    maturity = meta_result.get("maturity_level", "L0")
    rec = meta_result.get("final_recommendation", "minor_revision")

    new_status = "revision"
    if rec == "accept":
        new_status = "accepted"
    elif rec == "reject":
        new_status = "rejected"

    conn.execute("UPDATE papers SET maturity_level = ?, status = ?, updated_at = ? WHERE paper_id = ?",
                 (maturity, new_status, now, paper_id))
    conn.commit()
    conn.close()

    results["new_status"] = new_status
    results["maturity_level"] = maturity

    return results


def run_rail_evaluation(paper_id, model=None):
    """Run the Rail 4-scenario evaluation.

    Returns eval_dict.
    """
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise ValueError(f"Paper {paper_id} not found")

    title = paper["title"]
    abstract = paper["abstract"]
    full_text = paper["full_text"] or ""
    now = datetime.utcnow().isoformat()

    eval_result, raw = evaluate_paper(title, abstract, full_text, model=model)

    for scenario in eval_result.get("scenarios", []):
        conn.execute("""
            INSERT INTO eval_results (paper_id, scenario, score, assessment, gaps, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (paper_id, scenario.get("name", ""),
              scenario.get("score", 0),
              scenario.get("assessment", ""),
              json.dumps(scenario.get("gaps", [])), now))
    conn.commit()

    record_decision(paper_id, "rail_evaluation", model or "default", "",
                    f"Title: {title}", eval_result.get("summary", "")[:500])

    conn.close()
    return eval_result


def promote_to_arena(paper_id):
    """Promote an accepted paper to the Arena. Delegates to arena module."""
    from arena import promote_paper
    return promote_paper(paper_id)


def run_full_pipeline(topic, authors="AI Scientist", callback=None):
    """Run the complete AI Scientist pipeline end-to-end:
    idea → novelty → method → compose → submit → review → revision suggestions.

    Args:
        topic: Research topic/description
        authors: Author names
        callback: Optional function called with (step_name, step_data) for progress updates

    Returns dict with all intermediate and final results.
    """
    def emit(step, data):
        if callback:
            callback(step, data)

    results = {"topic": topic, "authors": authors}

    # Phase 1: WRITE
    # Step 1: Idea generation
    emit("idea_start", {"message": "Generating research ideas (5 candidates, 2 critique rounds)..."})
    idea, idea_log = run_idea_pipeline(topic)
    results["idea"] = idea
    emit("idea_done", {"idea": idea, "log": idea_log})

    # Step 2: Novelty check
    idea_text = f"{idea.get('title', '')}: {idea.get('description', '')}"
    emit("novelty_start", {"message": "Checking novelty against arXiv..."})
    assessment, papers_found, novelty_log = run_novelty_check(idea_text)
    results["novelty"] = {"assessment": assessment, "papers_found": len(papers_found),
                          "top_papers": papers_found[:10]}
    emit("novelty_done", {"assessment": assessment, "papers_found": len(papers_found)})

    # Step 3: Methodology
    emit("method_start", {"message": "Developing methodology..."})
    methodology, method_review, method_log = run_methodology_pipeline(idea, papers_found[:10])
    results["methodology"] = methodology
    emit("method_done", {"methodology": methodology[:500]})

    # Step 4: Paper composition
    emit("compose_start", {"message": "Composing full paper (7 sections)..."})
    sections, compose_log = compose_full_paper(idea, methodology, papers_found[:10])
    paper_md = format_paper_markdown(idea.get("title", "Untitled"), authors, sections)
    results["sections"] = sections
    results["full_paper"] = paper_md
    emit("compose_done", {"sections": list(sections.keys()) if isinstance(sections, dict) else [],
                          "length": len(paper_md)})

    # Phase 2: PUBLISH (Tier 1)
    emit("submit_start", {"message": "Publishing on aiXiv (Tier 1)..."})
    conn = get_db()
    from database import generate_paper_id
    paper_id = generate_paper_id()
    now = datetime.utcnow().isoformat()

    abstract_text = ""
    if isinstance(sections, dict):
        abstract_text = sections.get("abstract", idea.get("description", ""))
    elif isinstance(sections, list):
        for s in sections:
            if isinstance(s, dict) and s.get("name") == "abstract":
                abstract_text = s.get("content", "")
                break
    if not abstract_text:
        abstract_text = idea.get("description", "")

    conn.execute("""
        INSERT INTO papers (paper_id, title, authors, affiliation, abstract,
                          keywords, categories, full_text, pdf_path, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'submitted', ?, ?)
    """, (paper_id, idea.get("title", "Untitled"), authors,
          "NextGen PlatformAI C Corp", abstract_text,
          ", ".join(idea.get("keywords", [])) if isinstance(idea.get("keywords"), list) else "",
          "", paper_md, now, now))
    conn.commit()
    conn.close()
    results["paper_id"] = paper_id
    emit("submit_done", {"paper_id": paper_id})

    record_decision(paper_id, "full_pipeline_submit", "system", "",
                    f"Topic: {topic}", f"Paper {paper_id} created from full pipeline")

    # Phase 3: REVIEW
    emit("review_start", {"message": "Running full AI review (peer + red team + meta)..."})
    review_results = run_full_review(paper_id)
    results["review"] = review_results
    emit("review_done", {
        "status": review_results.get("new_status", ""),
        "maturity": review_results.get("maturity_level", "L0"),
        "recommendation": review_results.get("meta_review", {}).get("final_recommendation", ""),
    })

    # Phase 4: REVISE (generate suggestions)
    emit("revise_start", {"message": "Generating revision suggestions..."})
    peer_raw = review_results.get("peer_review", {})
    redteam_raw = review_results.get("redteam", {})
    meta_raw = review_results.get("meta_review", {})

    revision_result, revision_raw = generate_revisions(
        paper_md[:8000],
        json.dumps(peer_raw)[:3000] if isinstance(peer_raw, dict) else str(peer_raw)[:3000],
        json.dumps(redteam_raw)[:3000] if isinstance(redteam_raw, dict) else str(redteam_raw)[:3000],
        json.dumps(meta_raw)[:2000] if isinstance(meta_raw, dict) else str(meta_raw)[:2000],
    )
    results["revisions"] = revision_result
    emit("revise_done", {
        "num_suggestions": len(revision_result.get("revision_suggestions", [])),
    })

    emit("pipeline_complete", {
        "paper_id": paper_id,
        "title": idea.get("title", "Untitled"),
        "status": review_results.get("new_status", ""),
        "maturity": review_results.get("maturity_level", "L0"),
    })

    return results


def get_pipeline_stats():
    """Get statistics for the dashboard."""
    conn = get_db()
    stats = {}
    for status in STATES:
        row = conn.execute("SELECT COUNT(*) as c FROM papers WHERE status = ?", (status,)).fetchone()
        stats[status] = row["c"]

    stats["total"] = sum(stats.values())

    # Maturity distribution
    maturity_dist = {}
    for level in ["L0", "L1", "L2", "L3", "L4", "L5"]:
        row = conn.execute("SELECT COUNT(*) as c FROM papers WHERE maturity_level = ?", (level,)).fetchone()
        maturity_dist[level] = row["c"]
    stats["maturity_distribution"] = maturity_dist

    # Arena stats
    row = conn.execute("SELECT COUNT(*) as c FROM arena_papers").fetchone()
    stats["arena_count"] = row["c"]

    row = conn.execute("SELECT AVG(review_score) as avg FROM arena_papers").fetchone()
    stats["arena_avg_score"] = round(row["avg"], 1) if row["avg"] else 0

    # Review stats
    row = conn.execute("SELECT COUNT(*) as c FROM reviews").fetchone()
    stats["total_reviews"] = row["c"]

    # Review quality metrics
    review_rows = conn.execute(
        "SELECT overall_score, soundness, novelty, clarity, significance, reproducibility "
        "FROM reviews WHERE overall_score IS NOT NULL"
    ).fetchall()
    if review_rows:
        scores = [r["overall_score"] for r in review_rows if r["overall_score"]]
        stats["review_score_avg"] = round(sum(scores) / len(scores), 1) if scores else 0
        stats["review_score_min"] = min(scores) if scores else 0
        stats["review_score_max"] = max(scores) if scores else 0

        dims = {}
        for dim in ["soundness", "novelty", "clarity", "significance", "reproducibility"]:
            vals = [r[dim] for r in review_rows if r[dim]]
            dims[dim] = round(sum(vals) / len(vals), 1) if vals else 0
        stats["review_dimensions"] = dims
    else:
        stats["review_score_avg"] = 0
        stats["review_score_min"] = 0
        stats["review_score_max"] = 0
        stats["review_dimensions"] = {}

    # Recent activity (from decision records + reviews + papers)
    activity = []
    recent_papers = conn.execute(
        "SELECT paper_id, title, status, created_at FROM papers ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    for p in recent_papers:
        activity.append({
            "type": "paper",
            "paper_id": p["paper_id"],
            "title": (p["title"] or "")[:60],
            "detail": f"Status: {p['status']}",
            "timestamp": p["created_at"],
        })

    recent_reviews = conn.execute(
        "SELECT paper_id, review_layer, overall_score, recommendation, created_at "
        "FROM reviews ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    for r in recent_reviews:
        activity.append({
            "type": "review",
            "paper_id": r["paper_id"],
            "title": f"Review ({r['review_layer'] or 'peer'})",
            "detail": f"Score: {r['overall_score']}, Rec: {r['recommendation'] or '?'}",
            "timestamp": r["created_at"],
        })

    recent_redteam = conn.execute(
        "SELECT paper_id, overall_risk, created_at "
        "FROM redteam_reports ORDER BY created_at DESC LIMIT 5"
    ).fetchall()
    for rt in recent_redteam:
        activity.append({
            "type": "redteam",
            "paper_id": rt["paper_id"],
            "title": "Red Team Analysis",
            "detail": f"Risk: {rt['overall_risk']}",
            "timestamp": rt["created_at"],
        })

    # Sort by timestamp descending
    activity.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    stats["recent_activity"] = activity[:20]

    # System health
    stats["health"] = {
        "database": "ok",
        "tables": {},
    }
    for table in ["papers", "reviews", "redteam_reports", "meta_reviews",
                  "revisions", "eval_results", "writing_sessions", "arena_papers"]:
        try:
            row = conn.execute(f"SELECT COUNT(*) as c FROM {table}").fetchone()
            stats["health"]["tables"][table] = row["c"]
        except Exception:
            stats["health"]["tables"][table] = -1

    conn.close()
    return stats
