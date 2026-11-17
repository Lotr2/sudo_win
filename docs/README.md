# Documentation

Long-form design docs. The root `README.md` and `ARCHITECTURE.md` keep the key facts and link here for detail.

| Path | Contents |
|---|---|
| `docs/phases/phase<N>.md` | One file per phase: agents, tools, method, outputs, gate |

## Where things live

| Path | Contents |
|---|---|
| `workflow.py` | Entry point that chains the phases |
| `phases/phase<N>_<name>/` | One package per phase: `orchestrator.py`, `gates.py`, `agents/<role>.py` |
| `common/` | Shared code: model client, decision log, founder approval, guardrails |
| `prompts/` | One file per agent prompt, e.g. `prompts/researcher.md` |
| `tests/` | Every test, for all phases (`test_*.py`) |
| `outputs/` | Outputs of the committed demo run, `phase<N>_step<M>_<name>.<ext>` |
| `decision_log.json` | Decision log of the committed demo run |
| `runtime/` | Git-ignored working data of the current run |
