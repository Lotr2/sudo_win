"""`venture` command line.

    venture doctor                      check Ollama, the Gemini API and the models (never falls back silently)
    venture serve [--port 8000]         web UI + API
    venture run BRIEF_FILE [--demo]     start a run and work through it interactively
    venture resume RUN_ID [--once]      continue a run (interactive; --once stops at the next pause)
    venture status RUN_ID               where the run is and what it waits for
    venture answer RUN_ID TEXT          reply to the Liaison's question (or ask "why ...?")
    venture input RUN_ID name=value...  fill a form (MOCKED sign-ups, Phase 6 activity)
    venture approve RUN_ID ITEM_ID      approve an outbox item (interactive terminal confirmation)
    venture reject RUN_ID ITEM_ID --note TEXT
    venture gate RUN_ID go|repeat|pivot|stop [--variant V] [--note TEXT]   (interactive confirmation)
    venture confirm RUN_ID FIELD [VALUE]  confirm or correct an assumption
    venture ask RUN_ID QUESTION         ask anything about the run (answered from the record only)
    venture export RUN_ID [--tag weak --append]   copy a run to outputs/ and decision_log.json
    venture runs                        list runs

Approvals and gate decisions need an interactive terminal: you type part of the item's hash (or the gate
code) to confirm. There is no flag or setting that approves for you.
"""

from __future__ import annotations

import argparse
import json
import select
import sys
from pathlib import Path

from .approval import FounderConfirmation, gate_token
from .config import load_settings
from .router import doctor


def _print(s: str = "") -> None:
    print(s, flush=True)


def require_tty() -> None:
    if not sys.stdin.isatty():
        _print("This action needs an interactive terminal: run it yourself in a terminal so you can confirm it.")
        raise SystemExit(2)


def _settings(args):
    import os
    env = dict(os.environ)
    if getattr(args, "demo", False):
        env["RUN_MODE"] = "demo"
    return load_settings(env)


def _runtime(settings, run_id: str):
    from .runtime import Runtime
    if not (settings.runs_dir / run_id / "dossier.json").exists():
        _print(f"No run {run_id} in {settings.runs_dir}")
        raise SystemExit(1)
    return Runtime(settings, run_id)


def banner(settings) -> None:
    if settings.single_model:
        _print(f"[single-model mode] every role runs on {settings.model_oss}; same-model checks are flagged.")
    _print("[MOCKED] payments, publishing, emails and the Phase 6 event log.  [STUBBED] legal review.")


def render_pending(rt, p: dict | None) -> None:
    if p is None:
        _print(f"\nRun {rt.run_id} has no pending step (status: {rt.dossier.status}).")
        return
    k = p["kind"]
    _print(f"\n--- Phase {p.get('phase')} · waiting for the founder ({k}) ---")
    if k in {"question", "readback", "notes"}:
        _print(p.get("question") or p.get("summary", ""))
        if k == "readback":
            _print(f"Here is what I understood: {p['summary']}")
            if p.get("open_assumptions"):
                _print("Open assumptions: " + "; ".join(p["open_assumptions"]))
            _print("Reply 'yes' to confirm, or type a correction.")
    elif k == "approval":
        for it in p["items"]:
            item = rt.ctx.outbox.get(it["id"])
            _print(f"[{it['id']}] {it['title']}  status={item['status']}  hash={item['content_hash'][7:19]}")
            _print("    " + json.dumps(item["content"], ensure_ascii=False)[:600])
        _print(f"Approve: venture approve {rt.run_id} <item>   Reject: venture reject {rt.run_id} <item> --note '...'")
    elif k == "gate":
        _print(f"Gate {p['gate']} recommends {p['label'].upper()}: " + " ".join(p["reasons"]))
        memo = rt.ctx.store.outputs / p["memo_file"]
        _print(f"Memo: {memo}")
        if p.get("critical_unconfirmed"):
            _print("Decision-critical assumptions not confirmed: " + ", ".join(p["critical_unconfirmed"])
                   + f"  (venture confirm {rt.run_id} FIELD [VALUE])")
        if p.get("variants"):
            _print("Variants: " + ", ".join(f"{v['id']}={v['name']}" for v in p["variants"]))
        _print(f"Decide: venture gate {rt.run_id} go|repeat|pivot|stop   (confirmation code: {p['code']})")
    elif k == "mocked_input":
        _print(p["question"])
        for f in p["fields"]:
            _print(f"  {f['name']}: {f['label']} ({f['type']})")
        _print(f"Fill: venture input {rt.run_id} " + " ".join(f"{f['name']}=..." for f in p["fields"]))


def _input(prompt: str, timeout: int | None) -> str | None:
    _print(prompt)
    sys.stdout.write("> ")
    sys.stdout.flush()
    if timeout:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            _print(f"\n(no answer in {timeout}s: kept as a labelled assumption)")
            return None
    return sys.stdin.readline().rstrip("\n")


