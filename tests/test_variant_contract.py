"""Every variant has the same structure and obeys the shared-core contract (runs with the fake LLM, no Ollama).

Run one variant only:  pytest tests/test_variant_contract.py -k <name>
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

from core.base_variant import CONFIG_KEYS
from core.conventions import LLM_STEP_KEYS, LOG_KEYS, is_valid_output_name
from core.runner import VARIANTS_DIR, list_variants, run_variant

ROOT = Path(__file__).resolve().parent.parent
DESIGN_HEADINGS = ["## Idea in one sentence", "## Agent roster", "## Flow", "## Gates and approvals",
                   "## How it differs from baseline", "## Known weaknesses"]
REQUIRED_FILES = ["DESIGN.md", "config.yaml", "orchestrator.py", "scorecard.md"]
STANDARD_ROLES = {"researcher", "analyst", "builder", "marketer", "verifier"}
BANNED_IMPORTS = {"socket", "requests", "httpx", "urllib", "http", "aiohttp", "smtplib", "subprocess", "approval",
                  "payments", "refunds", "webhooks", "delivery", "reconcile", "sources", "core.outbox", "core.log",
                  "core.llm", "core.router", "core.state", "core.fake_llm", "core.parse", "os", "shutil", "pathlib",
                  "threading", "concurrent", "multiprocessing", "asyncio"}
SCALAR = {"string", "number", "integer", "boolean"}
BANNED_CALLS = {"open", "exec", "eval", "__import__", "write_text", "write_bytes", "_mint", "_write", "unlink", "remove"}
VARIANTS = list_variants()


def _roles(vdir: Path) -> set[str]:
    return {p.stem for p in (vdir / "agents").glob("*.py")}


@pytest.mark.parametrize("name", VARIANTS)
def test_structure(name):
    vdir = VARIANTS_DIR / name
    for f in REQUIRED_FILES:
        assert (vdir / f).is_file(), f"missing {f}"
    roles = _roles(vdir)
    assert roles, "no agents/<role>.py"
    assert {p.stem for p in (vdir / "prompts").glob("*.md")} == roles, "prompts/ must match agents/ role names"
    assert {p.stem for p in (vdir / "schemas").glob("*.json")} == roles, "schemas/ must match agents/ role names"
    extra = {p.name for p in vdir.iterdir() if p.name not in {"__pycache__"}} - set(REQUIRED_FILES) - {"agents", "prompts", "schemas"}
    assert not extra, f"non-template files in variant root: {extra}"
    for r in roles:
        schema = json.loads((vdir / "schemas" / f"{r}.json").read_text(encoding="utf-8"))
        assert schema.get("type") == "object", f"schemas/{r}.json must be an object"
        for field, spec in schema.get("properties", {}).items():
            flat = spec.get("type") in SCALAR or (spec.get("type") == "array" and (spec.get("items") or {}).get("type") in SCALAR)
            assert flat, f"schemas/{r}.json field {field!r} must be flat (scalar or list of scalars)"
    cfg = yaml.safe_load((vdir / "config.yaml").read_text(encoding="utf-8"))
    assert all(k in cfg for k in CONFIG_KEYS), f"config.yaml needs {CONFIG_KEYS}"
    assert "verifier" in roles or not cfg["verifier"].get("enabled"), "verifier enabled but no verifier role"
    from core.adapters import load_models
    known = set(load_models())
    used = {cfg["models"]["default"], *(cfg["models"].get("roles") or {}).values()}
    assert used <= known, f"models {used - known} are not in config/models.yaml"


@pytest.mark.parametrize("name", VARIANTS)
def test_design_md(name):
    text = (VARIANTS_DIR / name / "DESIGN.md").read_text(encoding="utf-8")
    found = [l.strip() for l in text.splitlines() if l.startswith("## ")]
    assert found == DESIGN_HEADINGS, f"DESIGN.md headings must be exactly {DESIGN_HEADINGS}, got {found}"
    assert "```mermaid" in text, "Flow needs one Mermaid diagram"
    assert text.count("```mermaid") == 1
    assert len(text.splitlines()) <= 80, "DESIGN.md is max 1 page"
    roster = text.split("## Agent roster", 1)[1].split("## Flow", 1)[0]
    assert re.search(r"\|\s*role\s*\|\s*phases\s*\|\s*reads\s*\|\s*writes\s*\|\s*model\s*\|", roster), "roster table header"
    listed = {m.group(1).strip() for m in re.finditer(r"^\|\s*([a-z_]+)\s*\|", roster, re.MULTILINE)} - {"role"}
    assert _roles(VARIANTS_DIR / name) <= listed, "every role must be listed in the roster"


@pytest.mark.parametrize("name", VARIANTS)
def test_agent_files_are_thin_and_cover_all_llm_steps(name):
    vdir = VARIANTS_DIR / name
    covered = []
    for p in (vdir / "agents").glob("*.py"):
        ns: dict = {}
        for node in ast.parse(p.read_text(encoding="utf-8")).body:   # read literals statically; never execute
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    ns[node.targets[0].id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
        assert ns.get("ROLE") == p.stem, f"agents/{p.name} must set ROLE = {p.stem!r}"
        covered += ns.get("STEPS", [])
    assert sorted(covered) == sorted(LLM_STEP_KEYS), "each LLM step must be covered by exactly one role"


@pytest.mark.parametrize("name", VARIANTS)
def test_no_bypass_in_variant_code(name):
    vdir = VARIANTS_DIR / name
    for p in [vdir / "orchestrator.py", *(vdir / "agents").glob("*.py")]:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad = {a.name for a in node.names} & BANNED_IMPORTS | {a.name.split(".")[0] for a in node.names} & BANNED_IMPORTS
                assert not bad, f"{p.name} imports {bad}"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in BANNED_IMPORTS and node.module.split(".")[0] not in BANNED_IMPORTS - {"core"}, \
                    f"{p.name} imports from {node.module}"
            if isinstance(node, ast.Call):
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                assert fn not in BANNED_CALLS, f"{p.name} calls {fn}()"
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"broker", "llm", "log", "outputs_dir", "outbox_dir"} or not (
                    isinstance(node.value, ast.Attribute) and node.value.attr == "ctx"), f"{p.name} reaches into ctx.{node.attr}"


@pytest.fixture(scope="module")
def fake_runs(tmp_path_factory):
    out = {}
    for name in VARIANTS:
        out[name] = run_variant(name, ROOT / "demo" / "founder_brief_idea1.md", ROOT / "demo" / "founder_decisions.yaml",
                                fake=True, run_dir=tmp_path_factory.mktemp(name.strip("_") or "v"))
    return out


@pytest.mark.parametrize("name", VARIANTS)
def test_fake_run_contract(name, fake_runs):
    s = fake_runs[name]
    res = s["results"]
    assert res[0]["idea"] == "idea1" and res[0]["status"] in ("pivot", "stopped"), res
    assert res[-1]["idea"] == "idea2" and res[-1]["status"] == "completed", res
    outputs = Path(s["outputs_dir"])
    names = sorted(p.name for p in outputs.iterdir())
    bad = [n for n in names if not is_valid_output_name(n)]
    assert not bad, f"non-standard output names {bad}"
    assert "phase2_gate_idea1-stop.md" in names, "idea1 must get a STOP recommendation at the phase 2 gate"
    assert "phase6_gate_idea2-repeat.md" in names or "phase6_gate_idea2-go.md" in names
    assert "payment_test_log.md" in names
    log = json.loads(Path(s["log_path"]).read_text(encoding="utf-8"))
    for e in log:
        assert all(k in e for k in LOG_KEYS), e
    approved = {e["action"].split(":", 1)[1] for e in log if e["action"].startswith("approve:") and e["approved_by"] == "founder"}
    outward = [e for e in log if e["agent"] == "outbox"]
    for e in outward:
        assert e["approved_by"] == "founder" and e["action_id"] in approved, f"outward action without approval: {e}"
    outbox_files = list((Path(s["run_dir"]) / "outbox").glob("*"))
    assert len(outbox_files) == len(outward), "every outbox file must match one approved outward log entry"
    refunds_done = [e for e in log if e["action"] == "execute_refund"]
    assert all(e["approved_by"] == "founder" for e in refunds_done) and "phase5.step4.refund" in approved
