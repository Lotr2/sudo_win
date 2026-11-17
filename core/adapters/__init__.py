"""Per-model adapters: request quirks (reasoning, chat-template fields) and final-answer extraction."""
from __future__ import annotations

from pathlib import Path

import yaml

from core.adapters.base import Adapter, GenericAdapter
from core.adapters.gpt_oss import GptOssAdapter
from core.adapters.qwen3 import Qwen3Adapter

ADAPTERS = {"generic": GenericAdapter, "gpt_oss": GptOssAdapter, "qwen3": Qwen3Adapter}
MODELS_FILE = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


def load_models(path: Path | None = None) -> dict:
    p = Path(path or MODELS_FILE)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def get_adapter(model: str, models_cfg: dict | None = None) -> Adapter:
    cfg = dict((models_cfg if models_cfg is not None else load_models()).get(model) or {})
    kind = cfg.pop("adapter", None)
    if kind is None:
        kind = "gpt_oss" if model.startswith("gpt-oss") else "qwen3" if model.startswith("qwen3") else "generic"
    return ADAPTERS[kind](model, **cfg)
