"""Pivot Memo Writer (Gemma 4 31B): writes the persevere-or-pivot memo, citing metric keys."""

from ..schemas import PivotMemo


def run(ctx, payload: dict) -> PivotMemo:
    keys = set(payload.get("metrics", {}))

    def check(m: PivotMemo) -> None:
        bad = [k for c in m.claims for k in c.metric_keys if k not in keys]
        if bad:
            raise ValueError(f"cite only these metric keys: {sorted(keys)}; unknown {bad}")

    return ctx.router.call("pivot_memo_writer", payload, PivotMemo, action="write_pivot_memo", validate=check)
