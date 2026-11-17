"""Tester, Coder, UI builder and Doc writer agents.

Each one asks the model for one file, scans it in code, and sends the specific problems back for a rewrite.
A file that still fails after `max_attempts` raises GenerationFailed; it is never used.
"""

from __future__ import annotations

from common.guardrails import screen_copy

import sandbox

from .base import as_json, load_prompt
from .product_manager import api_names, criteria


class GenerationFailed(Exception):
    def __init__(self, what: str, issues: list[str]):
        super().__init__(f"{what} kept failing checks: {'; '.join(issues)}")
        self.issues = issues


def _spec_text(spec: dict) -> str:
    return f"## MVP spec\n{as_json({k: spec[k] for k in ('product_name', 'summary', 'data_model', 'api', 'stories', 'out_of_scope')})}\n"


class _Agent:
    prompt = ""
    langs: tuple[str, ...] = ()
    what = ""

    def __init__(self, llm, max_attempts: int = 3):
        self.llm = llm
        self.system = load_prompt(self.prompt)
        self.max_attempts = max_attempts

    def _generate(self, user: str, check) -> tuple[str, list]:
        results, note = [], ""
        issues: list[str] = []
        for _ in range(self.max_attempts):
            code, res = self.llm.chat_code(self.system, user + note, self.langs)
            results.append(res)
            issues = check(code)
            if not issues:
                return code, results
            note = "\n\n## Your last version was rejected by automatic checks\n" + "\n".join(f"- {i}" for i in issues) + \
                   "\nFix all of these and reply with the complete file."
        raise GenerationFailed(self.what, issues)


class Tester(_Agent):
    name, prompt, langs, what = "tester", "tester", ("js", "javascript", "mjs"), "logic.test.js"

    def write_tests(self, spec: dict):
        ac_ids = [a["id"] for a in criteria(spec)]
        return self._generate(_spec_text(spec) + "\nWrite logic.test.js.", lambda c: sandbox.scan_tests(c, ac_ids))


class Coder(_Agent):
    name, prompt, langs, what = "coder", "coder", ("js", "javascript", "mjs"), "logic.js"

    def write_code(self, spec: dict, tests: str, previous: str | None = None, failures: str | None = None):
        user = _spec_text(spec) + f"\n## logic.test.js (fixed, do not change)\n```js\n{tests}```\n"
        if previous is not None:
            user += f"\n## Your current logic.js\n```js\n{previous}```\n"
        if failures:
            user += f"\n## Failing tests\n```\n{failures}\n```\nFix logic.js so every test passes.\n"
        else:
            user += "\nWrite logic.js."
        names = api_names(spec)
        return self._generate(user, lambda c: sandbox.scan_logic(c, names))


class UIBuilder(_Agent):
    name, prompt, langs, what = "ui_builder", "ui_builder", ("html",), "index.html"

    def write_ui(self, spec: dict, logic: str, feedback: str | None = None):
        user = _spec_text(spec) + f"\n## logic.js (tested; call these functions, do not change them)\n```js\n{logic}```\n"
        if feedback:
            user += f"\n## Founder feedback to apply\n{feedback}\n"
        user += "\nWrite index.html."
        names = api_names(spec)

        def check(html):
            issues = sandbox.scan_ui(html, names)
            issues += [f"copy rule: {i}" for i in screen_copy(html)]
            scripts = sandbox.module_scripts(html)
            if scripts and not issues:
                err = sandbox.check_module_syntax(scripts[0])
                if err:
                    issues.append(f"JavaScript syntax error in the module script: {err}")
            return issues

        return self._generate(user, check)


class DocWriter(_Agent):
    name, prompt, langs, what = "doc_writer", "doc_writer", ("markdown", "md"), "user guide"
    SECTIONS = ("## What it does", "## Getting started", "## Your data", "## What it does not do yet")

    def write_docs(self, spec: dict, test_summary: str):
        user = _spec_text(spec) + f"\n## Test results\n{test_summary}\n\nWrite the user guide."

        def check(md):
            issues = [f"missing section: {s}" for s in self.SECTIONS if s.lower() not in md.lower()]
            issues += [f"copy rule: {i}" for i in screen_copy(md)]
            return issues

        return self._generate(user, check)
