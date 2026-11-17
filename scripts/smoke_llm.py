"""Smoke-test every model server before a full run (see RUNNING.md, step 7).

    python scripts/smoke_llm.py                    # endpoints from LLM_ENDPOINTS or config/endpoints.yaml
    python scripts/smoke_llm.py --variant anan     # model from that variant's config.yaml (default)
    python scripts/smoke_llm.py --fake             # check the script itself, no server needed

Per endpoint: (1) a short call, (2) one metadata-JSON + content answer parsed and validated with the
researcher schema, (3) a truncation check: a tiny max_tokens must come back as finish_reason=length.
Exit code 0 only if every check passes on every endpoint.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.base_variant import BaseVariant  # noqa: E402
from core.fake_llm import fake_router  # noqa: E402
from core.llm import ChatClient, LLMError  # noqa: E402
from core.parse import ParseError, parse_answer  # noqa: E402
from core.router import Router  # noqa: E402

TASK = ("TASK (Find opportunities): List two concrete pains of independent online English tutors. "
        "Every claim cites a SOURCES URL or is marked ASSUMPTION.\n\n"
        "SOURCES (cite only these URLs):\n- https://example.org/smoke/a | Tutor forum | Tutors say lesson prep takes hours every week\n\n")


def check(client: ChatClient, model: str, schema: dict) -> bool:
    ok = True
    t0 = time.time()
    try:
        r = client.complete(model=model, system="Answer briefly.", prompt="Reply with the single word: ready", max_tokens=300)
        dt = time.time() - t0
        good = r.finish_reason == "stop" and bool(r.text.strip())
        print(f"  [1] short call   {'OK  ' if good else 'FAIL'} finish={r.finish_reason} answer={r.text.strip()[:30]!r} "
              f"reasoning_chars={len(r.reasoning)} tokens={r.prompt_tokens}+{r.output_tokens} "
              f"{dt:.1f}s ({r.output_tokens / dt if dt else 0:.0f} out tok/s incl. prompt)")
        ok &= good
    except LLMError as exc:
        print(f"  [1] short call   FAIL {exc}")
        return False

    prompt = TASK + BaseVariant._format_block(None, schema, True)
    t0 = time.time()
    r = client.complete(model=model, system="You are a researcher. Follow the OUTPUT FORMAT exactly.", prompt=prompt, max_tokens=1800)
    dt = time.time() - t0
    try:
        data, content = parse_answer(r.text, schema, True)
        print(f"  [2] JSON step    OK   finish={r.finish_reason} fields={sorted(data)} claims={len(data.get('claims', []))} "
              f"content_chars={len(content)} {dt:.1f}s ({r.output_tokens / dt if dt else 0:.0f} out tok/s)")
        ok &= r.finish_reason == "stop"
    except ParseError as exc:
        print(f"  [2] JSON step    FAIL {exc} finish={r.finish_reason}\n      raw answer: {r.text[:300]!r}")
        ok = False

    r = client.complete(model=model, system="You are a researcher.", prompt=prompt, max_tokens=16)
    good = r.finish_reason == "length"
    print(f"  [3] truncation   {'OK  ' if good else 'FAIL'} finish={r.finish_reason} (max_tokens=16 must give 'length')")
    return ok and good


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", default="anan")
    p.add_argument("--model", help="override the variant's default model name")
    p.add_argument("--fake", action="store_true")
    a = p.parse_args(argv)
    vdir = ROOT / "variants" / a.variant
    cfg = yaml.safe_load((vdir / "config.yaml").read_text(encoding="utf-8"))
    model = a.model or cfg["models"]["default"]
    schema = json.loads((vdir / "schemas" / "researcher.json").read_text(encoding="utf-8"))
    router = fake_router() if a.fake else Router.from_config()
    print(f"model={model}  endpoints={[(e.name, e.base_url) for e in router.endpoints]}")
    all_ok = True
    for ep in router.endpoints:
        print(f"\n== {ep.name}  {ep.base_url}")
        single = Router([ep], transport=router.transport, down_for_s=0, wait_for_slot_s=30)
        all_ok &= check(ChatClient(single, label="fake" if a.fake else ""), model, schema)
    print("\nALL CHECKS PASSED" if all_ok else "\nSOME CHECKS FAILED (see RUNNING.md, step 9)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
