"""Product manager agent: writes a precise, testable MVP spec from the validated business."""

from __future__ import annotations

import re

from .base import as_json, format_brief, load_prompt

IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
MAX_CRITERIA = 10


class ProductManager:
    name = "product_manager"

    def __init__(self, llm):
        self.llm = llm
        self.system = load_prompt("product_manager")

    def write_spec(self, handoff: dict, feedback: str | None = None, previous: dict | None = None):
        user = (f"## Founder brief\n{format_brief(handoff.get('brief') or {})}\n\n"
                f"## Lean Canvas\n{as_json(handoff.get('canvas') or {})}\n\n"
                f"## Demand-test result\n{as_json(handoff.get('evidence') or {})}\n")
        if previous:
            user += f"\n## Your previous spec\n{as_json(previous)}\n"
        if feedback:
            user += f"\n## Founder feedback to apply\n{feedback}\n"
        user += "\nWrite the MVP spec. Reply with the JSON object."
        result = self.llm.chat_json(self.system, user, required_keys=("buildable", "stories"))
        return normalise(result.data), result


def normalise(data: dict) -> dict:
    api = []
    for f in data.get("api") or []:
        if isinstance(f, dict) and IDENT.match(str(f.get("name", ""))):
            api.append({"name": f["name"], "signature": str(f.get("signature") or ""),
                        "description": str(f.get("description") or "")})
    stories = []
    for s in data.get("stories") or []:
        if not isinstance(s, dict):
            continue
        acs = [{"id": str(a.get("id") or "").strip().upper(), "function": str(a.get("function") or "").strip(),
                "text": str(a.get("text") or "").strip()}
               for a in s.get("acceptance_criteria") or [] if isinstance(a, dict)]
        stories.append({"id": str(s.get("id") or "").strip(), "story": str(s.get("story") or "").strip(),
                        "acceptance_criteria": acs})
    return {
        "buildable": data.get("buildable") is not False,
        "not_buildable_reason": data.get("not_buildable_reason") or None,
        "product_name": str(data.get("product_name") or "MVP").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "data_model": str(data.get("data_model") or "").strip(),
        "api": api,
        "stories": stories,
        "out_of_scope": [str(x) for x in data.get("out_of_scope") or []],
    }


def criteria(spec: dict) -> list[dict]:
    return [ac for s in spec["stories"] for ac in s["acceptance_criteria"]]


def api_names(spec: dict) -> list[str]:
    return [f["name"] for f in spec["api"]]


def validate_spec(spec: dict) -> list[str]:
    """Problems that make a spec unbuildable or untestable. Empty list = valid."""
    if not spec["buildable"]:
        return []
    problems = []
    acs = criteria(spec)
    names = api_names(spec)
    if not 1 <= len(spec["stories"]) <= 4:
        problems.append("needs 1 to 4 user stories")
    if not acs:
        problems.append("needs at least one acceptance criterion")
    if len(acs) > MAX_CRITERIA:
        problems.append(f"at most {MAX_CRITERIA} acceptance criteria")
    ids = [a["id"] for a in acs]
    if len(set(ids)) != len(ids) or not all(re.fullmatch(r"AC\d+", i) for i in ids):
        problems.append("criterion ids must be unique and look like AC1, AC2, ...")
    if "createState" not in names:
        problems.append("api must include createState()")
    if len(set(names)) != len(names):
        problems.append("api function names must be unique")
    unknown = sorted({a["function"] for a in acs if a["function"] not in names})
    if unknown:
        problems.append(f"criteria use functions not in the api: {', '.join(unknown)}")
    if any(not a["text"] for a in acs):
        problems.append("every criterion needs text")
    return problems


def drop_stories(spec: dict, story_ids: set[str]) -> dict:
    """Founder cuts scope: remove stories, and any api function no remaining criterion needs (except createState)."""
    kept = [s for s in spec["stories"] if s["id"] not in story_ids]
    used = {a["function"] for s in kept for a in s["acceptance_criteria"]} | {"createState"}
    dropped = [s["story"] for s in spec["stories"] if s["id"] in story_ids]
    return {**spec, "stories": kept, "api": [f for f in spec["api"] if f["name"] in used],
            "out_of_scope": spec["out_of_scope"] + [f"Cut from MVP by founder: {d}" for d in dropped]}
