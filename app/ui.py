"""Founder UI (Streamlit shell). Start: streamlit run app/ui.py  (docker compose up serves it on :8501)."""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

from core.conventions import GATE_OUTCOMES, MOCKED, PHASES, STEPS, STUBBED  # noqa: E402
from core.runner import list_variants, run_variant  # noqa: E402

UI_RUNS = ROOT / "runs" / "ui"
DEMO_DECISIONS = ROOT / "demo" / "founder_decisions.yaml"
OUTCOME_COLOR = {"go": "#15803d", "repeat": "#b45309", "pivot": "#7c3aed", "stop": "#b91c1c"}

st.set_page_config(page_title="sudo_win venture builder", layout="wide")


def badge(text: str, color: str = "#92400e", bg: str = "#fde68a") -> str:
    return f"<span style='background:{bg};color:{color};padding:2px 8px;border-radius:10px;font-size:0.8em;font-weight:600'>{text}</span>"


MOCK_BADGE, STUB_BADGE = badge(MOCKED), badge(STUBBED, "#1e3a8a", "#bfdbfe")


def _bg_run(variant, brief_path, decisions, fake, run_dir):
    try:
        run_variant(variant, brief_path, decisions, fake=fake, run_dir=run_dir, queue_dir=run_dir / "approvals")
    except Exception as exc:  # shown in the UI
        (run_dir / "error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")


