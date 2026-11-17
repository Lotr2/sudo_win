# sudo_win: an open-source team of AI agents from idea to sellable product

A founder brings a short brief: an idea plus skills, budget, time, target market and values. Six departments, one per phase, take it through **Discover → Validate → Build → Commercial setup → Launch (sell, deliver, get paid) → Operate and learn**. Plain code (a LangGraph state machine) runs the pipeline, the gates, approvals and the money path. Agents on two open-weight models do the work inside each department: GPT-OSS-20B runs locally and Gemma 4 31B runs on Google's free API tier. A Founder Liaison talks with the founder throughout.

- **Every phase ends in a gate** decided by code against thresholds fixed in advance: go, repeat, pivot or **stop**. Gate 2's target is hashed into the log before any result exists.
- **Nothing is published, sent, spent, signed or refunded** without a founder approval record bound to the item's content hash, created only by a button in the web UI or an interactive terminal confirmation.
- **Every market claim cites a source URL or is labelled `ASSUMPTION`**: agents cite evidence IDs that only the search tools can create.
- **Payments, publishing, emails and the Phase 6 event log are MOCKED.**

## Quickstart

```bash
docker compose up
```

Open **http://localhost:8000**, describe your idea and press **Start my project**. The first start downloads GPT-OSS-20B (about 13 GB). Gemma 4 31B needs a free Gemini API key: copy `.env.example` to `.env` and set `GEMINI_API_KEY`. Without a key, every role runs on GPT-OSS-20B (single-model mode, shown in the UI).

