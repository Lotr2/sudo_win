"""Model Router: sends each role to its model and reasoning effort, enforces JSON-schema output, and logs
every call. Implements ARCHITECTURE.md → Model routing:

1. The checker is never the maker. In single-model mode affected entries carry `same_model_check: true`.
2. Reasoning effort per role for GPT-OSS-20B (low / medium / high).
3. Structured output: schema → validate with pydantic → one retry → fail loudly (logged).
4. Gemma calls go through the free-tier rate limiter (in the Gemini client).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings
from .decision_log import log_action
from .llm_clients import ChatClient, GeminiClient, LlamaCppClient, ModelUnavailable, OllamaClient
from .rate_limit import RateLimiter


def oss_client(settings: Settings, timeout: float) -> ChatClient:
    """The GPT-OSS-20B client for the configured back end (OSS_BACKEND=ollama|llamacpp)."""
    if settings.oss_backend == "llamacpp":
        return LlamaCppClient(settings.llamacpp_url, timeout)
    return OllamaClient(settings.ollama_host, timeout)

T = TypeVar("T", bound=BaseModel)

OSS, GEMMA = "oss", "gemma"


@dataclass(frozen=True)
class Route:
    family: str
    effort: str | None = None  # GPT-OSS-20B reasoning effort
    num_ctx: int = 8192
    checks: str | None = None  # role whose work this role judges


ROUTES: dict[str, Route] = {
    # GPT-OSS-20B: research, building and judging
    "opportunity_scout": Route(OSS, "medium"),
    "market_researcher": Route(OSS, "low", 12288),
    "competitor_analyst": Route(OSS, "low", 12288),
    "skeptical_investor": Route(OSS, "high", 12288, checks="canvas_builder"),
    "experiment_designer": Route(OSS, "high"),
    "product_manager": Route(OSS, "medium"),
    "builder": Route(OSS, "medium", 16384),
    "pricing_analyst": Route(OSS, "medium"),
    "support_agent": Route(OSS, "low"),
    "critic": Route(OSS, "high", checks="pivot_memo_writer"),
    # Gemma 4 31B: parsing, drafting, copy and the conversation
    "founder_liaison": Route(GEMMA),
    "brief_parser": Route(GEMMA),
    "mom_test_writer": Route(GEMMA),
    "gate_reporter": Route(GEMMA),
    "canvas_builder": Route(GEMMA),
    "landing_copywriter": Route(GEMMA),
    "docs_writer": Route(GEMMA),
    "policy_drafter": Route(GEMMA),
    "launch_writers": Route(GEMMA),
    "pivot_memo_writer": Route(GEMMA),
    # Checkers for the agentic skills: they judge work written by another role.
    "fact_checker": Route(OSS, "low", 12288, checks="gate_reporter"),
    "product_reviewer": Route(GEMMA, checks="builder"),
    # The Policy Guard classifier runs on whichever model did NOT write the text it checks.
    "policy_classifier": Route(OSS, "low"),
}


class ModelOutputError(RuntimeError):
    """The model returned output that failed validation twice."""


def extract_json(text: str) -> str:
    """The first complete JSON object in a reply, ignoring code fences, thinking and trailing text."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence and fence.group(1).strip().startswith(("{", "[")):
        text = fence.group(1).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        return text
    start = min(starts)
    try:
        _, end = json.JSONDecoder().raw_decode(text[start:])
        return text[start:start + end]
    except json.JSONDecodeError:
        return text[start:]


class Router:
    def __init__(self, settings: Settings, oss: ChatClient, gemma: ChatClient | None,
                 prompts_dir: Path | None = None):
        self.settings = settings
        self.clients = {OSS: oss, GEMMA: gemma}
        self.prompts_dir = prompts_dir or settings.prompts_dir
        self.tokens_by_model: dict[str, int] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> "Router":
        oss = oss_client(settings, settings.ollama_timeout)
        gemma = None
        if not settings.single_model:
            limiter = RateLimiter(settings.gemma_rpm, settings.gemma_tpm, settings.gemma_rpd,
                                  state_path=settings.runs_dir / ".gemini_usage.json")
            gemma = GeminiClient(settings.gemini_api_key, limiter, settings.gemini_timeout)
        return cls(settings, oss, gemma)

    # ---- routing ----
    def model_name(self, family: str) -> str:
        return self.settings.model_oss if family == OSS else self.settings.model_gemma

    def family_for(self, role: str, author_family: str | None = None) -> str:
        route = ROUTES[role]
        family = route.family
        if role == "policy_classifier" and author_family:
            family = GEMMA if author_family == OSS else OSS
        if self.settings.single_model:
            family = OSS
        return family

    def author_family(self, role: str) -> str:
        """Model family that writes for `role` (used to pick the classifier's model)."""
        return self.family_for(role)

    def system_prompt(self, role: str) -> str:
        shared = self.prompts_dir / "_shared.md"
        own = self.prompts_dir / f"{role}.md"
        parts = [p.read_text() for p in (shared, own) if p.exists()]
        return "\n\n".join(parts) or f"You are the {role}. Reply with JSON only."

    # ---- calls ----
    def call(self, role: str, user: str | dict, schema: type[T], *, action: str,
             sources: list[str] | None = None, author_family: str | None = None,
             validate: Callable[[T], None] | None = None, extra_system: str = "") -> T:
        route = ROUTES[role]
        family = self.family_for(role, author_family)
        client = self.clients[family]
        model = self.model_name(family)
        effort = route.effort if family == OSS else None
        checked_family = None
        if route.checks:
            checked_family = self.family_for(route.checks)
        elif role == "policy_classifier" and author_family:
            checked_family = author_family
        same_model = checked_family is not None and checked_family == family
        system = self.system_prompt(role) + (f"\n\n{extra_system}" if extra_system else "")
        user_text = user if isinstance(user, str) else json.dumps(user, ensure_ascii=False, default=str)
        schema_json = schema.model_json_schema()

        total_tokens = 0
        meta: dict = {}
        error = ""
        started = time.monotonic()
        for attempt in (1, 2):
            prompt = user_text if attempt == 1 else (
                f"{user_text}\n\nYour previous reply was rejected: {error}\nReply again with valid JSON only.")
            try:
                result = client.chat(role=role, model=model, system=system, user=prompt, schema=schema_json,
                                     effort=effort, num_ctx=route.num_ctx)
            except ModelUnavailable as exc:
                log_action(role, f"{action}_failed", sources=sources, model=model, cost=0,
                           error=str(exc)[:300], reasoning_effort=effort)
                raise
            total_tokens += result.prompt_tokens + result.completion_tokens
            meta = result.meta
            try:
                parsed = schema.model_validate_json(extract_json(result.text))
                if validate:
                    validate(parsed)
            except (ValidationError, ValueError) as exc:
                error = str(exc)[:600]
                continue
            self.tokens_by_model[model] = self.tokens_by_model.get(model, 0) + total_tokens
            log_action(role, action, sources=sources, model=model, cost=0, reasoning_effort=effort,
                       tokens=total_tokens, attempts=attempt, same_model_check=True if same_model else None,
                       duration_s=round(time.monotonic() - started, 1), **{k: v for k, v in meta.items() if k != "fake"})
            return parsed
        self.tokens_by_model[model] = self.tokens_by_model.get(model, 0) + total_tokens
        log_action(role, f"{action}_failed", sources=sources, model=model, cost=0, reasoning_effort=effort,
                   tokens=total_tokens, attempts=2, error=f"invalid output twice: {error[:300]}")
        raise ModelOutputError(f"{role}: invalid output after one retry: {error[:300]}")


# ---------------- preflight ----------------

def doctor(settings: Settings, ollama: OllamaClient | None = None, gemini: GeminiClient | None = None) -> tuple[bool, list[str]]:
    """Check both model back ends. Never falls back silently: a missing model is an error."""
    lines: list[str] = []
    if settings.oss_backend == "llamacpp" and ollama is None:
        oss_ok = _doctor_llamacpp(settings, lines)
    else:
        oss_ok = _doctor_ollama(settings, lines, ollama)
    gemini_ok = _doctor_gemini(settings, lines, gemini)
    return oss_ok and gemini_ok, lines


def _doctor_llamacpp(settings: Settings, lines: list[str]) -> bool:
    client = LlamaCppClient(settings.llamacpp_url, timeout=15)
    try:
        status = client.health()
        models = client.models()
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  ERROR  cannot reach llama.cpp at {settings.llamacpp_url}: {exc}")
        lines.append("         Start llama-server with --jinja and set LLAMACPP_URL in .env, e.g. http://localhost:8090")
        return False
    lines.append(f"llama.cpp {settings.llamacpp_url}: {status}; serving {', '.join(models) or 'unknown model'} "
                 f"(requests are labelled {settings.model_oss})")
    if status != "ok":
        lines.append("  ERROR  llama-server is not ready (still loading the model?)")
        return False
    need = max(r.num_ctx for r in ROUTES.values())
    ctx = client.context_size()
    if ctx and ctx < need:
        lines.append(f"  WARN  server context is {ctx} tokens; the builder asks for {need}. Start llama-server with -c {need} or more.")
    try:
        probe = client.chat(role="doctor", model=settings.model_oss, system="Reply with JSON only.",
                            user='Return {"ok": true}.', effort="low", num_ctx=512,
                            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
        json.loads(extract_json(probe.text))
        lines.append("  OK  structured JSON output and reasoning effort (chat template) work")
        return True
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  ERROR  test request failed: {str(exc)[:200]}")
        lines.append("         llama-server must be started with --jinja for gpt-oss.")
        return False


def _doctor_ollama(settings: Settings, lines: list[str], ollama: OllamaClient | None) -> bool:
    ok = True
    ollama = ollama or OllamaClient(settings.ollama_host, timeout=15)
    try:
        version = ollama.version()
        tags = ollama.tags()
        lines.append(f"Ollama {settings.ollama_host}: version {version}")
        if settings.model_oss in tags or f"{settings.model_oss}:latest" in tags:
            think = "think parameter" if _ver(version) >= (0, 11) else "system prompt"
            lines.append(f"  OK  {settings.model_oss} found (reasoning effort via {think})")
        else:
            ok = False
            lines.append(f"  ERROR  model '{settings.model_oss}' not found. Available: {', '.join(tags) or 'none'}")
            lines.append("         Set MODEL_OSS in .env to one of these names.")
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append(f"  ERROR  cannot reach Ollama at {settings.ollama_host}: {exc}")
        lines.append("         Set OLLAMA_HOST in .env to the machine running Ollama, e.g. http://192.168.1.50:11434")
        lines.append("         Using llama.cpp instead? Set OSS_BACKEND=llamacpp and LLAMACPP_URL.")
    return ok


def _doctor_gemini(settings: Settings, lines: list[str], gemini: GeminiClient | None) -> bool:
    ok = True
    if settings.single_model:
        why = "GEMMA_ROLES_ON_OSS=1" if settings.gemma_roles_on_oss else "no GEMINI_API_KEY set"
        lines.append(f"Single-model mode ({why}): every role runs on {settings.model_oss}; "
                     "same-model checks are flagged in the decision log.")
    else:
        try:
            if gemini is None:
                gemini = GeminiClient(settings.gemini_api_key,
                                      RateLimiter(settings.gemma_rpm, settings.gemma_tpm, settings.gemma_rpd), 30)
            models = gemini.list_models()
            names = {m["name"].removeprefix("models/"): m for m in models}
            lines.append(f"Gemini API: {len(names)} models visible to the key")
            m = names.get(settings.model_gemma)
            if m and "generateContent" in m.get("supportedGenerationMethods", []):
                lines.append(f"  OK  {settings.model_gemma} accepts generateContent "
                             f"(limits: {settings.gemma_rpm}/min, {settings.gemma_tpm} tokens/min, {settings.gemma_rpd}/day)")
            else:
                ok = False
                gemmas = sorted(n for n in names if "gemma" in n)
                lines.append(f"  ERROR  model '{settings.model_gemma}' not available. Gemma models: {', '.join(gemmas) or 'none'}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            lines.append(f"  ERROR  Gemini API check failed: {str(exc)[:200]}")
    return ok


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)
