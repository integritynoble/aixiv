# AI Scientist Platform — Implementation Plan

## Vision

Build a full AI Scientist system on aiXiv (aixiv.platformai.org) that can **write** science papers and **review** them, following the SolveEverything.org framework. The platform has two tiers:

- **Tier 1 (aiXiv)**: Open publishing — any paper can be published (like arXiv), getting a permanent citable ID
- **Tier 2 (Arena)**: After AI review + author revision (with AI assistance), papers that pass are promoted to the Arena leaderboard

The system implements the **Targeting System** (structured evaluation criteria with L0-L5 maturity scoring) and the **Rail** (standardized infrastructure for scientific evaluation: OperatorGraph IR, 4-scenario evaluation, adversarial red-teaming, and arena leaderboard).

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    aiXiv Frontend                        │
│  index.html  │  scientist  │  writer  │  reviewer  │    │
│              │             │          │  arena     │    │
│              │             │          │  dashboard │    │
└──────────────┬──────────────────────────────────────────┘
               │ REST API + SSE
┌──────────────▼──────────────────────────────────────────┐
│                  FastAPI Backend                         │
│                                                          │
│  ┌──────────┐  ┌───────────┐  ┌────────────────────┐   │
│  │ Paper    │  │ AI Writer │  │ AI Reviewer         │   │
│  │ Manager  │  │ Pipeline  │  │ + Targeting System  │   │
│  └──────────┘  └───────────┘  └────────────────────┘   │
│                                                          │
│  ┌──────────┐  ┌───────────┐  ┌────────────────────┐   │
│  │ Revision │  │ Arena     │  │ The Rail            │   │
│  │ Tracker  │  │ Engine    │  │ (Eval Harness)      │   │
│  └──────────┘  └───────────┘  └────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │ LLM Layer: Claude API (primary) + multi-model    │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │ SQLite Database (papers, reviews, sessions, arena)│   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## Phase 1: Enhanced AI Writer Pipeline

**Goal**: Upgrade the basic writer to a multi-stage paper composition system (inspired by CompareGPT-AIScientist).

### 1.1 Idea Generation Agent
**File**: `backend/agents/idea_agent.py`

- **Idea Maker**: Given a research topic/data description, generates 5 candidate research ideas
- **Idea Critic**: Provides harsh, constructive critique of each idea
- **Iterative refinement**: Generate 5 → Critique → Select 2 best → Critique → Select 1 final
- **Output**: Paper title + structured 5-sentence idea description
- Uses conversation-based approach (no external dependencies like CMBAgent)

### 1.2 Literature & Novelty Check Agent
**File**: `backend/agents/literature_agent.py`

- Searches arXiv API for related work (free, no API key needed)
- Optional: Semantic Scholar API for broader coverage
- Decision engine: `novel` / `not_novel` / `needs_more_search`
- Up to 5 iterative search rounds with refined queries
- Output: Related papers list + novelty assessment

### 1.3 Methodology Agent
**File**: `backend/agents/method_agent.py`

- Takes the selected idea and generates detailed methodology
- Researcher agent: develops technical approach
- Reviewer agent: critiques and improves methodology
- Output: Structured methods section (algorithm, experimental design, evaluation protocol)

### 1.4 Paper Composition Agent
**File**: `backend/agents/paper_agent.py`

- Orchestrates section-by-section paper writing using LLM
- Pipeline: Keywords → Abstract → Introduction → Related Work → Methods → Results → Discussion → Conclusion
- Each section generated with full context of prior sections
- Supports iterative refinement per section
- LaTeX output support (optional, can also produce Markdown)

