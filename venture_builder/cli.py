"""`venture` command line.

    venture doctor                      check Ollama, the Gemini API and the models (never falls back silently)
    venture serve [--port 8000]         web UI + API
    venture run BRIEF_FILE [--demo]     start a run and work through it interactively
    venture run --idea "TEXT"           same, with the brief typed inline
    venture run                         same, asking for the brief in the terminal
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
import html as html_lib
import json
import os
import re
import select
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path

from . import labels
from .approval import FounderConfirmation, gate_token
from .config import load_settings
from .router import doctor


# ---------------- terminal styling ----------------

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
WIDTH = max(50, min(shutil.get_terminal_size((88, 24)).columns, 88))


def _style(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def bold(t: str) -> str: return _style(t, "1")
def dim(t: str) -> str: return _style(t, "2")
def green(t: str) -> str: return _style(t, "32")
def red(t: str) -> str: return _style(t, "31")
def yellow(t: str) -> str: return _style(t, "33")
def accent(t: str) -> str: return _style(t, "36")


def _print(s: str = "") -> None:
    print(s, flush=True)


def wrap(text: str, indent: str = "  ") -> str:
    paras = str(text or "").split("\n")
    return "\n".join(textwrap.fill(p, WIDTH, initial_indent=indent, subsequent_indent=indent) if p.strip() else ""
                     for p in paras)


def section(title: str) -> None:
    line = "─" * max(3, WIDTH - len(title) - 4)
    _print("\n" + accent(bold(f"── {title} ")) + accent(line))


def bullet(text: str, mark: str = "•") -> None:
    _print(textwrap.fill(str(text), WIDTH, initial_indent=f"  {mark} ", subsequent_indent="    "))


def advisor(text: str) -> None:
    _print("\n" + accent(bold("Advisor")))
    _print(wrap(text))


def tone(label: str, text: str) -> str:
    return green(text) if label in {"go", "persevere"} else red(text) if label == "stop" else yellow(text)


def show_value(v, indent: int = 2, depth: int = 0) -> None:
    """Print an outbox item's content as readable labelled text instead of JSON."""
    pad = " " * indent
    if isinstance(v, dict) and isinstance(v.get("html"), str):
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", v["html"])
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", html_lib.unescape(text)).strip()
        _print(dim(f"{pad}A web page. Its text reads:"))
        _print(wrap(text[:600] + ("…" if len(text) > 600 else ""), pad + "  "))
        _print(dim(f"{pad}(Open it in a browser from your documents to see the full page.)"))
        v = {k: x for k, x in v.items() if k != "html"}
        if not v:
            return
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, (dict, list)) and x:
                _print(f"{pad}{bold(labels.human(k))}:")
                show_value(x, indent + 2, depth + 1)
            else:
                _print(textwrap.fill(f"{labels.human(k)}: {x}", WIDTH, initial_indent=pad, subsequent_indent=pad + "  "))
    elif isinstance(v, list):
        for x in v[:20]:
            if isinstance(x, (dict, list)):
                show_value(x, indent + 2, depth + 1)
                _print()
            else:
                _print(textwrap.fill(str(x), WIDTH, initial_indent=pad + "• ", subsequent_indent=pad + "  "))
    else:
        _print(wrap(str(v), pad))


def require_tty() -> None:
    if not sys.stdin.isatty():
        _print("This step needs you at the keyboard: run the command yourself in a terminal so you can confirm it.")
        raise SystemExit(2)


def _settings(args):
    env = dict(os.environ)
    if getattr(args, "demo", False):
        env["RUN_MODE"] = "demo"
    return load_settings(env)


def _runtime(settings, run_id: str):
    from .runtime import Runtime
    if not (settings.runs_dir / run_id / "dossier.json").exists():
        _print(f"We couldn't find a project called {run_id}. See your projects with: venture runs")
        raise SystemExit(1)
    return Runtime(settings, run_id)


def banner(settings) -> None:
    title, tag = "Venture Builder", "Your AI team, from idea to a product you can sell"
    inner = max(len(title), len(tag)) + 2
    _print(accent("╭" + "─" * inner + "╮"))
    _print(accent("│ ") + bold(title.ljust(inner - 1)) + accent("│"))
    _print(accent("│ ") + tag.ljust(inner - 1) + accent("│"))
    _print(accent("╰" + "─" * inner + "╯"))
    if settings.single_model:
        _print(dim(f"AI model: {settings.model_oss}, running on this computer."))
    else:
        _print(dim(f"AI models: {settings.model_oss} (this computer) and {settings.model_gemma} (Google's free API)."))
    _print(yellow("Practice mode:") + " payments, publishing, emails and Phase 6 activity are MOCKED (simulated);")
    _print("legal review is STUBBED (drafts are not legal advice). Nothing happens without your OK.")