- **Ollama on another machine:** set `OLLAMA_HOST=http://<machine>:11434` in `.env`, then `docker compose -f docker-compose.yml -f compose.remote-ollama.yaml up`.
- **GPT-OSS-20B on llama.cpp instead of Ollama:** start `llama-server -m gpt-oss-20b.gguf --jinja -c 16384 --host 0.0.0.0 --port 8090`, then `docker compose -f docker-compose.yml -f compose.llamacpp.yaml up` (no bundled Ollama, no model download). Without Docker, set `OSS_BACKEND=llamacpp` and `LLAMACPP_URL=http://localhost:8090` in `.env`.
- **Terminal instead of the browser:** `docker compose exec app venture run briefs/plausible_client_kit.md` (interactive; `venture --help` lists every command). The brief can also be inline, `venture run --idea "..."`, or typed when asked: `venture run`.
- **Without Docker (development):** install [uv](https://docs.astral.sh/uv/), start SearXNG (`docker compose up searxng` with port 8080 published, or any SearXNG with JSON enabled), set `OLLAMA_HOST` and `SEARXNG_URL` in `.env`, then `uv run venture doctor` and `uv run venture serve`.
- **Tests (no model needed):** `uv run pytest`. Live checks against the real models: `uv run pytest -m live`.
- **Demo mode:** `RUN_MODE=demo` sets a time budget per step, at most 5 sources per variant, and caps the conversation (3 intake questions, 1 blocking question per phase, 30-second timeouts).

## What works and what is mocked

| Phase | Step | Status |
|---|---|---|
| All | Orchestrator (LangGraph, SQLite checkpoints), Venture Dossier, decision log on every agent, tool and gate call | Working |
| All | Model router: GPT-OSS-20B on Ollama + Gemma 4 31B on the Gemini API, free-tier rate limiter, `venture doctor` | Working |
| All | Founder Liaison: intake interview, read-back, kickoffs, clarifying questions chosen by code, gate walk-throughs, "why?" answers from the record | Working |
| All | Founder decisions in the committed demo run (`outputs/`, `decision_log.json`) | **MOCKED**: made by `scripts/simulate_founder.py` on a separate channel, each labelled `simulated_founder` in the log; the app itself only accepts a person's approvals in the UI or CLI |
| All | Policy Guard (rules, then a classifier on the other model) on the brief, chat, outputs and outbox | Working |
| 1 Discover | Brief parsing, Opportunity Scout, parallel research (SearXNG search, page fetch, Evidence Ledger), Scorer, Gate 1, interview script | Working |
| All | Agentic skills: research loop (search, read, reflect, refine) until the evidence bar is met; graded evidence (first-hand, on-market); findings kept only when several readings agree; idea shortlist from 10 candidates; memory between retries; fact-check of every explanation; product self-review; `evals/` | Working |
| 1 Discover | Customer interviews | **STUBBED**: the founder runs them and pastes notes; nothing is simulated |
| 2 Validate | Lean Canvas debate, experiment design, threshold lock with hash, landing page, Gate 2 | Working |
| 2 Validate | Publishing the landing page; sign-up counts | **MOCKED**: no hosting; the count is founder-entered and labelled MOCKED unless real |
| 3 Build | Spec, three product templates (guide, template pack, web tool) with acceptance tests, build–test loop in a no-network sandbox, user docs, Gate 3 | Working |
| 3 Build | Web tool behaviour in a browser | **STUBBED**: structure and safety are tested; the founder tries the preview |
| 4 Commercial setup | Pricing from sourced anchors, policy drafts, Compliance Linter, Gate 4 | Working |
| 4 Commercial setup | Legal review | **STUBBED**: drafts are marked "DRAFT: not legal advice" |
| 5 Launch | Store page and posts through the Approval Outbox, founder approves or rejects each item | Working |
| 5 Launch | Posting, publishing | **MOCKED**: a publish adapter logs the approved item; nothing goes online |
| 5 Launch | Sale to cash: idempotent delivery, founder-approved refunds that revoke access, reconciliation | Working, against **MOCKED** payment events (no provider, no keys) |
| 6 Operate | Metrics Engine, Support Agent (answers only from the user docs), memo with critic, Gate 6 | Working |
| 6 Operate | Event log (visitors, orders, refunds, tickets) and sending replies | **MOCKED**: entered by the founder and labelled MOCKED |

## Architecture

The system is organised like a small company: six departments, one per phase, each on the agent pattern its work needs (fan-out research, generator–critic debate, build–test loop, pipes and filters, transactional outbox, monitor loop). A LangGraph state machine moves one shared record, the Venture Dossier, through the phases, and pauses at a single founder node for every question, approval and gate decision. Gates are pure functions over evidence and thresholds locked before results exist. Agents cite evidence by ID from a ledger only tools can write, and any number must appear word for word in a cited quote. Agents can only draft into an outbox, and no LLM touches the money path. Full design: [ARCHITECTURE.md](ARCHITECTURE.md). Summary and stress tests: [docs/overview.md](docs/overview.md). Build notes: [docs/implementation-plan.md](docs/implementation-plan.md).

## Models and free resources

- **GPT-OSS-20B** (`gpt-oss:20b`, open-weight, Apache-2.0), served by **Ollama** (bundled in Docker, or on another machine) or by **llama.cpp**'s `llama-server` (`OSS_BACKEND=llamacpp`): research, building and judging, with reasoning effort per role.
- **Gemma 4 31B** (`gemma-4-31b-it`, open-weight), called through **Google's Gemini API free tier**: parsing, drafting, copy and the Founder Liaison. The router keeps to the free-tier limits (requests and tokens per minute, requests per day; configurable in `.env`) and honours the API's retry delays. Gemma's prompts are sent to Google; the founder is told at intake.
- **No paid service is required.** Without `GEMINI_API_KEY`, or with `GEMMA_ROLES_ON_OSS=1`, every role runs locally on GPT-OSS-20B and same-model checks are flagged in the log. Whenever both models are available, the model that checks a piece of work is never the one that wrote it.
- **Search:** self-hosted SearXNG in Docker, free and keyless.
- **Python packages:** LangGraph, pydantic, httpx, FastAPI, uvicorn (pinned in `pyproject.toml` / `requirements.txt`).

## Cost per run

**About $0.00 per run** (GPT-OSS-20B local, Gemma 4 31B on the Gemini API free tier). The committed demo run (`decision_log.json`) took the main idea through all six stages with about 237,000 tokens (the agentic skills read and check more) and the weak idea to its STOP with about 5,400 tokens, all on GPT-OSS-20B locally: $0.00.

## Demo video

Not recorded yet.

## What our system will NOT do

- **Promise income or success.** Copy, policies and replies are checked by code for income promises; no prompt asks for a forecast.
- **Fake trust or urgency.** No reviews, testimonials, sign-up or user counts, scarcity or countdown timers; the page templates have no slots for them and the Policy Guard blocks them.
- **Pose as anyone.** Posts are drafts in the founder's own voice for the founder to publish; the system never posts.
- **Act without approval.** Nothing is published, sent, spent, signed or refunded without a hash-bound founder approval record. Chat text never counts as approval, and there is no auto-approve setting.
- **Handle money or credentials.** It never holds, moves or refunds real money, never creates accounts, and never asks for or stores passwords or card numbers. All payments are MOCKED.
- **Build excluded products.** Physical goods; financial, investment or crypto products; health or legal advice products are stopped at intake.
- **Give legal advice.** Policy drafts are marked "DRAFT: not legal advice".
- **Paywall its own features.** Everything is open source (MIT) and free to run.

## How the project stays alive after the hackathon

- **Maintainers:** the team behind this repository, reviewing issues and pull requests on GitHub.
- **Roadmap:** real publish adapters and a real payment provider behind the same approval records; more product templates with acceptance packs; more search back ends and source-quality scoring; a public evaluation set of briefs (including weak ideas that should be stopped) run in CI; tested routing presets for other open-weight models.
- **Community:** every prompt is a plain file in `prompts/`, every gate a pure function in `gates.py`, and every product template a folder with its own tests, so a contribution can be a single prompt, rule or template.
- **Funding:** it runs at $0 on open-weight models, so it doesn't depend on API credits or a hosted service. It needs contributor time, not money.