### 1.5 Writer API Endpoints
**File**: `backend/app.py` (extended)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/write/idea` | POST | Start idea generation session |
| `POST /api/write/novelty` | POST | Run novelty check on an idea |
| `POST /api/write/method` | POST | Generate methodology |
| `POST /api/write/compose` | POST | Compose full paper |
| `POST /api/write/section` | POST | Write/revise a specific section |
| `POST /api/write/chat` | POST | Free-form chat with writer (existing, enhanced) |
| `GET /api/write/session/{id}` | GET | Get session state |

### 1.6 Writer Frontend
**File**: `templates/writer.html` (enhanced)

- Multi-step wizard UI: Idea → Novelty → Methods → Compose
- Real-time streaming responses (SSE)
- Section-by-section editing with AI assistance
- Export to PDF/LaTeX/Markdown
- Session persistence (resume later)

---

## Phase 2: The Targeting System (AI Reviewer)

**Goal**: Build a rigorous, structured AI review system grounded in SolveEverything.org's evaluation framework.

### 2.1 Core Reviewer Agent
**File**: `backend/agents/reviewer_agent.py`

Enhanced review system with three evaluation layers:

#### Layer 1: Standard Peer Review
- **Soundness** (1-5): Are the claims supported by evidence?
- **Novelty** (1-5): Does this contribute new knowledge?
- **Clarity** (1-5): Is the writing clear and well-organized?
- **Significance** (1-5): How important is this work?
- **Reproducibility** (1-5): Could someone replicate this?
- Strengths, Weaknesses, Questions for Authors

#### Layer 2: L0-L5 Maturity Assessment (SolveEverything Framework)
- Evaluate which maturity level the paper achieves:
  - L0 (Ill-Posed): Are objectives clearly defined?
  - L1 (Measurable): Are results quantified with proper baselines?
  - L2 (Repeatable): Could someone reproduce this work?
  - L3 (Automated): Is the approach systematic and scalable?
  - L4 (Industrialized): Does it advance toward commodity solutions?
  - L5 (Solved): Does it definitively resolve the problem?
- Specific assessment criteria per level
- Actionable feedback to advance to the next level

#### Layer 3: Domain-Specific Gate Analysis
- **Triad Law Gates** (for computational imaging papers):
  - Gate 1 (Recoverability): Is the measurement information-sufficient?
  - Gate 2 (Carrier Budget): Is the SNR adequate?
  - Gate 3 (Operator Mismatch): Is the forward model accurate?
- Extensible to other domains (add new gate systems as needed)

### 2.2 Red Team Agent
**File**: `backend/agents/redteam_agent.py`

- Adversarial analysis: tries to find flaws, logical gaps, unsupported claims
- Statistical validity checks (p-hacking, cherry-picking, small samples)
- Reproducibility assessment (are methods/data/code sufficient?)
- Attack scenarios: What if the assumptions fail? What are edge cases?
- Output: Red Team Report with severity levels (Critical, Major, Minor, Suggestion)

### 2.3 Meta-Reviewer Agent
**File**: `backend/agents/meta_reviewer_agent.py`

- Synthesizes multiple reviews (AI reviewer + Red Team)
- Makes final recommendation: Accept / Minor Revision / Major Revision / Reject
- Generates consolidated action items for authors
- Assigns overall maturity level (L0-L5)

### 2.4 Review API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/review/{paper_id}` | POST | Trigger full AI review (existing, enhanced) |
| `POST /api/review/{paper_id}/redteam` | POST | Trigger red team analysis |
| `POST /api/review/{paper_id}/meta` | POST | Trigger meta-review synthesis |
| `GET /api/reviews/{paper_id}` | GET | Get all reviews (existing) |
| `POST /api/review/{paper_id}/respond` | POST | Author response to review |

### 2.5 Reviewer Frontend
**File**: `templates/reviewer.html` (enhanced)

- Paper submission form (paste text or upload PDF)
- Review display with structured scores, radar chart
- L0-L5 maturity visualization (progress ladder)
- Red Team report display
- Author response interface
- Side-by-side view: paper + review

---

## Phase 3: The Revision Loop (AI-Assisted Revision)

**Goal**: Enable authors to revise papers with AI assistance based on review feedback.

### 3.1 Revision Agent
**File**: `backend/agents/revision_agent.py`

- Takes original paper + review feedback as input
- Generates specific revision suggestions for each weakness
- Tracks revision history (diff-based)
- Can auto-apply revisions with author approval
- Produces revision letter (response to reviewers)

