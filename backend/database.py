"""SQLite database for AI Scientist platform."""
import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'aixiv.db')

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS papers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        authors TEXT NOT NULL,
        affiliation TEXT DEFAULT '',
        abstract TEXT NOT NULL,
        keywords TEXT DEFAULT '',
        categories TEXT DEFAULT '',
        full_text TEXT DEFAULT '',
        pdf_path TEXT DEFAULT '',
        tex_path TEXT DEFAULT '',
        status TEXT DEFAULT 'submitted',
        maturity_level TEXT DEFAULT 'L0',
        version INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        reviewer_type TEXT DEFAULT 'ai',
        review_layer TEXT DEFAULT 'peer',
        overall_score INTEGER DEFAULT 0,
        soundness INTEGER DEFAULT 0,
        novelty INTEGER DEFAULT 0,
        clarity INTEGER DEFAULT 0,
        significance INTEGER DEFAULT 0,
        reproducibility INTEGER DEFAULT 0,
        summary TEXT DEFAULT '',
        strengths TEXT DEFAULT '',
        weaknesses TEXT DEFAULT '',
        questions TEXT DEFAULT '',
        recommendation TEXT DEFAULT '',
        detailed_feedback TEXT DEFAULT '',
        maturity_level TEXT DEFAULT 'L0',
        gate_analysis TEXT DEFAULT '',
        raw_review TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
    );

    CREATE TABLE IF NOT EXISTS redteam_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        overall_risk TEXT DEFAULT 'medium',
        confidence REAL DEFAULT 0.5,
        findings TEXT DEFAULT '[]',
        attack_scenarios TEXT DEFAULT '[]',
        summary TEXT DEFAULT '',
        raw_report TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
    );

    CREATE TABLE IF NOT EXISTS meta_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        final_recommendation TEXT DEFAULT '',
        confidence REAL DEFAULT 0.5,
        justification TEXT DEFAULT '',
        maturity_level TEXT DEFAULT 'L0',
        required_changes TEXT DEFAULT '[]',
        suggested_changes TEXT DEFAULT '[]',
        summary_for_authors TEXT DEFAULT '',
        arena_eligible INTEGER DEFAULT 0,
        arena_eligibility_reason TEXT DEFAULT '',
        raw_review TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
    );

    CREATE TABLE IF NOT EXISTS revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        version INTEGER DEFAULT 1,
        changes_summary TEXT DEFAULT '',
        revision_letter TEXT DEFAULT '',
        revised_text TEXT DEFAULT '',
        status TEXT DEFAULT 'draft',
        created_at TEXT NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
    );

    CREATE TABLE IF NOT EXISTS eval_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        scenario TEXT NOT NULL,
        score REAL DEFAULT 0,
        assessment TEXT DEFAULT '',
        gaps TEXT DEFAULT '[]',
        created_at TEXT NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
    );

    CREATE TABLE IF NOT EXISTS writing_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT UNIQUE NOT NULL,
        session_type TEXT DEFAULT 'chat',
        title TEXT DEFAULT '',
        current_step TEXT DEFAULT '',
        idea TEXT DEFAULT '',
        methodology TEXT DEFAULT '',
        sections TEXT DEFAULT '{}',
        current_content TEXT DEFAULT '',
        conversation_history TEXT DEFAULT '[]',
        related_papers TEXT DEFAULT '[]',
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS arena_papers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        authors TEXT NOT NULL,
        abstract TEXT NOT NULL,
        categories TEXT DEFAULT '',
        pdf_path TEXT DEFAULT '',
        review_score REAL DEFAULT 0,
        maturity_level TEXT DEFAULT 'L0',
        rail_compliant INTEGER DEFAULT 0,
        review_count INTEGER DEFAULT 0,
        badges TEXT DEFAULT '[]',
        promoted_at TEXT NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
    );

    CREATE TABLE IF NOT EXISTS decision_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        paper_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        model_used TEXT DEFAULT '',
        prompt_hash TEXT DEFAULT '',
        input_summary TEXT DEFAULT '',
        output_summary TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
    );
    """)
    conn.commit()
    conn.close()

def generate_paper_id():
    conn = get_db()
    now = datetime.utcnow()
    prefix = f"aiXiv:{now.strftime('%y%m')}"
    row = conn.execute(
        "SELECT paper_id FROM papers WHERE paper_id LIKE ? ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",)
    ).fetchone()
    if row:
        last_num = int(row['paper_id'].split('.')[-1])
        next_num = last_num + 1
    else:
        next_num = 6
    conn.close()
    return f"{prefix}.{next_num:03d}"