def event_text(row: dict) -> str | None:
    return None if row.get("kind") in {"error", "chat"} or row.get("message", "").startswith("run ") \
        else labels.event_text(row)


doc_label = labels.doc_label


def work(rt, fn, *args, **kwargs):
    """Run a (possibly minutes-long) step in the background and show what the team is doing meanwhile."""
    box: dict = {}

    def target():
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc

    seen = len(rt.ctx.store.progress_events())
    th = threading.Thread(target=target, daemon=True)
    th.start()
    spin, i, last, started = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", 0, None, time.monotonic()
    while th.is_alive():
        th.join(0.25)
        rows = rt.ctx.store.progress_events(seen)
        seen += len(rows)
        for row in rows:
            text = event_text(row)
            if text and text != last:
                if _COLOR:
                    sys.stdout.write("\r\033[K")
                _print(dim("  · ") + text)
                last = text
        if _COLOR:
            i += 1
            secs = int(time.monotonic() - started)
            sys.stdout.write(f"\r\033[K  {accent(spin[i % len(spin)])} {dim(f'Working… {secs // 60}:{secs % 60:02d}')}")
            sys.stdout.flush()
    if _COLOR:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def render_pending(rt, p: dict | None, hints: bool = True) -> None:
    """Show what the project is waiting for. With `hints`, also show the command to answer it."""
    rid = rt.run_id
    if p is None:
        status = rt.dossier.status if not rt.finished() else ("stopped" if rt.dossier.status == "stopped" else "finished")
        if status == "finished":
            section("All done")
            _print(wrap("Your project has been through all six stages."))
        elif status == "stopped":
            section("Project stopped")
            _print(wrap(f"This project stopped at stage {rt.dossier.phase}. Stopping early on weak evidence saves you "
                        "time and money. The gate report explains why."))
        elif rt.interrupted():
            section("Paused")
            _print(wrap("This project was interrupted part-way through a step. Nothing is lost."))
            if hints:
                _print(wrap(f"Continue with: venture resume {rid}"))
        else:
            _print(wrap(f"Nothing is waiting for you right now (status: {labels.STATUS.get(status, status)})."))
        return
    k = p["kind"]
    section(f"Your turn · Stage {p.get('phase')}: {labels.phase_name(p.get('phase') or 1)}")
    if k in {"question", "notes"}:
        _print(wrap(p.get("question", "")))
        if k == "notes":
            _print(dim(wrap("Talk to a few real potential customers, then paste your notes. The team never invents interviews.")))
        if hints:
            _print(dim(wrap(f"Answer with: venture answer {rid} \"your answer\"")))
    elif k == "readback":
        _print(bold("  Here's what we understood:"))
        _print(wrap(p.get("summary", ""), "    "))
        if p.get("open_assumptions"):
            _print(bold("\n  Things we're guessing for now:"))
            for a in p["open_assumptions"]:
                bullet(a)
        if hints:
            _print(dim(wrap(f"Confirm with: venture answer {rid} yes   (or type a correction instead of yes)")))
    elif k == "approval":
        _print(wrap("Please review before anything goes out. Practice mode: approved items are recorded, "
                    "not posted or sent (MOCKED)."))
        for n, it in enumerate(p["items"], 1):
            item = rt.ctx.outbox.get(it["id"])
            state = {"pending": yellow("waiting for you"), "approved": green("approved"),
                     "rejected": red("changes requested")}.get(item["status"], item["status"])
            _print(f"\n  {bold(f'{n}. ' + item['title'])}  ({state})")
            show_value(item["content"], 4)
        if hints:
            _print(dim(wrap(f"\nApprove: venture approve {rid} ITEM_ID    Ask for changes: venture reject {rid} ITEM_ID --note \"...\"")))
            _print(dim(wrap("Item IDs: " + ", ".join(it["id"] for it in p["items"]))))
    elif k == "gate":
        _print("  The team recommends: " + bold(tone(p["recommended"], labels.outcome_name(p["label"], p["gate"]).upper())))
        for r in p["reasons"]:
            bullet(r)
        for v in (p.get("versions") or [])[:4]:
            mark = green("passes") if not v["blockers"] else yellow("held back")
            _print(f"\n  {bold(v['name'])}: {bold(str(v['total']))} out of 100 ({mark})")
            for f in v["factors"]:
                filled = round(10 * f["points"] / f["max"]) if f["max"] else 0
                _print(f"    {accent('█' * filled)}{dim('░' * (10 - filled))} {f['points']:>2}/{f['max']:<2} {f['name']}")
                _print(dim(textwrap.fill(f["why"], WIDTH, initial_indent="           ", subsequent_indent="           ")))
            if v["blockers"]:
                _print(yellow(textwrap.fill("What holds it back: " + "; ".join(v["blockers"]) + ".", WIDTH,
                                            initial_indent="    ", subsequent_indent="    ")))
        if p.get("directions"):
            _print(bold("\n  Other directions worth considering:"))
            for o in p["directions"]:
                bullet(f"{o['kind'].capitalize()}: {o['suggestion']} ({o['why']})")
        _print(dim(f"\n  Full report: {rt.ctx.store.outputs / p['memo_file']}"))
        if p.get("critical_unconfirmed"):
            _print(yellow(wrap("\nBefore you can go ahead, please confirm these guesses: "
                               + ", ".join(labels.human(f) for f in p["critical_unconfirmed"]))))
        if p.get("variants"):
            _print(bold("\n  Versions of the idea:"))
            for n, v in enumerate(p["variants"], 1):
                sug = green("  (suggested)") if v["id"] == p.get("suggested_variant") else ""
                _print(f"    {n}) {v['name']}{sug}")
        _print(bold("\n  What would you like to do?"))
        for n, d in enumerate(GATE_CHOICES, 1):
            name = labels.outcome_name(d, p["gate"])
            rec = green("  ← recommended") if d == p["recommended"] else ""
            _print(f"    {n}) {name.ljust(17)} {dim(labels.OPTION_HELP[d])}{rec}")
        if hints:
            _print(dim(wrap(f"\nDecide with: venture gate {rid} go|repeat|pivot|stop")))
    elif k == "mocked_input":
        _print(wrap(p["question"]))
        _print(dim(wrap("These numbers are labelled MOCKED unless you say they are real.")))
        if hints:
            _print(dim(wrap(f"Fill in with: venture input {rid} " + " ".join(f"{f['name']}=..." for f in p["fields"]))))


