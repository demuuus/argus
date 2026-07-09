# ARGUS Installation Guide

🌐 [English](INSTALL.md) | [Indonesia](INSTALL.id.md)

This document is the official installation and configuration manual for ARGUS. It covers everything needed to get a working ARGUS deployment running end to end: the PostgreSQL database, the Flask web dashboard, the Telegram bot, the background scheduler, and the AI Security Copilot.

**What gets installed.** A PostgreSQL database, a Python virtual environment with ARGUS's dependencies, the Flask dashboard process (`app.py`), optionally the Telegram bot process (`bot/main.py`), and — if you want AI features — a locally running OpenAI-compatible LLM server that ARGUS calls over HTTP.

**Estimated installation time.** 30–60 minutes for a first-time single-machine install with PostgreSQL already available; 60–120 minutes if you are also installing PostgreSQL and a local LLM server from scratch.

**Minimum technical knowledge required.** Comfort with a command-line shell (Bash on Linux/macOS, PowerShell or Command Prompt on Windows), basic familiarity with editing text files, and enough SQL/PostgreSQL awareness to run `psql` commands as shown. No prior ARGUS knowledge is assumed.

**Scope.** This document covers installation, configuration, verification, updates, backup/restore, troubleshooting, and production hardening. For what ARGUS does and how it's architected, see [`README.md`](./README.md). For route/API details, see the [Documentation](#26-references) links.

