# sudo_win: an open-source team of AI agents from idea to sellable product

A founder brings a short brief: the idea, their skills, budget, time, target market and values. A team of agents running on a **local open-weight model (gpt-oss:20b)** takes it through six phases: **Discover → Validate → Build → Commercial setup → Launch → Operate and learn**.

- **Every phase ends in a gate** with thresholds fixed in code before the evidence arrives. The outcomes are go, repeat, pivot or **stop**, and the system recommends **stop** when the evidence is weak.
- **Nothing is published, sent, spent or refunded without an explicit founder approval** in the CLI.
- **Payments are MOCKED**, with no provider and no sandbox.
- **Every market claim** cites a source or is labelled `ASSUMPTION`.
- **Every agent action** is logged to `decision_log.json`.

## Quickstart

```bash
docker compose up
```

The first start downloads gpt-oss:20b (about 13 GB) and needs roughly 16 GB of RAM. When the log shows `Ready`, open the app in a second terminal:

```
docker compose exec app python workflow.py run
```

That runs the pipeline from wherever you are, one step after another. It asks before each step and stops whenever a gate does not pass or you stop it, and you pick up later by running it again. Other commands:

- `python workflow.py` shows where you are and the next command
- `python workflow.py discover` (or any phase name) runs one phase by hand
- `python workflow.py reset` clears the previous run (archives it to `runs/` or deletes it, after asking) so the next `run` starts at Phase 1
- `python workflow.py test` runs every smoke test

Pages served during a run:

- the waitlist page at http://localhost:8000
- the MVP preview at http://localhost:8001
- the store at http://localhost:8002

**Without Docker:** install Python 3.12+, Node.js 25+ and Ollama, run `ollama pull gpt-oss:20b`, then `python workflow.py run`. Settings such as `LLM_BASE_URL` are read from `.env` (copy `.env.example`), so a llama.cpp server works too, for example `LLM_BASE_URL=http://127.0.0.1:8090/v1`. Nothing needs to be pip-installed; the code uses only the Python standard library.

## What works and what is mocked

| Phase | Step | Status |
|---|---|---|
| 1 Discover | Interviewer asks questions until the concept is clear; founder confirms | Working |
| 1 Discover | Evaluator scores the idea (YC framework); go / pivot / stop gate | Working |
| 1 Discover | Web research for market claims | **STUBBED**: no web access; every market claim is labelled `ASSUMPTION` |
| 2 Validate | Lean Canvas, demand-test plan with locked thresholds | Working |
| 2 Validate | Waitlist landing page and sign-up collection (local) | Working |
| 2 Validate | Public hosting of the landing page | **STUBBED**: runs locally; the founder hosts it |
| 2 Validate | Demand gate on real sign-ups | Working |
| 3 Build | Spec, tests written before code, code fixed in a Node sandbox until tests pass | Working |
| 3 Build | Interface, user guide, build gate | Working |
| 3 Build | Interface behaviour in a browser | **STUBBED**: syntax and safety checked; the founder tries it before approving |
| 4 Commercial setup | Pricing with break-even; policies from templates with code checks | Working |
| 4 Commercial setup | Legal review | **STUBBED**: drafts are marked not legal advice |
| 5 Launch | Store page and founder-voiced launch posts (approved one by one) | Working |
| 5 Launch | Payment provider, card payments, fees, payouts | **MOCKED** |
| 5 Launch | Webhooks, idempotent delivery, founder-only refunds that revoke access, reconciliation | Working (against the MOCKED provider) |
| 5 Launch | Delivery and support emails | **MOCKED**: written to an outbox, not sent |
| 5 Launch | Posting launch posts | **STUBBED**: the founder posts them; the system never posts |
| 5 Launch | Subscription renewals | **STUBBED**: only the first payment is simulated |
| 6 Operate | Locked review targets, metrics, persevere / pivot / stop gate, memo | Working |
| 6 Operate | Support reply drafts with founder approval | Working (sending is **MOCKED**) |

## Architecture

Each phase is a folder (`phase-N-*/`) with an `orchestrator.py` that runs its agents, gates and approvals. Each phase hands a JSON file in `outputs/` to the next, and a pivot in Phase 6 writes a new brief for Phase 1. Agents only propose: deterministic code checks every output (copy rules, sandboxed tests, policy checks, hash-locked thresholds) and makes every gate decision. The founder approves every outward-facing step. Shared code lives in `common/` (model client, decision log, guardrails, approval) and agent prompts in `prompts/`. Full design, diagrams, gates and the sale-to-cash flow: [ARCHITECTURE.md](ARCHITECTURE.md).

## Models and free resources

- **Model:** `gpt-oss:20b` (open-weight, Apache-2.0), served locally by **Ollama** in Docker. No API key, no paid service, no account.
- **Any OpenAI-compatible server works:** set `LLM_BASE_URL` and `LLM_MODEL` (see `.env.example`). Other open-weight models such as `qwen3:8b` or `gpt-oss:120b` work the same way.
- **Everything else is free and local:**
  - Python standard library (no pip packages)
  - Node.js for Phase 3's test sandbox
  - SQLite for the store
  - Mermaid diagrams as text

## Cost per run

**About $0.00 per run** (local gpt-oss:20b, an estimated 60k–150k tokens for all six phases, depending on how many questions and fix attempts a run needs). Each model call's token count and cost (always 0) is recorded in `decision_log.json`. Electricity and your own hardware are not included.

## Demo video

Not recorded yet.

## What our system will NOT do

- **Promise income or success.** Copy and replies are checked in code for promises, and fake discounts are blocked.
- **Fake trust or urgency.** No reviews, testimonials, sign-ups, user counts, urgency or countdown timers. Drafts that contain them are rejected before the founder sees them.
- **Pose as anyone else.** It never poses as the founder, a customer or another brand. Launch posts must be in the founder's own voice, and the system never posts.
- **Act without approval.** Nothing is published, sent, spent, signed or refunded without explicit founder approval. Refunds can only be approved by the founder.
- **Handle money or credentials.** It never holds, moves or refunds real money, never creates accounts, and never asks for or stores passwords or card numbers. Payments are MOCKED.
- **Run unchecked code.** Generated code runs in Node's permission sandbox, with no network and no file writes.
- **Build excluded products.** Physical goods, financial, investment or crypto products, and health or legal advice products are stopped at the Phase 1 gate.
- **Give legal advice.** Policy drafts are templates marked "not legal advice".
- **Paywall its own features.** Every feature of this system is open source and free to run.

## How the project stays alive after the hackathon

- **Maintainers:** the team behind this repository, reviewing issues and pull requests on GitHub.
- **Roadmap:**
  1. Web research tools so market claims can cite sources instead of being assumptions.
  2. A browser-based founder dashboard over the same approval gates.
  3. A real payment provider behind the existing `payments.py` interface, always behind founder approval.
  4. More product types in Phase 3 beyond single-page web apps.
  5. An evaluation set of founder briefs to track gate quality across models.
- **Community:** the phases are independent folders with their own tests, and every prompt is a plain file in `prompts/`, so a contribution can be a single prompt, gate rule or phase.
- **Funding:** running it costs $0 on local open-weight models, so it doesn't depend on API credits or a hosted service. Sustaining it needs contributor time, not money.
