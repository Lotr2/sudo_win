"""Gate Reporter (Gemma 4 31B): turns a Gate 1-3 result into a readable memo; it cannot change the outcome."""

from ..schemas import GateMemo


def run(ctx, gate: int, result: dict, context: dict) -> GateMemo:
    def check(memo: GateMemo) -> None:
        # Stage 1 always offers real alternatives, whatever the outcome, so the founder sees options.
        if gate == 1 and context.get("founder") and len(memo.other_directions) < 2:
            raise ValueError("other_directions needs 2-5 alternatives that grow out of the founder's original idea")

    return ctx.router.call("gate_reporter", {"gate": gate, "result": result, "context": context}, GateMemo,
                           action=f"gate{gate}_memo", validate=check)
