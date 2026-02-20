"""AI Scientist Platform — FastAPI backend."""
import os
import sys
import json
import uuid
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

sys.path.insert(0, os.path.dirname(__file__))
from database import get_db, init_db, generate_paper_id
from auth import (
    get_current_user, get_optional_user,
    exchange_sso_token, create_jwt,
    SSO_REDIRECT_URL, SSO_CALLBACK_URL,
)

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "papers"
TEX_DIR = BASE_DIR / "tex_source"

app = FastAPI(title="aiXiv AI Scientist Platform")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.mount("/static", StaticFiles(directory=str(BASE_DIR)), name="static")

@app.on_event("startup")
def startup():
    init_db()
    UPLOAD_DIR.mkdir(exist_ok=True)
    TEX_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# Auth: Login, SSO Callback, Logout, User Info, API Key
# ═══════════════════════════════════════════════════════════════════

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    import urllib.parse
    sso_url = f"{SSO_REDIRECT_URL}?redirect={urllib.parse.quote(SSO_CALLBACK_URL)}"
    return templates.TemplateResponse("login.html", {
        "request": request, "sso_url": sso_url, "error": error,
    })


@app.get("/sso/callback")
async def sso_callback(request: Request, access_token: str = "", token: str = "", code: str = "", sso_token: str = ""):
    # CompareGPT SSO sends the token as access_token; accept other common names too
    tok = access_token or token or code or sso_token
    if not tok:
        logger.warning(f"SSO callback: no token. params={dict(request.query_params)}")
        return RedirectResponse("/login?error=No+token+received")

    user_info = await exchange_sso_token(tok)
    if not user_info or not user_info.get("user_id"):
        return RedirectResponse("/login?error=SSO+validation+failed")

    now = datetime.utcnow().isoformat()
    conn = get_db()
    existing = conn.execute("SELECT * FROM users WHERE user_id = ?",
                            (user_info["user_id"],)).fetchone()
    if existing:
        conn.execute("""
            UPDATE users SET user_name=?, sso_token=?, api_key=?, credit=?, token=?, updated_at=?
            WHERE user_id=?
        """, (user_info["user_name"], tok, user_info["api_key"],
              user_info["credit"], user_info["token"], now, user_info["user_id"]))
    else:
        conn.execute("""
            INSERT INTO users (user_id, user_name, role, credit, token, sso_token, api_key,
                              custom_api_key, custom_api_provider, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)
        """, (user_info["user_id"], user_info["user_name"], user_info.get("role", "user"),
              user_info["credit"], user_info["token"], tok,
              user_info["api_key"], now, now))
    conn.commit()
    conn.close()

    jwt_token = create_jwt(user_info["user_id"], user_info["user_name"],
                           user_info.get("role", "user"))
    response = RedirectResponse("/scientist")
    response.set_cookie("aixiv_token", jwt_token, httponly=True, samesite="lax",
                        max_age=60*60*24*7, path="/")
    return response


@app.get("/api/auth/logout")
async def logout():
    response = RedirectResponse("/login")
    response.delete_cookie("aixiv_token", path="/")
    return response


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    return JSONResponse({
        "user_id": user["user_id"],
        "user_name": user["user_name"],
        "role": user["role"],
        "has_custom_key": bool(user.get("effective_api_key")),
        "effective_provider": user.get("effective_provider"),
    })