### 3.2 Revision Tracking
**Database**: Add `revisions` table

```sql
CREATE TABLE revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    changes_summary TEXT DEFAULT '',
    revision_letter TEXT DEFAULT '',
    status TEXT DEFAULT 'draft',  -- draft, submitted, reviewed
    created_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
);
```

### 3.3 Revision API

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/revise/{paper_id}` | POST | Start AI-assisted revision |
| `GET /api/revisions/{paper_id}` | GET | Get revision history |
| `POST /api/revise/{paper_id}/submit` | POST | Submit revised paper for re-review |

### 3.4 Revision Frontend
- Diff view showing changes between versions
- AI revision suggestions with accept/reject per suggestion
- Revision letter composer
- Re-submission workflow

---

## Phase 4: The Rail (Evaluation Harness Infrastructure)

**Goal**: Implement the standardized evaluation infrastructure from the SolveEverything framework.

### 4.1 Evaluation Protocol Engine
**File**: `backend/rail/eval_engine.py`

Four-scenario evaluation for submitted papers:
1. **Ideal conditions**: Does the method work under the paper's own assumptions?
2. **Real-world noise**: Does it degrade gracefully with realistic noise?
3. **Operator mismatch**: Does it handle imperfect forward models?
4. **Adversarial perturbation**: Does it survive adversarial attacks?

Each scenario produces a structured score card.

### 4.2 Targeting System Engine
**File**: `backend/rail/targeting.py`

- Defines the structured criteria (the "targeting system") for each maturity level
- Automated checks where possible (e.g., does the paper define metrics? baseline comparisons?)
- Checklist-based assessment for L0-L5 level assignment
- Generates specific "next steps" to advance to the next maturity level

### 4.3 Decision Records (DR-AIS)
**File**: `backend/rail/decision_record.py`

- Immutable log of every AI decision during review
- Records: input context, model used, prompt, output, timestamp
- Enables auditability and transparency
- Stored as append-only JSON logs

### 4.4 Rail Database Extensions

```sql
CREATE TABLE eval_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    scenario TEXT NOT NULL,  -- ideal, noisy, mismatch, adversarial
    score REAL DEFAULT 0,
    details TEXT DEFAULT '',  -- JSON
    created_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
);

CREATE TABLE decision_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    action_type TEXT NOT NULL,  -- review, redteam, meta_review, revision
    model_used TEXT DEFAULT '',
    prompt_hash TEXT DEFAULT '',
    input_summary TEXT DEFAULT '',
    output_summary TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (paper_id) REFERENCES papers(paper_id)
);
```

---

## Phase 5: Arena & Publishing Pipeline

**Goal**: Build the complete two-tier publishing pipeline.

### 5.1 Paper Lifecycle State Machine

```
submitted → under_review → revision → re_review → accepted → published_arena
                                   ↓
                                rejected
