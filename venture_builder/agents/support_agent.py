"""Support Agent (GPT-OSS-20B): answers support questions from the user docs only (retrieval), citing
doc passage IDs. Replies are drafts for the outbox; refund requests always go to the founder."""

from ..schemas import SupportReply


def run(ctx, ticket: str, passages: list[dict]) -> SupportReply:
    known = {p["id"] for p in passages}

    def check(r: SupportReply) -> None:
        bad = [i for i in r.cited_doc_ids if i not in known]
        if bad:
            raise ValueError(f"cite only the given passages; unknown: {bad}")

    return ctx.router.call("support_agent", {"ticket": ticket, "doc_passages": passages}, SupportReply,
                           action="answer_ticket", validate=check)
