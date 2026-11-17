"""LangGraph state machine over phases and gates (ARCHITECTURE.md → D1).

Every department step is a node. A step either names the next node or asks the founder something; asking
routes to the single `founder` node, which calls LangGraph's `interrupt()` and, when the CLI or the web UI
resumes the run, returns to the step named in `resume_to`. No LLM decides what runs next: the transitions
are the ones written in the department modules. Checkpoints are stored in SQLite so a run can pause for
days and resume.
"""

from __future__ import annotations

import sqlite3
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .context import END as END_NODE, Ask, Context, Next
from .departments import build, commercial, discover, launch, operate, validate

NODES: dict = {}
for _mod in (discover, validate, build, commercial, launch, operate):
    NODES.update(_mod.NODES)

FIRST_NODE = "p1_parse"
NO_ANSWER = {"no_answer": True}  # LangGraph cannot resume with None

# Live contexts, by run id. Graph state holds only small, serialisable control fields.
CONTEXTS: dict[str, Context] = {}


class State(TypedDict, total=False):
    run_id: str
    next: str
    pending: dict | None
    answer: Any


def _wrap(name: str, fn):
    def node(state: State) -> State:
        ctx = CONTEXTS[state["run_id"]]
        answer = state.get("answer")
        if name.endswith("_gate_decided") and isinstance(answer, dict) and answer.get("rerun"):
            # A founder correction while the gate waited: re-run only the dependent steps.
            return {"pending": None, "next": answer["rerun"], "answer": None}
        with ctx.bound():
            ctx.store.progress(f"step {name}", kind="step", node=name)
            result = fn(ctx, state.get("answer"))
            ctx.dossier.history.append({"node": name})
            ctx.save()
        if isinstance(result, Ask):
            return {"pending": result.as_dict(ctx.dossier.phase), "next": result.resume_to, "answer": None}
        target = result.node if isinstance(result, Next) else str(result)
        return {"pending": None, "next": target, "answer": None}

    node.__name__ = name
    return node


def founder(state: State) -> State:
    """The only place the graph waits: the founder's reply arrives as the resume value."""
    reply = interrupt(state["pending"])
    if reply == NO_ANSWER:
        reply = None  # demo-mode timeout: the question stays a labelled assumption
    return {"answer": reply, "pending": None}


def route(state: State) -> str:
    if state.get("pending"):
        return "founder"
    nxt = state.get("next") or END_NODE
    return END if nxt == END_NODE else nxt


def build_graph(checkpointer=None):
    g = StateGraph(State)
    targets = {name: name for name in NODES} | {"founder": "founder", END: END}
    for name, fn in NODES.items():
        g.add_node(name, _wrap(name, fn))
        g.add_conditional_edges(name, route, targets)
    g.add_node("founder", founder)
    g.add_conditional_edges("founder", route, targets)
    g.add_edge(START, FIRST_NODE)
    return g.compile(checkpointer=checkpointer)


def sqlite_checkpointer(path: str) -> SqliteSaver:
    conn = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(conn)