def start_run(variant: str, brief_text: str, use_decisions: bool, fake: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = UI_RUNS / variant / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    brief_path = run_dir / "brief.md"
    brief_path.write_text(brief_text, encoding="utf-8")
    threading.Thread(target=_bg_run, args=(variant, brief_path, DEMO_DECISIONS if use_decisions else None, fake, run_dir),
                     daemon=True).start()
    st.session_state.run_dir = str(run_dir)


def events(run_dir: Path) -> list[dict]:
    f = run_dir / "events.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []


# ------------------------------------------------------------------ sidebar
st.sidebar.title("sudo_win")
st.sidebar.caption("Open-source agent team: idea → validated, sellable digital product. The founder decides.")
variants = list_variants()
variant = st.sidebar.selectbox("Architecture variant", variants, index=variants.index("_template") if "_template" in variants else 0)
fake = st.sidebar.checkbox("Fake LLM (structure check only, no model)", value=False)
past = sorted((UI_RUNS / variant).glob("*"), reverse=True) if (UI_RUNS / variant).exists() else []
if past:
    pick = st.sidebar.selectbox("Open an earlier run", ["(current)"] + [p.name for p in past])
    if pick != "(current)":
        st.session_state.run_dir = str(UI_RUNS / variant / pick)
st.sidebar.markdown(f"{MOCK_BADGE} payments, publishing, sending, delivery<br>{STUB_BADGE} policy templates, outbox senders",
                    unsafe_allow_html=True)

run_dir = Path(st.session_state["run_dir"]) if st.session_state.get("run_dir") else None
tab_brief, tab_progress, tab_approvals, tab_outputs, tab_outbox = st.tabs(
    ["Founder brief", "Progress", "Approvals", "Outputs", "Outbox"])

# ------------------------------------------------------------- brief form
with tab_brief:
    st.subheader("Founder brief")
    st.caption("Requests that break the rules (fake reviews, income promises, impersonation, moving money, excluded products) are refused before any step runs.")
    with st.form("brief"):
        c1, c2 = st.columns(2)
        bid = c1.text_input("Idea id (lowercase, used in file names)", "idea1")
        idea = c1.text_area("Idea", "")
        skills = c2.text_input("Skills", "")
        budget = c2.text_input("Budget", "")
        tme = c2.text_input("Time available", "")
        market = c1.text_input("Target market", "")
        values = c2.text_input("Values", "")
        use_dec = st.checkbox("Use the founder decisions file (demo/founder_decisions.yaml); otherwise decide here", value=False)
        if st.form_submit_button("Start run") and idea.strip():
            text = (f"# Founder brief: {bid}\n\n- id: {bid}\n- idea: {idea}\n- skills: {skills}\n- budget: {budget}\n"
                    f"- time: {tme}\n- target_market: {market}\n- values: {values}\n")
            start_run(variant, text, use_dec, fake)
            st.success("Run started. Watch Progress and Approvals.")


# ----------------------------------------------------------------- progress
@st.fragment(run_every=2)
def progress_view():
    if not run_dir:
        st.info("No run yet.")
        return
    ev = events(run_dir)
    if (run_dir / "error.txt").exists():
        st.error((run_dir / "error.txt").read_text(encoding="utf-8"))
    status = [e for e in ev if e["type"] == "status"]
    if status:
        st.write("Status: " + " → ".join(f"{e['idea']}: **{e['status']}**" for e in status[-4:]))
    ideas = list(dict.fromkeys(e["idea"] for e in ev if e.get("idea")))
    for idea_id in ideas:
        st.markdown(f"#### {idea_id}")
        step_state = {e["step"]: e for e in ev if e["type"] == "step" and e["idea"] == idea_id}
        gate_state = {e["phase"]: e for e in ev if e["type"] == "gate" and e["idea"] == idea_id}
        cols = st.columns(6)
        for i, (ph, (name, core)) in enumerate(PHASES.items()):
            with cols[i]:
                st.markdown(f"**{ph}. {name}**")
                for s in [s for s in STEPS if s.phase == ph]:
                    e = step_state.get(s.key)
                    icon = {"done": "✅", "running": "⏳", "failed": "❌"}.get(e["status"], "·") if e else "·"
                    extra = f" ({e['outward']})" if e and e.get("outward") else ""
                    st.caption(f"{icon} {s.num}. {s.title}{extra}")
                g = gate_state.get(ph)
                if g:
                    out = g.get("final") or g["outcome"]
                    st.markdown(badge(f"gate: {g['outcome'].upper()}" + (f" → founder: {out.upper()}" if g.get("final") else " (awaiting founder)"),
                                      "white", OUTCOME_COLOR.get(out, "#374151")), unsafe_allow_html=True)


with tab_progress:
    progress_view()


# ---------------------------------------------------------------- approvals
@st.fragment(run_every=2)
def approvals_view():
    if not run_dir:
        st.info("No run yet.")
        return
    pend = sorted((run_dir / "approvals" / "pending").glob("*.json")) if (run_dir / "approvals" / "pending").exists() else []
    if not pend:
        st.caption("Nothing waiting for the founder.")
    for f in pend:
        req = json.loads(f.read_text(encoding="utf-8"))
        with st.container(border=True):
            label = MOCK_BADGE if req["kind"] != "gate" else ""
            st.markdown(f"**{req['action_id']}** ({req['idea']}) {label}", unsafe_allow_html=True)
            st.write(req["summary"])
            with st.expander("Content to approve"):
                st.text(req["content"])
            decided = run_dir / "approvals" / "decided" / f"{req['id']}.json"
            if req["kind"] == "gate":
                choice = st.selectbox("Founder decision", ["accept"] + list(GATE_OUTCOMES), key=f"sel_{req['id']}")
                if st.button("Submit decision", key=f"sub_{req['id']}"):
                    decided.write_text(json.dumps({"choice": choice}), encoding="utf-8")
            else:
                c1, c2 = st.columns(2)
                if c1.button("Approve", key=f"ap_{req['id']}", type="primary"):
                    decided.write_text(json.dumps({"choice": "approve"}), encoding="utf-8")
                if c2.button("Reject", key=f"rj_{req['id']}"):
                    decided.write_text(json.dumps({"choice": "reject"}), encoding="utf-8")


with tab_approvals:
    approvals_view()

# ------------------------------------------------------------------ outputs
with tab_outputs:
    out_dir = run_dir / "outputs" if run_dir else None
    files = sorted(out_dir.iterdir()) if out_dir and out_dir.exists() else []
    if not files:
        st.info("No outputs yet.")
    else:
        name = st.selectbox("Output file", [f.name for f in files])
        f = out_dir / name
        tags = []
        if name.endswith(".html") or name.startswith("phase5") or "payment_test_log" in name or "metrics" in name:
            tags.append(MOCK_BADGE)
        if any(k in name for k in ("terms", "privacy", "refund-policy")):
            tags.append(STUB_BADGE)
        if "_gate_" in name:
            out = name.rsplit("-", 1)[-1].removesuffix(".md")
            tags.append(badge(out.upper(), "white", OUTCOME_COLOR.get(out, "#374151")))
        st.markdown(" ".join(tags), unsafe_allow_html=True)
        text = f.read_text(encoding="utf-8")
        if name.endswith(".html"):
            components.html(text, height=520, scrolling=True)
        elif name.endswith(".json"):
            st.json(json.loads(text))
        elif name.endswith(".csv"):
            st.code(text)
        else:
            st.markdown(text)

# ------------------------------------------------------------------- outbox
with tab_outbox:
    st.markdown(f"{MOCK_BADGE} Approved publish/send actions are written here only. Nothing leaves this machine.",
                unsafe_allow_html=True)
    ob = run_dir / "outbox" if run_dir else None
    for f in (sorted(ob.iterdir()) if ob and ob.exists() else []):
        with st.expander(f.name):
            st.text(f.read_text(encoding="utf-8")[:4000])
