"""Landing Copywriter (Gemma 4 31B): writes the waitlist page copy (no testimonials, counts or timers)."""

from ..schemas import LandingCopy


def run(ctx, canvas: dict, variant: dict, feedback: str = "", brand: dict | None = None) -> LandingCopy:
    b = brand or {}
    payload = {"canvas": canvas, "variant": variant, "feedback": feedback,
               "brand": {"name": b.get("name"), "color_given": bool(b.get("color")), "has_logo": bool(b.get("logo"))}}
    return ctx.router.call("landing_copywriter", payload, LandingCopy, action="write_landing_page")
