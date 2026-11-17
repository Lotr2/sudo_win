"""Founder Liaison (Gemma 4 31B): the system's one voice to the founder. It interviews, reads back, opens
each phase, puts objections to the founder, walks through gates and answers "why?" from the record.
It has no approval or gate tools: it can only produce text."""

from ..schemas import AskAnswer, LiaisonMessage, LiaisonQuestion, ProfileUpdates, ReadBack

NOT_IN_RECORD = ("I don't have that in this project's records yet, so I won't guess. You can ask me about the "
                 "research, the versions of your idea, the scores, or any stage check.")


def intake_question(ctx, profile: dict, field: str, asked: list[str]) -> LiaisonQuestion:
    return ctx.router.call("founder_liaison", {"task": "intake_question", "profile": profile, "field": field,
                                                "already_asked": asked}, LiaisonQuestion, action="intake_question")


def extract_updates(ctx, question: str, answer: str, profile: dict) -> ProfileUpdates:
    return ctx.router.call("founder_liaison", {"task": "extract_updates", "question": question, "answer": answer,
                                                "profile": profile}, ProfileUpdates, action="extract_answer")


def read_back(ctx, profile: dict, open_assumptions: list[str]) -> ReadBack:
    return ctx.router.call("founder_liaison", {"task": "read_back", "profile": profile,
                                                "open_assumptions": open_assumptions}, ReadBack, action="read_back")


def message(ctx, task: str, **payload) -> str:
    out = ctx.router.call("founder_liaison", {"task": task, **payload}, LiaisonMessage, action=task)
    return out.message


def answer_from_record(ctx, question: str, record: dict) -> AskAnswer:
    """Answer only from the dossier, ledgers and log; cited IDs must exist, otherwise NOT_IN_RECORD."""
    ids = set(record)

    def check(ans: AskAnswer) -> None:
        bad = [i for i in ans.cited_ids if i not in ids]
        if bad:
            raise ValueError(f"cited ids not in the record: {bad}")

    try:
        ans = ctx.router.call("founder_liaison", {"task": "ask_anything", "question": question, "record": record},
                              AskAnswer, action="ask_anything", validate=check)
    except Exception:  # noqa: BLE001 - invalid citations twice: never invent an answer
        return AskAnswer(answer=NOT_IN_RECORD, cited_ids=[])
    if not ans.cited_ids:
        return AskAnswer(answer=NOT_IN_RECORD, cited_ids=[])
    return ans
