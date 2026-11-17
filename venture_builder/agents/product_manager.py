"""Product Manager (GPT-OSS-20B, medium): writes the spec as machine-checkable acceptance criteria.

The spec must fill the fields of its own product form (and only those): an empty `item_fields` list for a template
pack, for example, would make the acceptance test that every template has its fields pass trivially."""

from ..schemas import Spec

REQUIRED = {
    "guide": "sections: 4-7 items, each {heading, purpose}",
    "template_pack": "item_count: 4-10 and item_fields: 3-5 headings every template has",
    "web_tool": "inputs: 2-5 number inputs, each {id, label}",
}


def check(spec: Spec) -> None:
    form = spec.product_form
    ok = {"guide": 4 <= len(spec.sections) <= 7,
          "template_pack": 4 <= spec.item_count <= 10 and 3 <= len(spec.item_fields) <= 5,
          "web_tool": 2 <= len(spec.inputs) <= 5}[form]
    if not ok:
        raise ValueError(f"product_form is {form}: fill {REQUIRED[form]}, and leave the other forms' fields empty")


def normalise(spec: Spec) -> Spec:
    """Keep only the fields that belong to the spec's product form."""
    form = spec.product_form
    return spec.model_copy(update={
        "sections": spec.sections if form == "guide" else [],
        "item_count": spec.item_count if form == "template_pack" else 0,
        "item_fields": spec.item_fields if form == "template_pack" else [],
        "inputs": spec.inputs if form == "web_tool" else [],
    })


def run(ctx, payload: dict) -> Spec:
    return normalise(ctx.router.call("product_manager", payload, Spec, action="write_spec", validate=check))
