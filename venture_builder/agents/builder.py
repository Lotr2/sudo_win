"""Builder (GPT-OSS-20B, medium): fills the product template until the acceptance tests pass.

A template pack must have exactly the spec's number of templates. The model's answer is checked for that (so
the router's one retry says how many are missing); if it still falls short, the builder asks only for the
missing templates and appends them, and extra templates are cut. The acceptance tests stay as strict as before.
"""

from ..router import ModelOutputError
from ..schemas import GuideContent, PackContent, WebToolContent

SCHEMAS = {"guide": GuideContent, "template_pack": PackContent, "web_tool": WebToolContent}


def run(ctx, form: str, spec: dict, failures: list[str], previous: dict | None) -> object:
    payload = {"product_form": form, "spec": spec, "failing_tests": failures, "previous_attempt": previous}
    if form != "template_pack" or not spec.get("item_count"):
        return ctx.router.call("builder", payload, SCHEMAS[form], action="build_product")
    want = int(spec["item_count"])

    def exact_count(content: PackContent) -> None:
        if len(content.items) != want:
            raise ValueError(f"items: return exactly {want} templates (item_count); you returned {len(content.items)}")

    try:
        return ctx.router.call("builder", payload, PackContent, action="build_product", validate=exact_count)
    except ModelOutputError:
        content = ctx.router.call("builder", payload, PackContent, action="build_product")
    for _ in range(want):                              # top up: ask only for the templates that are missing
        missing = want - len(content.items)
        if missing <= 0:
            break
        names = [i.name for i in content.items]
        more = ctx.router.call("builder", {
            "product_form": form, "spec": spec | {"item_count": missing}, "previous_attempt": None,
            "failing_tests": [f"Write {missing} more template(s) for this pack, different from the ones it already "
                              f"has: {', '.join(names) or 'none'}. Return only the new ones."]},
            PackContent, action="build_missing_templates")
        for item in more.items[:missing]:
            if item.name in names:                     # keep it, but give it a name of its own
                item.name = f"{item.name} ({len(names) + 1})"
            names.append(item.name)
            content.items.append(item)
    content.items = content.items[:want]
    return content
