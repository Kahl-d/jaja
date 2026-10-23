"""The owner's policy, style and identity values. The code ships neutral defaults; your values live in your data folder.

Three sections, each a YAML mapping:
  identity   who you are on paper: name, emails, signature, resume file name, profile URLs.
  style      how you write: banned characters and words, greeting and sign-off form, message rules.
  policy     your search rules: years gates, compensation, work authorization wording, location rules.

Lookup order (later wins): config/defaults.d/*.yaml in the repo (neutral defaults) -> <DATA_ROOT>/config/*.yaml
(your files; any file name, merged in name order). Under pytest the data folder is ignored and scripts/tests/persona.d/*.yaml
(a synthetic persona) is layered on the defaults instead, so tests never depend on a real person.

    import agency_config as cfg
    cfg.get("style.no_em_dash", False)         # dotted path with a default
    cfg.section("identity")                    # the whole mapping
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import agency_paths as _ap

_CACHE: dict[str, Any] | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _dirs() -> list[Path]:
    dirs = [_ap.CODE_ROOT / "config" / "defaults.d"]
    if _under_pytest():
        dirs.append(_ap.CODE_ROOT / "scripts" / "tests" / "persona.d")
    else:
        dirs.append(_ap.DATA_ROOT / "config")
    return dirs


def _under_pytest() -> bool:
    import sys
    return "pytest" in sys.modules


def load(force: bool = False) -> dict[str, Any]:
    global _CACHE
    if _CACHE is None or force:
        out: dict[str, Any] = {}
        for d in _dirs():
            if d.is_dir():
                for f in sorted(d.glob("*.yaml")):
                    _merge(out, _load_yaml(f))
        _CACHE = out
    return _CACHE


def reload() -> dict[str, Any]:
    return load(force=True)


def get(path: str, default: Any = None) -> Any:
    node: Any = load()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def section(name: str) -> dict[str, Any]:
    value = load().get(name)
    return value if isinstance(value, dict) else {}


def override(values: dict[str, Any]) -> None:
    """Layer values over the loaded config (tests, one-off runs). Cleared by reload()."""
    _merge(load(), values)