```

States:
- `submitted`: Paper uploaded to aiXiv (Tier 1 - publicly visible immediately)
- `under_review`: AI review in progress
- `revision`: Authors revising based on review feedback
- `re_review`: Revised paper under re-review
- `accepted`: Paper passes review (ready for Arena)
- `published_arena`: Promoted to Tier 2 Arena
- `rejected`: Did not pass review (stays on aiXiv Tier 1)

### 5.2 Arena Scoring & Leaderboard
**File**: `backend/arena.py` (enhanced)

- Composite score: weighted average of review scores
- Maturity level bonus: higher L-level = higher ranking
- Tie-breaking by novelty score, then recency
- Categories: ranked within domain categories
- Arena badges: "L3 Certified", "Red-Team Cleared", etc.

### 5.3 Arena Frontend
**File**: `templates/arena.html` (enhanced)

- Leaderboard table with sortable columns
- Filter by category, maturity level, score range
- Paper cards with review summary, maturity badge
- Radar chart for multi-dimensional scores
- Comparison mode: compare two papers side-by-side

### 5.4 Dashboard
**File**: `templates/dashboard.html` (enhanced)

- Pipeline statistics: papers at each stage
- Review quality metrics
- Maturity level distribution chart
- Recent activity feed
- System health indicators

---

## Phase 6: Full Workflow Integration

**Goal**: Connect all components into a seamless end-to-end workflow.

### 6.1 End-to-End AI Scientist Workflow

```
[User provides research topic / data description]
        ↓
    Phase 1: WRITE
        ↓
    [1] Idea Generation (5 ideas → critique → select 1)
        ↓
    [2] Novelty Check (arXiv search → assessment)
        ↓
    [3] Methodology Development (methods + experiment plan)
        ↓
    [4] Paper Composition (section-by-section with AI)
        ↓
    Phase 2: PUBLISH (Tier 1 - aiXiv)
        ↓
    [5] Submit to aiXiv → Get paper ID (aiXiv:YYMM.NNN)
        ↓
    Phase 3: REVIEW (Targeting System)
        ↓
    [6] AI Peer Review (Soundness, Novelty, Clarity, Significance)
        ↓
    [7] L0-L5 Maturity Assessment
        ↓
    [8] Red Team Analysis
        ↓
    [9] Meta-Review Synthesis → Recommendation
        ↓
    Phase 4: REVISE
        ↓
    [10] AI-Assisted Revision based on feedback
        ↓
    [11] Author approves revisions → Re-submit
        ↓
    [12] Re-review → Accept/Reject
        ↓
    Phase 5: ARENA (Tier 2)
        ↓
    [13] Accepted papers promoted to Arena
        ↓
    [14] Arena leaderboard ranking