GATE_CHOICES = ["go", "repeat", "pivot", "stop"]


def read_brief(args) -> str:
    """The brief from --idea, a file, or typed in the terminal (ends at an empty line or Ctrl-D)."""
    if args.idea and args.brief:
        _print("Please give either a brief file or --idea, not both.")
        raise SystemExit(2)
    if args.idea:
        return args.idea.strip()
    if args.brief:
        path = Path(args.brief)
        if not path.is_file():
            _print(f"We couldn't find the file {args.brief}. Check the name, or run `venture run` to type your idea.")
            raise SystemExit(1)
        return path.read_text().strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    section("Tell us about your idea")
    _print(wrap("Plain words are perfect: what you'd like to sell and to whom. Add your skills, budget, time, "
                "and what matters to you if you like. Your advisor will ask follow-up questions."))
    _print(dim("  Press Enter on an empty line when you're done."))
    lines: list[str] = []
    try:
        while True:
            line = input(accent("  › ") if not lines else "    ")
            if not line.strip():
                if lines:
                    break
                continue
            lines.append(line)
    except EOFError:
        pass
    return "\n".join(lines).strip()


def _input(prompt: str, timeout: int | None) -> str | None:
    if prompt:
        _print(dim(wrap(prompt)))
    sys.stdout.write(accent("  › "))
    sys.stdout.flush()
    if timeout:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            _print(yellow(f"\n  No answer within {timeout} seconds: we'll treat it as a guess for now (labelled ASSUMPTION)."))
            return None
    return sys.stdin.readline().rstrip("\n")


def _ask(prompt: str) -> str:
    return input(f"  {prompt} " + accent("› ")).strip()


