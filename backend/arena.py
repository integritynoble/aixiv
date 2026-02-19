"""Arena scoring, ranking, and badge engine for Tier 2 papers."""
import json
from datetime import datetime
from database import get_db


# Badge definitions
BADGE_RULES = {
    "L3 Certified": lambda paper, reviews, redteam, evals: paper["maturity_level"] in ("L3", "L4", "L5"),
    "L4 Certified": lambda paper, reviews, redteam, evals: paper["maturity_level"] in ("L4", "L5"),
    "L5 Certified": lambda paper, reviews, redteam, evals: paper["maturity_level"] == "L5",
    "Red-Team Cleared": lambda paper, reviews, redteam, evals: (
        redteam and redteam["overall_risk"] == "low"
    ),
    "Rail Compliant": lambda paper, reviews, redteam, evals: (
        evals and all(e["score"] >= 5 for e in evals)
    ),
    "High Soundness": lambda paper, reviews, redteam, evals: (
        reviews and any(r["soundness"] >= 4 for r in reviews)
    ),
    "High Novelty": lambda paper, reviews, redteam, evals: (
        reviews and any(r["novelty"] >= 4 for r in reviews)
    ),
}

# Maturity level bonus for composite scoring
MATURITY_BONUS = {"L0": 0, "L1": 0.5, "L2": 1.0, "L3": 1.5, "L4": 2.0, "L5": 2.5}


def compute_composite_score(reviews, maturity_level):
    """Compute composite arena score from reviews + maturity bonus.

    Score = weighted_review_avg + maturity_bonus
    """
    if not reviews:
        return 0.0

    avg_score = sum(r["overall_score"] for r in reviews) / len(reviews)
    bonus = MATURITY_BONUS.get(maturity_level, 0)
    return round(avg_score + bonus, 2)


def compute_badges(paper, reviews, redteam, evals):
    """Determine which badges a paper has earned."""
    badges = []
    for badge_name, rule_fn in BADGE_RULES.items():
        try:
            if rule_fn(paper, reviews, redteam, evals):
                badges.append(badge_name)
        except (KeyError, TypeError):
            continue
    return badges


def promote_paper(paper_id):
    """Promote a paper to the Arena with computed score and badges.

    Returns dict with promotion details.
    """
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise ValueError(f"Paper {paper_id} not found")

    reviews = conn.execute("SELECT * FROM reviews WHERE paper_id = ?", (paper_id,)).fetchall()
    if not reviews:
        conn.close()
        raise ValueError("Paper must be reviewed before promotion")

    reviews = [dict(r) for r in reviews]

    redteam_row = conn.execute(
        "SELECT * FROM redteam_reports WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()
    redteam = dict(redteam_row) if redteam_row else None

    evals = conn.execute("SELECT * FROM eval_results WHERE paper_id = ?", (paper_id,)).fetchall()
    evals = [dict(e) for e in evals]

    paper_dict = dict(paper)
    maturity = paper_dict["maturity_level"] or "L0"

    composite_score = compute_composite_score(reviews, maturity)
    badges = compute_badges(paper_dict, reviews, redteam, evals)
    rail_compliant = "Rail Compliant" in badges

    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO arena_papers
        (paper_id, title, authors, abstract, categories, pdf_path,
         review_score, maturity_level, rail_compliant, review_count, badges, promoted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, paper_dict["title"], paper_dict["authors"], paper_dict["abstract"],
          paper_dict["categories"], paper_dict["pdf_path"], composite_score, maturity,
          1 if rail_compliant else 0, len(reviews), json.dumps(badges), now))

    conn.execute("UPDATE papers SET status = 'published_arena', updated_at = ? WHERE paper_id = ?",
                 (now, paper_id))
    conn.commit()
    conn.close()

    return {
        "paper_id": paper_id,
        "review_score": composite_score,
        "maturity_level": maturity,
        "badges": badges,
        "rail_compliant": rail_compliant,
        "review_count": len(reviews),
    }


def get_leaderboard(category=None, maturity=None, limit=100):
    """Get arena leaderboard with optional filters.

    Returns list of paper dicts sorted by composite score.
    """
    conn = get_db()
    query = "SELECT * FROM arena_papers"
    params = []
    conditions = []

    if category:
        conditions.append("categories LIKE ?")
        params.append(f"%{category}%")
    if maturity:
        conditions.append("maturity_level = ?")
        params.append(maturity)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY review_score DESC, promoted_at DESC LIMIT ?"
    params.append(limit)

    papers = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for p in papers:
        d = dict(p)
        try:
            d["badges"] = json.loads(d.get("badges", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["badges"] = []
        result.append(d)

    return result


def get_arena_stats():
    """Get aggregate arena statistics."""
    conn = get_db()

    total = conn.execute("SELECT COUNT(*) as c FROM arena_papers").fetchone()["c"]
    avg_score_row = conn.execute("SELECT AVG(review_score) as a FROM arena_papers").fetchone()
    avg_score = round(avg_score_row["a"], 1) if avg_score_row["a"] else 0

    rail_compliant = conn.execute(
        "SELECT COUNT(*) as c FROM arena_papers WHERE rail_compliant = 1"
    ).fetchone()["c"]

    maturity_dist = {}
    for level in ["L0", "L1", "L2", "L3", "L4", "L5"]:
        row = conn.execute(
            "SELECT COUNT(*) as c FROM arena_papers WHERE maturity_level = ?", (level,)
        ).fetchone()
        maturity_dist[level] = row["c"]

    conn.close()

    return {
        "total": total,
        "avg_score": avg_score,
        "rail_compliant": rail_compliant,
        "maturity_distribution": maturity_dist,
    }
