"""Docs Writer (Gemma 4 31B): writes the user guide and FAQ from the spec and the passing build."""

from ..schemas import UserDocs


def run(ctx, spec: dict, product_summary: str) -> UserDocs:
    return ctx.router.call("docs_writer", {"spec": spec, "product": product_summary}, UserDocs,
                           action="write_user_docs")
