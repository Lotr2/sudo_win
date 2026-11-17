# Progress

Update this file when a task changes state (`[ ]` open, `[~]` in progress, `[x]` done). Times are Tuesday 2026-11-17.

## Status

- **Current block:** 12:30 → 14:30, variants pass the contract test.
- **Shared core:** on `main`. Tests: 47 passing, with the fake LLM and no Ollama. `compare_variants.py --all --fake-llm`: about 2 s.
- **Winner:** not picked yet (16:45).

## Done (shared core block, by 12:30)

- [x] Step catalogue + output naming (`core/conventions.py`): 6 phases, Core steps, `phase<N>_step<M>_<idea>-<name>`, gates `phase<N>_gate_<idea>-<outcome>`
- [x] `BaseVariant` contract (`core/base_variant.py`), runner (`core/runner.py`), CLI (`python -m app.run`)
- [x] `approval.py` (single-use HMAC receipts, `@requires_approval`, decisions file / UI queue / CLI, no auto-approve)
- [x] `guardrails.py` (request screen + output lint + excluded categories); 11 trick prompts in `evals/`
- [x] Outbox-only publish/send (`core/outbox.py`); no network-posting or email code (test enforced)
- [x] `sources.py` (HN Algolia + Wikipedia, read-only GET) + provenance check; `gates.py` thresholds
- [x] Commerce mocks: payments, webhooks (idempotent), delivery (tokens), refunds (founder-approved, revokes), reconcile
- [x] `core/llm.py` (Ollama structured outputs, `think: false` for qwen3) + FakeLLM; `core/log.py` (7 keys validated)
- [x] Streamlit UI shell (`app/ui.py`): variant selector, brief form, live progress, gates, approvals, outputs, outbox, badges
- [x] `docker-compose.yml` (ollama + model-pull + app, NVIDIA GPU reservation): `docker compose config` valid
- [x] `variants/_template/` (6 roles) + `tests/test_variant_contract.py` + `scripts/compare_variants.py`
- [x] MIT `LICENSE`, `.env.example`, `.gitignore`, `requirements.txt`, `Dockerfile`

## Tasks by person

### anan
- [ ] Create `variants/anan/` from the template (tuned baseline); contract green by 14:30
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
- [ ] Create `variants/mostafa/` (bigger workers, wider context); contract green by 14:30
- [ ] Make the UI presentable; rough end-to-end through the UI by ~17:00 (idea1 → STOP, idea2 → phase 6) to measure real runtime and failure points
- [ ] Screen recording (≤ 3 min) for README `## Demo video` after the 19:30 run

## Open risks

- Real searches return hits for almost any query, so idea1 may not STOP with real sources (owner: aly, after the first real run).
- Docker on the GPU machine is not verified yet (owner: aly).
- README, ARCHITECTURE.md and the committed demo run are not started (after 17:15, everyone).
