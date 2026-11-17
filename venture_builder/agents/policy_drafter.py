"""Policy Drafter (Gemma 4 31B): drafts terms, privacy and refund policies, each marked
"DRAFT: not legal advice"."""

from ..schemas import PolicyDrafts


def run(ctx, payload: dict) -> PolicyDrafts:
    return ctx.router.call("policy_drafter", payload, PolicyDrafts, action="draft_policies")