def confirm_item(rt, item_id: str) -> FounderConfirmation | None:
    item = rt.ctx.outbox.get(item_id)
    h = item["content_hash"].removeprefix("sha256:")
    _print(f"\n{item['title']}\n{json.dumps(item['content'], ensure_ascii=False, indent=1)[:3000]}")
    typed = input(f"To approve, type the first 6 characters of the hash {h[:12]} (anything else cancels): ").strip()
    if len(typed) < 6 or not h.startswith(typed.lower()):
        _print("Not approved.")
        return None
    return FounderConfirmation(channel="cli", item_ref=item_id, typed_hash_prefix=typed)


def confirm_gate(p: dict, decision: str) -> FounderConfirmation | None:
    code = gate_token(p["gate"], p["attempt"])[:6]
    typed = input(f"To record '{decision}' for Gate {p['gate']}, type the code {code}: ").strip()
    if typed.lower() != code:
        _print("Not recorded.")
        return None
    return FounderConfirmation(channel="cli", item_ref=f"gate{p['gate']}-{p['attempt']}", typed_hash_prefix=typed)


def interactive(rt) -> None:
    """Work through a run in the terminal: Liaison turns inline; free text or '?question' at any prompt."""
    seen = len(rt.ctx.store.conversation())
    p = rt.pending() if rt.started() else rt.advance()
    while p is not None:
        for turn in rt.ctx.store.conversation()[seen:]:
            if turn["speaker"] == "liaison":
                _print(f"\nLiaison: {turn['text']}")
        seen = len(rt.ctx.store.conversation())
        render_pending(rt, p)
        k = p["kind"]
        try:
            if k in {"question", "readback", "notes"}:
                text = _input("Your reply ('?question' to ask about the run):", p.get("timeout"))
                if text is None:
                    p = rt.timeout()
                    continue
                out = rt.reply(text)
                if "liaison" in out:
                    _print(f"\nLiaison: {out['liaison']}")
                    seen = len(rt.ctx.store.conversation())
            elif k == "approval":
                for it in p["items"]:
                    if rt.ctx.outbox.get(it["id"])["status"] != "pending":
                        continue
                    choice = input(f"[{it['id']}] approve, reject or skip? (a/r/s, or type a message): ").strip()
                    if choice.lower() in {"a", "approve"}:
                        conf = confirm_item(rt, it["id"])
                        if conf:
                            rt.approve(it["id"], conf)
                    elif choice.lower() in {"r", "reject"}:
                        rt.reject(it["id"], input("What should change? ").strip(), "cli")
                    elif choice.lower() not in {"s", "skip", ""}:
                        _print("Liaison: " + rt.reply(choice).get("liaison", ""))
            elif k == "gate":
                choice = input("Decision (go/repeat/pivot/stop), 'confirm FIELD [VALUE]', or a message: ").strip()
                if choice.split(" ")[0] in {"go", "repeat", "pivot", "stop"}:
                    variant = None
                    if p.get("variants") and choice in {"go", "pivot"}:
                        variant = input(f"Variant [{p.get('suggested_variant')}]: ").strip() or p.get("suggested_variant")
                    note = input("Note (required when you override the recommendation): ").strip() \
                        if choice != p["recommended"] else ""
                    conf = confirm_gate(p, choice)
                    if conf:
                        rt.decide_gate(choice, conf, variant_id=variant, note=note)
                elif choice.startswith("confirm "):
                    parts = choice.split(" ", 2)
                    _print(str(rt.confirm_assumption(parts[1], parts[2] if len(parts) > 2 else None)))
                elif choice:
                    _print("Liaison: " + rt.reply(choice).get("liaison", ""))
            elif k == "mocked_input":
                values = {}
                for f in p["fields"]:
                    values[f["name"]] = input(f"{f['label']}: ").strip() if f["type"] != "text" else \
                        "\n".join(iter(lambda: input(f"{f['label']} (empty line ends): "), ""))
                rt.submit_input(values)
        except (PermissionError, ValueError) as exc:
            _print(f"Not done: {exc}")
        except KeyboardInterrupt:
            _print(f"\nPaused. Continue later with: venture resume {rt.run_id}")
            return
        p = rt.pending()
    for turn in rt.ctx.store.conversation()[seen:]:
        if turn["speaker"] == "liaison":
            _print(f"\nLiaison: {turn['text']}")
    _print(f"\nRun {rt.run_id} {rt.dossier.status}. Outputs: {rt.ctx.store.outputs}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="venture", description="Open-source venture builder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    s = sub.add_parser("serve"); s.add_argument("--host", default="0.0.0.0"); s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("run"); r.add_argument("brief"); r.add_argument("--demo", action="store_true")
    r.add_argument("--once", action="store_true")
    r2 = sub.add_parser("resume"); r2.add_argument("run_id"); r2.add_argument("--once", action="store_true")
    sub.add_parser("status").add_argument("run_id")
    a = sub.add_parser("answer"); a.add_argument("run_id"); a.add_argument("text", nargs="+")
    i = sub.add_parser("input"); i.add_argument("run_id"); i.add_argument("pairs", nargs="+")
    ap_ = sub.add_parser("approve"); ap_.add_argument("run_id"); ap_.add_argument("item_id")
    rj = sub.add_parser("reject"); rj.add_argument("run_id"); rj.add_argument("item_id"); rj.add_argument("--note", required=True)
    g = sub.add_parser("gate"); g.add_argument("run_id"); g.add_argument("decision", choices=["go", "repeat", "pivot", "stop"])
    g.add_argument("--variant"); g.add_argument("--note", default="")
    c = sub.add_parser("confirm"); c.add_argument("run_id"); c.add_argument("field"); c.add_argument("value", nargs="?")
    q = sub.add_parser("ask"); q.add_argument("run_id"); q.add_argument("question", nargs="+")
    e = sub.add_parser("export"); e.add_argument("run_id"); e.add_argument("--tag", default=""); e.add_argument("--append", action="store_true")
    sub.add_parser("runs")
    args = ap.parse_args(argv)
    settings = _settings(args)

    if args.cmd == "doctor":
        ok, lines = doctor(settings)
        for ln in lines:
            _print(ln)
        _print("doctor: OK" if ok else "doctor: FAILED")
        return 0 if ok else 1
    if args.cmd == "serve":
        import uvicorn
        from .ui.server import create_app
        banner(settings)
        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
        return 0
    if args.cmd == "runs":
        if settings.runs_dir.exists():
            for d in sorted(p for p in settings.runs_dir.iterdir() if (p / "dossier.json").exists()):
                info = json.loads((d / "dossier.json").read_text())
                _print(f"{d.name}  phase {info['phase']}  {info['status']}  {info['brief'][:60]!r}")
        return 0
    if args.cmd == "run":
        from .runtime import Runtime
        ok, lines = doctor(settings)
        if not ok:
            for ln in lines:
                _print(ln)
            _print("Fix the problems above (venture doctor) before starting a run.")
            return 1
        banner(settings)
        rt = Runtime.new(settings, Path(args.brief).read_text())
        _print(f"Run {rt.run_id} started.")
        if args.once or not sys.stdin.isatty():
            render_pending(rt, rt.advance())
            return 0
        interactive(rt)
        return 0

    rt = _runtime(settings, args.run_id)
    if args.cmd == "resume":
        banner(settings)
        if args.once or not sys.stdin.isatty():
            p = rt.pending() if rt.started() else rt.advance()
            render_pending(rt, p)
            return 0
        interactive(rt)
    elif args.cmd == "status":
        st = rt.status()
        _print(json.dumps({k: v for k, v in st.items() if k != "pending"}, indent=1, default=str))
        render_pending(rt, st["pending"])
    elif args.cmd == "answer":
        out = rt.reply(" ".join(args.text))
        if "liaison" in out:
            _print(f"Liaison: {out['liaison']}")
        render_pending(rt, rt.pending())
    elif args.cmd == "input":
        values = dict(pair.split("=", 1) for pair in args.pairs)
        rt.submit_input(values)
        render_pending(rt, rt.pending())
    elif args.cmd == "approve":
        require_tty()
        conf = confirm_item(rt, args.item_id)
        if conf:
            rt.approve(args.item_id, conf)
        render_pending(rt, rt.pending())
    elif args.cmd == "reject":
        rt.reject(args.item_id, args.note, "cli")
        render_pending(rt, rt.pending())
    elif args.cmd == "gate":
        require_tty()
        p = rt.pending()
        if not p or p["kind"] != "gate":
            _print("No gate decision is waiting.")
            return 1
        conf = confirm_gate(p, args.decision)
        if conf:
            try:
                rt.decide_gate(args.decision, conf, variant_id=args.variant, note=args.note)
            except PermissionError as exc:
                _print(str(exc))
                return 1
        render_pending(rt, rt.pending())
    elif args.cmd == "confirm":
        _print(json.dumps(rt.confirm_assumption(args.field, args.value)))
        render_pending(rt, rt.pending())
    elif args.cmd == "ask":
        _print("Liaison: " + rt.ask(" ".join(args.question)))
    elif args.cmd == "export":
        from .runtime import export_run
        files = export_run(settings, args.run_id, tag=args.tag, clear=not args.append)
        _print(f"Exported {len(files)} files to outputs/ and the log to decision_log.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
