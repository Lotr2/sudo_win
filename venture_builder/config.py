"""Settings, read from the environment and an optional untracked `.env` file.

The Gemini API key is never printed: `Settings.__repr__` hides it and `public()` omits it.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    ollama_host: str = "http://localhost:11434"
    model_oss: str = "gpt-oss:20b"
    gemini_api_key: str = field(default="", repr=False)
    model_gemma: str = "gemma-4-31b-it"
    gemma_roles_on_oss: bool = False
    run_mode: str = "full"
    searxng_url: str = "http://localhost:8080"
    ollama_timeout: float = 600.0
    gemini_timeout: float = 180.0
    search_timeout: float = 20.0
    fetch_timeout: float = 15.0
    gemma_rpm: int = 30
    gemma_tpm: int = 15000
    gemma_rpd: int = 14400
    runs_dir: Path = ROOT / "runs"
    config_dir: Path = ROOT / "config"
    prompts_dir: Path = ROOT / "prompts"

    @property
    def single_model(self) -> bool:
        """True when every role runs on GPT-OSS-20B (no key, or explicitly asked for)."""
        return self.gemma_roles_on_oss or not self.gemini_api_key

    @property
    def demo(self) -> bool:
        return self.run_mode == "demo"

    def public(self) -> dict:
        """Settings safe to show or log: never includes the key."""
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "gemini_api_key"}
        out["gemini_api_key_set"] = bool(self.gemini_api_key)
        out["single_model"] = self.single_model
        return {k: str(v) if isinstance(v, Path) else v for k, v in out.items()}

    def thresholds(self) -> dict:
        return tomllib.loads((self.config_dir / "thresholds.toml").read_text())

    def scoring(self) -> dict:
        return tomllib.loads((self.config_dir / "scoring.toml").read_text())


def _under_root(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def load_settings(env: dict[str, str] | None = None, dotenv: Path | None = ROOT / ".env") -> Settings:
    merged = read_dotenv(dotenv) if dotenv else {}
    merged.update({k: v for k, v in (env if env is not None else os.environ).items()})

    def get(name: str, default: str = "") -> str:
        value = merged.get(name, "")
        return value if value != "" else default

    return Settings(
        ollama_host=get("OLLAMA_HOST", "http://localhost:11434").rstrip("/"),
        model_oss=get("MODEL_OSS", "gpt-oss:20b"),
        gemini_api_key=get("GEMINI_API_KEY", ""),
        model_gemma=get("MODEL_GEMMA", "gemma-4-31b-it"),
        gemma_roles_on_oss=_bool(merged.get("GEMMA_ROLES_ON_OSS")),
        run_mode="demo" if get("RUN_MODE", "full") == "demo" else "full",
        searxng_url=get("SEARXNG_URL", "http://localhost:8080").rstrip("/"),
        ollama_timeout=float(get("OLLAMA_TIMEOUT", "600")),
        gemini_timeout=float(get("GEMINI_TIMEOUT", "180")),
        search_timeout=float(get("SEARCH_TIMEOUT", "20")),
        fetch_timeout=float(get("FETCH_TIMEOUT", "15")),
        gemma_rpm=int(get("GEMMA_RPM", "30")),
        gemma_tpm=int(get("GEMMA_TPM", "15000")),
        gemma_rpd=int(get("GEMMA_RPD", "14400")),
        runs_dir=_under_root(get("RUNS_DIR", "runs")),
    )
