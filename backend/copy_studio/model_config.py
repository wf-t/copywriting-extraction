from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MODEL_CONFIG: dict[str, Any] = {
    "api_providers": {
        "common": {"proxy": "", "print_model_input": False},
        "deepseek": {"api_key": "", "base_url": "https://api.deepseek.com/v1", "enable_proxy": False},
        "dashscope": {"api_key": "", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "enable_proxy": False},
    },
    "models": {"llm": "deepseek-chat"},
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base, ensure_ascii=False))
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ModelConfigStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.path = self.root / "data" / "model_config.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        config = deep_merge(DEFAULT_MODEL_CONFIG, {})
        if self.path.exists():
            try:
                config = deep_merge(config, json.loads(self.path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
        overrides = {
            "DEEPSEEK_API_KEY": ("api_providers", "deepseek", "api_key"),
            "DEEPSEEK_BASE_URL": ("api_providers", "deepseek", "base_url"),
            "DASHSCOPE_API_KEY": ("api_providers", "dashscope", "api_key"),
        }
        for variable, keys in overrides.items():
            if value := os.environ.get(variable, "").strip():
                config[keys[0]][keys[1]][keys[2]] = value
        if model := os.environ.get("DEEPSEEK_MODEL", "").strip():
            config["models"]["llm"] = model
        return config

    def save(self, config: dict[str, Any]) -> dict[str, Any]:
        merged = deep_merge(DEFAULT_MODEL_CONFIG, config)
        self.path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return merged

