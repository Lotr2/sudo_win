# Implementation plan

How the design in [ARCHITECTURE.md](../ARCHITECTURE.md) is turned into code: what exists, the target tree, the build order, and every contradiction found between the design documents with its resolution.

## What exists and what is missing

| Exists | Kept? |
|---|---|
| `README.md`, `ARCHITECTURE.md`, `docs/overview.md`, `docs/phase-workflows.md` | Kept; updated to match what is built |
| `LICENSE` (MIT), `.gitignore`, `.dockerignore`, `.env.example` | Kept; extended |
| `docker-compose.yml`, `Dockerfile`, `config/searxng/settings.yml` | Kept; `model-init` renamed, remote override added |
| `config/scoring.toml`, `config/thresholds.toml` | Kept; Gate 6 and rate-limit values added |
| `venture_builder/` stubs (one docstring per module, from the code map) | Kept as the file layout; every module is implemented |
| `tests/test_layout.py` | Kept |

Missing (everything that runs): settings, schemas, the model router with an Ollama and a Gemini client, the rate limiter, the decision log middleware, the ledgers, gates, outbox, policy guard, orchestrator, tools, all 20 agents and their prompts, product templates and acceptance packs, the QA sandbox, sale to cash, the event log and metrics, the web UI, the CLI, and the tests.

## Target file tree

```
pyproject.toml, uv.lock, requirements.txt      dependencies (LangGraph, pydantic, httpx, FastAPI, uvicorn)
docker-compose.yml, compose.remote-ollama.yaml  one-command setup; override for a remote Ollama
config/                                        scoring.toml, thresholds.toml, searxng/settings.yml
prompts/<role>.md                              one prompt per agent (+ policy_classifier.md)
briefs/                                        founder briefs for the demo runs
venture_builder/
  config.py            settings from the environment and .env
  schemas.py           pydantic models: dossier, evidence, every agent's output, gate results
  decision_log.py      logging middleware (one entry per agent, tool or gate action)
  router.py            routing table, router rules, preflight (venture doctor)
  llm_clients.py       Ollama /api/chat client, Gemini generateContent client, fake client hook
  rate_limit.py        Gemini free-tier limiter (requests/min, tokens/min, requests/day)
  ledger.py            Evidence Ledger, citation check, verbatim-number check
  assumptions.py       Assumption Ledger
  clarifier.py         sensitivity and contradiction checks
  dossier.py           Venture Dossier and the per-run store (runs/<run_id>/)
  gates.py             Gates 1-6 as pure functions
  approval.py          Approval Outbox, hash-bound approval records, gate decisions, MOCKED publish adapter
  policy_guard.py      rules + classifier; Compliance Linter
  orchestrator.py      LangGraph state machine (nodes, routing, founder interrupts)
  runtime.py           drives the graph for the CLI and the web UI
  departments/         one module per phase: discover, validate, build, commercial, launch, operate
  agents/<role>.py     one module per agent role
  tools/               search.py (SearXNG), fetch.py (readable text, quoted snippets), research.py
  templates/           guide, template_pack, web_tool, each with an acceptance-test pack
  qa.py                QA runner: acceptance tests in a no-network sandbox
  payments.py, webhooks.py, delivery.py, refunds.py, reconcile.py, events.py, metrics.py
  outputs.py           writes phase outputs and exports the demo run
  cli.py               `venture` command
  ui/                  FastAPI server and a single-page UI
tests/                 smoke tests on a fake model client; `live` marker for real models
outputs/, decision_log.json   the committed demo run
```

## Build order

1. Spine: settings, schemas, decision log, router + clients + rate limiter + preflight, Evidence Ledger, gates, outbox, policy guard, orchestrator.
2. Tools: SearXNG search, page fetch with quoted snippets, `ledger.add`.
3. Founder conversation: Liaison, Assumption Ledger, Clarifier, `conversation.jsonl`.
4. Phases 1-2, Gates 1-2, threshold lock, Gate Reporter.
5. Phases 3-4: templates and acceptance packs, QA sandbox, Compliance Linter, Gates 3-4.
6. Phase 5 and sale to cash, `outputs/payment_test_log.md`.
7. Phase 6: MOCKED event log, metrics, Support Agent, memo and critic, Gate 6.
8. Interfaces: web UI and CLI, demo mode.
9. Tests and repository files.
10. Real demo runs.
11. Final checklist.

## Status

| Milestone | State |
|---|---|
| 1-9: spine, tools, founder conversation, Phases 1-6, interfaces, tests, repository files | Done; 80 smoke tests pass on the fake model client |
| Live checks | Gemma 4 31B on the Gemini API: verified (structured output, rate limiter). GPT-OSS-20B on Ollama: not yet run |
| 10: real demo runs | Not run yet |
| 11: final checklist | Done except for the items that need the demo run (`outputs/`, `decision_log.json`, cost figure) |

