"""Competitor Analyst (GPT-OSS-20B, low): plans searches about alternatives, their prices and complaints,
then states findings that cite only evidence IDs."""

from ..schemas import ResearchFindings, SearchPlan

KINDS = ["alternative", "complaint", "price"]


def plan(ctx, variant: dict, n: int) -> SearchPlan:
    return ctx.router.call("competitor_analyst", {"task": "plan", "variant": variant, "max_queries": n,
                                                   "angles": KINDS}, SearchPlan, action="plan_competitor_search")


def findings(ctx, variant: dict, evidence: list[dict]) -> ResearchFindings:
    return ctx.router.call("competitor_analyst", {"task": "findings", "variant": variant, "evidence": evidence,
                                                   "allowed_kinds": KINDS}, ResearchFindings,
                           action="competitor_findings", validate=lambda f: ctx.ledger.require_known(
                               [i for x in f.findings for i in x.evidence_ids]),
                           sources=ctx.ledger.urls([e["id"] for e in evidence]))
