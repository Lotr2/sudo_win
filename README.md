# sudo_win: an open-source team of AI agents from idea to sellable product

A founder brings a short brief: an idea plus skills, budget, time, target market and values. Six departments, one per phase, take it through **Discover → Validate → Build → Commercial setup → Launch (sell, deliver, get paid) → Operate and learn**, and the system says **"don't launch this"** when the evidence is weak. Plain code (a LangGraph state machine) runs the pipeline, the gates, approvals and the money path; agents on open-weight models do the work inside each department; a Founder Liaison talks with the founder throughout. It is the open, honest alternative to closed AI business tools: no income promises, sourced evidence, the founder in control, and $0 to run.

- **Every phase ends in a gate** decided by code against thresholds fixed in advance: go, repeat, pivot or **stop**. Gate 2's target is hashed into the log before any result exists. Every recommendation comes with a points breakdown, the evidence behind it, and other directions to try.
- **Nothing is published, sent, spent, signed or refunded** without a founder approval record bound to the item's content hash, created only by a button in the web UI or an interactive terminal confirmation.
- **Every market claim cites a source URL or is labelled `ASSUMPTION`**: agents cite evidence IDs that only the search tools can create, and any number must appear word for word in the cited quote.
- **Payments, publishing, emails and the Phase 6 event log are MOCKED.** No payment provider, sandbox or keys exist anywhere in the code.

## Quickstart

```bash
docker compose up
```

Open **http://localhost:8000**, describe your idea (or press "Fill in an example") and press **Start my project**. The first start downloads GPT-OSS-20B (about 13 GB). Optional: copy `.env.example` to `.env` and set a free `GEMINI_API_KEY` to run the writing roles on Gemma 4 31B; without a key, every role runs on GPT-OSS-20B (single-model mode, shown in the UI).