> **A note on accuracy.** Every command, environment variable, and behavior described below reflects what is actually implemented in the current ARGUS codebase, verified directly against the source (`app.py`, `bot/main.py`, `bot/database/db.py`, `bot/migrate.py`, `bot/database/schema.sql`, `bot/Ai/llm.py`, `bot/jobs/daily_scan.py`). Anywhere this guide describes something that is not yet implemented (Docker packaging, for example), it is explicitly marked **Planned**.

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Supported Operating Systems](#2-supported-operating-systems)
3. [Software Dependencies](#3-software-dependencies)
4. [Project Installation](#4-project-installation)
5. [PostgreSQL Installation](#5-postgresql-installation)
6. [Database Initialization](#6-database-initialization)
7. [Environment Configuration](#7-environment-configuration)
8. [AI Installation](#8-ai-installation)
9. [External API Configuration](#9-external-api-configuration)
10. [Telegram Bot Configuration](#10-telegram-bot-configuration)
11. [Dashboard Configuration](#11-dashboard-configuration)
12. [Scheduler Configuration](#12-scheduler-configuration)
13. [Running ARGUS](#13-running-argus)
14. [First-Time Setup](#14-first-time-setup)
15. [Verification Checklist](#15-verification-checklist)
16. [Updating ARGUS](#16-updating-argus)
17. [Backup & Restore](#17-backup--restore)
18. [Troubleshooting](#18-troubleshooting)
19. [Logging](#19-logging)
20. [Security Recommendations](#20-security-recommendations)
21. [Performance Recommendations](#21-performance-recommendations)
22. [Docker Installation (Future Support)](#22-docker-installation-future-support)
23. [Production Deployment](#23-production-deployment)
24. [Uninstallation](#24-uninstallation)
25. [Frequently Asked Questions](#25-frequently-asked-questions)
26. [References](#26-references)

---

## 1. System Requirements

### Minimum Requirements

| Component | Minimum |
|---|---|
| CPU | 2 cores |
| RAM | 4 GB (8 GB if you plan to run a local LLM on the same machine) |
| Storage | 10 GB free (grows with CVE history, reports, and conversation data) |
| GPU | Not required. Optional — only relevant if you run a GPU-accelerated local LLM server |
| Operating System | 64-bit Windows 10/11, or a modern 64-bit Linux distribution |
| Python | 3.11 or later (3.12 recommended) — required by the pinned dependency set in `requirements.txt` |
| PostgreSQL | 14 or later |
| Network Connectivity | Outbound HTTPS access to the NVD API, the CISA KEV feed, and (if used) the FIRST EPSS API; outbound access to the Telegram Bot API if running the bot |

### Recommended Requirements

| Deployment size | CPU | RAM | Storage | Notes |
|---|---|---|---|---|
| **Small lab / single analyst** | 2–4 cores | 8 GB | 20 GB SSD | Everything (PostgreSQL, ARGUS, optional local LLM) on one machine is fine at this scale |
| **Medium organization** | 4–8 cores | 16 GB | 50–100 GB SSD | Run PostgreSQL on dedicated storage; consider a separate host for the LLM server if AI features are used heavily |
| **Enterprise deployment** | 8+ cores | 32 GB+ | 200 GB+ SSD, with a backup target | Separate database host, separate LLM inference host, reverse proxy in front of the dashboard, monitoring and log aggregation |

**Hardware notes by workload:**

- **AI workloads** — CPU inference of a local LLM is usable for low-volume chat and background CVE analysis but will be slow for larger models; a GPU with sufficient VRAM for your chosen model dramatically improves response latency. See [§8 AI Installation](#8-ai-installation) for model-size guidance.
- **Large databases** — PostgreSQL performance scales with available RAM for shared buffers and effective cache size; see [§21 Performance Recommendations](#21-performance-recommendations) for tuning guidance once your `matches`/`cves` table row counts grow into the hundreds of thousands.
- **Historical reports** — Generated PDF reports accumulate under `bot/dashboard/generated_reports/`; budget storage accordingly if you retain reports long-term rather than archiving them externally.
- **Large asset inventories** — Scan duration scales with asset count and NVD API rate limits (see [§9](#9-external-api-configuration)); an NVD API key is strongly recommended once you have more than a handful of assets.

---

## 2. Supported Operating Systems

| Platform | Status |
|---|---|
| Ubuntu 22.04 / 24.04 LTS | Supported |
| Debian 11 / 12 | Supported |
| Fedora (recent releases) | Supported |
| RHEL-compatible distributions (RHEL, Rocky Linux, AlmaLinux) | Supported |
| Windows 10 (64-bit) | Supported |
| Windows 11 | Supported |
| macOS | Not explicitly covered here, but should work with the Linux instructions substituted with Homebrew equivalents (untested by the project) |

ARGUS is a pure-Python application with no OS-specific compiled extensions beyond what `psycopg2-binary`, `matplotlib`, and `pillow` already ship as prebuilt wheels for, so it does not require platform-specific code changes. Instructions below are split into **Linux** and **Windows** wherever the steps actually differ.

---

## 3. Software Dependencies

| Dependency | Why it's required |
|---|---|
| **Python 3.11+** | Runtime for the Flask dashboard and the Telegram bot |
| **pip** | Installs Python dependencies from `requirements.txt` |
| **PostgreSQL 14+** | Primary datastore for all ARGUS data — assets, findings, CVEs, reports, users, AI conversations |
| **Git** | Clone and update the ARGUS repository |
| **An OpenAI-compatible LLM server** (e.g. `llama.cpp`'s server, or Ollama exposing its OpenAI-compatible endpoint) | Powers the AI Security Copilot chat and automated CVE analysis. Optional — ARGUS runs fully without it, with AI features disabled |
| **Visual C++ Runtime** (Windows only) | Some pinned Python packages (e.g. `psycopg2-binary`, `matplotlib`, `numpy`, `pillow`) ship prebuilt Windows wheels that link against the Visual C++ runtime; install the [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) if you hit a DLL load error |
| **Build tools** (Linux only, occasionally) | If a prebuilt wheel is unavailable for your exact Python/OS combination, `pip` will attempt to compile a package from source, which requires a C compiler and PostgreSQL client headers. See [§18 Troubleshooting](#18-troubleshooting) |

> ARGUS's `requirements.txt` does **not** include a production WSGI server (e.g. Gunicorn) or a Docker toolchain. Both are discussed separately in [§23](#23-production-deployment) and [§22](#22-docker-installation-future-support) respectively, since they are operational choices, not application dependencies.

---

## 4. Project Installation

### 4.1 Clone the repository

```bash
git clone <repo-url> argus
cd argus
```

This creates the top-level `argus/` directory containing `app.py` (the dashboard entry point), `requirements.txt`, and the `bot/` package, which contains the Telegram bot entry point (`bot/main.py`) and every shared module (database access, scanner, AI, reports, scheduler).

### 4.2 Create a virtual environment

Isolating ARGUS's dependencies in a virtual environment avoids conflicts with other Python projects on the same machine.

**Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell):**
```powershell
py -m venv venv
venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
py -m venv venv
venv\Scripts\activate.bat
```

You should see `(venv)` prepended to your shell prompt once activated. Every `pip` and `python` command in the rest of this guide assumes the virtual environment is active.

### 4.3 Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This installs Flask, Flask-Login, Flask-WTF, `psycopg2-binary`, APScheduler, `python-telegram-bot`, ReportLab, matplotlib, and their transitive dependencies — the same `requirements.txt` is used by both the dashboard and the Telegram bot, so one install covers both.

### 4.4 Prepare the environment file

ARGUS reads configuration from a `.env` file at the project root (loaded via `python-dotenv`). Create one now; the full variable reference is in [§7](#7-environment-configuration).

```bash
touch .env        # Linux/macOS
type nul > .env    # Windows Command Prompt
```

Do not commit this file — `.gitignore` already excludes `.env` by default.

---

## 5. PostgreSQL Installation

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

### Linux (Fedora/RHEL-compatible)

```bash
sudo dnf install -y postgresql-server postgresql-contrib
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

### Windows

1. Download the installer from the [PostgreSQL official downloads page](https://www.postgresql.org/download/windows/).
2. Run the installer, keeping the default port (`5432`) unless you have a conflict.
3. Set a password for the `postgres` superuser when prompted — record it; you will need it below.
4. Ensure "Command Line Tools" is selected in the component list so `psql` is available on your `PATH`.

### Database and user creation (all platforms, via `psql`)

Connect as the PostgreSQL superuser:

```bash
psql -U postgres
```

Then run:

```sql
CREATE USER argus_user WITH PASSWORD 'change-this-password';
CREATE DATABASE argus_db OWNER argus_user ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE argus_db TO argus_user;
\q
```

**Database encoding.** Use `UTF8` as shown — CVE descriptions and asset notes can contain a wide range of Unicode characters, and a non-UTF8 database encoding will cause insertion failures.

**Timezone recommendation.** ARGUS stores timestamps as `TIMESTAMPTZ` throughout its schema, so the server's configured timezone does not affect correctness, but setting the PostgreSQL server timezone to `UTC` is recommended for consistent log correlation, since the scheduler's cron jobs (see [§12](#12-scheduler-configuration)) run in the scheduler process's local timezone:

```sql
ALTER SYSTEM SET timezone = 'UTC';
```
Restart PostgreSQL after this change for it to take effect.

### Verify connectivity

```bash
psql -U argus_user -d argus_db -h localhost -c "SELECT version();"
```

You should see the PostgreSQL version string printed. If this fails, see [§18 Troubleshooting](#18-troubleshooting).

### Common mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Creating the database with the default `SQL_ASCII` encoding | Insert failures on non-ASCII CVE descriptions | Recreate the database with `ENCODING 'UTF8'` |
| Leaving PostgreSQL's `pg_hba.conf` on `peer` authentication for a TCP connection | `psql: FATAL: Peer authentication failed` when connecting with `-h localhost` | Set the relevant line to `md5` or `scram-sha-256` and reload PostgreSQL |
| Forgetting to grant privileges after creating the database with a different owner | Permission-denied errors when ARGUS tries to create tables | Re-run the `GRANT ALL PRIVILEGES` statement, or recreate the database with `OWNER argus_user` as shown above |

---

## 6. Database Initialization

ARGUS's schema is **self-healing** — you generally do not need to run anything manually before starting the application.

- When `app.py` starts, it calls an internal `_ensure_schema()` routine that applies every table/column/index it needs with `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` statements. This is safe to run every time the application starts, including against an already-up-to-date database.
- `bot/main.py` (the Telegram bot) performs the equivalent migration on its own startup path.
- A standalone migration script, `bot/migrate.py`, applies the same set of idempotent migrations and can be run manually — useful for pre-provisioning a database before first running the application, or for CI/deployment pipelines that want the schema ready ahead of time:

```bash
cd bot
python migrate.py
```

Each migration step prints `OK` or `FAILED` with the underlying error, and the script continues through the remaining steps even if one fails, so a single bad statement does not abort the whole run.

- `bot/database/schema.sql` is also included as the baseline schema reference (`psql -U argus_user -d argus_db -f bot/database/schema.sql`), useful for reviewing the full table layout offline, but is **not required** as an installation step given the self-healing behavior above.

### Verification

```bash
psql -U argus_user -d argus_db -c "\dt"
```

After first running `app.py` or `bot/migrate.py`, you should see at minimum: `assets`, `cves`, `matches`, `alerts`, `reports`, `users`, `ai_conversations`, `ai_messages`, `cve_ai_analysis`, `risk_snapshots`, and `ai_response_cache`.

### Integrity checks

```sql
SELECT COUNT(*) FROM assets;
SELECT COUNT(*) FROM cves;
SELECT COUNT(*) FROM matches;
```

On a fresh install these should all return `0` without error — an error here indicates the schema did not apply correctly; re-run `python migrate.py` and check its output.

### Rollback and recovery

ARGUS does not ship a down-migration/rollback tool — migrations are additive (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`) and are not designed to be reversed automatically. If a migration needs to be undone, restore from a backup taken before the migration ran (see [§17 Backup & Restore](#17-backup--restore)) rather than attempting to manually reverse individual `ALTER TABLE` statements.

---

## 7. Environment Configuration

All configuration is via environment variables in the `.env` file at the project root, loaded automatically by both `app.py` and `bot/main.py`.

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `SECRET_KEY` | **Required** (dashboard) | None — app raises `RuntimeError` and refuses to start if unset | Flask session-signing key. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | **Required** (dashboard) | None — app refuses to start if unset | Password for the built-in `admin` account |
| `VIEWER_PASSWORD` | **Required** (dashboard) | None — app refuses to start if unset | Password for the built-in `viewer` (read-only) account |
| `DB_HOST` | Optional | `localhost` | PostgreSQL host |
| `DB_NAME` | Optional | `argus_db` | PostgreSQL database name |
| `DB_USER` | Optional | `postgres` | PostgreSQL user |
| `DB_PASSWORD` | **Required** | None — connection will fail without it | PostgreSQL password |
| `DB_PORT` | Optional | `5432` | PostgreSQL port |
| `DB_POOL_MIN_CONN` | Optional | `2` | Minimum connections kept open in ARGUS's internal connection pool |
| `DB_POOL_MAX_CONN` | Optional | `20` | Maximum connections in the pool |
| `NVD_API_KEY` | Optional but recommended | None (unauthenticated, low rate limit) | Raises your NVD API rate limit substantially; see [§9](#9-external-api-configuration) |
| `TOKEN` | Required only if running the Telegram bot | None — bot raises `RuntimeError` and refuses to start if unset | Telegram Bot API token, from BotFather |
| `CHAT_ID` | Required only for Telegram alert delivery | None — alerts are silently skipped if unset | Target Telegram chat/channel ID that scan alerts are sent to |
| `LLM_URL` | Optional | None — AI chat endpoint returns a clear "not configured" error if unset | Full URL of an OpenAI-compatible `/v1/chat/completions` endpoint (e.g. a local `llama.cpp` server) |
| `RUN_SCHEDULER` | Optional | `true` | Set to `false` on one process if you run both `app.py` and `bot/main.py` under the same supervisor and want to avoid double-scheduling background jobs (see [§12](#12-scheduler-configuration)) |
| `SESSION_COOKIE_SECURE` | Optional | `true` | Set to `false` **only** for local/LAN HTTP testing; leave `true` in any deployment served over HTTPS |

**Security considerations:**

- `.env` contains plaintext secrets (database password, admin/viewer passwords, bot token). It is already excluded from version control via `.gitignore`; ensure your deployment process (backups, CI logs, container images) does not inadvertently capture it either.
- There are deliberately **no insecure defaults** for `SECRET_KEY`, `ADMIN_PASSWORD`, or `VIEWER_PASSWORD` — the application will not start without them, by design.
- `DB_PASSWORD` has no hardcoded default; leaving it unset will produce a startup warning and a connection failure rather than a silent insecure connection.

**Example `.env`:**

```ini
# Core
SECRET_KEY=replace-with-a-long-random-value
ADMIN_PASSWORD=replace-with-a-strong-password
VIEWER_PASSWORD=replace-with-a-different-strong-password

# Database
DB_HOST=localhost
DB_NAME=argus_db
DB_USER=argus_user
DB_PASSWORD=replace-with-your-db-password
DB_PORT=5432

# NVD
NVD_API_KEY=replace-with-your-nvd-api-key

# Telegram (optional)
TOKEN=replace-with-your-telegram-bot-token
CHAT_ID=replace-with-your-telegram-chat-id

# AI (optional)
LLM_URL=http://127.0.0.1:8080/v1/chat/completions

# Deployment
SESSION_COOKIE_SECURE=true
RUN_SCHEDULER=true
```

> **Variables not used by this codebase.** If you have seen other vulnerability-management projects use variables like `DATABASE_URL`, `POSTGRES_*`, `OPENCVE_URL`, `TELEGRAM_TOKEN`, `OLLAMA_HOST`, `MODEL_NAME`, `SESSION_TIMEOUT`, `LOG_LEVEL`, or `REPORT_DIRECTORY`, note that **ARGUS does not read any of these**. Use the exact variable names in the table above — `DB_*` (not `POSTGRES_*`), `TOKEN` (not `TELEGRAM_TOKEN`), and `LLM_URL` (not `OLLAMA_HOST`/`MODEL_NAME`). The session lifetime (8 hours) and log verbosity are currently fixed in code rather than environment-configurable, and the reports output directory is fixed relative to the application (`bot/dashboard/generated_reports/`), not environment-configurable.

---

## 8. AI Installation

The AI Security Copilot (chat and automated CVE analysis) is **optional** — ARGUS runs completely normally with it unset; the `/api/chat` endpoint simply returns an explicit "not configured" error, and the background CVE analysis job has nothing to do.

ARGUS's AI client (`bot/Ai/llm.py`) speaks the OpenAI-compatible `/v1/chat/completions` schema over plain HTTP and does not embed a specific vendor SDK. It has been evaluated against a local `llama.cpp` server. Below are installation paths for two common local options; either (or any other server implementing the same API shape) works as a drop-in `LLM_URL` target.

### Option A: llama.cpp server (evaluated configuration)

**Linux:**
```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
cmake -B build
cmake --build build --config Release -j
```

Download a GGUF-format model (see model recommendations below), then start the server:

```bash
./build/bin/llama-server -m /path/to/model.gguf --host 0.0.0.0 --port 8080
```

**Windows:** download a prebuilt release from the llama.cpp GitHub releases page, or build with Visual Studio's CMake tooling following the same `cmake -B build` / `cmake --build build` steps in a Developer Command Prompt.

### Option B: Ollama (OpenAI-compatible endpoint)

Ollama is not integrated by any Ollama-specific code path in ARGUS, but its OpenAI-compatible API surface fits the same `LLM_URL` contract.

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
ollama serve
```

**Windows:** download the installer from [ollama.com/download](https://ollama.com/download), then from a terminal:
```powershell
ollama pull llama3.1:8b
```
Ollama's OpenAI-compatible endpoint is exposed at `http://localhost:11434/v1/chat/completions` by default — point `LLM_URL` at that address plus your model name where the server requires it.

### Model recommendations

| Use case | Suggested model class | Approx. memory (Q4 quantization) |
|---|---|---|
| Lightweight / CPU-only | 7B–8B parameter instruction-tuned model | ~5–6 GB RAM |
| Balanced | 13B–14B parameter instruction-tuned model | ~9–10 GB RAM |
| Higher quality, GPU available | 30B+ parameter model | Requires sufficient VRAM; consult your chosen model's card |

**Quantization.** 4-bit (Q4_K_M or similar) quantized GGUF models are a reasonable default for CPU inference — they trade a small amount of accuracy for a large reduction in memory footprint and latency. Use higher precision (Q5/Q6/Q8 or unquantized) only if you have the RAM/VRAM headroom and want maximum answer quality.

**CPU vs. GPU.** CPU inference works but is noticeably slower per response, which matters for the interactive chat endpoint more than for the background batch analysis job (which already paces itself with a delay between requests — see [§12](#12-scheduler-configuration)). A GPU with enough VRAM to hold the model materially improves chat responsiveness.

### Verifying the model server

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Say OK if you can read this."}]}'
```

A successful response contains a `choices[0].message.content` field. If this fails, ARGUS's AI features will fail the same way — verify at this layer first before troubleshooting inside ARGUS.

### How ARGUS connects to the model

Set `LLM_URL` in `.env` to the full completions URL (see the example in [§7](#7-environment-configuration)). ARGUS sends a system prompt plus the user's message (and, for chat, recent conversation history) as a standard `messages` array with `temperature: 0.3` and a `max_tokens` cap, and reads back `choices[0].message.content`. No further ARGUS-side configuration (model name selection, API keys) is required unless your specific server requires them as part of the request — in which case they are outside ARGUS's current configuration surface and would need to be handled at the server/proxy level in front of `LLM_URL`.

---

## 9. External API Configuration

| Service | Authentication | Notes |
|---|---|---|
| **NVD API** | Optional `NVD_API_KEY` | Request a free key at the [NVD API key request page](https://nvd.nist.gov/developers/request-an-api-key). Unauthenticated requests are rate-limited much more aggressively than authenticated ones; a key is strongly recommended for any inventory beyond a handful of assets. The client falls back across CVSS v3.1 → v3.0 → v2 automatically depending on what a given CVE record publishes. |
| **CISA KEV feed** | None required | Public JSON feed, fetched and cached in memory for 24 hours with retry/backoff on transient failures. No configuration needed. |
| **FIRST EPSS API** | None required | Public API, queried in a single batched request per asset scan to minimize call volume. No configuration needed. |
| **OpenCVE** | N/A | Referenced in the wider ARGUS documentation set as a related data-source project, but **there is no OpenCVE client or `OPENCVE_URL` configuration in the current codebase.** Do not set an `OPENCVE_URL` variable expecting it to be read. |
| **Future threat intelligence feeds** | N/A | Planned — see `README.md` §17 Roadmap. No configuration surface exists yet. |

### Rate limits and timeouts

- NVD: unauthenticated requests are limited to a small number of requests per 30-second window; an API key raises this substantially. ARGUS does not currently implement its own additional client-side rate limiting beyond what the NVD client's request pacing provides — if you manage a very large inventory, scans may take proportionally longer rather than failing outright.
- KEV: the 24-hour in-memory cache means ARGUS makes at most one KEV feed request per day under normal operation, regardless of asset count.
- LLM endpoint: requests use a 120-second timeout in `bot/Ai/llm.py`; a local model that takes longer than this to respond will surface as a timeout error to the caller.

### Connectivity testing

```bash
# NVD
curl -s "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=1"

# CISA KEV
curl -s "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json" | head -c 200
```

Both should return JSON. If either fails from your deployment environment, check outbound firewall/proxy rules before troubleshooting inside ARGUS.

---

## 10. Telegram Bot Configuration

The Telegram bot is optional — the dashboard functions fully without it.

### 10.1 Creating a bot and obtaining a token

1. In Telegram, message **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a display name and a unique username ending in `bot`).
3. BotFather returns a token in the form `123456789:ABCdefGhIJKlmNoPQRsTUVwxyz`. This is your `TOKEN`.

### 10.2 Obtaining a chat ID for alerts

1. Send any message to your new bot (or add it to a group/channel).
2. Query `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser or with `curl` after sending the message.
3. Locate `"chat":{"id": ...}` in the response — that numeric (possibly negative, for groups) value is your `CHAT_ID`.

### 10.3 Bot permissions

For a private one-to-one chat, no special permissions are needed. For a group or channel, ensure the bot has permission to send messages (and, if you restrict who can post, that it is added as an admin or explicitly allowed to post).

### 10.4 Environment configuration

Set both `TOKEN` and `CHAT_ID` in `.env` as shown in [§7](#7-environment-configuration). `TOKEN` is required for the bot process to start at all; `CHAT_ID` is required only for alert delivery — the bot's interactive commands work without it, but scan alerts will be silently skipped if it is unset.

### 10.5 Running the bot

```bash
cd bot
python main.py
```

### 10.6 Testing commands

In your Telegram chat with the bot, send `/start` — you should receive "Argus Online 🟢". Then try `/help` for the full command list, and `/status` to confirm the bot can reach both PostgreSQL and the NVD API.

### 10.7 Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Bot process exits immediately with `RuntimeError: TOKEN environment variable is not set` | `.env` missing `TOKEN`, or bot not run from a directory where `.env` is discoverable | Set `TOKEN`; run `python main.py` from the `bot/` directory |
| Bot doesn't respond at all | Invalid token, or bot blocked/not started by the user | Re-verify the token with BotFather; make sure you've sent `/start` to the bot first |
| Alerts never arrive | `CHAT_ID` unset or incorrect | Re-derive `CHAT_ID` via `getUpdates` as shown above |
| `/status` reports a database failure | PostgreSQL unreachable or credentials wrong | Re-verify `DB_*` variables and that PostgreSQL is running (see [§18](#18-troubleshooting)) |

---

## 11. Dashboard Configuration

The dashboard is a standard Flask application (`app.py`) with the following behavior baked in:

- **Host/port** — When run directly (`python app.py`), it binds to `0.0.0.0:5000`. There is no environment variable to change this for the direct-run path; either edit the `app.run(...)` call at the bottom of `app.py`, or bind a different host/port at the WSGI-server layer in production (see [§23](#23-production-deployment)).
- **Debug mode** — Hardcoded to `debug=False` in the direct-run path. Do not enable Flask debug mode in any deployment reachable by anyone other than you — it exposes an interactive debugger capable of arbitrary code execution.
- **Production mode** — For anything beyond local testing, run behind Gunicorn rather than `python app.py`; see [§23](#23-production-deployment) for the exact command and the single-worker constraint.
- **Secret key** — `SECRET_KEY` from `.env`, required at startup (see [§7](#7-environment-configuration)).
- **Session configuration** — `HttpOnly`, `SameSite=Lax`, secure-by-default cookies (`SESSION_COOKIE_SECURE`), and a fixed 8-hour session lifetime.
- **Static files** — Served from `bot/dashboard/static/` by Flask's default static file handling; in production, consider serving these directly from your reverse proxy for better performance (see [§23](#23-production-deployment)).
- **Generated reports** — Written to `bot/dashboard/generated_reports/`, created automatically on startup if it doesn't exist. This path is currently fixed rather than environment-configurable.

---

## 12. Scheduler Configuration

ARGUS uses APScheduler to run the following background jobs, all defined in `bot/jobs/daily_scan.py`:

| Job | Schedule | Purpose |
|---|---|---|
| Daily scan | Every day at 06:00 | Re-scans all assets against NVD/KEV/EPSS |
| Risk snapshot | Every day at 06:30 | Records a point-in-time risk aggregate for trend charts |
| Weekly report | Mondays at 07:00 | Generates the weekly PDF report |
| Monthly report | 1st of each month at 07:00 | Generates the monthly PDF report |
| AI analysis batch | Every 5 minutes | Processes up to 5 pending CVEs through the AI analysis pipeline |
| AI analysis watchdog | Every 5 minutes | Recovers analysis rows stuck in a `processing` state after a crash |
| Chat cache purge | Every 30 minutes | Removes expired AI chat response cache entries |

**Time zones.** All cron-style schedules above run in the time zone of the host/process running the scheduler (APScheduler's default is the local system time zone unless the process environment specifies otherwise). Set your server's system time zone deliberately — the `06:00` daily scan means 06:00 **local to that machine**, not UTC, unless you've explicitly configured the OS to UTC.

**Who starts the scheduler.** Both `app.py` and `bot/main.py` are capable of starting the scheduler on their own startup. If you run both processes simultaneously (dashboard + bot) under the same supervisor, set `RUN_SCHEDULER=false` on **one** of them — otherwise every job runs twice, doubling scan frequency, duplicate report generation, and duplicate AI analysis batches. `RUN_SCHEDULER` defaults to `true` (enabled) if unset.

**Job verification.** Application logs (see [§19](#19-logging)) record scheduler start and each job invocation. To verify jobs are registered without waiting for a scheduled time, check the log line emitted at startup confirming the scheduler started, and inspect the `risk_snapshots` table growing daily as external confirmation the jobs are actually firing:

```sql
SELECT * FROM risk_snapshots ORDER BY id DESC LIMIT 5;
```

**Troubleshooting.** If scheduled jobs never seem to run: confirm `RUN_SCHEDULER` is not set to `false` on every process, confirm the process is staying up (a crashing/restarting process never reaches steady-state scheduling), and check for a `SchedulerAlreadyRunningError` in logs, which indicates the module was re-imported and `scheduler.start()` was called twice in the same process (this is guarded against, but worth ruling out if using an unusual WSGI/process-reload configuration).

---

## 13. Running ARGUS

### Startup order

```
PostgreSQL
    ↓
Dashboard (app.py) and/or Telegram Bot (bot/main.py)
    ↓
Scheduler (started automatically by whichever of the above starts first, per RUN_SCHEDULER)
    ↓
AI (only if LLM_URL is configured — used on demand by chat and by the scheduler's AI analysis job)
    ↓
Scanner (invoked on demand via the dashboard/bot, and automatically by the daily scan job)
```

**Why order matters.** PostgreSQL must be reachable before either the dashboard or the bot starts, because both call schema-migration logic and read/write data immediately on startup — starting either against an unreachable database results in an immediate connection error (see [§18](#18-troubleshooting)). The scheduler, AI layer, and scanner are not separate processes you start yourself — they are invoked by the dashboard/bot process, so there is no separate "start the scheduler" or "start the scanner" step beyond starting `app.py` and/or `bot/main.py` correctly configured.

### Starting the dashboard

```bash
source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
python app.py
```

Visit `http://localhost:5000` (or your configured host/port) in a browser.

### Starting the Telegram bot (optional, separate process)

```bash
source venv/bin/activate
cd bot
python main.py
```

You can run the dashboard and the bot on the same machine or different machines, as long as both can reach the same PostgreSQL database and you've set `RUN_SCHEDULER` correctly per [§12](#12-scheduler-configuration) if running both.

---

## 14. First-Time Setup

1. **Log in as the built-in administrator.** Navigate to `/login` and sign in with username `admin` and the password you set as `ADMIN_PASSWORD`.
2. **(Optional) Create an additional user.** Use `/register` to create a self-service account; new accounts default to the `viewer` role. To grant `admin`, update the role directly in the database: `UPDATE users SET role = 'admin' WHERE username = 'yourname';`
3. **Add your first asset.** From the dashboard, go to `/add_asset` and fill in vendor, product, version, and type — or via Telegram, send `/add Vendor Product Version [Type]`.
4. **Run your first scan.** From the asset detail page, trigger a scan — or via Telegram, `/scan <asset_id>`.
5. **Review findings.** Check `/findings` in the dashboard, or `/findings <asset_id>` in Telegram, to confirm matched CVEs appear with severity and KEV indicators.
6. **Generate your first report.** From `/reports`, trigger an on-demand report — or via Telegram, `/report`.
7. **Test the AI chat** (if `LLM_URL` is configured). Open the dashboard's chat interface and ask a question like "what should I fix first?" — confirm you get a grounded answer referencing your actual findings, not a generic response.
8. **Test alerts** (if the bot is configured). Run a scan on an asset with at least one finding and confirm a consolidated alert message arrives in your configured `CHAT_ID` chat.
9. **Verify installation** using the checklist in [§15](#15-verification-checklist).

---

## 15. Verification Checklist

- [ ] Dashboard loads at `/` and `/login` without error
- [ ] Login succeeds with the `admin` and `viewer` built-in accounts
- [ ] Database connectivity confirmed — `psql` connects, and `\dt` lists ARGUS's tables
- [ ] An asset can be added and appears in `/assets`
- [ ] A scan completes and produces at least one row in `matches` for an asset with known vulnerable software
- [ ] `/findings` shows the scanned findings with severity and (if applicable) a KEV flag
- [ ] Charts render on `/charts` without a 500 error
- [ ] Risk engine — a finding's risk score is non-zero and reflects CVSS/criticality/KEV/EPSS as expected
- [ ] Scheduler is active — `risk_snapshots` gains a new row after the scheduled time passes, or immediately at bot startup (`bot/main.py` records an immediate snapshot on launch)
- [ ] Reports — `/generate_report/<type>` produces a downloadable PDF under `/download/<report_id>`
- [ ] Telegram bot responds to `/start`, `/help`, and `/status` (if configured)
- [ ] Alerts — a scan with new findings delivers a Telegram message to `CHAT_ID` (if configured)
- [ ] AI chat responds to a question with content grounded in your actual data, not a generic answer (if `LLM_URL` is configured)
- [ ] Conversation memory — asking a follow-up question in the same conversation reflects prior context (if AI is configured)
- [ ] AI CVE analysis — after adding an asset with known CVEs, `cve_ai_analysis` rows transition from `pending` to `done` within a few scheduler cycles (if AI is configured)

---

## 16. Updating ARGUS

```bash
# 1. Back up first — see §17
# 2. Stop the dashboard and bot processes
# 3. Pull the latest code
git pull

# 4. Activate your virtual environment and update dependencies
source venv/bin/activate
pip install -r requirements.txt --upgrade

# 5. Apply any new schema migrations
cd bot
python migrate.py
cd ..

# 6. Review .env against the current variable reference in §7 for any new variables
# 7. Restart the dashboard and, if used, the bot
python app.py
```

**Why this order:** dependencies should be current before the application code that depends on them runs; schema migrations should be applied before the application starts serving requests against a database it expects to already be current (both `app.py` and `bot/main.py` also self-apply migrations at startup, so step 5 is a belt-and-suspenders step rather than strictly required, but running it explicitly surfaces migration errors before user-facing traffic hits the app).

**AI model updates.** If you update your local LLM server or swap models, no ARGUS-side migration is needed — `LLM_URL` and the model's own configuration are independent of ARGUS's database schema. Re-run the connectivity test in [§8](#8-ai-installation) after any model server change.

**Rollback.** If an update introduces a regression: stop the affected process(es), restore the pre-update code (`git checkout <previous-tag-or-commit>`), reinstall that version's `requirements.txt`, and — if the update included a schema migration you need to undo — restore the database from the backup taken in step 1, since ARGUS does not provide automated down-migrations (see [§6](#6-database-initialization)).

---

## 17. Backup & Restore

### What to back up

| Item | Location | Backup method |
|---|---|---|
| Database (all application data — assets, findings, CVEs, reports metadata, users, AI conversations, AI cache) | PostgreSQL (`argus_db`) | `pg_dump` |
| Generated PDF reports | `bot/dashboard/generated_reports/` | File-level copy |
| Configuration / environment variables | `.env` | File-level copy, stored securely (it contains secrets) |
| AI conversation history | Included in the database (`ai_conversations`, `ai_messages`) | Covered by the `pg_dump` above — no separate step needed |
| AI response cache | Included in the database (`ai_response_cache`) | Covered by the `pg_dump` above; low-value to restore since entries are short-TTL, but included by default with a full dump |

### Database backup

```bash
pg_dump -U argus_user -h localhost -d argus_db -F c -f argus_backup.dump
```

The `-F c` custom format supports selective and parallel restore; use plain SQL (`-F p`) instead if you want a human-readable/editable dump file.

### Database restore

```bash
# Into a fresh, empty database:
createdb -U postgres -O argus_user argus_db_restored
pg_restore -U argus_user -h localhost -d argus_db_restored argus_backup.dump
```

Point `DB_NAME` at the restored database name once you've verified it, or restore directly over the original database name if replacing it entirely (drop and recreate the database first in that case).

### Full restore procedure

1. Stop the dashboard and bot processes.
2. Restore the database as shown above.
3. Restore `bot/dashboard/generated_reports/` from your file-level backup, if report history matters to you.
4. Restore `.env` from your secure backup (or recreate it — see [§7](#7-environment-configuration)).
5. Start the dashboard, confirm login and data are as expected, then start the bot if used.
6. Run through the [Verification Checklist](#15-verification-checklist).

### Disaster recovery recommendations

- Automate `pg_dump` on a schedule (cron/Task Scheduler) separate from ARGUS's own internal scheduler, and store dumps off the host running PostgreSQL.
- Treat `.env` as a secret requiring the same protection as a password vault entry — back it up, but not alongside unencrypted application backups.
- Test your restore procedure periodically rather than assuming a backup file is valid; a `pg_restore` dry run against a scratch database is inexpensive insurance.

---

## 18. Troubleshooting

| Symptom | Possible Cause(s) | Resolution | Verification |
|---|---|---|---|
| App fails to start: `RuntimeError: SECRET_KEY is missing` | `.env` not present, not loaded, or missing `SECRET_KEY` | Set `SECRET_KEY` in `.env`; confirm you're running `python app.py` from the directory containing `.env` | Restart; error should not recur |
| App fails to start: `ADMIN_PASSWORD and VIEWER_PASSWORD must be set` | Built-in credentials unset | Set both in `.env` | Restart; login page loads |
| `psycopg2.OperationalError: could not connect to server` | PostgreSQL not running, wrong host/port, or firewall blocking | Confirm PostgreSQL is running (`systemctl status postgresql` / Services panel on Windows); verify `DB_HOST`/`DB_PORT` | `psql -U argus_user -d argus_db -h $DB_HOST -p $DB_PORT` succeeds |
| `psycopg2.OperationalError: FATAL: password authentication failed` | Wrong `DB_PASSWORD`, or `pg_hba.conf` auth method mismatch | Reset the password with `ALTER USER argus_user WITH PASSWORD '...'`; check `pg_hba.conf` uses `md5`/`scram-sha-256` for the connection type used | Same `psql` test as above |
| `/api/chat` returns "ARGUS AI is not configured" | `LLM_URL` unset | Set `LLM_URL` to a running OpenAI-compatible endpoint | `curl` test from [§8](#8-ai-installation) succeeds, then retry the chat endpoint |
| AI chat/analysis requests time out | LLM server overloaded, model too large for available hardware, or server not actually running | Confirm the server responds to the `curl` test in [§8](#8-ai-installation) within a reasonable time; consider a smaller/more quantized model or GPU acceleration | Repeat the `curl` test; check LLM server's own logs |
| "Model not found" from the LLM server | Model not pulled/downloaded, or wrong model name configured at the server | Re-run the model pull/download step for your chosen server (Ollama: `ollama pull <model>`; llama.cpp: confirm the `-m` path is correct) | Server-side model list command succeeds |
| Scans fail or return no results for known-vulnerable software | NVD API unreachable, rate-limited, or vendor/product/version strings don't match NVD's CPE naming | Verify NVD connectivity per [§9](#9-external-api-configuration); add `NVD_API_KEY` if hitting rate limits; check vendor/product spelling against NVD's own search UI | Direct `curl` to the NVD API succeeds; a manually re-run scan produces expected matches |
| Telegram bot doesn't respond | Invalid/missing `TOKEN`, bot not started with `/start`, network egress blocked to Telegram's API | Re-verify `TOKEN`; send `/start` first; confirm outbound HTTPS to `api.telegram.org` is allowed | Bot replies to `/start` |
| Scheduler jobs never fire | `RUN_SCHEDULER=false` on every process, or the process keeps crashing/restarting before reaching a scheduled time | Ensure at least one process has `RUN_SCHEDULER` unset or `true`; check process uptime/logs for crash loops | `risk_snapshots` table gains new rows at the expected time |
| Scheduler jobs fire twice | Both `app.py` and `bot/main.py` running with the scheduler enabled on both | Set `RUN_SCHEDULER=false` on one process | Duplicate report/alert volume stops after restart |
| `Permission denied` writing to `generated_reports/` or `logs/` | Filesystem permissions don't allow the running user to write | `chmod`/`chown` the directory appropriately on Linux, or adjust folder permissions in Windows Explorer's Security tab | Report generation succeeds |
| `Address already in use` / port conflict on `5000` | Another process already bound to port 5000 | Stop the conflicting process, or run behind Gunicorn on a different port (see [§23](#23-production-deployment)) and adjust your reverse proxy accordingly | `python app.py` starts without the bind error |
| `pip install` fails compiling a package from source | No prebuilt wheel for your exact Python/OS/architecture combination, missing build tools | Install a compiler toolchain (`build-essential` on Debian/Ubuntu, Xcode Command Line Tools on macOS, Visual Studio Build Tools on Windows) and PostgreSQL client dev headers (`libpq-dev` on Debian/Ubuntu) | `pip install -r requirements.txt` completes |
| `bot/migrate.py` reports `FAILED` for a specific migration | A conflicting manual schema change, or insufficient database privileges | Read the printed error under the failed step; grant missing privileges or manually resolve the conflicting object, then re-run | Re-running `python migrate.py` shows `OK` for that step |
| Report generation fails/hangs | `matplotlib`/`reportlab` missing a system font or dependency, or extremely large finding counts causing slow rendering | Check the application log around the failure for the underlying exception; for very large reports, consider narrowing the report's date/asset scope if such filtering is available in your version | Re-run report generation; check `generated_reports/` for the output file |
| High memory usage / OOM on the LLM server | Model too large for available RAM/VRAM | Switch to a smaller model or a more aggressive quantization (see [§8](#8-ai-installation)) | Model server starts and responds without being killed by the OS |
| Windows: paths with backslashes cause errors in scripts copied from Linux examples | Shell syntax difference, not an ARGUS bug | Use the Windows-specific command variants shown in this guide (PowerShell/Command Prompt), not Linux `bash` syntax verbatim | Command completes without a path-parsing error |
| Linux: `Permission denied` running `python main.py` after clone | Script/file ownership from `git clone` run as a different user, or `venv` created by root | Ensure the virtual environment and project directory are owned by the user running ARGUS: `chown -R $USER:$USER argus/` | Commands run without `Permission denied` |

---

## 19. Logging

**Log destination.** ARGUS does not currently write logs to a file by default. `bot/main.py` configures `logging.basicConfig(...)` with a timestamped format at `INFO` level, which — absent an explicit file handler — sends output to the console (stderr) the process was started from. `app.py` uses the module-level `logging.getLogger(__name__)` without its own `basicConfig` call, so its effective log level and handler follow Python's/Flask's default logging behavior unless you configure logging explicitly at the process/supervisor level.

**The `logs/` directory** exists in the repository and is excluded from version control via `.gitignore`, but nothing in the current codebase writes to it automatically — treat it as reserved for your own logging configuration (e.g., redirecting stdout there) rather than an active log sink out of the box.

**Log levels.** The bot process defaults to `INFO`. There is currently no `LOG_LEVEL` environment variable read by the codebase — to change verbosity, edit the `logging.basicConfig(level=...)` call in `bot/main.py`, or configure logging externally (e.g., a `logging.conf` loaded by your process supervisor, or capturing stdout through Gunicorn/systemd's own logging).

**Debug mode.** `app.py`'s direct-run path is hardcoded to `debug=False`. Do not change this in any shared or production environment.

**Viewing logs in production.** If you follow the systemd deployment in [§23](#23-production-deployment), use `journalctl -u argus -f` to tail logs. If you capture stdout to a file via your process supervisor, use `tail -f` on that file.

**Rotating logs.** Since ARGUS does not manage its own log files, use your process supervisor's or OS's log rotation (`logrotate` on Linux for a redirected stdout file, or systemd/journald's own retention settings) rather than expecting ARGUS to rotate anything itself.

**Sensitive information.** Application logs can include error details (e.g., a failed SQL statement or an HTTP error body) that may reference internal identifiers. They should not include raw passwords or the `SECRET_KEY`/`TOKEN` values based on the current logging call sites reviewed, but treat all application logs as internal-only rather than safe for indiscriminate sharing, since log content can change as the codebase evolves.

---

## 20. Security Recommendations

- **Protect `.env`.** File permissions should restrict read access to the user account running ARGUS (`chmod 600 .env` on Linux). Never commit it; `.gitignore` already excludes it.
- **Use HTTPS in any deployment reachable over a network you don't fully control.** Terminate TLS at a reverse proxy (see [§23](#23-production-deployment)) and keep `SESSION_COOKIE_SECURE=true`.
- **Change default credentials immediately.** `ADMIN_PASSWORD` and `VIEWER_PASSWORD` have no built-in defaults, but choose strong, unique values rather than something easily guessed — these are the only two accounts that exist before any self-registration occurs.
- **Database permissions.** Use a dedicated `argus_user` (as created in [§5](#5-postgresql-installation)) rather than connecting as the PostgreSQL superuser; grant it only the privileges it needs on `argus_db`.
- **Firewall configuration.** Restrict inbound access to PostgreSQL's port (5432) to only the hosts running ARGUS. Restrict inbound access to the dashboard's port to your reverse proxy, not the public internet directly.
- **Reverse proxy.** Put nginx or Caddy in front of Gunicorn rather than exposing Flask's dev server or Gunicorn directly to untrusted networks (see [§23](#23-production-deployment)).
- **Least privilege.** Use the `viewer` role for anyone who doesn't need to modify assets or findings; reserve `admin` for those who do.
- **File permissions.** Ensure `generated_reports/` and the database data directory are not world-readable, since reports and the database can contain internal asset details.
- **Secrets management.** For anything beyond a single-operator lab deployment, consider a secrets manager (e.g., your cloud provider's, or HashiCorp Vault) to inject `.env` values at process start rather than storing them in a plaintext file long-term.
- **Regular updates.** Keep PostgreSQL, Python, and ARGUS's pinned dependencies current — periodically re-run `pip list --outdated` inside your virtual environment and review changelogs before upgrading, especially for `Flask`, `Flask-Login`, and `Flask-WTF`, which are security-relevant.
- **Production deployment recommendation, in short:** dedicated non-root service account, Gunicorn with exactly one worker (see [§23](#23-production-deployment) for why), reverse proxy with HTTPS, firewalled database, `.env` permissions locked down, and regular backups per [§17](#17-backup--restore).

---

## 21. Performance Recommendations

- **PostgreSQL settings.** For anything beyond a small lab install, tune `shared_buffers` (roughly 25% of available RAM on a dedicated database host), `effective_cache_size` (roughly 50–75% of available RAM), and `work_mem` based on concurrent query load. Use PostgreSQL's own tuning documentation as the authority here — ARGUS does not require non-standard settings.
- **Connection pooling.** ARGUS already implements internal connection pooling (`bot/database/db.py`, a `ThreadedConnectionPool`) rather than opening a raw connection per query — `DB_POOL_MIN_CONN`/`DB_POOL_MAX_CONN` control its size. Raise `DB_POOL_MAX_CONN` if you run many concurrent dashboard users and see connection contention, but stay under PostgreSQL's own `max_connections` setting (default 100) across all ARGUS processes combined.
- **AI model selection.** Smaller/more quantized models respond faster and are usually sufficient for the structured, context-grounded analysis ARGUS performs (see [§10 AI Capabilities in README.md](./README.md#10-ai-capabilities)); reserve larger models for cases where you've observed a real quality gap, not by default.
- **Pagination.** The dashboard already paginates findings and asset list views — avoid disabling or bypassing this if you customize templates, since rendering full unpaginated tables against a large `matches` table will be slow.
- **Background jobs.** Scans, reports, and AI analysis already run via APScheduler rather than blocking request handling — avoid triggering large on-demand operations (e.g., a full-inventory manual scan) during peak dashboard usage hours if your asset count is large.
- **Report generation.** PDF generation cost scales with the number of findings included; scheduled weekly/monthly reports amortize this cost off-hours (07:00) rather than during typical usage.
- **Caching.** The AI chat response cache avoids redundant LLM calls for repeated questions against unchanged data; leave the cache-purge job (every 30 minutes) running rather than disabling it, so the cache doesn't grow unbounded.
- **Hardware.** See [§1 System Requirements](#1-system-requirements) for sizing guidance by deployment scale.

---

## 22. Docker Installation (Future Support)

> **Status: Planned.** ARGUS does not currently ship a `Dockerfile` or `docker-compose.yml` — the `docker/` directory in the repository is a placeholder. The section below describes the intended future containerized deployment model so operators can plan for it; none of the commands below will work until this is implemented.

**Planned architecture.** A multi-container Compose stack with:
- A `postgres` service using the official PostgreSQL image, with a named volume for data persistence.
- An `argus-dashboard` service built from a `Dockerfile` in the project root, running `app.py` behind Gunicorn.
- An optional `argus-bot` service running `bot/main.py`, sharing the same image but a different entrypoint.
- An optional `llm` service (e.g., an `llama.cpp` server image) for fully self-contained AI functionality.

**Planned volumes.**
- `postgres_data` — PostgreSQL data directory.
- `argus_reports` — mapped to `bot/dashboard/generated_reports/`, so generated PDFs survive container recreation.
- `argus_env` or a Compose `env_file` directive — for `.env`, rather than baking secrets into the image.

**Planned networks.** An internal Compose network so `argus-dashboard`/`argus-bot` reach `postgres` and `llm` by service name (e.g., `DB_HOST=postgres`) without exposing the database port to the host at all.

**Planned environment variable handling.** The same variables documented in [§7](#7-environment-configuration) would be passed through Compose's `env_file:` or `environment:` directives — no new Docker-specific variables are planned beyond standard Compose networking substitutions (e.g., `DB_HOST=postgres` instead of `localhost`).

**Until this lands,** deploy ARGUS using the virtual-environment approach in this guide, optionally under a process supervisor as described in [§23](#23-production-deployment). Track `README.md`'s Roadmap section for status.

---

## 23. Production Deployment

### Gunicorn

Install Gunicorn into your virtual environment (it is not in `requirements.txt`, since it's a deployment choice rather than an application dependency):

```bash
pip install gunicorn
```

Run with **exactly one worker**:

```bash
gunicorn -w 1 -b 127.0.0.1:5000 app:app
```

**Why exactly one worker.** `app.py` performs schema migration and starts the background scheduler at module import time. Gunicorn's default worker model imports the application module once per worker process — running multiple workers would run schema migration and start the scheduler (daily scans, reports, AI analysis) once per worker, duplicating every scheduled job. Stay at one worker until the codebase is refactored to an application-factory pattern that separates import-time side effects from request handling.

If you need more request-handling concurrency than one Gunicorn worker provides, scale via Gunicorn's `--threads` flag (multiple threads within the single worker) rather than additional worker processes, or run the bot (`bot/main.py`) as a fully separate process with `RUN_SCHEDULER=false` so only the dashboard's single worker owns scheduling.

### Reverse proxy (nginx example)

```nginx
server {
    listen 80;
    server_name argus.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name argus.example.com;

    ssl_certificate     /etc/letsencrypt/live/argus.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/argus.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

### SSL certificates

Use [Certbot](https://certbot.eff.org/) for free, auto-renewing Let's Encrypt certificates, or your organization's existing certificate issuance process for internal deployments.

### systemd service (Linux)

```ini
# /etc/systemd/system/argus.service
[Unit]
Description=ARGUS Vulnerability Management Dashboard
After=network.target postgresql.service

[Service]
Type=simple
User=argus
WorkingDirectory=/opt/argus
Environment="PATH=/opt/argus/venv/bin"
ExecStart=/opt/argus/venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now argus
sudo systemctl status argus
```

Create an equivalent unit for `bot/main.py` if running the Telegram bot as a service, setting `RUN_SCHEDULER=false` in its `Environment=` block if the dashboard service already owns scheduling.

### Automatic restart

The systemd unit above (`Restart=on-failure`) restarts the process automatically on a crash. Combine with `RestartSec` to avoid a tight crash-restart loop consuming resources if the underlying failure is persistent (e.g., database unreachable).

### Log rotation

See [§19 Logging](#19-logging) — since ARGUS logs to stdout by default, systemd's journal handles retention (`journalctl` with `SystemMaxUse=` in `journald.conf`), or redirect to a file and manage it with `logrotate`.

### Monitoring and health checks

There is no dedicated `/health` endpoint distinct from the dashboard's own routes in the current codebase; use the Telegram bot's `/status` command (which performs a real database and NVD API check) as a functional health signal if the bot is running, or monitor the dashboard's `/login` page for a `200` response as a basic liveness check, combined with monitoring PostgreSQL and your LLM server (if used) independently.

### Backups

Automate the `pg_dump` procedure in [§17](#17-backup--restore) on a schedule independent of ARGUS itself (cron/systemd timer), and verify backups periodically.

---

## 24. Uninstallation

1. **Stop services.**
   ```bash
   sudo systemctl stop argus       # if using systemd
   sudo systemctl disable argus
   ```
   Or, if running manually, stop the `app.py`/`main.py` processes (Ctrl+C, or kill the process if backgrounded).

2. **Remove the Python environment.**
   ```bash
   rm -rf venv/
   ```

3. **Delete the database** (irreversible — back up first if there's any chance you'll want the data later):
   ```sql
   DROP DATABASE argus_db;
   DROP USER argus_user;
   ```

4. **Remove generated reports.**
   ```bash
   rm -rf bot/dashboard/generated_reports/*
   ```

5. **Clean the AI response cache and conversation data.** Covered by dropping the database in step 3; no separate cache files exist outside PostgreSQL.

6. **Remove AI models (optional)** — only relevant if you installed a local LLM server specifically for ARGUS and don't need it for anything else:
   ```bash
   # llama.cpp — simply delete the downloaded .gguf file(s)
   rm /path/to/model.gguf

   # Ollama
   ollama rm <model-name>
   ```

7. **Complete cleanup.**
   ```bash
   cd .. && rm -rf argus/
   ```
   Remove any systemd unit files created in [§23](#23-production-deployment):
   ```bash
   sudo rm /etc/systemd/system/argus.service
   sudo systemctl daemon-reload
   ```

---

## 25. Frequently Asked Questions

**Can ARGUS run fully offline?** The core asset/finding/risk/dashboard/reporting functionality requires network access only for NVD, KEV, and EPSS data during scans — it does not require a network connection to simply browse existing data. AI features require reaching your configured `LLM_URL`, but if that endpoint is a local server on the same machine or LAN, no internet access is required for AI either.

**Can I use a cloud-hosted LLM instead of a local one?** Yes, functionally — `LLM_URL` accepts any reachable OpenAI-compatible `/v1/chat/completions` endpoint, including a cloud provider's compatible API surface. Be aware this sends your findings/asset context (whatever the context builder assembles for a given question) to that external service; evaluate this against your own data-handling requirements before doing so, since ARGUS itself does not filter or redact this content before sending it.

**Can I use a different database engine (e.g., MySQL)?** No — the entire `database/` layer is written against `psycopg2` and PostgreSQL-specific SQL (e.g., `ON CONFLICT`, `TIMESTAMPTZ`). Swapping engines would require rewriting that layer; it is not a supported configuration option.

**Can I disable AI features entirely?** Yes — simply leave `LLM_URL` unset. The chat endpoint returns an explicit "not configured" message, and the AI analysis background job has nothing to process without a reachable LLM, effectively idling.

**Can I use SQLite instead of PostgreSQL?** No, for the same reason as above — the schema and queries rely on PostgreSQL-specific features (`SERIAL`, `TIMESTAMPTZ`, `ON CONFLICT`, JSON/array handling in places) that do not map directly to SQLite.

**Can multiple users connect to the dashboard at once?** Yes — the dashboard is a standard multi-user Flask web application with session-based per-user authentication (`admin`, `viewer`, and any self-registered accounts). Concurrent access is bounded by your WSGI server's concurrency configuration (see [§23](#23-production-deployment)) and the database connection pool size (see [§21](#21-performance-recommendations)).

**How much RAM is actually required?** For ARGUS itself (dashboard + bot + PostgreSQL) at small scale, 4 GB is workable. Add substantially more if you also run a local LLM on the same host — see [§8](#8-ai-installation) for model-size-to-RAM guidance.

**Can I deploy ARGUS in Docker today?** Not out of the box — see [§22](#22-docker-installation-future-support). It is on the roadmap but not implemented in the current codebase.

**Can I integrate Active Directory / LDAP / SSO?** Not currently — authentication is limited to the built-in `admin`/`viewer` accounts and self-registered local accounts stored in the `users` table. Enterprise SSO is listed as a roadmap item in `README.md`, not an existing capability.

---

## 26. References

- [`README.md`](./README.md) — project overview, features, architecture, and current project status
- `API.md` — dashboard route/API reference (not yet published — see `README.md` §16 Documentation for current status)
- `ARCHITECTURE.md` — extended architecture documentation (not yet published — see `README.md` §5 and §8 in the meantime)
- `DATABASE.md` — full schema reference (not yet published — see `bot/database/schema.sql` and `bot/migrate.py` directly)
- `AI.md` — AI Security Copilot design reference (not yet published — see `README.md` §10 and [§8](#8-ai-installation)/[§9](#9-external-api-configuration) of this document)
- `DEPLOYMENT.md` — containerized/production deployment (not yet published — see [§22](#22-docker-installation-future-support) and [§23](#23-production-deployment) of this document)
- `SECURITY.md` — security model and responsible disclosure process (not yet published — see `README.md` §11 and [§20](#20-security-recommendations) of this document)
- `ROADMAP.md` — planned features (not yet published — see `README.md` §17 Roadmap)
