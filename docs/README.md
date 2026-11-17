# Documentation

The root [`ARCHITECTURE.md`](../ARCHITECTURE.md) is the full design. This folder holds the views that sit beside it.

| File | Contents |
|---|---|
| [`overview.md`](overview.md) | One-page summary: pipeline, spine, constraints and stress tests |
| [`phase-workflows.md`](phase-workflows.md) | Mermaid diagrams for model routing, the founder conversation and every phase |
| [`implementation-plan.md`](implementation-plan.md) | How the design became code: file tree, build order, status, and how contradictions between the design documents were resolved |

## Where things live

| Path | Contents |
|---|---|
| `venture_builder/` | All code: spine modules (`orchestrator.py`, `gates.py`, `approval.py`, …), `agents/<role>.py`, `tools/`, `ui/`, `cli.py` |
| `config/` | Scoring weights and gate thresholds (TOML), SearXNG settings |
| `prompts/` | One file per agent prompt, `prompts/<role>.md` |
| `tests/` | All tests (`test_*.py`), including guardrail trick prompts |
| `evals/` | Briefs with expected Stage 1 outcomes and readability checks; `python evals/run_evals.py` |
| `scripts/` | `simulate_founder.py`, the labelled simulated founder used for the committed demo run |
| `briefs/` | The founder briefs used for the demo run (a plausible idea and a weak one) |
| `outputs/` | Outputs of the committed demo run, `phase<N>_step<M>_<name>.<ext>` |
| `decision_log.json` | Decision log of the committed demo run |
| `runs/<run_id>/` | Git-ignored working data of each run (dossier, ledgers, conversation, SQLite stores) |
