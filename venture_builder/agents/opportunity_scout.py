"""Opportunity Scout (GPT-OSS-20B, medium): writes 3-4 variants of the idea that fit the profile."""

from ..schemas import Variants


def run(ctx, profile: dict, lessons: list[str], templates: list[str]) -> Variants:
    def check(v: Variants) -> None:
        if not 2 <= len(v.variants) <= 4:
            raise ValueError("return 3 or 4 variants")
        ids = [x.id for x in v.variants]
        if len(set(ids)) != len(ids):
            raise ValueError("variant ids must be unique")

    return ctx.router.call("opportunity_scout", {"profile": profile, "lessons_from_earlier_attempts": lessons,
                                                  "supported_product_forms": templates},
                           Variants, action="find_opportunities", validate=check)
