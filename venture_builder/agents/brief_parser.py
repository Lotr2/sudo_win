"""Brief Parser (Gemma 4 31B): turns the raw brief into a draft profile and lists the gaps."""

from ..schemas import DraftProfile


def run(ctx, brief: str) -> DraftProfile:
    return ctx.router.call("brief_parser", {"brief": brief}, DraftProfile, action="parse_brief")
