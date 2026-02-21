# aiXiv -- Open Research Archive

**Live site:** [https://aixiv.platformai.org](https://aixiv.platformai.org)

aiXiv is the publication platform for the [Physics World Model (PWM)](https://github.com/integritynoble/Physics_World_Model) project -- an open, reproducible toolkit that aims to make any imaging system **self-specifying**, **self-diagnosing**, and **self-correcting**.

---

## Papers

| ID | Title | Authors | Categories | Target |
|----|-------|---------|------------|--------|
| [2502.001](https://aixiv.platformai.org/paper_pwm_flagship.html) | Physics World Models for Computational Imaging: A Universal Physics-Information Law for Recoverability, Carrier Noise, and Operator Mismatch | Chengshuai Yang, Xin Yuan | physics.comp-img, cs.CV | Nature |
| [2502.002](https://aixiv.platformai.org/paper_rail.html) | The Rail for Computational Imaging: A Physics World Model for Industrializing Image Reconstruction | Chengshuai Yang | physics.comp-img, cs.CV | -- |
| [2502.003](https://aixiv.platformai.org/paper_inversenet.html) | InverseNet: Benchmarking Operator Mismatch and Calibration Across Compressive Imaging Modalities | Chengshuai Yang | cs.CV, physics.comp-img | ECCV |
| [2502.004](https://aixiv.platformai.org/paper_pwmi_cassi.html) | Correcting Forward Model Mismatch in CASSI via Two-Stage Differentiable Calibration | Chengshuai Yang | cs.CV, physics.optics | ECCV |
| [2502.005](https://aixiv.platformai.org/paper_ct_qc_copilot.html) | CT QC Copilot: Clinical Implementation of an Automated Quality-Control Decision-Support System | Chengshuai Yang | physics.med-ph, cs.AI | JACMP |
| [2502.006](https://aixiv.platformai.org/paper_ct_qc_platform.html) | An Open, Reproducible CT Quality-Control Platform with Versioned CasePacks and Immutable Baselines | Chengshuai Yang | physics.med-ph, cs.SE | -- |

---

## Architecture

```
aixiv.platformai.org
├── Static frontend (nginx)          # Paper listings, detail pages, about
│   ├── index.html                   # Homepage with search and paper cards
│   ├── paper_*.html                 # Individual paper detail pages (6)
│   ├── about.html / submit.html     # Info pages
│   ├── papers/*.pdf                 # Paper PDFs + supplementary
│   ├── html_papers/                 # Experimental HTML renders
│   └── tex_source/                  # TeX source archives
│
└── FastAPI backend (:8501)          # API + authenticated pages
    ├── backend/app.py               # Main application, routes, SSO
    ├── backend/auth.py              # JWT auth, CompareGPT SSO integration
    ├── backend/database.py          # SQLite (users, papers, sessions)
    ├── backend/orchestrator.py      # Multi-agent pipeline orchestration
    ├── backend/agents/              # AI agents (idea, literature, method, paper, reviewer, etc.)
    ├── backend/rail/                # Evaluation engine, targeting, maturity assessment
    └── templates/                   # Jinja2 templates (profile, login, etc.)
```

### Routing

Nginx serves static files directly from the project root and proxies dynamic routes to the FastAPI backend:

| Route | Served By | Description |
|-------|-----------|-------------|
| `/` | nginx | Static homepage, paper pages, PDFs |
| `/api/*` | FastAPI | REST API with SSE streaming |
| `/profile` | FastAPI | User profile with SSO/local auth |
| `/login` | FastAPI | Login page (SSO + local) |
| `/sso/callback` | FastAPI | CompareGPT SSO callback |
| `/submit` | nginx | Paper submission info |

---

## Setup

### Prerequisites

- Python 3.10+
- nginx
- SSL certificate (Let's Encrypt)

### Backend

```bash
cd backend
pip install -r ../requirements.txt
cp .env.example .env   # Configure API keys, SSO secrets
python app.py           # Runs on port 8501
```

The backend is managed as a systemd service (`aixiv-backend.service`).

### Nginx

The nginx config is at `/etc/nginx/sites-available/aixiv.platformai.org`. It serves static files from the project root and proxies `/api/*`, `/profile`, `/login`, and `/sso/*` to the FastAPI backend.

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Authentication

Two login methods:

1. **CompareGPT SSO** -- OAuth-style redirect to `comparegpt.io`, token validated against `auth.comparegpt.io/sso/validate`, JWT cookie set on callback
2. **Local auth** -- Username/password registration with bcrypt hashing, stored in SQLite

Both methods issue a signed JWT cookie (`aixiv_token`) valid for 7 days.

---

## Project Structure

```
.
├── index.html                    # Homepage
├── about.html                    # About + PWM vision page
├── submit.html                   # Submission info
├── style.css                     # Site-wide styles
├── paper_pwm_flagship.html       # Paper detail: PWM Flagship
├── paper_rail.html               # Paper detail: Rail
├── paper_inversenet.html         # Paper detail: InverseNet
├── paper_pwmi_cassi.html         # Paper detail: PWMI-CASSI
├── paper_ct_qc_copilot.html      # Paper detail: CT QC Copilot
├── paper_ct_qc_platform.html     # Paper detail: CT QC Platform
├── papers/                       # PDFs (main + supplementary)
├── html_papers/                  # HTML paper renders
├── tex_source/                   # TeX source archives
├── backend/
│   ├── app.py                    # FastAPI application
│   ├── auth.py                   # Authentication module
│   ├── database.py               # SQLite database
│   ├── orchestrator.py           # Pipeline orchestration
│   ├── agents/                   # AI agent modules
│   │   ├── base_agent.py
│   │   ├── idea_agent.py
│   │   ├── literature_agent.py
│   │   ├── method_agent.py
│   │   ├── paper_agent.py
│   │   ├── reviewer_agent.py
│   │   ├── meta_reviewer_agent.py
│   │   ├── redteam_agent.py
│   │   └── revision_agent.py
│   └── rail/
│       ├── eval_engine.py        # Evaluation engine
│       └── targeting.py          # L0-L5 maturity assessment
├── templates/                    # Jinja2 templates
│   ├── profile.html
│   ├── login.html
│   └── ...
├── data/                         # SQLite database files
└── requirements.txt              # Python dependencies
```

---

## Related Repositories

- **[Physics_World_Model](https://github.com/integritynoble/Physics_World_Model)** -- Core PWM framework, OperatorGraph registry, paper source files, and validation code

---

## License

Operated by NextGen PlatformAI C Corp.

## Contact

Chengshuai Yang -- [integrityyang@gmail.com](mailto:integrityyang@gmail.com)
