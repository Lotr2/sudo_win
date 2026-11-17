# Documentation

The root [`ARCHITECTURE.md`](../ARCHITECTURE.md) is the full design. This folder holds the views that sit beside it.

| File | Contents |
|---|---|
| [`overview.md`](overview.md) | One-page summary: pipeline, spine, constraints and stress tests |
| [`phase-workflows.md`](phase-workflows.md) | Mermaid diagrams for model routing, the founder conversation and every phase |

## Where things live

| Path | Contents |
|---|---|
| `venture_builder/` | All code: spine modules (`orchestrator.py`, `gates.py`, `approval.py`, …), `agents/<role>.py`, `tools/`, `ui/`, `cli.py` |
| `config/` | Scoring weights and gate thresholds (TOML), SearXNG settings |
| `prompts/` | One file per agent prompt, `prompts/<role>.md` |
| `tests/` | All tests (`test_*.py`), including guardrail trick prompts |
| `outputs/` | Outputs of the committed demo run, `phase<N>_step<M>_<name>.<ext>` |
| `decision_log.json` | Decision log of the committed demo run |
| `runs/<run_id>/` | Git-ignored working data of each run (dossier, ledgers, conversation, SQLite stores) |
