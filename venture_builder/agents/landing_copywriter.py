"""Landing Copywriter (Gemma 4 31B): writes the waitlist page copy (no testimonials, counts or timers)."""

from ..schemas import LandingCopy


def run(ctx, canvas: dict, variant: dict, feedback: str = "") -> LandingCopy:
    return ctx.router.call("landing_copywriter", {"canvas": canvas, "variant": variant, "feedback": feedback},
                           LandingCopy, action="write_landing_page")
