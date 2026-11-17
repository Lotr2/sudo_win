"""Mom Test Writer (Gemma 4 31B): interview questions about past behaviour, plus a notes template."""

from ..schemas import InterviewScript


def run(ctx, variant: dict) -> InterviewScript:
    return ctx.router.call("mom_test_writer", {"variant": variant}, InterviewScript, action="write_interview_script")