```

### 6.2 Orchestrator
**File**: `backend/orchestrator.py`

- Manages the full workflow pipeline
- Tracks state transitions for each paper
- Handles async operations (long-running AI calls)
- SSE streaming for real-time progress updates to frontend
- Error recovery and retry logic

### 6.3 Updated Navigation

```
aiXiv Header Navigation:
[Papers] [AI Scientist] [Writer] [Reviewer] [Arena] [Dashboard]
```

The AI Scientist page becomes the hub that orchestrates the full workflow.

---

## Implementation Order

### Sprint 1: Core Agent Framework (Week 1)
1. Create `backend/agents/` module structure
2. Implement `base_agent.py` — shared LLM calling, streaming, error handling
3. Implement `idea_agent.py` — idea generation with maker/critic loop
4. Implement `literature_agent.py` — arXiv search + novelty check
5. Implement `method_agent.py` — methodology generation
6. Add new API endpoints for writing pipeline
7. Update `templates/writer.html` with multi-step wizard UI

### Sprint 2: Enhanced Review System (Week 2)
8. Implement `reviewer_agent.py` — 3-layer review (peer + maturity + gates)
9. Implement `redteam_agent.py` — adversarial analysis
10. Implement `meta_reviewer_agent.py` — review synthesis
11. Add review API endpoints
12. Update `templates/reviewer.html` with structured review display
13. Update database schema (add new tables)

### Sprint 3: Revision Loop & Rail (Week 3)
14. Implement `revision_agent.py` — AI-assisted revision
15. Implement `backend/rail/targeting.py` — targeting system engine
16. Implement `backend/rail/eval_engine.py` — 4-scenario evaluation
17. Implement `backend/rail/decision_record.py` — DR-AIS audit logging
18. Add revision API endpoints
19. Add revision tracking UI

### Sprint 4: Arena & Full Integration (Week 4)
20. Implement `backend/orchestrator.py` — full workflow management
21. Implement paper lifecycle state machine
22. Enhance `backend/arena.py` — scoring, ranking, badges
23. Update `templates/arena.html` — leaderboard, filters, comparison
24. Update `templates/dashboard.html` — analytics, charts
25. Update `templates/scientist.html` — full workflow wizard
26. SSE streaming for real-time progress
27. End-to-end testing

---

## File Structure (After Implementation)

```
pwm_aixiv/
├── backend/
│   ├── app.py                    # FastAPI app (enhanced)
│   ├── database.py               # Database management (enhanced)
│   ├── ai_scientist.py           # Legacy (kept for backward compat, delegates to agents/)
│   ├── orchestrator.py           # Full workflow orchestrator
│   ├── arena.py                  # Arena scoring & ranking engine
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base_agent.py         # Base LLM agent (shared utilities)
│   │   ├── idea_agent.py         # Idea generation (maker + critic)
│   │   ├── literature_agent.py   # Literature search + novelty check
│   │   ├── method_agent.py       # Methodology generation
│   │   ├── paper_agent.py        # Section-by-section paper composition
│   │   ├── reviewer_agent.py     # 3-layer peer review
│   │   ├── redteam_agent.py      # Adversarial red team analysis
│   │   ├── meta_reviewer_agent.py # Meta-review synthesis
│   │   └── revision_agent.py     # AI-assisted paper revision
│   ├── rail/
│   │   ├── __init__.py
│   │   ├── targeting.py          # Targeting system (L0-L5 criteria engine)
│   │   ├── eval_engine.py        # 4-scenario evaluation protocol
│   │   └── decision_record.py    # DR-AIS audit logging
│   └── run.sh
├── templates/
│   ├── scientist.html            # AI Scientist hub (enhanced workflow wizard)
│   ├── writer.html               # Multi-step writer (enhanced)
│   ├── reviewer.html             # Structured review UI (enhanced)
│   ├── dashboard.html            # Analytics dashboard (enhanced)
│   └── arena.html                # Arena leaderboard (enhanced)
├── html_papers/                  # HTML-rendered papers
├── papers/                       # PDF files
├── tex_source/                   # LaTeX sources
├── data/                         # SQLite database
├── index.html                    # Main paper listing
├── about.html                    # About page
├── submit.html                   # Submission instructions
├── paper_*.html                  # Individual paper pages
├── style.css                     # Stylesheet
└── plan.md                       # This plan
```

---

## Tech Stack

- **Backend**: Python 3, FastAPI, Uvicorn
- **Database**: SQLite with WAL mode
- **AI/LLM**: Anthropic Claude API (claude-sonnet-4-20250514 primary, claude-opus-4-20250514 for complex reviews)
- **Literature Search**: arXiv API (free), optional Semantic Scholar
- **Frontend**: HTML5, CSS3, Vanilla JavaScript (no framework, consistent with existing)
- **Templating**: Jinja2
- **Streaming**: Server-Sent Events (SSE)
- **Deployment**: aixiv.platformai.org

---

## Key Design Principles

1. **SolveEverything.org Alignment**: Every component maps to the Industrial Intelligence Stack
   - Targeting System = Layer 4 (structured evaluation criteria)
   - The Rail = Layer combination (task taxonomy + observability + verification)
   - Arena = Layer 8 (outcome-based incentives / ranking by quality)

2. **Simplicity**: No heavyweight frameworks (LangGraph, LangChain). Direct Claude API calls with clean Python orchestration. This keeps the codebase maintainable and reduces dependencies.

3. **Two-Tier Publishing**: aiXiv (open, no gatekeeping) → Arena (quality-gated through AI review + revision). This mirrors the SolveEverything principle of "From Projects to Pipelines."

4. **Auditability**: Every AI decision is logged via DR-AIS records. This follows the SolveEverything principle of "Decision Records for AI Systems."

5. **Incremental Maturity**: Papers can improve their L-level over time through revisions. The system is designed to help papers advance, not just judge them.

6. **Domain Extensibility**: The Triad Law Gate analysis is one example of domain-specific evaluation. The architecture supports adding new gate systems for other research domains.

---

## Dependencies

```
# Python packages needed (add to requirements.txt)
fastapi>=0.100.0
uvicorn>=0.22.0
anthropic>=0.30.0      # Claude API client
arxiv>=2.0.0           # arXiv API client for literature search
python-multipart       # File upload support
jinja2                 # Template rendering
httpx                  # Async HTTP client (for arXiv API)
pydantic>=2.0          # Data validation
```

No need for LangChain, LangGraph, or other heavy ML frameworks — the system uses direct Claude API calls for maximum simplicity and control.
