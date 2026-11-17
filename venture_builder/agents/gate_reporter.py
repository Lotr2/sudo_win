"""Gate Reporter (Gemma 4 31B): turns a Gate 1-3 result into a readable memo; it cannot change the outcome."""

from ..schemas import GateMemo


def run(ctx, gate: int, result: dict, context: dict) -> GateMemo:
    return ctx.router.call("gate_reporter", {"gate": gate, "result": result, "context": context}, GateMemo,
                           action=f"gate{gate}_memo")