def confirm_item(rt, item_id: str) -> FounderConfirmation | None:
    item = rt.ctx.outbox.get(item_id)
    h = item["content_hash"].removeprefix("sha256:")
    _print(f"\n  {bold(item['title'])}")
    show_value(item["content"], 4)
    typed = _ask(f"To approve, type the code {bold(h[:6])} (anything else cancels):")
    if len(typed) < 6 or not h.startswith(typed.lower()):
        _print(yellow("  Not approved. Nothing was changed."))
        return None
    _print(green("  Approved.") + dim(" (Practice mode: recorded, not posted or sent.)"))
    return FounderConfirmation(channel="cli", item_ref=item_id, typed_hash_prefix=typed)


def confirm_gate(p: dict, decision: str) -> FounderConfirmation | None:
    code = gate_token(p["gate"], p["attempt"])[:6]
    name = labels.outcome_name(decision, p["gate"])
    typed = _ask(f"To record \"{name}\", type the code {bold(code)}:")
    if typed.lower() != code:
        _print(yellow("  Not recorded. Nothing was changed."))
        return None
    return FounderConfirmation(channel="cli", item_ref=f"gate{p['gate']}-{p['attempt']}", typed_hash_prefix=typed)


def _gate_choice(text: str) -> str | None:
    t = text.strip().lower()
    if t in {"1", "2", "3", "4"}:
        return GATE_CHOICES[int(t) - 1]
    words = {"go": "go", "go ahead": "go", "persevere": "go", "keep going": "go", "repeat": "repeat",
             "try again": "repeat", "pivot": "pivot", "change direction": "pivot", "stop": "stop", "stop here": "stop"}
    return words.get(t)


def _pick_variant(p: dict) -> str | None:
    variants = p.get("variants") or []
    if not variants:
        return None
    suggested = p.get("suggested_variant")
    while True:
        t = _ask(f"Which version? Type its number (Enter = suggested):")
        if not t:
            return suggested
        if t.isdigit() and 1 <= int(t) <= len(variants):
            return variants[int(t) - 1]["id"]
        if any(v["id"] == t for v in variants):
            return t
        _print(yellow(f"  Please type a number from 1 to {len(variants)}."))


def _show_new_turns(rt, seen: int) -> int:
    turns = rt.ctx.store.conversation()
    for turn in turns[seen:]:
        if turn["speaker"] == "liaison":
            advisor(turn["text"])
    return len(turns)


