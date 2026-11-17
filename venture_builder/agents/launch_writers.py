"""Launch Writers (Gemma 4 31B): the store page and one post per channel, as drafts for the outbox.
Posts are written for the founder to publish under their own name; the system never posts."""

from ..schemas import LaunchDrafts


def run(ctx, payload: dict) -> LaunchDrafts:
    return ctx.router.call("launch_writers", payload, LaunchDrafts, action="write_launch_drafts")
