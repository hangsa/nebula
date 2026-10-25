from __future__ import annotations
import os
import re
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from backend.config import settings
from backend.llm.base_provider import LLMConfig
from backend.llm.model_router import get_model_router

CONFIG_PATH = Path("config/model_tiers.yaml")
ENV_PATH = Path("backend/.env")

# Three providers that ship with nebula and have legacy Settings attrs
# (`anthropic_api_key`, `deepseek_api_key`, `minimax_api_key`) so pydantic
# -settings picks up .env values without a restart. The migration step seeds
# any of these that aren't already in the YAML, so a legacy config that
# only references anthropic still produces a minimax entry — avoids a
# silent drop when the user only configured an env var.
_BUILTIN_PROVIDERS = frozenset({"anthropic", "deepseek", "minimax"})

# Builtin provider → the env var that "activates" it. A builtin is only
# seeded into the providers block when the matching .env key is present
# (non-empty), so an operator who never configures ANTHROPIC_API_KEY won't
# see an unused anthropic card in the AI Console.
_BUILTIN_API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}


class LLMConfigError(ValueError):
    def __init__(self, message: str, invalid_paths: list[str]):
        super().__init__(message)
        self.invalid_paths = invalid_paths


def read_yaml() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml_atomic(data: dict, config_path: Optional[Path] = None) -> None:
    """Atomic write. Same mkstemp + os.replace pattern as
    backfill_behavior_examples._atomic_write_json — survives kill mid-write
    and never leaves a stray .tmp file.

    `config_path` defaults to the production CONFIG_PATH. Tests pass a
    per-test tmp path."""
    target = Path(config_path) if config_path is not None else CONFIG_PATH
    for tier in (data.get("tiers") or {}).values():
        if isinstance(tier, dict):
            tier.pop("models", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=".model_tiers.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                data,
                f,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _env_replace(src: str, dst: Path) -> None:
    """Wrapper around os.replace to allow monkeypatching in tests."""
    os.replace(src, dst)


def _parse_env(text: str) -> tuple[list[str], dict[str, str]]:
    """Return (raw_lines, key->value). Lines that are not assignments are kept
    verbatim in raw_lines so comments and blanks survive untouched."""
    raw = text.splitlines(keepends=False) if text else []
    values: dict[str, str] = {}
    for line in raw:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE_RE.match(line)
        if match:
            values[match.group(1)] = match.group(2)
    return raw, values


def _format_env_value(value: str) -> str:
    if any(ch.isspace() for ch in value) or any(ch in value for ch in ['"', "'", "#"]):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_env_atomic(env_path: Path, updates: dict[str, str]) -> None:
    """Atomic update of a `.env` file. Preserves key order, comments, and
    blank lines. Adds new keys at the end (after a blank line if missing).
    """
    env_path.parent.mkdir(parents=True, exist_ok=True)
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    raw, existing = _parse_env(text)

    new_raw: list[str] = []
    seen_keys: set[str] = set()
    for line in raw:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            new_raw.append(line)
            continue
        match = _ENV_LINE_RE.match(line)
        if not match:
            new_raw.append(line)
            continue
        key = match.group(1)
        if key in updates:
            new_raw.append(f"{key}={_format_env_value(updates[key])}")
            seen_keys.add(key)
        else:
            new_raw.append(line)

    appended = False
    for key, value in updates.items():
        if key in seen_keys:
            continue
        if not appended and new_raw and new_raw[-1].strip() != "":
            new_raw.append("")
            appended = True
        elif not new_raw:
            appended = True
        new_raw.append(f"{key}={_format_env_value(value)}")

    payload = "\n".join(new_raw) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        dir=env_path.parent,
        prefix=".env.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        _env_replace(tmp_name, env_path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


ALLOWED_PROVIDER_TYPES = {"anthropic", "openai_compatible", "mock"}

# Model ids round-trip the provider's own naming — uppercase letters and dots
# are accepted, but characters that would break YAML structure or shell
# escaping (spaces, quotes, colons, brackets) are still forbidden.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _collect_global_models(data: dict) -> dict[str, str]:
    """Map model id -> provider key for every provider's models dict."""
    out: dict[str, str] = {}
    providers = data.get("providers") or {}
    if not isinstance(providers, dict):
        return out
    for pid, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        models = provider.get("models") or {}
        if not isinstance(models, dict):
            continue
        for mid in models:
            out[mid] = pid
    return out


def validate(data: dict) -> None:
    """Validate a config dict. Raises LLMConfigError with `invalid_paths`
    on failure. Path format: dotted, e.g.
    `tiers.tier_1.models.0.id`, `providers.anthropic.models.claude-opus-4`.
    """
    if not isinstance(data, dict):
        raise LLMConfigError("配置根节点必须是对象", ["$"])

    invalid: list[str] = []

    providers = data.get("providers") or {}
    if not isinstance(providers, dict):
        raise LLMConfigError("providers 必须是对象", ["providers"])

    global_models = _collect_global_models(data)
    seen_ids: set[str] = set()
    first_seen_path: dict[str, str] = {}
    for pid, provider in providers.items():
        path = f"providers.{pid}"
        if not isinstance(provider, dict):
            invalid.append(path)
            continue
        ptype = provider.get("type")
        if ptype not in ALLOWED_PROVIDER_TYPES:
            invalid.append(f"{path}.type")
        if not provider.get("api_key_env"):
            invalid.append(f"{path}.api_key_env")
        base_url = provider.get("base_url")
        if ptype == "openai_compatible" and not base_url:
            invalid.append(f"{path}.base_url")
        models = provider.get("models") or {}
        if not isinstance(models, dict):
            invalid.append(f"{path}.models")
            continue
        for mid, model in models.items():
            if not isinstance(mid, str) or not _MODEL_ID_RE.match(mid):
                invalid.append(f"{path}.models.<id>")
                continue
            mpath = f"{path}.models.{mid}"
            if not isinstance(model, dict):
                invalid.append(mpath)
                continue
            if mid in seen_ids:
                # Report both occurrences so callers can locate each one.
                invalid.append(first_seen_path[mid])
                invalid.append(f"providers.{pid}.models[duplicate_id={mid}]")
            else:
                first_seen_path[mid] = mpath
            seen_ids.add(mid)
            max_tokens = model.get("max_tokens")
            if (
                not isinstance(max_tokens, int)
                or isinstance(max_tokens, bool)
            ):
                invalid.append(f"{mpath}.max_tokens")
            cost_in = model.get("cost_per_1k_input")
            cost_out = model.get("cost_per_1k_output")
            if not isinstance(cost_in, (int, float)) or isinstance(cost_in, bool):
                invalid.append(f"{mpath}.cost_per_1k_input")
            if not isinstance(cost_out, (int, float)) or isinstance(cost_out, bool):
                invalid.append(f"{mpath}.cost_per_1k_output")
            temperature = model.get("temperature")
            if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
                invalid.append(f"{mpath}.temperature")

    tiers = data.get("tiers") or {}
    if not isinstance(tiers, dict):
        raise LLMConfigError("tiers 必须是对象", ["tiers"])

    known_tier_names = set(tiers.keys())
    if "tier_0" not in known_tier_names:
        invalid.append("tier_0")

    for tier_name, tier in tiers.items():
        if not tier_name.strip() or not isinstance(tier, dict):
            invalid.append(f"tiers.{tier_name or '<empty>'}")
            continue

        default = tier.get("default")
        if default and default != "none" and default not in global_models:
            invalid.append(f"tiers.{tier_name}.default")
        fallback = tier.get("fallback")
        if fallback and fallback not in global_models:
            invalid.append(f"tiers.{tier_name}.fallback")

    mappings = data.get("agent_mapping") or {}
    if not isinstance(mappings, dict):
        invalid.append("agent_mapping")
        mappings = {}

    for agent_name, tasks in mappings.items():
        if not agent_name.strip() or not isinstance(tasks, dict):
            invalid.append(f"agent_mapping.{agent_name or '<empty>'}")
            continue
        for task_name, mapping in tasks.items():
            if not task_name.strip() or not isinstance(mapping, dict):
                invalid.append(f"agent_mapping.{agent_name}.{task_name or '<empty>'}")
                continue
            tier_name = mapping.get("tier")
            if tier_name and tier_name not in known_tier_names:
                invalid.append(f"agent_mapping.{agent_name}.{task_name}.tier")
                continue
            model_id = mapping.get("model")
            if model_id and model_id != "default" and model_id not in global_models:
                invalid.append(f"agent_mapping.{agent_name}.{task_name}.model")
            fallback = mapping.get("fallback")
            if fallback and fallback != "default" and fallback not in global_models:
                invalid.append(f"agent_mapping.{agent_name}.{task_name}.fallback")

    if invalid:
        raise LLMConfigError(
            f"配置校验失败：{len(invalid)} 项错误", invalid
        )


def find_references(data: dict, target: str) -> list[str]:
    """Return dotted paths referencing the given target.

    `target` forms:
      `provider:<id>` — finds any tier/agent_mapping entry that references a
        model owned by that provider.
      `model:<id>` — finds any tier/agent_mapping entry that references that
        model id directly.
    """
    if ":" not in target:
        raise ValueError(f"target must be 'provider:<id>' or 'model:<id>', got {target!r}")
    kind, value = target.split(":", 1)
    paths: list[str] = []
    tiers = data.get("tiers") or {}
    provider_map = _collect_global_models(data)

    if kind == "model":
        target_id = value
        target_provider = provider_map.get(target_id)
    elif kind == "provider":
        target_id = None
        target_provider = value
    else:
        raise ValueError(f"unknown target kind {kind!r}")

    for tier_name, tier in tiers.items():
        if not isinstance(tier, dict):
            continue
        default = tier.get("default")
        if isinstance(default, str):
            if kind == "model" and default == target_id:
                paths.append(f"tiers.{tier_name}.default")
            elif kind == "provider" and provider_map.get(default) == target_provider:
                paths.append(f"tiers.{tier_name}.default")
        fallback = tier.get("fallback")
        if isinstance(fallback, str):
            if kind == "model" and fallback == target_id:
                paths.append(f"tiers.{tier_name}.fallback")
            elif kind == "provider" and provider_map.get(fallback) == target_provider:
                paths.append(f"tiers.{tier_name}.fallback")

    mappings = data.get("agent_mapping") or {}
    for agent_name, tasks in mappings.items():
        if not isinstance(tasks, dict):
            continue
        for task_name, mapping in tasks.items():
            if not isinstance(mapping, dict):
                continue
            for field in ("model", "fallback"):
                value = mapping.get(field)
                if not isinstance(value, str):
                    continue
                if kind == "model" and value == target_id:
                    paths.append(f"agent_mapping.{agent_name}.{task_name}.{field}")
                elif kind == "provider" and provider_map.get(value) == target_provider:
                    paths.append(f"agent_mapping.{agent_name}.{task_name}.{field}")
    return paths


def validate_removal(data: dict, target: str) -> None:
    paths = find_references(data, target)
    if paths:
        sample = "，".join(paths[:3]) + ("…" if len(paths) > 3 else "")
        raise LLMConfigError(
            f"无法删除 {target}，仍有 {len(paths)} 处引用：{sample}",
            paths,
        )


def reload_router() -> dict:
    """Validate disk config, then swap the live router's tiers / mappings in."""
    validate(read_yaml())
    router = get_model_router()
    router.reload_config()
    return {"tiers": len(router._tiers), "agents": len(router._mappings)}


def _setting_for_env(env_name: str) -> str:
    """Map an API key env name (e.g. ANTHROPIC_API_KEY) to the matching
    Settings attribute (`anthropic_api_key`). Falls back to the env name
    so unknown keys just return empty (no API key configured)."""
    mapping = {
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "DEEPSEEK_API_KEY": "deepseek_api_key",
        "MINIMAX_API_KEY": "minimax_api_key",
    }
    return mapping.get(env_name, env_name.lower())


def _resolve_api_key_for(provider_id: str, api_key_env: str) -> str:
    """Resolve the actual API key string for a provider, in priority order:
    1. STORYFORGE_PROVIDER_API_KEY_<PID> (new prefix, hot-reloadable,
       works for any provider id including custom).
    2. Builtin settings attr (anthropic_api_key / deepseek_api_key /
       minimax_api_key) when api_key_env matches the legacy builtin env name.
    3. The provider-declared api_key_env in os.environ.

    Returns "" when no key is configured. Mirrors the `configured` check in
    provider_status() so a probe with the same provider sees the same key.
    """
    prefixed_key = f"STORYFORGE_PROVIDER_API_KEY_{provider_id.upper()}"
    value = os.environ.get(prefixed_key, "")
    if value:
        return value
    settings_attr = _setting_for_env(api_key_env) if api_key_env else ""
    if settings_attr:
        value = getattr(settings, settings_attr, "") or ""
        if value:
            return value
    if api_key_env:
        value = os.environ.get(api_key_env, "")
        if value:
            return value
    return ""


def provider_status() -> list[dict]:
    """Return one record per entry in `providers`.

    Each record carries: `provider`, `type`, `base_url`, `api_key_env`,
    `api_key_configured`, `enabled`, and `models` (list of {id, display_name}).
    Secrets are NEVER serialized.
    """
    cfg = read_yaml()
    providers = cfg.get("providers") or {}
    out: list[dict] = []
    for pid, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        api_key_env = provider.get("api_key_env", "")
        configured = bool(_resolve_api_key_for(pid, api_key_env))
        models_out = []
        for mid, model in (provider.get("models") or {}).items():
            if not isinstance(model, dict):
                continue
            models_out.append({
                "id": mid,
                "display_name": model.get("display_name", mid),
                "cost_per_1k_input": model.get("cost_per_1k_input", 0),
                "cost_per_1k_output": model.get("cost_per_1k_output", 0),
                "max_tokens": model.get("max_tokens", 200000),
                "temperature": model.get("temperature", 0.7),
                "json_mode": bool(model.get("json_mode", False)),
                "stream": bool(model.get("stream", True)),
            })
        out.append({
            "provider": pid,
            "type": provider.get("type", "openai_compatible"),
            "display_name": provider.get("display_name", pid),
            "base_url": provider.get("base_url", ""),
            "api_key_env": api_key_env,
            "api_key_configured": configured,
            "enabled": bool(provider.get("enabled", True)),
            "models": models_out,
        })
    return out


def _provider_class_for_type(provider_type: str):
    """Map the YAML `type` field to a concrete LLM provider class.

    Returns None when the type is unknown — callers must raise.
    """
    if provider_type == "anthropic":
        from backend.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider
    if provider_type == "minimax":
        from backend.llm.minimax_provider import MiniMaxProvider
        return MiniMaxProvider
    if provider_type == "mock":
        from backend.llm.mock_provider import MockProvider
        return MockProvider
    # Default + explicit "openai_compatible"
    from backend.llm.openai_compatible_provider import OpenAICompatibleProvider
    return OpenAICompatibleProvider


async def probe_provider(provider_id: str) -> dict:
    """Probe a saved provider — connection check + model listing.

    Reads the provider's YAML entry, resolves the API key via the same
    priority order as provider_status(), instantiates the right provider
    class, and calls its async probe().

    Returns a dict suitable for direct JSON serialization:
      {success, latency_ms, models: [{id, display_name}], error?, error_code?}

    Raises LLMConfigError only when the provider id is missing from the
    config (404 path in the endpoint). Auth/connection failures are
    surfaced via the `success=False` payload, NOT as exceptions, so the
    AI Console can render the error inline.
    """
    cfg = read_yaml()
    providers = cfg.get("providers") or {}
    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        raise LLMConfigError(f"provider '{provider_id}' 不存在", ["provider_id"])

    api_key_env = provider.get("api_key_env", "") or ""
    api_key = _resolve_api_key_for(provider_id, api_key_env)
    if not api_key:
        return {
            "success": False,
            "latency_ms": 0,
            "models": None,
            "error": f"API Key 未配置（{api_key_env or '未设置 api_key_env'}）",
            "error_code": "auth_error",
        }

    base_url = provider.get("base_url") or ""
    provider_type = provider.get("type", "openai_compatible")
    cls = _provider_class_for_type(provider_type)
    # Anthropic probe needs a real model id; use the first configured model
    # or fall back to claude-haiku-4-5 (the cheapest available).
    model = "probe"
    if provider_type == "anthropic":
        models_dict = provider.get("models") or {}
        model = next(iter(models_dict.keys()), None) or "claude-haiku-4-5"
    try:
        instance = cls(LLMConfig(
            provider=provider_id,
            model=model,
            api_key=api_key,
            base_url=base_url or None,
        ))
    except Exception as e:
        return {
            "success": False,
            "latency_ms": 0,
            "models": None,
            "error": f"无法构造 provider: {type(e).__name__}: {e}",
            "error_code": "provider_error",
        }
    result = await instance.probe()
    return asdict(result)


def _legacy_default_base_url(provider_id: str) -> str:
    if provider_id == "deepseek":
        return "https://api.deepseek.com/v1"
    if provider_id == "minimax":
        return "https://api.minimax.chat/v1"
    if provider_id == "anthropic":
        return "https://api.anthropic.com"
    return ""


def _legacy_provider_type(provider_id: str) -> str:
    return "anthropic" if provider_id == "anthropic" else "openai_compatible"


def _builtin_provider_seed(pid: str) -> dict:
    """Default entry for a builtin provider that was never explicitly
    configured. Empty models — the AI Console or a follow-up YAML edit
    fills them in. Idempotency is the caller's responsibility."""
    return {
        "type": _legacy_provider_type(pid),
        "display_name": pid,
        "base_url": _legacy_default_base_url(pid),
        "api_key_env": f"{pid.upper()}_API_KEY",
        "enabled": True,
        "models": {},
    }


def _env_has_api_key(env_path: Path, key_name: str) -> bool:
    """Return True iff `env_path` contains a non-empty assignment for `key_name`.

    Used by the builtin-provider seeder to avoid surfacing a provider card
    in the AI Console for which the operator never configured an API key.
    """
    if not env_path.exists():
        return False
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return False
    _, values = _parse_env(text)
    return bool(values.get(key_name))


def _ensure_builtin_providers(
    providers: dict[str, dict],
    env_path: Optional[Path] = None,
) -> list[str]:
    """Idempotent: add any builtin (anthropic/deepseek/minimax) missing
    from `providers` AND activated by a non-empty API key in `env_path`.

    The env check prevents a no-op anthropic card from appearing in the
    AI Console for operators who never configured ANTHROPIC_API_KEY. When
    `env_path` is None, fallback to the default ENV_PATH (still gated).

    Mutates `providers` in place; returns the list of added provider ids.
    """
    target_env = env_path if env_path is not None else ENV_PATH
    added: list[str] = []
    for pid in _BUILTIN_PROVIDERS:
        if pid in providers and isinstance(providers[pid], dict):
            continue
        key_env = _BUILTIN_API_KEY_ENV.get(pid, "")
        if key_env and not _env_has_api_key(target_env, key_env):
            continue
        providers[pid] = _builtin_provider_seed(pid)
        added.append(pid)
    return added


def seed_builtin_providers(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> dict:
    """Recovery path for already-migrated YAMLs that silently lost a
    builtin because no tier ever referenced it. Reads the YAML, re-adds
    missing anthropic/deepseek/minimax entries with empty models, syncs
    their env keys when configured, validates, then writes atomically.

    Returns `{"added": list[str], "summary": LLMRouterSummary}`.
    """
    # Resolve lazily so tests that monkey-patch `cfg_mod.CONFIG_PATH` take
    # effect. Catching `None` here also works for default-argument callers
    # without one — `Path` is NOT yet frozen to the original at def-time.
    if config_path is None:
        config_path = CONFIG_PATH
    if not config_path.exists():
        raise LLMConfigError("配置文件不存在", ["$"])
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise LLMConfigError("配置文件根节点必须是对象", ["$"])

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        raw["providers"] = providers
    added = _ensure_builtin_providers(providers, env_path=env_path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise LLMConfigError("配置文件根节点必须是对象", ["$"])

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        raw["providers"] = providers
    added = _ensure_builtin_providers(providers, env_path=env_path)

    if env_path is None:
        env_path = Path("backend/.env")
    env_updates: dict[str, str] = {}
    for pid in _BUILTIN_PROVIDERS:
        key = getattr(settings, f"{pid}_api_key", "")
        if not key:
            continue
        env_updates[f"STORYFORGE_PROVIDER_API_KEY_{pid.upper()}"] = key
        env_updates[f"{pid.upper()}_API_KEY"] = key
    if env_updates:
        try:
            write_env_atomic(env_path, env_updates)
        except Exception as e:
            print(f"[llm_config] warning: failed to sync env keys to {env_path}: {e}")

    validate(raw)
    write_yaml_atomic(raw, config_path)
    summary = reload_router()
    return {"added": added, "summary": summary}


def migrate_legacy_yaml(
    config_path: Optional[Path] = None,
    env_path: Optional[Path] = None,
) -> dict:
    """Convert the legacy `tiers.*.models[*].{provider,cost_*,max_tokens}`
    layout into the v2 schema with a top-level `providers` block.

    Returns a summary dict: `{backup_path, summary}`.

    Raises LLMConfigError when the YAML already uses v2 (i.e. has a
    `providers` key) or the structure cannot be interpreted.
    """
    # Resolve lazily so tests that monkey-patch `cfg_mod.CONFIG_PATH`
    # actually take effect (default-argument values freeze at def time).
    if config_path is None:
        config_path = CONFIG_PATH
    if not config_path.exists():
        raise LLMConfigError("配置文件不存在", ["$"])
    raw_text = config_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text) or {}
    if not isinstance(raw, dict):
        raise LLMConfigError("配置文件根节点必须是对象", ["$"])
    if "providers" in raw:
        raise LLMConfigError("已是新结构，无需迁移", ["providers"])

    tiers = raw.get("tiers") or {}
    providers: dict[str, dict] = {}
    for tier in tiers.values():
        if not isinstance(tier, dict):
            continue
        for m in tier.get("models") or []:
            if not isinstance(m, dict):
                continue
            pid = m.get("provider")
            mid = m.get("id")
            if not pid or not mid:
                continue
            providers.setdefault(
                pid,
                {
                    "type": _legacy_provider_type(pid),
                    "display_name": pid,
                    "base_url": _legacy_default_base_url(pid),
                    "api_key_env": f"{pid.upper()}_API_KEY",
                    "enabled": True,
                    "models": {},
                },
            )
            models = providers[pid]["models"]
            if mid in models:
                continue
            models[mid] = {
                "display_name": mid,
                "cost_per_1k_input": m.get("cost_per_1k_input", 0),
                "cost_per_1k_output": m.get("cost_per_1k_output", 0),
                "max_tokens": m.get("max_tokens", 200000),
                "temperature": 0.7,
                "json_mode": pid in {"deepseek", "minimax"},
                "stream": True,
            }

    # Seed any builtin (anthropic/deepseek/minimax) that no tier referenced
    # AND has a matching non-empty API key in `.env`, so configured keys for
    # those providers aren't silently dropped. Builtins without a configured
    # key are skipped (no orphan provider card in the AI Console).
    # After this, the env_sync loop below also covers builtin entries.
    _ensure_builtin_providers(providers, env_path=env_path)

    # Detect global id collisions; rename by prefixing provider id.
    collisions: dict[str, list[str]] = {}
    for pid, provider in providers.items():
        for mid in list(provider["models"].keys()):
            collisions.setdefault(mid, []).append(pid)
    renamed = False
    # Disambiguate colliding model ids by prefixing the owning provider id.
    # Use `__` (double underscore) instead of `/` so the resulting id still
    # matches _MODEL_ID_RE — `/` would break YAML keys and downstream routers
    # that pass the id verbatim to provider APIs.
    for mid, providers_sharing in collisions.items():
        if len(providers_sharing) <= 1:
            continue
        renamed = True
        for pid in providers_sharing:
            old = providers[pid]["models"].pop(mid)
            new_mid = f"{pid}__{mid}"
            providers[pid]["models"][new_mid] = old

    # Rewrite agent_mapping references for renamed ids.
    if renamed:
        mappings = raw.get("agent_mapping") or {}
        for agent, tasks in mappings.items():
            if not isinstance(tasks, dict):
                continue
            for task, mapping in tasks.items():
                if not isinstance(mapping, dict):
                    continue
                for key in ("model", "fallback"):
                    val = mapping.get(key)
                    if isinstance(val, str) and val in collisions:
                        mapping[key] = f"{collisions[val][0]}__{val}"

        # Rewrite tier default/fallback references for renamed ids so the
        # post-migration config still passes `validate()` (default/fallback
        # must be in providers.*.models, not the un-prefixed collision name).
        tiers = raw.get("tiers") or {}
        for tier in tiers.values():
            if not isinstance(tier, dict):
                continue
            for key in ("default", "fallback"):
                val = tier.get(key)
                if isinstance(val, str) and val in collisions:
                    tier[key] = f"{collisions[val][0]}__{val}"

    # Persist backup.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = config_path.with_suffix(config_path.suffix + f".bak-{ts}")
    backup_path.write_text(raw_text, encoding="utf-8")

    raw["providers"] = providers

    # Sync .env (best-effort).
    if env_path is None:
        env_path = Path("backend/.env")
    env_updates: dict[str, str] = {}
    for pid in providers:
        key = getattr(settings, f"{pid}_api_key", "")
        if not key:
            continue
        # Write BOTH the new prefixed name and the legacy alias so
        # pydantic-settings picks the value up on the next reload without
        # requiring a process restart.
        env_updates[f"STORYFORGE_PROVIDER_API_KEY_{pid.upper()}"] = key
        env_updates[f"{pid.upper()}_API_KEY"] = key
    if env_updates:
        try:
            write_env_atomic(env_path, env_updates)
        except Exception as e:
            # Env sync is best-effort; a malformed .env or permissions issue
            # must not abort the YAML migration.
            print(f"[llm_config] warning: failed to sync env keys to {env_path}: {e}")

    # Build the full new dict first so we can validate before touching disk.
    new_raw = dict(raw)
    new_raw["providers"] = providers
    validate(new_raw)

    write_yaml_atomic(new_raw, config_path)
    return {
        "backup_path": str(backup_path),
        "summary": {
            "providers": list(providers.keys()),
            "renamed_ids": renamed,
        },
    }