def interactive(rt) -> None:
    """Work through a project in the terminal. Type a question at any prompt to ask the advisor."""
    seen = len(rt.ctx.store.conversation())
    try:
        if not rt.started():
            _print(dim("\n  Your team is reading your idea…"))
            p = work(rt, rt.advance)
        elif rt.interrupted():
            _print(dim("\n  Picking up where the project left off…"))
            p = work(rt, rt.continue_interrupted)
        else:
            p = rt.pending()
    except KeyboardInterrupt:
        _print(f"\n  Paused. Your progress is saved. Continue later with: venture resume {rt.run_id}")
        return
    except Exception as exc:  # noqa: BLE001
        _problem(rt, exc)
        return
    while p is not None:
        seen = _show_new_turns(rt, seen)
        render_pending(rt, p, hints=False)
        k = p["kind"]
        try:
            if k in {"question", "readback", "notes"}:
                if k == "readback":
                    prompt = "Is that right? Type yes, or type what to correct. (Start with ? to ask a question.)"
                elif k == "notes":
                    prompt = "Paste your notes, then press Enter. Press Enter on an empty line to continue without them."
                elif p.get("optional"):
                    prompt = "Type anything the team should know, or just press Enter to continue."
                else:
                    prompt = "Type your answer and press Enter (an empty line skips it). Start with ? to ask a question."
                text = _input(prompt, p.get("timeout"))
                if text is None:
                    p = work(rt, rt.timeout)
                    continue
                if not text.strip():
                    _print(dim("  Skipped. The team will make a clearly marked guess (ASSUMPTION) for now."))
                    p = work(rt, rt.skip)
                    continue
                out = work(rt, rt.reply, text)
                if "liaison" in out:
                    seen = _show_new_turns(rt, seen)
            elif k == "approval":
                for it in p["items"]:
                    if rt.ctx.outbox.get(it["id"])["status"] != "pending":
                        continue
                    choice = _ask(f"\"{it['title']}\": [a] approve  [c] ask for changes  [s] skip for now:").lower()
                    if choice in {"a", "approve", "y", "yes"}:
                        conf = confirm_item(rt, it["id"])
                        if conf:
                            work(rt, rt.approve, it["id"], conf)
                    elif choice in {"c", "r", "reject", "change", "changes"}:
                        note = _ask("What should change?")
                        if note:
                            work(rt, rt.reject, it["id"], note, "cli")
                            _print(green("  Thanks. The team will take your feedback into account."))
                        else:
                            _print(yellow("  No feedback given, so nothing was changed."))
                    elif choice not in {"s", "skip", ""}:
                        work(rt, rt.reply, choice)
                        seen = _show_new_turns(rt, seen)
            elif k == "gate":
                choice = _ask("Type 1-4, or type a question for your advisor:")
                decision = _gate_choice(choice)
                if decision:
                    variant = _pick_variant(p) if decision in {"go", "pivot"} else None
                    note = ""
                    if decision != p["recommended"]:
                        _print(wrap(f"You're choosing \"{labels.outcome_name(decision, p['gate'])}\" instead of the "
                                    f"recommended \"{labels.outcome_name(p['recommended'], p['gate'])}\". That's your call; "
                                    "it's recorded next to the recommendation."))
                        note = _ask("Briefly, why?")
                        if not note:
                            _print(yellow("  A short reason is needed to go against the recommendation. Nothing was changed."))
                            p = rt.pending()
                            continue
                    conf = confirm_gate(p, decision)
                    if conf:
                        work(rt, rt.decide_gate, decision, conf, variant_id=variant, note=note)
                elif choice.startswith("confirm "):
                    parts = choice.split(" ", 2)
                    work(rt, rt.confirm_assumption, parts[1], parts[2] if len(parts) > 2 else None)
                    _print(green(f"  Confirmed: {labels.human(parts[1])}."))
                elif choice:
                    work(rt, rt.reply, choice)
                    seen = _show_new_turns(rt, seen)
            elif k == "mocked_input":
                values = {}
                for f in p["fields"]:
                    if f["type"] == "text":
                        _print(dim(f"  {f['label']} (empty line ends):"))
                        values[f["name"]] = "\n".join(iter(lambda: input("    "), ""))
                    elif f["type"] == "bool":
                        default = bool(f.get("default", True))
                        a = _ask(f"{f['label']} [{'Y/n' if default else 'y/N'}]").lower()
                        values[f["name"]] = default if not a else a in {"y", "yes"}
                    else:
                        while True:
                            a = _ask(f"{f['label']}:")
                            try:
                                if float(a) >= 0:
                                    break
                            except ValueError:
                                pass
                            _print(yellow("  Please type a number (0 or more)."))
                        values[f["name"]] = a
                work(rt, rt.submit_input, values)
        except (PermissionError, ValueError) as exc:
            _print(yellow(wrap(f"That didn't go through: {exc}")))
        except KeyboardInterrupt:
            _print(f"\n  Paused. Your progress is saved. Continue later with: venture resume {rt.run_id}")
            return
        except Exception as exc:  # noqa: BLE001
            _problem(rt, exc)
            return
        p = rt.pending()
    _show_new_turns(rt, seen)
    render_pending(rt, None, hints=False)
    docs = sorted(rt.ctx.store.outputs.iterdir())
    if docs:
        _print(bold("\n  Your documents") + dim(f"  ({rt.ctx.store.outputs})"))
        for d in docs:
            _print(f"    {doc_label(d.name)}  {dim(d.name)}")