## Continuing on another machine

1. `uv sync`, then create `.env` from `.env.example`: `OLLAMA_HOST` (the machine running Ollama), `GEMINI_API_KEY`, and `SEARXNG_URL` (any SearXNG with the JSON format enabled; `config/searxng/settings.yml` has the settings). `.env` is never committed, so it must be recreated on each machine.
2. `uv run venture doctor` must print `doctor: OK`. If a model name differs, set `MODEL_OSS` or `MODEL_GEMMA` to a name doctor lists.
3. `uv run pytest -m live` checks both models.
4. Plausible brief, all six phases: `uv run venture run briefs/plausible_client_kit.md` (interactive) or `uv run venture serve` and the web UI. The founder answers every question and makes every approval and gate decision; Phase 2 sign-ups and Phase 6 activity are entered as MOCKED values.
5. Weak brief until it stops: `uv run venture run briefs/weak_meal_coach.md`; Gate 1 should recommend STOP (health product).
6. Export: `uv run venture export <plausible_run_id>`, then `uv run venture export <weak_run_id> --tag weak --append` (weak-run files become `phase1_gate_weak_decision.md` and so on; both runs' log entries go to `decision_log.json`, each tagged with its run id).
7. Fill the README `## Cost per run` line from the tokens in `decision_log.json`: `jq '[.[].tokens // 0] | add' decision_log.json`.
8. `uv run pytest` again (the layout test checks the exported file names and log keys).

## Contradictions and resolutions

Order of precedence: `CLAUDE.md`, then `ARCHITECTURE.md`, then `docs/phase-workflows.md`, then `docs/overview.md`.

| # | Contradiction | Resolution |
|---|---|---|
| 1 | `CLAUDE.md` allows sandbox payments; the problem statement forbids any payments | Mocks only: no payment SDK, keys or test mode. Every payment event is labelled `MOCKED` |
| 2 | `CLAUDE.md` names the phase "Sell, deliver and get paid"; the design calls it "Launch" | The department keeps the name Launch; README and outputs say "Phase 5 (Launch: sell, deliver and get paid)" |
| 3 | `CLAUDE.md` asks for a gate decision file per gate (`phase<N>_gate_<name>`); `ARCHITECTURE.md` lists no gate file for Phases 5 and 6 | Every gate writes `phase<N>_gate_decision.md`, Phases 5 and 6 included |
| 4 | `ARCHITECTURE.md` runs QA "in a container with no network"; the app already runs inside a container and cannot start nested containers | The QA runner starts an isolated Python subprocess whose audit hook blocks every socket and subprocess call, inside a network namespace (`unshare -rn`) when the host allows it. The test report records which isolation was used |
| 5 | `docs/overview.md` says Gate 6 checks "memo passes rubric?"; the `ARCHITECTURE.md` gate table decides Gate 6 from metrics against targets | Both apply: a memo that fails the critic's rubric gives repeat; otherwise the outcome comes from metrics against targets locked at the start of Phase 6 |
| 6 | `ARCHITECTURE.md` compares Phase 6 metrics with "the key metrics from the Lean Canvas", which are free text | Numeric targets come from `config/thresholds.toml` `[gate6]` and are locked and hashed before the MOCKED events are entered; the canvas key metrics are quoted beside them |
| 7 | `docs/overview.md` stress tests say "the parser fills gaps as labelled ASSUMPTIONs"; `ARCHITECTURE.md` has the Liaison ask first | The Liaison asks; only unanswered gaps become assumptions |
| 8 | Demo mode promises Phases 1-2 in a few minutes "on a laptop"; Gemma's free tier is rate-limited and GPT-OSS runs on another machine | Demo mode keeps the caps (5 sources per variant, question limits, per-node time budget). Timing depends on the remote machine and the rate limit, so no minute figure is promised |
| 9 | The decision log is "one entry per agent action"; `CLAUDE.md` wants the committed log small | Every run keeps its full log in `runs/<run_id>/decision_log.json`; only the demo run's log is exported to the root `decision_log.json` |
| 10 | Gemma 4 31B free-tier limits are not stated in the design | Defaults of 30 requests/min, 15,000 tokens/min and 14,400 requests/day (`ASSUMPTION`: Google's published Gemma free-tier limits; configurable in `.env`). A 429 response's `retryDelay` is always honoured |
| 11 | Pivot at Gate 2 or Gate 6 means "back to an earlier phase"; the target is not named | Gate 1 pivot: the founder picks another variant. Gate 2, 3 and 6 pivot: back to Phase 1 with the profile kept and the lesson stored in the dossier |
| 12 | The refund in the payment test run needs a founder approval; the test suite needs one too | In real runs the founder approves the refund item in the CLI or UI; tests use a test-only fixture. The production code has no auto-approve path |
