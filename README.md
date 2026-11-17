# sudo_win: an open-source team of AI agents from idea to sellable product

A founder brings a short brief: an idea plus skills, budget, time, target market and values. Six departments, one per phase, take it through **Discover → Validate → Build → Commercial setup → Launch → Operate and learn**. Plain code runs the pipeline, the gates, approvals and the money path; agents on two open-weight models (GPT-OSS-20B locally, Gemma 4 31B on Google's free API tier) do the work inside each department, and a Founder Liaison talks with the founder throughout.

- **Status: designed, not built yet.** Every part is **STUBBED**; see the table below.
- **Every phase ends in a gate** decided by code against thresholds fixed in advance: go, repeat, pivot or **stop**.
- **Nothing is published, sent, spent or signed** without a founder approval record from the UI or CLI.
- **Payments, publishing and the event log are MOCKED.**

## Quickstart

```bash
docker compose up
```

The first start downloads GPT-OSS-20B (about 13 GB). Gemma 4 31B needs a free Gemini API key: copy `.env.example` to `.env` and set `GEMINI_API_KEY`. Without a key every role runs on GPT-OSS-20B (single-model mode, announced at startup). Then, in a second terminal:

```
docker compose exec app python -m venture_builder.cli
```

## What works and what is mocked

| Phase | Step | Status |
|---|---|---|
| 1 Discover | Intake interview, brief parsing, policy check, research, scoring, Gate 1 | **STUBBED** |
| 2 Validate | Lean Canvas debate, locked experiment targets, landing page, Gate 2 | **STUBBED** |
| 3 Build | Spec as tests, build–test loop, user guide, Gate 3 | **STUBBED** |
| 4 Commercial setup | Pricing, policy drafts, compliance linter, Gate 4 | **STUBBED** |
| 5 Launch | Store page and posts through the approval outbox; publishing will be **MOCKED** | **STUBBED** |
| 5 Launch | Sale to cash: idempotent delivery, refunds revoke access, reconciliation; payments will be **MOCKED** | **STUBBED** |
| 6 Operate and learn | Metrics, support drafts, persevere-or-pivot memo, Gate 6; event log will be **MOCKED** | **STUBBED** |

Target status for each part: [ARCHITECTURE.md · Built vs. stubbed](ARCHITECTURE.md#built-vs-stubbed).

## Architecture

The system is organised like a small company: six departments, one per phase, each on the agent pattern its work needs (fan-out research, generator–critic debate, build–test loop, pipes and filters, transactional outbox, monitor loop). A deterministic orchestrator moves one shared record, the Venture Dossier, through the phases; every phase ends at a gate that code evaluates against thresholds locked before results exist. Agents cite evidence by ID from a ledger only tools can write, so URLs cannot be invented, and anything unsourced is labelled `ASSUMPTION`. Agents can only draft into an outbox; executors need a hash-bound founder approval record, and no LLM touches the money path. Full design: [ARCHITECTURE.md](ARCHITECTURE.md); summary and stress tests: [docs/overview.md](docs/overview.md).

## Models and free resources

- **GPT-OSS-20B** (`gpt-oss:20b`, open-weight, Apache-2.0), served locally by **Ollama**: research, building and judging.
- **Gemma 4 31B** (open-weight), called through **Google's Gemini API free tier**: parsing, drafting, copy and the Founder Liaison. The free key goes in `.env` as `GEMINI_API_KEY`; Gemma's prompts are sent to Google.
- **No paid service is required.** Without a key, or with `GEMMA_ROLES_ON_OSS=1`, every role runs on GPT-OSS-20B locally and same-model checks are flagged in the log. The model that checks a piece of work is never the one that wrote it whenever both models are available.
- **Already have Ollama?** Set `OLLAMA_HOST=http://host.docker.internal:11434` and `SKIP_MODEL_PULL=1` in `.env` to use it and skip the download.
- **Search:** self-hosted SearXNG in Docker, free and keyless.
- **Orchestration:** LangGraph (MIT), the only pip dependency, pinned in `requirements.txt`.

## Cost per run

**About $0.00 per run** (GPT-OSS-20B local, Gemma 4 31B on the Gemini API free tier). Token counts will be logged per call in `decision_log.json`.

## Demo video

Not recorded yet.

## What our system will NOT do

- Promise income or success.
- Create fake reviews, testimonials, sign-ups or countdown timers.
- Pose as the founder, a customer or another brand.
- Publish, send, spend or sign anything without explicit founder approval.
- Hold, move or refund money by itself, create accounts, or store passwords or card numbers.
- Build physical goods, financial, investment or crypto products, or health or legal advice products.
- Hide core features behind a paywall.

## How the project stays alive after the hackathon

TBD.