def _problem(rt, exc: Exception) -> None:
    section("Something went wrong")
    _print(wrap("The last step hit a problem. This usually means the AI model was slow or briefly unavailable. "
                "Your progress is saved."))
    _print(wrap(f"Try again with: venture resume {rt.run_id}"))
    _print(dim(wrap(f"Technical details: {type(exc).__name__}: {str(exc)[:300]}")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="venture", description="Venture Builder: your AI team, from idea to a product you can sell.",
        epilog="Start here: `venture run` (you'll be asked about your idea), or `venture serve` for the web app.")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    sub.add_parser("doctor", help="check that the AI models and search are reachable")
    s = sub.add_parser("serve", help="open the web app (http://localhost:8000)")
    s.add_argument("--host", default="0.0.0.0"); s.add_argument("--port", type=int, default=8000)
    r = sub.add_parser("run", help="start a new project: from a brief file, --idea TEXT, or by typing it")
    r.add_argument("brief", nargs="?", help="path to a brief file (Markdown or text)")
    r.add_argument("--idea", help="your idea as text instead of a file")
    r.add_argument("--practice", action="store_true",
                   help="just trying it out: results you enter are practice numbers (MOCKED); default is a real project")
    r.add_argument("--brand-name", help="your brand name, shown on the landing page")
    r.add_argument("--brand-color", help="your brand colour as #rrggbb")
    r.add_argument("--logo", help="your logo: a PNG, JPEG or WebP file under 100 KB")
    r.add_argument("--demo", action="store_true", help="quick demo mode (time limits, fewer sources)")
    r.add_argument("--once", action="store_true", help="stop at the first question instead of continuing")
    r2 = sub.add_parser("resume", help="continue a project where it left off")
    r2.add_argument("run_id"); r2.add_argument("--once", action="store_true")
    sub.add_parser("runs", help="list your projects")
    sub.add_parser("status", help="show where a project is and what it's waiting for").add_argument("run_id")
    a = sub.add_parser("answer", help="answer your advisor's question"); a.add_argument("run_id"); a.add_argument("text", nargs="+")
    i = sub.add_parser("input", help="fill in a form (practice numbers, MOCKED)"); i.add_argument("run_id"); i.add_argument("pairs", nargs="+")
    ap_ = sub.add_parser("approve", help="approve an item before it goes out"); ap_.add_argument("run_id"); ap_.add_argument("item_id")
    rj = sub.add_parser("reject", help="ask for changes to an item"); rj.add_argument("run_id"); rj.add_argument("item_id")
    rj.add_argument("--note", required=True)
    g = sub.add_parser("gate", help="record your decision at the end of a stage")
    g.add_argument("run_id"); g.add_argument("decision", choices=GATE_CHOICES)
    g.add_argument("--variant"); g.add_argument("--note", default="")
    c = sub.add_parser("confirm", help="confirm or correct a guess the team made")
    c.add_argument("run_id"); c.add_argument("field"); c.add_argument("value", nargs="?")
    q = sub.add_parser("ask", help="ask your advisor about a project"); q.add_argument("run_id"); q.add_argument("question", nargs="+")
    e = sub.add_parser("export", help="copy a project's documents to outputs/ (for the repository)")
    e.add_argument("run_id"); e.add_argument("--tag", default=""); e.add_argument("--append", action="store_true")
    args = ap.parse_args(argv)
    settings = _settings(args)

    if args.cmd == "doctor":
        ok, lines = doctor(settings)
        for ln in lines:
            _print(ln)
        _print(green("doctor: OK. Everything is ready.") if ok else red("doctor: FAILED. See the messages above."))
        return 0 if ok else 1
    if args.cmd == "serve":
        import uvicorn
        from .ui.server import create_app
        banner(settings)
        shown = "localhost" if args.host in {"0.0.0.0", "127.0.0.1"} else args.host
        _print(green(f"\nThe web app is running: open http://{shown}:{args.port} in your browser.") + dim("  (Ctrl-C stops it.)"))
        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
        return 0
    if args.cmd == "runs":
        found = []
        if settings.runs_dir.exists():
            found = sorted((p for p in settings.runs_dir.iterdir() if (p / "dossier.json").exists()), reverse=True)
        if not found:
            _print("No projects yet. Start one with: venture run")
            return 0
        section("Your projects")
        for d in found:
            info = json.loads((d / "dossier.json").read_text())
            first = (info["brief"].strip().splitlines() or [""])[0][:70]
            _print(f"  {bold(first or 'Untitled project')}")
            _print(dim(f"    {d.name} · stage {info['phase']} of 6 ({labels.phase_name(info['phase'])}) · "
                       f"{labels.STATUS.get(info['status'], info['status'])}"))
        _print(dim("\n  Continue one with: venture resume PROJECT_ID"))
        return 0
    if args.cmd == "run":
        from .runtime import Runtime
        banner(settings)
        _print(dim("\n  Checking that the AI models are ready…"))
        ok, lines = doctor(settings)
        if not ok:
            section("Not ready yet")
            _print(wrap("The app can't reach everything it needs. Details:"))
            for ln in lines:
                _print("  " + ln)
            _print(wrap("Fix the problems above, then check again with: venture doctor"))
            return 1
        brief = read_brief(args)
        if not brief:
            _print(yellow("  No idea given, so nothing was started."))
            return 1
        brand = {}
        if args.brand_name or args.brand_color or args.logo:
            from .landing import clean_brand
            raw = {"name": args.brand_name or "", "color": args.brand_color or ""}
            if args.logo:
                import base64
                lp = Path(args.logo)
                kind = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}.get(lp.suffix.lower())
                if not kind or not lp.is_file():
                    _print(yellow("  The logo must be an existing .png, .jpg or .webp file."))
                    return 1
                raw["logo"] = f"data:image/{kind};base64," + base64.b64encode(lp.read_bytes()).decode()
            try:
                brand = clean_brand(raw)
            except ValueError as exc:
                _print(yellow(f"  {exc}"))
                return 1
        rt = Runtime.new(settings, brief, data_mode="practice" if args.practice else "real")
        if brand:
            rt.set_brand(brand)
        _print(dim("  Real project: results you enter are recorded as your own (founder-reported) numbers."
                   if not args.practice else
                   "  Practice run: results you enter are labelled MOCKED and never count as real proof."))
        _print(green(f"\n  Project started.") + dim(f" Its ID is {rt.run_id}; continue it any time with: venture resume {rt.run_id}"))
        if args.once or not sys.stdin.isatty():
            render_pending(rt, work(rt, rt.advance))
            return 0
        interactive(rt)
        return 0

    rt = _runtime(settings, args.run_id)
    try:
        return _project_command(args, settings, rt)
    except (PermissionError, ValueError) as exc:
        _print(yellow(wrap(f"That didn't go through: {exc}")))
        return 1


