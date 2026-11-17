"""Pydantic models: the Venture Dossier, evidence, every agent's output and every gate result.

Agent output models use plain field types (strings, numbers, lists of objects) so the same JSON schema
works with Ollama's `format` and with the Gemini API's response schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EvidenceType = Literal["web", "founder_reported", "mocked", "simulated", "assumption"]
Outcome = Literal["go", "repeat", "pivot", "stop"]
ProductForm = Literal["guide", "template_pack", "web_tool"]


class Strict(BaseModel):
    model_config = {"extra": "ignore"}


# ---------- evidence ----------

class EvidenceItem(Strict):
    id: str
    type: EvidenceType
    url: str | None = None
    domain: str | None = None
    snippet: str
    fetched_at: str
    query: str | None = None
    tool: str


class Claim(Strict):
    text: str
    evidence_ids: list[str] = []
    label: Literal["SOURCED", "ASSUMPTION"] = "SOURCED"
    sources: list[str] = []


# ---------- Phase 1 ----------

class DraftProfile(Strict):
    idea: str
    skills: list[str] = []
    budget_usd: float | None = None
    hours_per_week: float | None = None
    market: str | None = None
    values: list[str] = []
    gaps: list[str] = Field(default=[], description="Names of fields that are missing or vague")


class FieldUpdate(Strict):
    field: Literal["idea", "skills", "budget_usd", "hours_per_week", "market", "values", "other"]
    value: str = Field(description="The new value as the founder stated it")


class ProfileUpdates(Strict):
    updates: list[FieldUpdate] = []
    is_question: bool = Field(False, description="True if the founder asked a question instead of answering")


class LiaisonQuestion(Strict):
    question: str
    why_it_matters: str


class ReadBack(Strict):
    summary: str = Field(description="3-5 sentences restating the brief")
    open_assumptions: list[str] = []


class LiaisonMessage(Strict):
    message: str


class AskAnswer(Strict):
    answer: str
    cited_ids: list[str] = Field(default=[], description="IDs of dossier, ledger or log entries the answer uses")


class PolicyVerdict(Strict):
    allowed: bool
    category: Literal[
        "ok", "excluded_product", "fake_social_proof", "fake_urgency", "income_promise",
        "impersonation", "money_movement", "credentials", "harmful",
    ]
    reason: str


class Variant(Strict):
    id: str = Field(description="Short id such as v1, v2")
    name: str
    target_customer: str
    problem: str
    product_form: ProductForm
    skills_needed: list[str] = []
    est_build_hours: float
    budget_needed_usd: float


class Variants(Strict):
    variants: list[Variant]


class SearchPlan(Strict):
    queries: list[str]


class Finding(Strict):
    kind: Literal["pain", "demand", "channel", "alternative", "complaint", "price"]
    claim: str
    evidence_ids: list[str]


class ResearchFindings(Strict):
    findings: list[Finding] = []


class InterviewScript(Strict):
    intro: str
    questions: list[str]
    notes_template: str


class Direction(Strict):
    kind: Literal["customer group", "product idea", "market"] = Field(
        description="customer group = same idea, different buyers; product idea = different product for these "
                    "customers; market = same skills applied in another domain or place")
    suggestion: str = Field(description="One plain sentence: the concrete alternative")
    why: str = Field(description="One plain sentence linking it to the founder's idea, skills or what the research found")


class GateMemo(Strict):
    summary: str
    explanation: str
    what_would_change_it: list[str] = []
    other_directions: list[Direction] = []


class VariantScore(Strict):
    variant_id: str
    pain: float
    founder_fit: float
    reachability: float
    buildability: float
    competition_gap: float
    score: float
    pain_signals: int
    pain_domains: int
    fit_rules_pass: bool
    inputs: dict = {}


class GateResult(Strict):
    gate: int
    outcome: Outcome
    reasons: list[str]
    mocked_evidence: bool = False
    rests_on: list[str] = []
    critical_unconfirmed: list[str] = []
    details: dict = {}

    @property
    def label(self) -> str:
        return f"{self.outcome} (MOCKED evidence)" if self.mocked_evidence else self.outcome


# ---------- Phase 2 ----------

class CanvasBox(Strict):
    text: str
    evidence_ids: list[str] = []
    assumption: bool = True


class LeanCanvas(Strict):
    problem: CanvasBox
    customer_segments: CanvasBox
    unique_value: CanvasBox
    solution: CanvasBox
    channels: CanvasBox
    revenue: CanvasBox
    costs: CanvasBox
    key_metrics: CanvasBox
    unfair_advantage: CanvasBox


class BoxScore(Strict):
    box: str
    score: int = Field(ge=1, le=5)
    comment: str


class Risk(Strict):
    assumption: str
    severity: int = Field(ge=1, le=5)
    fatal: bool = False


class InvestorReview(Strict):
    scores: list[BoxScore]
    risks: list[Risk]
    objections: list[str] = []


class ExperimentProposal(Strict):
    kind: Literal["waitlist", "conversion"]
    description: str
    metric: str
    target: float
    window_days: int
    rationale: str


class LandingCopy(Strict):
    headline: str
    subheadline: str
    benefits: list[str]
    cta_text: str
    body: str


# ---------- Phase 3 ----------

class SpecSection(Strict):
    heading: str
    purpose: str


class SpecInput(Strict):
    id: str = Field(description="lowercase_snake_case element id")
    label: str


class Spec(Strict):
    title: str
    summary: str
    product_form: ProductForm
    features: list[str]
    sections: list[SpecSection] = Field(default=[], description="guide: required sections")
    item_count: int = Field(0, description="template_pack: number of templates")
    item_fields: list[str] = Field(default=[], description="template_pack: headings every template has")
    inputs: list[SpecInput] = Field(default=[], description="web_tool: number inputs")
    min_words_per_section: int = 80


class GuideSection(Strict):
    heading: str
    body: str


class GuideContent(Strict):
    title: str
    sections: list[GuideSection]


class PackItem(Strict):
    name: str
    fields: list[GuideSection]


class PackContent(Strict):
    title: str
    items: list[PackItem]


class WebToolContent(Strict):
    title: str
    description: str
    compute_js: str = Field(description="Body of `function compute(v)`: v maps input ids to numbers; return a string")
    result_label: str


class FaqItem(Strict):
    q: str
    a: str


class UserDocs(Strict):
    user_guide: str
    faq: list[FaqItem]


# ---------- Phase 4 ----------

class Tier(Strict):
    name: str
    price: float
    includes: list[str]


class Pricing(Strict):
    currency: str = "USD"
    price: float
    tiers: list[Tier] = []
    reasoning: str
    evidence_ids: list[str] = []


class PolicyDrafts(Strict):
    terms: str
    privacy: str
    refunds: str
    refund_window_days: int


# ---------- Phase 5 ----------

class StorePage(Strict):
    headline: str
    description: str
    whats_included: list[str]
    price_line: str
    refund_line: str


class ChannelPost(Strict):
    channel: str
    text: str


class LaunchDrafts(Strict):
    store_page: StorePage
    posts: list[ChannelPost]


# ---------- Phase 6 ----------

class SupportReply(Strict):
    answer: str
    cited_doc_ids: list[str] = []
    escalate: bool = False
    is_refund_request: bool = False


class MemoClaim(Strict):
    text: str
    metric_keys: list[str] = []


class PivotMemo(Strict):
    recommendation: Literal["persevere", "pivot", "stop"]
    body: str
    claims: list[MemoClaim]


class ClaimCheck(Strict):
    claim_index: int
    matches_data: bool
    comment: str


class CriticReview(Strict):
    checks: list[ClaimCheck]
    rubric_score: int = Field(ge=1, le=5)
    gaps: list[str] = []
