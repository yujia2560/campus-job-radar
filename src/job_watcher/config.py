from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Source


class ConfigError(ValueError):
    pass


def load_config(path: str | Path) -> tuple[list[Source], dict[str, Any]]:
    config_path = Path(path)
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"配置文件不存在：{config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是有效 JSON：{exc}") from exc

    if not isinstance(data.get("sources"), list):
        raise ConfigError("配置必须包含 sources 数组")

    sources: list[Source] = []
    keys: set[str] = set()
    for index, raw in enumerate(data["sources"], start=1):
        missing = [name for name in ("key", "company", "track", "ats", "url") if not raw.get(name)]
        if missing:
            raise ConfigError(f"第 {index} 个来源缺少字段：{', '.join(missing)}")
        if raw["key"] in keys:
            raise ConfigError(f"来源 key 重复：{raw['key']}")
        keys.add(raw["key"])
        sources.append(
            Source(
                key=str(raw["key"]),
                company=str(raw["company"]),
                track=str(raw["track"]),
                ats=str(raw["ats"]),
                url=str(raw["url"]),
                priority=int(raw.get("priority", 2)),
                enabled=bool(raw.get("enabled", True)),
                cities=[str(city) for city in raw.get("cities", [])],
                notes=str(raw.get("notes", "")),
            )
        )

    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError("settings 必须是对象")
    return sources, settings
