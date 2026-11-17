"""Product templates for Phase 3 (template method). Each template renders the Builder's JSON into files and
ships an acceptance-test pack (`acceptance_<form>.py`) that the QA runner executes in the sandbox."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
FORMS = ["guide", "template_pack", "web_tool"]


def pick(form: str) -> str:
    """Template Picker [code]: the spec's product form must be one of the supported templates."""
    if form not in FORMS:
        raise ValueError(f"no template for product form '{form}'")
    return form


def scaffold(form: str, spec: dict, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "spec.json").write_text(json.dumps(spec, indent=1))
    shutil.copy(HERE / f"acceptance_{form}.py", out / "test_acceptance.py")


def render(form: str, content: dict, out: Path) -> list[str]:
    files: list[str] = []
    if form == "guide":
        parts = [f"# {content['title']}", ""]
        for s in content["sections"]:
            parts += [f"## {s['heading']}", "", s["body"].strip(), ""]
        (out / "guide.md").write_text("\n".join(parts))
        files.append("guide.md")
    elif form == "template_pack":
        items_dir = out / "templates"
        items_dir.mkdir(exist_ok=True)
        index = [f"# {content['title']}", ""]
        for n, item in enumerate(content["items"], 1):
            name = f"{n:02d}.md"
            body = [f"# {item['name']}", ""]
            for f in item["fields"]:
                body += [f"## {f['heading']}", "", f["body"].strip(), ""]
            (items_dir / name).write_text("\n".join(body))
            index.append(f"- [{item['name']}](templates/{name})")
            files.append(f"templates/{name}")
        (out / "index.md").write_text("\n".join(index) + "\n")
        files.append("index.md")
    elif form == "web_tool":
        spec = json.loads((out / "spec.json").read_text())
        inputs = "\n".join(
            f'      <label for="{i["id"]}">{html.escape(i["label"])}</label>\n'
            f'      <input id="{i["id"]}" type="number" step="any">' for i in spec.get("inputs", []))
        ids = json.dumps([i["id"] for i in spec.get("inputs", [])])
        page = (HERE / "web_tool.html").read_text()
        page = (page.replace("{{TITLE}}", html.escape(content["title"]))
                    .replace("{{DESCRIPTION}}", html.escape(content["description"]))
                    .replace("{{INPUTS}}", inputs)
                    .replace("{{RESULT_LABEL}}", html.escape(content["result_label"]))
                    .replace("{{INPUT_IDS}}", ids)
                    .replace("{{COMPUTE_BODY}}", content["compute_js"].replace("</script", "<\\/script")))
        (out / "index.html").write_text(page)
        files.append("index.html")
    return files