def _project_command(args, settings, rt) -> int:
    if args.cmd == "resume":
        banner(settings)
        if args.once or not sys.stdin.isatty():
            if not rt.started():
                p = work(rt, rt.advance)
            elif rt.interrupted():
                p = work(rt, rt.continue_interrupted)
            else:
                p = rt.pending()
            render_pending(rt, p)
            return 0
        interactive(rt)
    elif args.cmd == "status":
        st = rt.status()
        section("Project status")
        _print(f"  Stage {st['phase']} of 6: {labels.phase_name(st['phase'])} · {labels.STATUS.get(st['status'], st['status'])}")
        for n, gt in st["gates"].items():
            _print(f"  Stage {n} decision: " + tone(gt["outcome"], labels.outcome_name(gt["label"], int(n))))
        if st["outputs"]:
            _print(f"  Documents: {len(st['outputs'])} in {rt.ctx.store.outputs}")
        render_pending(rt, st["pending"])
    elif args.cmd == "answer":
        out = work(rt, rt.reply, " ".join(args.text))
        if "liaison" in out:
            advisor(out["liaison"])
        render_pending(rt, rt.pending())
    elif args.cmd == "input":
        values = dict(pair.split("=", 1) for pair in args.pairs)
        work(rt, rt.submit_input, values)
        render_pending(rt, rt.pending())
    elif args.cmd == "approve":
        require_tty()
        conf = confirm_item(rt, args.item_id)
        if conf:
            work(rt, rt.approve, args.item_id, conf)
        render_pending(rt, rt.pending())
    elif args.cmd == "reject":
        work(rt, rt.reject, args.item_id, args.note, "cli")
        _print(green("  Thanks. The team will take your feedback into account."))
        render_pending(rt, rt.pending())
    elif args.cmd == "gate":
        require_tty()
        p = rt.pending()
        if not p or p["kind"] != "gate":
            _print("There is no decision waiting right now.")
            return 1
        conf = confirm_gate(p, args.decision)
        if conf:
            work(rt, rt.decide_gate, args.decision, conf, variant_id=args.variant, note=args.note)
        render_pending(rt, rt.pending())
    elif args.cmd == "confirm":
        out = work(rt, rt.confirm_assumption, args.field, args.value)
        what = "Corrected" if "corrected" in out else "Confirmed"
        _print(green(f"  {what}: {labels.human(args.field)}.") + (dim(" The affected steps are being redone.") if out.get("rerun") else ""))
        render_pending(rt, rt.pending())
    elif args.cmd == "ask":
        advisor(work(rt, rt.ask, " ".join(args.question)))
    elif args.cmd == "export":
        from .runtime import export_run
        files = export_run(settings, args.run_id, tag=args.tag, clear=not args.append)
        _print(f"Exported {len(files)} files to outputs/ and the log to decision_log.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
