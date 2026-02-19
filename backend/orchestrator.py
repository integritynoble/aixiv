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
    """Promote an accepted paper to the Arena."""
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise ValueError(f"Paper {paper_id} not found")

    if paper["status"] not in ("accepted", "under_review", "revision"):
        # Allow promotion if there are reviews
        pass

    reviews = conn.execute("SELECT * FROM reviews WHERE paper_id = ?", (paper_id,)).fetchall()
    meta = conn.execute(
        "SELECT * FROM meta_reviews WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()

    if not reviews:
        conn.close()
        raise ValueError("Paper must be reviewed before promotion")

    avg_score = sum(r["overall_score"] for r in reviews) / len(reviews)
    maturity = paper["maturity_level"]

    # Determine badges
    badges = []
    if maturity in ("L3", "L4", "L5"):
        badges.append(f"{maturity} Certified")

    redteam = conn.execute(
        "SELECT * FROM redteam_reports WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()
    if redteam and redteam["overall_risk"] in ("low",):
        badges.append("Red-Team Cleared")

    evals = conn.execute("SELECT * FROM eval_results WHERE paper_id = ?", (paper_id,)).fetchall()
    if evals and all(e["score"] >= 5 for e in evals):
        badges.append("Rail Compliant")

    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO arena_papers
        (paper_id, title, authors, abstract, categories, pdf_path,
         review_score, maturity_level, rail_compliant, review_count, badges, promoted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, paper["title"], paper["authors"], paper["abstract"],
          paper["categories"], paper["pdf_path"], avg_score, maturity,
          1 if "Rail Compliant" in badges else 0,
          len(reviews), json.dumps(badges), now))

    conn.execute("UPDATE papers SET status = 'published_arena', updated_at = ? WHERE paper_id = ?",
                 (now, paper_id))
    conn.commit()
    conn.close()

    return {
        "paper_id": paper_id,
        "review_score": avg_score,
        "maturity_level": maturity,
        "badges": badges,
    }


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

    conn.close()
    return stats
