"""Opportunity Scout (GPT-OSS-20B, medium): writes `count` distinct versions of the idea that fit the profile;
code then pre-screens and shortlists them (departments/discover.py)."""

from ..schemas import Variants


def run(ctx, profile: dict, lessons: list[str], templates: list[str], count: int = 4) -> Variants:
    def check(v: Variants) -> None:
        if not 2 <= len(v.variants) <= max(4, count):
            raise ValueError(f"return {count} variants")
        ids = [x.id for x in v.variants]
        if len(set(ids)) != len(ids):
            raise ValueError("variant ids must be unique")

    return ctx.router.call("opportunity_scout", {"profile": profile, "lessons_from_earlier_attempts": lessons,
                                                  "supported_product_forms": templates, "count": count},
                           Variants, action="find_opportunities", validate=check)