| Want to | Run |
|---|---|
| Use the terminal instead of the browser | `docker compose exec app venture run --idea "..."` (or a brief file, or just `venture run` and type it); `venture --help` lists every command |
| Use GPT-OSS-20B already running on llama.cpp | start `llama-server -m gpt-oss-20b.gguf --jinja -c 16384 --host 0.0.0.0 --port 8090`, then `docker compose -f docker-compose.yml -f compose.llamacpp.yaml up` (no model download) |
| Use Ollama on another machine | set `OLLAMA_HOST=http://<machine>:11434` in `.env`, then `docker compose -f docker-compose.yml -f compose.remote-ollama.yaml up` |
| Run without Docker | install [uv](https://docs.astral.sh/uv/), point `OLLAMA_HOST` (or `OSS_BACKEND=llamacpp`) and `SEARXNG_URL` at your services in `.env`, then `uv run venture doctor` and `uv run venture serve` |
| Keep a live demo short | `RUN_MODE=demo`: time budget per step, 5 sources per version, 3 intake questions, 30-second question timeouts |
| Start a real project or a practice run | the web form asks; in the terminal, `venture run` is real and `venture run --practice` is practice. Every log entry carries `"project": "real"` or `"practice"`, and a person's decisions carry `real_founder: true` |
| Add a finished real project to the repository | `venture export RUN_ID --tag real --append` (adds its outputs as `phase<N>_step<M>_real_…` and its log entries to `decision_log.json`) |
| Check that it works | `uv run pytest` (118 smoke tests on a fake model, no model needed; also run on every push by `.github/workflows/tests.yml`); `uv run python evals/run_evals.py` (8 briefs on the real models) |

## What works and what is mocked

| Phase | Step | Status |
|---|---|---|
| All | Orchestrator (LangGraph, SQLite checkpoints, pause and resume), Venture Dossier, decision log on every agent, tool and gate call | Working |
| All | Model router: GPT-OSS-20B on Ollama or llama.cpp, Gemma 4 31B on the Gemini API free tier, schema-checked output, `venture doctor` | Working |
| All | Founder Liaison: intake interview, read-back, kickoffs, clarifying questions chosen by code, stage walk-throughs, answers from the record | Working |
| All | Policy Guard (rules, then a classifier on the other model) on the brief, every chat message, outputs and outbox items | Working |
| All | Agentic skills: research loop until the evidence bar is met, graded evidence, agreement between readings, idea shortlist, memory between retries, fact-check of every explanation, product self-review | Working |
| All | Web UI (guided form, stage tracker, approvals, readable documents) and CLI | Working |
| All | Founder decisions in the committed demo run (`outputs/`, `decision_log.json`) | **MOCKED**: made by `scripts/simulate_founder.py` on a separate channel and labelled `simulated_founder` in every log entry; the app itself only accepts a person's approvals in the UI or CLI |
| 1 Discover | Brief parsing, idea shortlist, research (SearXNG search, page reading, Evidence Ledger), Scorer with a points breakdown per version, Gate 1, interview script | Working |
| 1 Discover | Customer interviews | **STUBBED**: the founder runs them and pastes notes; interviews are never simulated |
| 2 Validate | Lean Canvas debate, experiment design, threshold lock with hash, landing page (founder's brand, three layouts), Gate 2 | Working |
| 2 Validate | Publishing the landing page | **MOCKED**: no hosting; the founder hosts the page |
| 2 Validate | Sign-up counts | Entered by the founder: recorded as founder-reported in a real project, **MOCKED** when marked as practice numbers |
| 3 Build | Product plan checked per product type, three templates (guide, template pack, web tool) with acceptance tests, build–test loop in a no-network sandbox, self-review, user docs, Gate 3 | Working |
| 3 Build | Web tool behaviour in a browser | **STUBBED**: structure and safety are tested; the founder tries the preview |
| 4 Commercial setup | Pricing from sourced anchors, policy drafts, Compliance Linter, Gate 4 | Working |
| 4 Commercial setup | Legal review | **STUBBED**: drafts are marked "DRAFT: not legal advice" |
| 5 Launch | Store page and posts through the Approval Outbox; the founder approves or rejects each item | Working |
| 5 Launch | Posting, publishing | **MOCKED**: a publish adapter logs the approved item; nothing goes online |
| 5 Launch | Sale to cash: payment triggers delivery once, founder-approved refunds revoke access, reconciliation | Working, against **MOCKED** payment events (no provider, no sandbox, no keys) |
| 6 Operate | Metrics Engine, Support Agent (answers only from the user docs), review memo with a fact-checking critic, Gate 6 | Working |
| 6 Operate | Activity (visitors, orders, refunds, questions) | Entered by the founder: founder-reported in a real project, **MOCKED** in a practice run; the app never collects it itself |
| 6 Operate | Sending support replies | **MOCKED**: approved replies are recorded, not sent |

## Architecture

The system is organised like a small company: six departments, one per phase, each on the agent pattern its work needs (fan-out research, generator–critic debate, build–test loop, pipes and filters, transactional outbox, monitor loop). A LangGraph state machine moves one shared record, the Venture Dossier, through the phases and pauses at a single founder node for every question, approval and stage decision. Gates are pure functions over evidence and thresholds locked before results exist, and every outcome is explained point by point. Agents cite evidence by ID from a ledger only tools can write; a checker on the other model reviews critical work, and a fact-checker checks every explanation before the founder reads it. Agents can only draft into an outbox, and no LLM touches the money path. Design with diagrams: [ARCHITECTURE.md](ARCHITECTURE.md); every step in full and the design decisions: [docs/architecture-details.md](docs/architecture-details.md); one-page summary and stress tests: [docs/overview.md](docs/overview.md).

## Models and free resources

- **GPT-OSS-20B** (`gpt-oss:20b`, open-weight, Apache-2.0) on **Ollama** (bundled in Docker) or **llama.cpp** (`OSS_BACKEND=llamacpp`): research, building and judging, with reasoning effort set per role. Any other open-weight model Ollama or llama.cpp serves (GPT-OSS-120B, Qwen) can be used by setting `MODEL_OSS`.
- **Gemma 4 31B** (`gemma-4-31b-it`, open-weight) on **Google's Gemini API free tier**, optional: parsing, drafting, copy and the Founder Liaison. The router keeps to the free-tier limits and honours retry delays. Gemma's prompts go to Google, and the founder is told at intake.
- **No paid service is required.** Without `GEMINI_API_KEY`, or with `GEMMA_ROLES_ON_OSS=1`, every role runs locally on GPT-OSS-20B and same-model checks are flagged in the log. When both models are available, the model that checks a piece of work is never the one that wrote it.
- **Search:** self-hosted SearXNG in Docker, free and keyless, with automatic fallback to engines that still answer when public engines block it.
- **Python packages:** LangGraph, pydantic, httpx, FastAPI, uvicorn (pinned in `pyproject.toml` and `requirements.txt`).

## Cost per run

**$0.00 per run** (GPT-OSS-20B local; Gemma 4 31B, if used, on the Gemini API free tier). The committed demo run (`decision_log.json`) took the main idea through all six stages with about 251,000 tokens and the weak idea to its STOP with about 5,000 tokens, all on GPT-OSS-20B locally. In the evals, Stage 1 takes 40–70 seconds and 55,000–75,000 tokens per idea, and an excluded idea is stopped in about 10 seconds and 5,000 tokens (`evals/results.md`). Tokens are logged per call, so other setups can be costed from the log.

## Demo video

Not recorded yet.

## What our system will NOT do

- **Promise income or success.** Copy, policies and replies are checked by code for income promises; no prompt asks for a forecast.
- **Fake trust or urgency.** No reviews, testimonials, sign-up or user counts, scarcity or countdown timers: the page templates have no slots for them, and the Policy Guard refuses requests like "write 50 five-star reviews" (tested in `tests/` and `evals/`).
- **Pose as anyone.** Posts are drafts in the founder's own voice for the founder to publish; the system never posts as the founder, a customer or another brand.
- **Act without approval.** Nothing is published, sent, spent, signed or refunded without a hash-bound founder approval record. Chat text never counts as approval, and there is no auto-approve setting.
- **Handle money or credentials.** It never holds, moves or refunds real money, uses no payment provider (not even a sandbox), never creates accounts, and never asks for or stores passwords or card numbers.
- **Build excluded products.** Physical goods; financial, investment or crypto products; health or legal advice products are stopped at intake, with safe alternatives suggested.
- **Invent evidence.** Customer interviews are never simulated, unsourced claims are labelled `ASSUMPTION`, and practice numbers are labelled `MOCKED` and never count as validation.
- **Give legal advice.** Policy drafts are marked "DRAFT: not legal advice".
- **Paywall its own features.** Everything is open source (MIT) and free to run.

## How the project stays alive after the hackathon

- **Maintainers:** the team behind this repository, reviewing issues and pull requests on GitHub.
- **Roadmap:** real publish adapters behind the same approval records; more product templates with acceptance packs; more search back ends; the real-model evals run on every prompt change with the pass rate published; tested presets for other open-weight models and smaller hardware.
- **Community:** every prompt is a plain file in `prompts/`, every gate a pure function in `gates.py`, every product template a folder with its own tests, and every expected behaviour a case in `evals/cases.json`, so a contribution can be a single prompt, rule, template or test brief.
- **Funding:** it runs at $0 on open-weight models, so it doesn't depend on API credits or a hosted service. It needs contributor time, not money.
