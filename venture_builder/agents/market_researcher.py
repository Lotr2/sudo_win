"""Market Researcher (GPT-OSS-20B, low): plans searches about pain and demand for one variant, then states
findings that cite only evidence IDs from the ledger."""

from ..schemas import ResearchFindings, SearchPlan

KINDS = ["pain", "demand", "channel"]


def plan(ctx, variant: dict, n: int) -> SearchPlan:
    return ctx.router.call("market_researcher", {"task": "plan", "variant": variant, "max_queries": n,
                                                  "angles": KINDS}, SearchPlan, action="plan_market_search")


def findings(ctx, variant: dict, evidence: list[dict]) -> ResearchFindings:
    return ctx.router.call("market_researcher", {"task": "findings", "variant": variant, "evidence": evidence,
                                                  "allowed_kinds": KINDS}, ResearchFindings,
                           action="market_findings", validate=lambda f: ctx.ledger.require_known(
                               [i for x in f.findings for i in x.evidence_ids]),
                           sources=ctx.ledger.urls([e["id"] for e in evidence]))