@app.post("/api/auth/apikey")
async def set_api_key(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    provider = data.get("provider", "")
    api_key = data.get("api_key", "")

    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        UPDATE users SET custom_api_key=?, custom_api_provider=?, updated_at=?
        WHERE user_id=?
    """, (api_key, provider, now, user["user_id"]))
    conn.commit()
    conn.close()

    return JSONResponse({"status": "ok", "provider": provider, "has_key": bool(api_key)})


# ═══════════════════════════════════════════════════════════════════
# API: Paper Submission
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/submit")
async def submit_paper(
    request: Request,
    title: str = Form(...),
    authors: str = Form(...),
    affiliation: str = Form(""),
    abstract: str = Form(...),
    keywords: str = Form(""),
    categories: str = Form(""),
    full_text: str = Form(""),
    pdf_file: UploadFile = File(None),
    user: dict = Depends(get_current_user),
):
    paper_id = generate_paper_id()
    now = datetime.utcnow().isoformat()

    pdf_path = ""
    if pdf_file and pdf_file.filename:
        safe_name = paper_id.replace(":", "_").replace(".", "_") + ".pdf"
        pdf_path = f"papers/{safe_name}"
        with open(BASE_DIR / pdf_path, "wb") as f:
            shutil.copyfileobj(pdf_file.file, f)

    conn = get_db()
    conn.execute("""
        INSERT INTO papers (paper_id, title, authors, affiliation, abstract,
                          keywords, categories, full_text, pdf_path, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
    """, (paper_id, title, authors, affiliation, abstract,
          keywords, categories, full_text, pdf_path, now, now))
    conn.commit()
    conn.close()

    return JSONResponse({"paper_id": paper_id, "status": "submitted",
                         "message": f"Paper submitted as {paper_id}. Published on aiXiv (Tier 1). Ready for AI review."})


# ═══════════════════════════════════════════════════════════════════
# API: Full Review Pipeline (Peer + RedTeam + Meta)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/review/{paper_id}")
async def review_paper_endpoint(paper_id: str, user: dict = Depends(get_current_user)):
    from orchestrator import run_full_review
    try:
        results = run_full_review(paper_id, api_key=user.get("effective_api_key"),
                                  api_provider=user.get("effective_provider"))
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Review pipeline failed: {str(e)}")

    return JSONResponse({
        "paper_id": paper_id,
        "status": results["new_status"],
        "maturity_level": results["maturity_level"],
        "peer_review": results["peer_review"],
        "redteam": results["redteam"],
        "meta_review": results["meta_review"],
    })


@app.post("/api/review/{paper_id}/peer")
async def peer_review_only(paper_id: str, user: dict = Depends(get_current_user)):
    """Run only peer review (Layer 1-3)."""
    from agents.reviewer_agent import review_paper, extract_flat_scores
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    try:
        result, raw = review_paper(paper["title"], paper["abstract"], paper["full_text"] or "",
                                    api_key=user.get("effective_api_key"),
                                    api_provider=user.get("effective_provider"))
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Peer review failed: {str(e)}")

    flat = extract_flat_scores(result)
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO reviews (paper_id, reviewer_type, review_layer, overall_score,
                           soundness, novelty, clarity, significance, reproducibility,
                           summary, strengths, weaknesses, questions, recommendation,
                           detailed_feedback, maturity_level, gate_analysis, raw_review, created_at)
        VALUES (?, 'ai', 'peer', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, flat["overall_score"], flat["soundness"], flat["novelty"],
          flat["clarity"], flat["significance"], flat.get("reproducibility", 3),
          flat["summary"], flat["strengths"], flat["weaknesses"],
          flat["questions"], flat["recommendation"], flat["detailed_feedback"],
          flat.get("maturity_level", "L0"), flat.get("gate_analysis", ""), raw, now))
    conn.commit()
    conn.close()

    return JSONResponse({"paper_id": paper_id, "review": result, "raw": raw})


@app.post("/api/review/{paper_id}/redteam")
async def redteam_review(paper_id: str, user: dict = Depends(get_current_user)):
    """Run red team analysis."""
    from agents.redteam_agent import redteam_paper
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    try:
        result, raw = redteam_paper(paper["title"], paper["abstract"], paper["full_text"] or "",
                                    api_key=user.get("effective_api_key"),
                                    api_provider=user.get("effective_provider"))
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Red team analysis failed: {str(e)}")

    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO redteam_reports (paper_id, overall_risk, confidence, findings,
                                    attack_scenarios, summary, raw_report, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, result.get("overall_risk", "medium"),
          result.get("confidence_in_conclusions", 0.5),
          json.dumps(result.get("findings", [])),
          json.dumps(result.get("attack_scenarios", [])),
          result.get("summary", ""), raw, now))
    conn.commit()
    conn.close()

    return JSONResponse({"paper_id": paper_id, "redteam": result})


@app.post("/api/review/{paper_id}/meta")
async def meta_review_endpoint(paper_id: str, user: dict = Depends(get_current_user)):
    """Run meta-review synthesis."""
    from agents.meta_reviewer_agent import meta_review
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    # Get latest peer review and redteam
    review = conn.execute(
        "SELECT raw_review FROM reviews WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()
    redteam = conn.execute(
        "SELECT raw_report FROM redteam_reports WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()

    if not review:
        conn.close()
        raise HTTPException(400, "Peer review required before meta-review")

    try:
        result, raw = meta_review(
            paper["title"], paper["abstract"],
            review["raw_review"] if review else "",
            redteam["raw_report"] if redteam else "",
            api_key=user.get("effective_api_key"),
            api_provider=user.get("effective_provider"),
        )
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Meta-review failed: {str(e)}")

    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO meta_reviews (paper_id, final_recommendation, confidence,
                                 justification, maturity_level, required_changes,
                                 suggested_changes, summary_for_authors,
                                 arena_eligible, arena_eligibility_reason,
                                 raw_review, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, result.get("final_recommendation", ""),
          result.get("confidence", 0.5),
          result.get("justification", ""),
          result.get("maturity_level", "L0"),
          json.dumps(result.get("required_changes", [])),
          json.dumps(result.get("suggested_changes", [])),
          result.get("summary_for_authors", ""),
          1 if result.get("arena_eligible") else 0,
          result.get("arena_eligibility_reason", ""),
          raw, now))
    conn.commit()
    conn.close()

    return JSONResponse({"paper_id": paper_id, "meta_review": result})


# ═══════════════════════════════════════════════════════════════════
# API: Get Reviews
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/reviews/{paper_id}")
async def get_reviews(paper_id: str):
    conn = get_db()
    reviews = conn.execute(
        "SELECT * FROM reviews WHERE paper_id = ? ORDER BY created_at DESC", (paper_id,)
    ).fetchall()
    redteam = conn.execute(
        "SELECT * FROM redteam_reports WHERE paper_id = ? ORDER BY created_at DESC", (paper_id,)
    ).fetchall()
    meta = conn.execute(
        "SELECT * FROM meta_reviews WHERE paper_id = ? ORDER BY created_at DESC", (paper_id,)
    ).fetchall()
    conn.close()
    return JSONResponse({
        "reviews": [dict(r) for r in reviews],
        "redteam_reports": [dict(r) for r in redteam],
        "meta_reviews": [dict(r) for r in meta],
    })


# ═══════════════════════════════════════════════════════════════════
# API: Revision
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/revise/{paper_id}")
async def revise_paper(paper_id: str, user: dict = Depends(get_current_user)):
    """Generate AI-assisted revision suggestions."""
    from agents.revision_agent import generate_revisions
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    # Gather all feedback
    review = conn.execute(
        "SELECT raw_review FROM reviews WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()
    redteam = conn.execute(
        "SELECT raw_report FROM redteam_reports WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()
    meta = conn.execute(
        "SELECT raw_review FROM meta_reviews WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()

    paper_text = paper["full_text"] or paper["abstract"]

    try:
        result, raw = generate_revisions(
            paper_text,
            review["raw_review"] if review else "",
            redteam["raw_report"] if redteam else "",
            meta["raw_review"] if meta else "",
            api_key=user.get("effective_api_key"),
            api_provider=user.get("effective_provider"),
        )
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Revision generation failed: {str(e)}")

    now = datetime.utcnow().isoformat()
    version = (paper["version"] or 1) + 1
    conn.execute("""
        INSERT INTO revisions (paper_id, version, changes_summary, revision_letter,
                              revised_text, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'draft', ?)
    """, (paper_id, version,
          json.dumps(result.get("revision_suggestions", [])),
          result.get("revision_letter", ""),
          json.dumps(result.get("new_content", [])), now))
    conn.commit()
    conn.close()

    return JSONResponse({
        "paper_id": paper_id,
        "version": version,
        "revisions": result,
    })


@app.get("/api/revisions/{paper_id}")
async def get_revisions(paper_id: str):
    conn = get_db()
    revisions = conn.execute(
        "SELECT * FROM revisions WHERE paper_id = ? ORDER BY version DESC", (paper_id,)
    ).fetchall()
    conn.close()
    return JSONResponse([dict(r) for r in revisions])


@app.post("/api/revise/{paper_id}/submit")
async def submit_revision(paper_id: str, request: Request, user: dict = Depends(get_current_user)):
    """Submit revised paper for re-review."""
    data = await request.json()
    revised_text = data.get("revised_text", "")
    revised_abstract = data.get("revised_abstract", "")

    conn = get_db()
    now = datetime.utcnow().isoformat()

    updates = ["status = 're_review'", "updated_at = ?"]
    params = [now]
    if revised_text:
        updates.append("full_text = ?")
        params.append(revised_text)
    if revised_abstract:
        updates.append("abstract = ?")
        params.append(revised_abstract)

    version_row = conn.execute("SELECT MAX(version) as v FROM revisions WHERE paper_id = ?",
                               (paper_id,)).fetchone()
    new_version = (version_row["v"] or 1) + 1 if version_row else 2
    updates.append("version = ?")
    params.append(new_version)

    params.append(paper_id)
    conn.execute(f"UPDATE papers SET {', '.join(updates)} WHERE paper_id = ?", params)
    conn.commit()
    conn.close()

    return JSONResponse({"paper_id": paper_id, "status": "re_review", "version": new_version})


@app.post("/api/revise/{paper_id}/apply")
async def apply_selected_revisions(paper_id: str, request: Request, user: dict = Depends(get_current_user)):
    """Apply selected revision suggestions to the paper.

    Request body: {
        "accepted_ids": ["REV-001", "REV-003"],
        "rejected_ids": ["REV-002"]
    }
    """
    from agents.revision_agent import apply_revisions
    data = await request.json()
    accepted_ids = set(data.get("accepted_ids", []))
    rejected_ids = set(data.get("rejected_ids", []))

    if not accepted_ids:
        raise HTTPException(400, "No revisions accepted")

    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    # Get latest revision suggestions
    rev_row = conn.execute(
        "SELECT * FROM revisions WHERE paper_id = ? ORDER BY version DESC LIMIT 1",
        (paper_id,)
    ).fetchone()
    if not rev_row:
        conn.close()
        raise HTTPException(400, "No revision suggestions found")

    try:
        suggestions = json.loads(rev_row["changes_summary"])
    except (json.JSONDecodeError, TypeError):
        suggestions = []

    # If suggestions is a dict with revision_suggestions key, unwrap
    if isinstance(suggestions, dict) and "revision_suggestions" in suggestions:
        suggestions = suggestions["revision_suggestions"]

    accepted = [s for s in suggestions if isinstance(s, dict) and s.get("id") in accepted_ids]
    if not accepted:
        conn.close()
        raise HTTPException(400, "None of the accepted IDs match revision suggestions")

    paper_text = paper["full_text"] or paper["abstract"]
    try:
        revised = apply_revisions(paper_text, accepted,
                                  api_key=user.get("effective_api_key"),
                                  api_provider=user.get("effective_provider"))
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Failed to apply revisions: {str(e)}")

    now = datetime.utcnow().isoformat()
    new_version = (paper["version"] or 1) + 1
    conn.execute("""
        UPDATE papers SET full_text = ?, version = ?, updated_at = ? WHERE paper_id = ?
    """, (revised, new_version, now, paper_id))

    conn.execute("""
        INSERT INTO revisions (paper_id, version, changes_summary, revision_letter,
                              revised_text, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'applied', ?)
    """, (paper_id, new_version,
          json.dumps({"accepted": list(accepted_ids), "rejected": list(rejected_ids)}),
          f"Applied {len(accepted_ids)} revisions, rejected {len(rejected_ids)}",
          revised[:2000], now))
    conn.commit()
    conn.close()

    return JSONResponse({
        "paper_id": paper_id,
        "version": new_version,
        "applied": list(accepted_ids),
        "rejected": list(rejected_ids),
        "revised_text_preview": revised[:500],
    })


# ═══════════════════════════════════════════════════════════════════
# API: Rail Evaluation
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/rail/evaluate/{paper_id}")
async def rail_evaluate(paper_id: str, user: dict = Depends(get_current_user)):
    """Run 4-scenario Rail evaluation."""
    from orchestrator import run_rail_evaluation
    try:
        result = run_rail_evaluation(paper_id, api_key=user.get("effective_api_key"),
                                     api_provider=user.get("effective_provider"))
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"Rail evaluation failed: {str(e)}")

    return JSONResponse({"paper_id": paper_id, "evaluation": result})


@app.post("/api/rail/targeting/{paper_id}")
async def targeting_assess(paper_id: str, user: dict = Depends(get_current_user)):
    """Run targeting system maturity assessment."""
    from rail.targeting import assess_maturity
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    try:
        result, raw = assess_maturity(paper["title"], paper["abstract"], paper["full_text"] or "",
                                      api_key=user.get("effective_api_key"),
                                      api_provider=user.get("effective_provider"))
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Targeting assessment failed: {str(e)}")

    # Update paper maturity level
    level = result.get("current_level", "L0")
    now = datetime.utcnow().isoformat()
    conn.execute("UPDATE papers SET maturity_level = ?, updated_at = ? WHERE paper_id = ?",
                 (level, now, paper_id))
    conn.commit()
    conn.close()

    return JSONResponse({"paper_id": paper_id, "assessment": result})


@app.get("/api/rail/decisions/{paper_id}")
async def get_decisions(paper_id: str):
    """Get DR-AIS decision audit log for a paper."""
    from rail.decision_record import get_decisions
    records = get_decisions(paper_id)
    return JSONResponse(records)


# ═══════════════════════════════════════════════════════════════════
# API: Targeting System (dedicated — saves to DB)
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/targeting/assess/{paper_id}")
async def targeting_assess_full(paper_id: str, request: Request,
                                user: dict = Depends(get_current_user)):
    """Run targeting maturity assessment, save full detail to DB, return checklist."""
    from rail.targeting import assess_maturity
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    target_level = data.get("target_level", "L3") if isinstance(data, dict) else "L3"

    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    try:
        result, raw = assess_maturity(paper["title"], paper["abstract"], paper["full_text"] or "",
                                      api_key=user.get("effective_api_key"),
                                      api_provider=user.get("effective_provider"))
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Targeting assessment failed: {str(e)}")

    current_level = result.get("current_level", "L0")
    targeting_score = result.get("targeting_score", 0)
    maturity_assessment = result.get("maturity_assessment", {})
    now = datetime.utcnow().isoformat()

    # Save to targeting_assessments table
    conn.execute("""
        INSERT INTO targeting_assessments
            (paper_id, current_level, target_level, targeting_score, assessment_json, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, current_level, target_level, targeting_score,
          json.dumps(maturity_assessment),
          user.get("effective_provider", ""), now))

    # Also update paper maturity level
    conn.execute("UPDATE papers SET maturity_level = ?, updated_at = ? WHERE paper_id = ?",
                 (current_level, now, paper_id))
    conn.commit()
    conn.close()

    return JSONResponse({
        "paper_id": paper_id,
        "current_level": current_level,
        "target_level": target_level,
        "targeting_score": targeting_score,
        "next_level": result.get("next_level"),
        "advancement_requirements": result.get("advancement_requirements", []),
        "summary": result.get("summary", ""),
        "maturity_assessment": maturity_assessment,
    })


@app.post("/api/targeting/roadmap/{paper_id}")
async def targeting_roadmap(paper_id: str, request: Request,
                            user: dict = Depends(get_current_user)):
    """Generate LLM roadmap for advancing paper to target level. Updates most recent assessment."""
    from rail.roadmap import generate_roadmap
    data = await request.json()
    current_level = data.get("current_level", "L1")
    target_level = data.get("target_level", "L3")
    gap_items = data.get("gap_items", [])
    assessment_id = data.get("assessment_id")

    conn = get_db()
    paper = conn.execute("SELECT title, abstract FROM papers WHERE paper_id = ?",
                         (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    try:
        roadmap_md = generate_roadmap(
            paper["title"], paper["abstract"],
            current_level, target_level, gap_items,
            api_key=user.get("effective_api_key"),
            api_provider=user.get("effective_provider"),
        )
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"Roadmap generation failed: {str(e)}")

    # Update the specified assessment row (or latest) with the roadmap
    if assessment_id:
        conn.execute("UPDATE targeting_assessments SET roadmap = ? WHERE id = ? AND paper_id = ?",
                     (roadmap_md, assessment_id, paper_id))
    else:
        conn.execute("""
            UPDATE targeting_assessments SET roadmap = ?
            WHERE paper_id = ? AND id = (
                SELECT id FROM targeting_assessments WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1
            )
        """, (roadmap_md, paper_id, paper_id))
    conn.commit()
    conn.close()

    return JSONResponse({"paper_id": paper_id, "roadmap": roadmap_md})


@app.get("/api/targeting/history/{paper_id}")
async def targeting_history(paper_id: str):
    """Return list of past targeting assessments for a paper."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, paper_id, current_level, target_level, targeting_score,
               assessment_json, roadmap, model, created_at
        FROM targeting_assessments
        WHERE paper_id = ?
        ORDER BY created_at DESC
    """, (paper_id,)).fetchall()
    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["assessment_json"] = json.loads(d["assessment_json"])
        except (json.JSONDecodeError, TypeError):
            d["assessment_json"] = {}
        result.append(d)

    return JSONResponse(result)


# ═══════════════════════════════════════════════════════════════════
# API: Arena
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/promote/{paper_id}")
async def promote_to_arena(paper_id: str, user: dict = Depends(get_current_user)):
    from orchestrator import promote_to_arena
    try:
        result = promote_to_arena(paper_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Promotion failed: {str(e)}")

    return JSONResponse({
        "paper_id": paper_id,
        "status": "published_arena",
        "review_score": result["review_score"],
        "maturity_level": result["maturity_level"],
        "badges": result["badges"],
        "message": "Paper promoted to Arena (Tier 2)!"
    })


@app.get("/api/arena")
async def list_arena():
    conn = get_db()
    papers = conn.execute(
        "SELECT * FROM arena_papers ORDER BY review_score DESC"
    ).fetchall()
    conn.close()
    result = []
    for p in papers:
        d = dict(p)
        try:
            d["badges"] = json.loads(d.get("badges", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["badges"] = []
        result.append(d)
    return JSONResponse(result)


# ═══════════════════════════════════════════════════════════════════
# API: Writer Pipeline
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/write/idea")
async def write_idea(request: Request, user: dict = Depends(get_current_user)):
    """Generate research ideas for a topic."""
    from agents.idea_agent import run_idea_pipeline
    data = await request.json()
    topic = data.get("topic", "")
    if not topic:
        raise HTTPException(400, "topic is required")

    session_id = data.get("session_id", str(uuid.uuid4()))

    try:
        idea, log = run_idea_pipeline(topic, api_key=user.get("effective_api_key"),
                                      api_provider=user.get("effective_provider"))
    except Exception as e:
        raise HTTPException(500, f"Idea generation failed: {str(e)}")

    conn = get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO writing_sessions (session_id, session_type, title, current_step,
                                     idea, status, created_at, updated_at)
        VALUES (?, 'pipeline', ?, 'idea', ?, 'active', ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            idea = excluded.idea, current_step = 'idea', updated_at = excluded.updated_at
    """, (session_id, idea.get("title", ""), json.dumps(idea), now, now))
    conn.commit()
    conn.close()

    return JSONResponse({"session_id": session_id, "idea": idea, "log": log})


@app.post("/api/write/novelty")
async def write_novelty(request: Request, user: dict = Depends(get_current_user)):
    """Run novelty check on an idea."""
    from agents.literature_agent import run_novelty_check
    data = await request.json()
    session_id = data.get("session_id", "")
    idea_text = data.get("idea_text", "")

    if not idea_text and session_id:
        conn = get_db()
        session = conn.execute("SELECT idea FROM writing_sessions WHERE session_id = ?",
                               (session_id,)).fetchone()
        conn.close()
        if session and session["idea"]:
            idea_data = json.loads(session["idea"])
            idea_text = f"{idea_data.get('title', '')}: {idea_data.get('description', '')}"

    if not idea_text:
        raise HTTPException(400, "idea_text or session_id with existing idea required")

    try:
        assessment, papers, log = run_novelty_check(idea_text,
                                                     api_key=user.get("effective_api_key"),
                                                     api_provider=user.get("effective_provider"))
    except Exception as e:
        raise HTTPException(500, f"Novelty check failed: {str(e)}")

    if session_id:
        conn = get_db()
        now = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE writing_sessions SET current_step = 'novelty',
                related_papers = ?, updated_at = ?
            WHERE session_id = ?
        """, (json.dumps(papers[:10]), now, session_id))
        conn.commit()
        conn.close()

    return JSONResponse({
        "session_id": session_id,
        "assessment": assessment,
        "papers_found": len(papers),
        "top_papers": papers[:10],
        "log": log,
    })


@app.post("/api/write/method")
async def write_method(request: Request, user: dict = Depends(get_current_user)):
    """Generate methodology."""
    from agents.method_agent import run_methodology_pipeline
    data = await request.json()
    session_id = data.get("session_id", "")
    idea = data.get("idea", "")

    related_papers = None
    if session_id:
        conn = get_db()
        session = conn.execute("SELECT * FROM writing_sessions WHERE session_id = ?",
                               (session_id,)).fetchone()
        conn.close()
        if session:
            if not idea and session["idea"]:
                idea = json.loads(session["idea"])
            if session["related_papers"]:
                related_papers = json.loads(session["related_papers"])

    if not idea:
        raise HTTPException(400, "idea or session_id with existing idea required")

    try:
        methodology, review, log = run_methodology_pipeline(idea, related_papers,
                                                              api_key=user.get("effective_api_key"),
                                                              api_provider=user.get("effective_provider"))
    except Exception as e:
        raise HTTPException(500, f"Methodology generation failed: {str(e)}")

    if session_id:
        conn = get_db()
        now = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE writing_sessions SET current_step = 'method',
                methodology = ?, updated_at = ?
            WHERE session_id = ?
        """, (methodology, now, session_id))
        conn.commit()
        conn.close()

    return JSONResponse({
        "session_id": session_id,
        "methodology": methodology,
        "review_feedback": review,
        "log": log,
    })


@app.post("/api/write/compose")
async def write_compose(request: Request, user: dict = Depends(get_current_user)):
    """Compose full paper."""
    from agents.paper_agent import compose_full_paper, format_paper_markdown
    data = await request.json()
    session_id = data.get("session_id", "")
    idea = data.get("idea", "")
    methodology = data.get("methodology", "")
    authors = data.get("authors", "")

    related_papers = None
    if session_id:
        conn = get_db()
        session = conn.execute("SELECT * FROM writing_sessions WHERE session_id = ?",
                               (session_id,)).fetchone()
        conn.close()
        if session:
            if not idea and session["idea"]:
                idea = json.loads(session["idea"])
            if not methodology and session["methodology"]:
                methodology = session["methodology"]
            if session["related_papers"]:
                related_papers = json.loads(session["related_papers"])

    if not idea or not methodology:
        raise HTTPException(400, "idea and methodology required (or session_id with existing data)")

    try:
        sections, log = compose_full_paper(idea, methodology, related_papers,
                                              api_key=user.get("effective_api_key"),
                                              api_provider=user.get("effective_provider"))
    except Exception as e:
        raise HTTPException(500, f"Paper composition failed: {str(e)}")

    title = idea.get("title", "Untitled") if isinstance(idea, dict) else "Untitled"
    paper_md = format_paper_markdown(title, authors, sections)

    if session_id:
        conn = get_db()
        now = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE writing_sessions SET current_step = 'compose',
                sections = ?, current_content = ?, status = 'completed', updated_at = ?
            WHERE session_id = ?
        """, (json.dumps(sections), paper_md, now, session_id))
        conn.commit()
        conn.close()

    return JSONResponse({
        "session_id": session_id,
        "sections": sections,
        "full_paper": paper_md,
        "log": log,
    })


@app.post("/api/write/section")
async def write_section(request: Request, user: dict = Depends(get_current_user)):
    """Write or revise a specific section."""
    from agents.paper_agent import compose_section, revise_section
    data = await request.json()
    section_name = data.get("section", "")
    idea = data.get("idea", "")
    methodology = data.get("methodology", "")
    feedback = data.get("feedback", "")
    current_content = data.get("current_content", "")
    session_id = data.get("session_id", "")

    if session_id:
        conn = get_db()
        session = conn.execute("SELECT * FROM writing_sessions WHERE session_id = ?",
                               (session_id,)).fetchone()
        conn.close()
        if session:
            if not idea and session["idea"]:
                idea = json.loads(session["idea"])
            if not methodology and session["methodology"]:
                methodology = session["methodology"]

    if not section_name:
        raise HTTPException(400, "section name required")

    try:
        if feedback and current_content:
            result = revise_section(section_name, current_content, feedback,
                                    api_key=user.get("effective_api_key"),
                                    api_provider=user.get("effective_provider"))
        else:
            result = compose_section(section_name, idea, methodology,
                                     api_key=user.get("effective_api_key"),
                                     api_provider=user.get("effective_provider"))
    except Exception as e:
        raise HTTPException(500, f"Section writing failed: {str(e)}")

    return JSONResponse({"section": section_name, "content": result})


@app.post("/api/write/chat")
async def write_chat(request: Request, user: dict = Depends(get_current_user)):
    """Free-form chat with AI writer."""
    from agents.base_agent import call_llm
    data = await request.json()
    session_id = data.get("session_id", "")
    prompt = data.get("prompt", "")
    if not prompt:
        raise HTTPException(400, "prompt is required")

    conn = get_db()
    now = datetime.utcnow().isoformat()

    history = []
    if session_id:
        session = conn.execute(
            "SELECT * FROM writing_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session:
            history = json.loads(session["conversation_history"])
        else:
            session_id = str(uuid.uuid4())
    else:
        session_id = str(uuid.uuid4())

    writer_system = """You are an AI Scientist Writer — a world-class scientific writing assistant.
Follow the SolveEverything.org framework:
- Help move research from ill-posed ideas to measurable, repeatable, industrialized results
- Ensure papers have clear metrics, reproducible results, and auditable claims
- Write in clear, concise academic prose with LaTeX math when appropriate."""

    history.append({"role": "user", "content": prompt})

    try:
        reply = call_llm(writer_system, history, max_tokens=4096,
                         api_key=user.get("effective_api_key"),
                         api_provider=user.get("effective_provider"))
    except Exception as e:
        conn.close()
        raise HTTPException(500, f"AI writing failed: {str(e)}")

    history.append({"role": "assistant", "content": reply})

    conn.execute("""
        INSERT INTO writing_sessions (session_id, session_type, current_content,
                                     conversation_history, status, created_at, updated_at)
        VALUES (?, 'chat', ?, ?, 'active', ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            current_content = excluded.current_content,
            conversation_history = excluded.conversation_history,
            updated_at = excluded.updated_at
    """, (session_id, reply, json.dumps(history), now, now))
    conn.commit()
    conn.close()

    return JSONResponse({"session_id": session_id, "response": reply})


@app.get("/api/write/session/{session_id}")
async def get_session(session_id: str):
    conn = get_db()
    session = conn.execute(
        "SELECT * FROM writing_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    if not session:
        raise HTTPException(404, "Session not found")
    d = dict(session)
    for field in ["idea", "sections", "conversation_history", "related_papers"]:
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return JSONResponse(d)


# ═══════════════════════════════════════════════════════════════════
# API: Papers & Dashboard
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/papers")
async def list_papers(status: str = None):
    conn = get_db()
    if status:
        papers = conn.execute(
            "SELECT * FROM papers WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        papers = conn.execute("SELECT * FROM papers ORDER BY created_at DESC").fetchall()
    conn.close()
    return JSONResponse([dict(p) for p in papers])


@app.get("/api/paper/{paper_id}")
async def get_paper(paper_id: str):
    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    conn.close()
    if not paper:
        raise HTTPException(404, "Paper not found")
    return JSONResponse(dict(paper))


@app.get("/api/dashboard")
async def dashboard_stats():
    from orchestrator import get_pipeline_stats
    stats = get_pipeline_stats()
    return JSONResponse(stats)


# ═══════════════════════════════════════════════════════════════════
# Page Routes
# ═══════════════════════════════════════════════════════════════════

@app.get("/scientist", response_class=HTMLResponse)
async def scientist_page(request: Request, user: dict = Depends(get_optional_user)):
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("scientist.html", {"request": request, "user": user})

@app.get("/writer", response_class=HTMLResponse)
async def writer_page(request: Request, user: dict = Depends(get_optional_user)):
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("writer.html", {"request": request, "user": user})

@app.get("/reviewer", response_class=HTMLResponse)
async def reviewer_page(request: Request, user: dict = Depends(get_optional_user)):
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("reviewer.html", {"request": request, "user": user})

@app.get("/arena", response_class=HTMLResponse)
async def arena_page(request: Request, user: dict = Depends(get_optional_user)):
    return templates.TemplateResponse("arena.html", {"request": request, "user": user})

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, user: dict = Depends(get_optional_user)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})

@app.get("/targeting", response_class=HTMLResponse)
async def targeting_page(request: Request, user: dict = Depends(get_optional_user)):
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("targeting.html", {"request": request, "user": user})

@app.get("/pwm", response_class=HTMLResponse)
async def pwm_page(request: Request, user: dict = Depends(get_optional_user)):
    return templates.TemplateResponse("pwm.html", {"request": request, "user": user})

@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: dict = Depends(get_optional_user)):
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})

@app.get("/writer", response_class=HTMLResponse)
async def writer_redirect(request: Request):
    return RedirectResponse("/scientist?mode=write", status_code=301)


# ═══════════════════════════════════════════════════════════════════
# API: SSE Streaming Endpoints
# ═══════════════════════════════════════════════════════════════════

async def _sse_event(event_type: str, data: dict) -> str:
    """Format a server-sent event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


@app.post("/api/stream/review/{paper_id}")
async def stream_review(paper_id: str, user: dict = Depends(get_current_user)):
    """SSE stream for the full review pipeline."""
    import asyncio

    async def generate():
        yield await _sse_event("status", {"step": "start", "message": "Starting review pipeline..."})

        conn = get_db()
        paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        if not paper:
            conn.close()
            yield await _sse_event("error", {"message": "Paper not found"})
            return
        conn.close()

        # Step 1: Peer Review
        yield await _sse_event("status", {"step": "peer_review", "message": "Running peer review (3-layer analysis)..."})
        try:
            from orchestrator import run_full_review
            results = run_full_review(paper_id, api_key=user.get("effective_api_key"),
                                      api_provider=user.get("effective_provider"))
            yield await _sse_event("peer_review", {"review": results.get("peer_review", {})})
            yield await _sse_event("status", {"step": "peer_review_done", "message": "Peer review complete"})
            yield await _sse_event("redteam", {"report": results.get("redteam", {})})
            yield await _sse_event("status", {"step": "redteam_done", "message": "Red team analysis complete"})
            yield await _sse_event("meta_review", {"review": results.get("meta_review", {})})
            yield await _sse_event("status", {"step": "meta_done", "message": "Meta-review complete"})
            yield await _sse_event("complete", {
                "paper_id": paper_id,
                "status": results.get("new_status", ""),
                "maturity_level": results.get("maturity_level", "L0"),
                "peer_review": results.get("peer_review", {}),
                "redteam": results.get("redteam", {}),
                "meta_review": results.get("meta_review", {}),
            })
        except Exception as e:
            yield await _sse_event("error", {"message": str(e)})

    return StreamingResponse(generate(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/stream/write/idea")
async def stream_idea(request: Request, user: dict = Depends(get_current_user)):
    """SSE stream for idea generation."""
    data = await request.json()
    topic = data.get("topic", "")
    session_id = data.get("session_id", str(uuid.uuid4()))

    async def generate():
        if not topic:
            yield await _sse_event("error", {"message": "topic is required"})
            return

        yield await _sse_event("status", {"step": "start", "message": "Generating 5 research ideas..."})

        try:
            from agents.idea_agent import run_idea_pipeline
            idea, log = run_idea_pipeline(topic, api_key=user.get("effective_api_key"),
                                          api_provider=user.get("effective_provider"))

            for entry in log:
                yield await _sse_event("log", {"entry": entry})

            conn = get_db()
            now = datetime.utcnow().isoformat()
            conn.execute("""
                INSERT INTO writing_sessions (session_id, session_type, title, current_step,
                                             idea, status, created_at, updated_at)
                VALUES (?, 'pipeline', ?, 'idea', ?, 'active', ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    idea = excluded.idea, current_step = 'idea', updated_at = excluded.updated_at
            """, (session_id, idea.get("title", ""), json.dumps(idea), now, now))
            conn.commit()
            conn.close()

            yield await _sse_event("complete", {"session_id": session_id, "idea": idea})
        except Exception as e:
            yield await _sse_event("error", {"message": str(e)})

    return StreamingResponse(generate(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/stream/pipeline")
async def stream_full_pipeline(request: Request, user: dict = Depends(get_current_user)):
    """SSE stream for the full AI Scientist pipeline: write → submit → review → revise."""
    data = await request.json()
    topic = data.get("topic", "")
    authors = data.get("authors", "AI Scientist")

    async def generate():
        if not topic:
            yield await _sse_event("error", {"message": "topic is required"})
            return

        import queue
        import threading

        event_queue = queue.Queue()

        def callback(step, step_data):
            event_queue.put((step, step_data))

        def run_pipeline():
            try:
                from orchestrator import run_full_pipeline
                result = run_full_pipeline(topic, authors, callback=callback,
                                           api_key=user.get("effective_api_key"),
                                           api_provider=user.get("effective_provider"))
                event_queue.put(("_result", result))
            except Exception as e:
                event_queue.put(("_error", {"message": str(e)}))

        thread = threading.Thread(target=run_pipeline, daemon=True)
        thread.start()

        while True:
            try:
                step, data_item = event_queue.get(timeout=300)
            except queue.Empty:
                yield await _sse_event("error", {"message": "Pipeline timed out"})
                return

            if step == "_result":
                # Serialize final result (trim large fields)
                final = {
                    "paper_id": data_item.get("paper_id", ""),
                    "title": data_item.get("idea", {}).get("title", "Untitled"),
                    "status": data_item.get("review", {}).get("new_status", ""),
                    "maturity": data_item.get("review", {}).get("maturity_level", "L0"),
                    "idea": data_item.get("idea", {}),
                    "novelty": data_item.get("novelty", {}),
                    "review_summary": {
                        "recommendation": data_item.get("review", {}).get("meta_review", {}).get("final_recommendation", ""),
                        "maturity_level": data_item.get("review", {}).get("maturity_level", "L0"),
                    },
                    "num_revisions": len(data_item.get("revisions", {}).get("revision_suggestions", [])),
                }
                yield await _sse_event("pipeline_complete", final)
                return
            elif step == "_error":
                yield await _sse_event("error", data_item)
                return
            else:
                yield await _sse_event(step, data_item)

    return StreamingResponse(generate(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ═══════════════════════════════════════════════════════════════════
# API: Author Response to Review
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/review/{paper_id}/respond")
async def author_respond(paper_id: str, request: Request, user: dict = Depends(get_current_user)):
    """Author responds to review with rebuttal/revisions.

    Request body: {
        "response_text": "Author's response to reviewers",
        "addressed_items": ["R1.1", "R1.3", "RT.2"],
        "revised_text": "Optionally, the revised full text",
        "revised_abstract": "Optionally, the revised abstract"
    }
    """
    data = await request.json()
    response_text = data.get("response_text", "")
    addressed_items = data.get("addressed_items", [])
    revised_text = data.get("revised_text", "")
    revised_abstract = data.get("revised_abstract", "")

    if not response_text:
        raise HTTPException(400, "response_text is required")

    conn = get_db()
    paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
    if not paper:
        conn.close()
        raise HTTPException(404, "Paper not found")

    now = datetime.utcnow().isoformat()

    # Generate revision letter using AI
    from agents.revision_agent import generate_revision_letter

    # Get review feedback
    review = conn.execute(
        "SELECT raw_review FROM reviews WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id,)
    ).fetchone()

    try:
        letter = generate_revision_letter(
            paper["title"],
            review["raw_review"] if review else "",
            response_text,
            api_key=user.get("effective_api_key"),
            api_provider=user.get("effective_provider"),
        )
    except Exception:
        letter = response_text

    # Create revision record
    version = (paper["version"] or 1) + 1
    conn.execute("""
        INSERT INTO revisions (paper_id, version, changes_summary, revision_letter,
                              revised_text, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'submitted', ?)
    """, (paper_id, version,
          json.dumps({"addressed_items": addressed_items, "author_notes": response_text}),
          letter,
          revised_text or "", now))

    # Update paper if revised text provided
    updates = ["updated_at = ?"]
    params = [now]
    if revised_text:
        updates.append("full_text = ?")
        params.append(revised_text)
    if revised_abstract:
        updates.append("abstract = ?")
        params.append(revised_abstract)

    updates.append("version = ?")
    params.append(version)

    # Transition to re_review if in revision state
    if paper["status"] in ("revision", "rejected"):
        updates.append("status = ?")
        params.append("re_review")

    params.append(paper_id)
    conn.execute(f"UPDATE papers SET {', '.join(updates)} WHERE paper_id = ?", params)
    conn.commit()
    conn.close()

    from rail.decision_record import record_decision
    record_decision(paper_id, "author_response", "author", "",
                    f"Addressed: {', '.join(addressed_items)}", response_text[:500])

    return JSONResponse({
        "paper_id": paper_id,
        "version": version,
        "revision_letter": letter,
        "status": "re_review" if paper["status"] in ("revision", "rejected") else paper["status"],
    })


# ═══════════════════════════════════════════════════════════════════
# API: Paper Comparison (Arena)
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/arena/compare")
async def compare_papers(paper_ids: str = ""):
    """Compare multiple arena papers side by side.

    Query param: paper_ids=aiXiv:2502.001,aiXiv:2502.002
    """
    if not paper_ids:
        raise HTTPException(400, "paper_ids query parameter required (comma-separated)")

    ids = [pid.strip() for pid in paper_ids.split(",") if pid.strip()]
    if len(ids) < 2:
        raise HTTPException(400, "At least 2 paper IDs required for comparison")

    conn = get_db()
    comparison = []
    for pid in ids[:5]:  # max 5 papers
        paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (pid,)).fetchone()
        if not paper:
            continue

        review = conn.execute(
            "SELECT * FROM reviews WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1",
            (pid,)
        ).fetchone()
        arena = conn.execute(
            "SELECT * FROM arena_papers WHERE paper_id = ?", (pid,)
        ).fetchone()

        entry = {
            "paper_id": pid,
            "title": paper["title"],
            "authors": paper["authors"],
            "abstract": paper["abstract"][:300],
            "maturity_level": paper["maturity_level"] or "L0",
            "status": paper["status"],
            "scores": {},
            "arena_score": None,
            "badges": [],
        }
        if review:
            for dim in ["soundness", "novelty", "clarity", "significance", "reproducibility"]:
                entry["scores"][dim] = review[dim] if review[dim] else 0
            entry["scores"]["overall"] = review["overall_score"] if review["overall_score"] else 0
        if arena:
            entry["arena_score"] = arena["review_score"]
            try:
                entry["badges"] = json.loads(arena["badges"]) if arena["badges"] else []
            except (json.JSONDecodeError, TypeError):
                entry["badges"] = []

        comparison.append(entry)

    conn.close()
    return JSONResponse(comparison)


# ═══════════════════════════════════════════════════════════════════
# API: Export (Writer)
# ═══════════════════════════════════════════════════════════════════

@app.get("/api/write/export/{session_id}")
async def export_paper(session_id: str, format: str = "markdown"):
    """Export paper from a writing session.

    Query param: format=markdown|latex
    """
    conn = get_db()
    session = conn.execute(
        "SELECT * FROM writing_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()

    if not session:
        raise HTTPException(404, "Session not found")

    content = session["current_content"] or ""
    title = session["title"] or "Untitled"

    if format == "latex":
        # Convert markdown-style paper to LaTeX
        latex = _md_to_latex(title, content)
        return HTMLResponse(content=latex, media_type="text/plain",
                          headers={"Content-Disposition": f'attachment; filename="{title[:40]}.tex"'})
    else:
        return HTMLResponse(content=content, media_type="text/markdown",
                          headers={"Content-Disposition": f'attachment; filename="{title[:40]}.md"'})


def _md_to_latex(title, md_content):
    """Simple markdown to LaTeX converter for papers."""
    lines = md_content.split("\n")
    latex_lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{graphicx}",
        r"\usepackage{hyperref}",
        r"\usepackage[margin=1in]{geometry}",
        "",
        r"\title{" + title.replace("_", r"\_") + "}",
        r"\date{}",
        "",
        r"\begin{document}",
        r"\maketitle",
        "",
    ]
    for line in lines:
        if line.startswith("# "):
            latex_lines.append(r"\section{" + line[2:] + "}")
        elif line.startswith("## "):
            latex_lines.append(r"\subsection{" + line[3:] + "}")
        elif line.startswith("### "):
            latex_lines.append(r"\subsubsection{" + line[4:] + "}")
        elif line.startswith("- "):
            latex_lines.append(r"\begin{itemize}")
            latex_lines.append(r"\item " + line[2:])
            latex_lines.append(r"\end{itemize}")
        else:
            latex_lines.append(line)
    latex_lines.append("")
    latex_lines.append(r"\end{document}")
    return "\n".join(latex_lines)


# ═══════════════════════════════════════════════════════════════════
# API: Reference Check
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/reference/check")
async def reference_check(request: Request, user: dict = Depends(get_current_user)):
    """Extract and verify references in a paper using LLM."""
    from agents.base_agent import call_llm, parse_json_from_response
    data = await request.json()
    paper_id = data.get("paper_id")
    full_text = data.get("full_text", "")

    if paper_id and not full_text:
        conn = get_db()
        paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        conn.close()
        if paper:
            full_text = paper["full_text"] or paper["abstract"]

    if not full_text:
        raise HTTPException(400, "full_text or paper_id required")

    ref_system = """You are a Reference Checker for academic papers. Extract all references and assess each one.
Output a JSON array (no wrapper):
[{"index":1,"citation":"full citation text","authors":["Name1"],"title":"Reference title",
  "year":2024,"venue":"Journal or Conference","status":"verifiable|suspicious|unverifiable",
  "notes":"Brief assessment of credibility"}]
status meanings:
- verifiable: complete citation with real-looking authors, title, venue, year
- suspicious: year in future, authors/venue look wrong or inconsistent, possible hallucination
- unverifiable: too incomplete to assess"""

    prompt = f"""Extract and assess all references from this paper. Focus on the References/Bibliography section.

Paper text (up to last 4000 chars where references usually are):
{full_text[-4000:]}

Return ONLY the JSON array of references."""

    try:
        response = call_llm(ref_system, [{"role": "user", "content": prompt}],
                            max_tokens=4096, temperature=0.1,
                            api_key=user.get("effective_api_key"),
                            api_provider=user.get("effective_provider"))
        references = parse_json_from_response(response)
        if not isinstance(references, list):
            references = []
    except Exception as e:
        raise HTTPException(500, f"Reference check failed: {str(e)}")

    return JSONResponse({"references": references, "total": len(references)})


# ═══════════════════════════════════════════════════════════════════
# API: PWM Computational Imaging
# ═══════════════════════════════════════════════════════════════════

@app.post("/api/pwm/analyze")
async def pwm_analyze(request: Request, user: dict = Depends(get_current_user)):
    """AI analysis of a computational imaging / PWM problem."""
    from agents.base_agent import call_llm
    data = await request.json()
    imaging_type = data.get("imaging_type", "spectral")
    problem = data.get("problem", "")
    physical_params = data.get("physical_params", "")
    goal = data.get("goal", "reconstruction")

    if not problem:
        raise HTTPException(400, "problem description is required")

    pwm_system = """You are an expert in computational imaging and Physics World Models (PWM).
You specialise in:
- Solving inverse problems with known/learnable physical measurement operators
- Hyperspectral imaging (CASSI, CASSI-H), lensless cameras, compressive sensing
- Operator mismatch detection and calibration correction
- End-to-end differentiable physics-based learning
- Physics-constrained deep learning for imaging reconstruction

Provide detailed, actionable technical analysis."""

    prompt = f"""## Computational Imaging Problem Analysis

**Imaging Modality:** {imaging_type}
**Goal:** {goal}
**Problem:** {problem}
{f'**Physical Parameters:** {physical_params}' if physical_params else ''}

Please provide a comprehensive PWM-based analysis with these sections:

### 1. Problem Formulation
Mathematical formulation of the forward model y = Φx + n, identifying the measurement operator Φ, signal x, and noise n.

### 2. Key Challenges
Main technical challenges specific to this problem (operator mismatch, noise, ill-posedness, etc.).

### 3. Recommended PWM Approach
Specific Physics World Model methodology for this problem, including:
- Network architecture recommendations
- How to embed the physical model as a differentiable layer
- Training strategy and loss functions
- Data requirements

### 4. Relevant Methods & Papers
Key algorithms and papers to compare against (algorithm names, venues, years).

### 5. Evaluation Protocol
Recommended metrics (PSNR, SSIM, SAM, etc.) and evaluation datasets.

### 6. Implementation Guidance
Practical steps to implement this approach in PyTorch/JAX.

### 7. Expected Maturity Level
Expected L0-L5 maturity level once the method is fully implemented, and what would be needed to advance."""

    try:
        analysis = call_llm(pwm_system, [{"role": "user", "content": prompt}],
                            max_tokens=3000, temperature=0.4,
                            api_key=user.get("effective_api_key"),
                            api_provider=user.get("effective_provider"))
    except Exception as e:
        raise HTTPException(500, f"PWM analysis failed: {str(e)}")

    return JSONResponse({"analysis": analysis, "imaging_type": imaging_type, "goal": goal})


@app.post("/api/pwm/review")
async def pwm_review(request: Request, user: dict = Depends(get_current_user)):
    """Specialized PWM review of a computational imaging paper."""
    from agents.base_agent import call_llm
    data = await request.json()
    paper_id = data.get("paper_id")
    paper_text = data.get("paper_text", "")

    if paper_id and not paper_text:
        conn = get_db()
        paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", (paper_id,)).fetchone()
        conn.close()
        if paper:
            paper_text = f"Title: {paper['title']}\n\n{paper['abstract']}\n\n{paper['full_text'] or ''}"

    if not paper_text:
        raise HTTPException(400, "paper_id or paper_text required")

    pwm_review_system = """You are a specialized reviewer for computational imaging papers using the Physics World Model (PWM) framework.
Evaluate papers specifically on: physics model correctness, operator specification, reconstruction quality claims, reproducibility of physics parameters, and comparison with physics-based baselines."""

    prompt = f"""Review this computational imaging paper from a Physics World Model perspective:

{paper_text[:5000]}

Evaluate:
1. **Physics Model Correctness**: Is the measurement operator properly specified? Are physics constraints respected?
2. **Forward Model**: Is y = Φx + n properly formulated? Is Φ realistic and well-defined?
3. **Operator Mismatch**: Does the paper address potential calibration errors or operator mismatch?
4. **Reconstruction Quality**: Are PSNR/SSIM/SAM metrics reported on standard datasets?
5. **Physics Baselines**: Are traditional physics-based methods (ADMM, primal-dual, etc.) compared?
6. **Reproducibility**: Are physical parameters (wavelengths, spatial resolution, mask patterns) fully specified?
7. **PWM Maturity**: What L0-L5 level does this work achieve for the computational imaging field?
8. **Key Strengths & Weaknesses**: Top 3 strengths and weaknesses from a physics-informed perspective.
9. **Recommendation**: Accept / Minor Revision / Major Revision / Reject with justification."""

    try:
        review = call_llm(pwm_review_system, [{"role": "user", "content": prompt}],
                          max_tokens=2500, temperature=0.3,
                          api_key=user.get("effective_api_key"),
                          api_provider=user.get("effective_provider"))
    except Exception as e:
        raise HTTPException(500, f"PWM review failed: {str(e)}")

    return JSONResponse({"review": review})


# ═══════════════════════════════════════════════════════════════════
# API: Save paper output to CompareGPT directory
# ═══════════════════════════════════════════════════════════════════

COMPAREGPT_OUTPUT_DIR = Path("/home/spiritai/CompareGPT-AIScientist/output")

@app.post("/api/write/save-output/{session_id}")
async def save_paper_output(session_id: str):
    """Save composed paper to CompareGPT output directory."""
    conn = get_db()
    session = conn.execute(
        "SELECT * FROM writing_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()

    if not session or not session["current_content"]:
        raise HTTPException(404, "Session or paper content not found")

    try:
        out_dir = COMPAREGPT_OUTPUT_DIR / session_id
        out_dir.mkdir(parents=True, exist_ok=True)

        paper_path = out_dir / "paper.md"
        paper_path.write_text(session["current_content"], encoding="utf-8")

        # Also save metadata
        meta = {
            "session_id": session_id,
            "title": session["title"] or "",
            "step": session["current_step"] or "",
            "created_at": session["created_at"],
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

        return JSONResponse({"path": str(paper_path), "session_id": session_id})
    except Exception as e:
        raise HTTPException(500, f"Failed to save output: {str(e)}")


# ═══════════════════════════════════════════════════════════════════
# API: Local Auth (register + login)
# ═══════════════════════════════════════════════════════════════════

import hashlib

@app.post("/api/auth/register")
async def local_register(request: Request):
    """Register a local account (username + email + password)."""
    data = await request.json()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    password = data.get("password", "")

    if not username or not password:
        raise HTTPException(400, "username and password are required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    # Hash password
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    user_id = f"local_{username.lower().replace(' ', '_')}"

    conn = get_db()
    existing = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ? OR user_name = ?",
        (user_id, username)
    ).fetchone()
    if existing:
        conn.close()
        raise HTTPException(400, "Username already taken")

    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO users (user_id, user_name, role, credit, token, sso_token, api_key,
                          custom_api_key, custom_api_provider, created_at, updated_at)
        VALUES (?, ?, 'user', 0, 0, ?, '', '', '', ?, ?)
    """, (user_id, username, pw_hash, now, now))
    conn.commit()
    conn.close()

    jwt_token = create_jwt(user_id, username, "user")
    response = JSONResponse({"user_id": user_id, "user_name": username, "status": "registered", "redirect": "/dashboard"})
    response.set_cookie("aixiv_token", jwt_token, httponly=True, samesite="lax",
                        max_age=60*60*24*7, path="/")
    return response


@app.post("/api/auth/login")
async def local_login(request: Request):
    """Local login with username/email + password."""
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password", "")

    if not username or not password:
        raise HTTPException(400, "username and password are required")

    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    user_id = f"local_{username.lower().replace(' ', '_')}"

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE (user_id = ? OR user_name = ?) AND sso_token = ?",
        (user_id, username, pw_hash)
    ).fetchone()
    conn.close()

    if not user:
        raise HTTPException(401, "Invalid username or password")

    jwt_token = create_jwt(user["user_id"], user["user_name"], user["role"])
    response = JSONResponse({"user_id": user["user_id"], "user_name": user["user_name"], "redirect": "/dashboard"})
    response.set_cookie("aixiv_token", jwt_token, httponly=True, samesite="lax",
                        max_age=60*60*24*7, path="/")
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8501)
