# Progress

Update this file when a task changes state (`[ ]` open, `[~]` in progress, `[x]` done). Times are Tuesday 2026-11-17.

## Status

- **Current block:** 12:30 → 14:30, variants pass the contract test.
- **Shared core:** on branch `variants-shared-core` (not `main`). Tests: 76 passing in about 10 s with fake endpoints and no model server. `compare_variants.py --all --fake-llm --jobs 2`: about 5 s.
- **Winner:** not picked yet (16:45).

## Done (shared core block, by 12:30)

- [x] Step catalogue + output naming (`core/conventions.py`): 6 phases, Core steps, `phase<N>_step<M>_<idea>-<name>`, gates `phase<N>_gate_<idea>-<outcome>`
- [x] `BaseVariant` contract (`core/base_variant.py`), runner (`core/runner.py`), CLI (`python -m app.run`)
- [x] `approval.py` (single-use HMAC receipts, `@requires_approval`, decisions file / UI queue / CLI, no auto-approve)
- [x] `guardrails.py` (request screen + output lint + excluded categories); 11 trick prompts in `evals/`
- [x] Outbox-only publish/send (`core/outbox.py`); no network-posting or email code (test enforced)
- [x] `sources.py` (HN Algolia + Wikipedia, read-only GET) + provenance check; `gates.py` thresholds
- [x] Commerce mocks: payments, webhooks (idempotent), delivery (tokens), refunds (founder-approved, revokes), reconcile
- [x] `core/log.py` (7 keys validated)
- [x] Streamlit UI shell (`app/ui.py`): variant selector, brief form, live progress, gates, approvals, outputs, outbox, badges
- [x] ~~`docker-compose.yml` with Ollama~~ (superseded by the llama-server compose below)
- [x] `variants/_template/` (6 roles) + `tests/test_variant_contract.py` + `scripts/compare_variants.py`
- [x] MIT `LICENSE`, `.env.example`, `.gitignore`, `requirements.txt`, `Dockerfile`

## Done (model-agnostic, durable, two-GPU rework; decisions D7-D12)

- [x] `config/endpoints.yaml` + `LLM_ENDPOINTS`; `config/models.yaml`; adapters for gpt-oss (low reasoning, harmony final channel), qwen3 (`<think>` stripped), generic
- [x] `core/llm.py`: one OpenAI-compatible client; `core/router.py`: least-in-flight routing, slots, timeouts, failover, health
- [x] Flat schemas; metadata JSON + `---CONTENT---` + text; forgiving parser with validation-error retry (`core/parse.py`); only the final answer is parsed
- [x] `finish_reason=length` fails the step; per-role token budgets with prompt trimming; one call per MVP feature (code saved as files under `runs/<run>/product/`)
- [x] `core/state.py`: every step saved; `--resume`; outward actions keyed and run once (outbox idempotency key, refund once); verifier fails closed
- [x] Waves of independent steps run in parallel; several ideas at once; payment log one section per idea
- [x] UI starts the orchestrator as its own process, can resume runs, checks endpoint health
- [x] `docker-compose.yml` single-PC (llama-server + UI); `deploy/gpu-node`, `deploy/orchestrator` for the three-machine setup (all `docker compose config` valid, not yet run on GPUs)
- [x] Fake endpoints with fault injection; new tests: router, parse, adapters, resume, parallel + faults

## Tasks by person

### anan
- [x] Create `variants/anan/` from the template (tuned baseline for gpt-oss-20b); contract green — on branch `anans-approach`
- [ ] Real run of `anan` on gpt-oss-20b: check word limits, budgets, `trimmed_prompts`, JSON retries and truncations
- [ ] Founder: review and sign off `demo/founder_brief_idea1.md`, `founder_brief_idea2.md`, `founder_decisions.yaml` (drafts)
- [ ] Runner: let the UI pass a pivot brief when the founder picks "pivot" without a decisions file
- [ ] 17:15: promote the winner to the root layout (`orchestrator.py`, `agents/`, `prompts/`, `schemas/`, `config.yaml`); make the runner load the root layout; remove other variants; summarize them in `ARCHITECTURE.md`

### ziad
- [ ] Create `variants/ziad/` (critic-heavy); contract green by 14:30
- [ ] Add trick prompts the rules miss (paraphrases) as `*.verifier.md`; check false positives on honest briefs
- [ ] README `## What our system will NOT do`

### aly
- [ ] **First:** verify `docker compose up` on the 16 GB GPU machine (models pulled, UI on :8501, a real run starts). Record the GPU model for the README
- [ ] Create `variants/aly/` (generalists); contract green by 14:30
- [ ] After the first real run: check that idea1 still gets STOP. Provenance proves a URL was fetched, not that it is relevant; tighten `gates.py` (e.g. count only verifier-supported claims) if needed
- [ ] README `## Models and free resources`, `## Cost per run` (from the demo run's tokens)

### mostafa
- [ ] Create `variants/mostafa/` (wide context, more parallel); contract green by 14:30
- [ ] Make the UI presentable; rough end-to-end through the UI by ~17:00 (idea1 → STOP, idea2 → phase 6) to measure real runtime and failure points
- [ ] Screen recording (≤ 3 min) for README `## Demo video` after the 19:30 run

## Open risks

- Not yet measured on real GPUs: tokens/s, KV headroom with 4 slots x 8192, JSON retry and truncation rates for gpt-oss-20b (owner: aly, first real run).
- llama-server image tag and `-hf` download not yet verified on the GPU PCs' OS (owner: aly).

- Real searches return hits for almost any query, so idea1 may not STOP with real sources (owner: aly, after the first real run).
- Docker on the GPU machine is not verified yet (owner: aly).
- README, ARCHITECTURE.md and the committed demo run are not started (after 17:15, everyone).
