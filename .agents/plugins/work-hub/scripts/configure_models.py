#!/usr/bin/env python3
"""CLI tool to inspect, switch presets, customize roles, and validate models.json."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import tempfile
from typing import Any

REQUIRED_ROLES: tuple[str, ...] = (
    "coordinator",
    "planner",
    "impl_g1",
    "impl_g2_g3",
    "verifier_g1",
    "verifier_g2_g3",
    "monitor",
    "research",
)

RESERVED_PRESET_NAMES = frozenset({"custom"})


class ConfigError(Exception):
    """Controlled domain/validation error reported cleanly without a traceback."""


def default_config_path() -> str:
    env_path = os.environ.get("WORK_HUB_MODELS_FILE", "").strip()
    if env_path:
        return os.path.abspath(env_path)
    plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(plugin_root, "models.json")


def resolve_config_path(cli_config: str | None) -> str:
    if cli_config is not None:
        cleaned = cli_config.strip()
        if not cleaned:
            raise ConfigError("--config path must not be empty.")
        return os.path.abspath(cleaned)
    return default_config_path()


def _validate_nonempty_token(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        raise ConfigError(
            f"Invalid {label}: expected a non-empty string without whitespace, got {value!r}."
        )
    return value


def _matches_blocked(model: str, blocked_patterns: list[str]) -> str | None:
    if model.lower() == "inherit":
        return None
    lowered = model.lower()
    for pat in blocked_patterns:
        if fnmatch.fnmatchcase(lowered, pat.lower()):
            return pat
    return None


def _validate_roles_map(roles_obj: Any, context: str) -> dict[str, str]:
    if not isinstance(roles_obj, dict):
        raise ConfigError(f"{context} must be a JSON object mapping role names to model IDs.")

    expected = set(REQUIRED_ROLES)
    actual = set(roles_obj.keys())
    missing = [r for r in REQUIRED_ROLES if r not in actual]
    unknown = sorted(actual - expected)
    if missing:
        raise ConfigError(f"{context} is missing required role(s): {', '.join(missing)}.")
    if unknown:
        raise ConfigError(
            f"{context} contains unknown role(s): {', '.join(unknown)}. "
            f"Allowed roles: {', '.join(REQUIRED_ROLES)}."
        )

    normalized: dict[str, str] = {}
    for role in REQUIRED_ROLES:
        val = _validate_nonempty_token(roles_obj[role], f"{context}.{role}")
        normalized[role] = "inherit" if val.lower() == "inherit" else val
    return normalized


def validate_config(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError("Top-level models.json content must be a JSON object.")

    schema_ver = data.get("schema_version")
    if isinstance(schema_ver, bool) or schema_ver != 1:
        raise ConfigError(f"Unsupported or missing schema_version: {schema_ver!r} (expected 1).")

    preset = _validate_nonempty_token(data.get("preset"), "preset")

    if "min_swarm_model" in data:
        _validate_nonempty_token(data.get("min_swarm_model"), "min_swarm_model")

    raw_blocked = data.get("blocked_models")
    if not isinstance(raw_blocked, list):
        raise ConfigError("'blocked_models' must be a JSON array of model patterns.")
    blocked: list[str] = []
    for idx, pat in enumerate(raw_blocked):
        token = _validate_nonempty_token(pat, f"blocked_models[{idx}]")
        if fnmatch.fnmatchcase("inherit", token.lower()):
            raise ConfigError(
                f"blocked_models[{idx}] ({token!r}) cannot block the reserved 'inherit' value."
            )
        blocked.append(token)

    presets_raw = data.get("presets")
    if not isinstance(presets_raw, dict) or not presets_raw:
        raise ConfigError("'presets' must be a non-empty JSON object.")

    for preset_name, preset_entry in presets_raw.items():
        pname = _validate_nonempty_token(preset_name, "preset key")
        if pname in RESERVED_PRESET_NAMES:
            raise ConfigError(f"Preset name {pname!r} is reserved and cannot be defined in 'presets'.")
        if not isinstance(preset_entry, dict):
            raise ConfigError(f"Preset {pname!r} must be a JSON object.")
        if "description" in preset_entry and not isinstance(preset_entry["description"], str):
            raise ConfigError(f"Preset {pname!r} 'description' must be a string.")
        _validate_roles_map(preset_entry.get("roles"), f"presets.{pname}.roles")

    if preset != "custom" and preset not in presets_raw:
        available = ", ".join(sorted(presets_raw.keys()))
        raise ConfigError(
            f"Active preset {preset!r} does not exist in 'presets'. Available: {available}."
        )

    roles = _validate_roles_map(data.get("roles"), "roles")
    for role, model in roles.items():
        hit = _matches_blocked(model, blocked)
        if hit is not None:
            raise ConfigError(
                f"Role {role!r} uses model {model!r}, which conflicts with blocked pattern {hit!r}."
            )

    return data


def load_config(config_path: str) -> dict[str, Any]:
    if not os.path.exists(config_path):
        raise ConfigError(f"Config file not found: {config_path}")
    if not os.path.isfile(config_path):
        raise ConfigError(f"Config path is not a regular file: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Failed to read config file {config_path}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Invalid JSON in {config_path} (line {exc.lineno}, col {exc.colno}): {exc.msg}"
        ) from exc

    return validate_config(parsed)


def atomic_write_config(config_path: str, data: dict[str, Any]) -> None:
    parent = os.path.dirname(os.path.abspath(config_path)) or "."
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=".models.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, config_path)
    except OSError as exc:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise ConfigError(f"Failed to write config file {config_path}: {exc}") from exc


def apply_preset(data: dict[str, Any], preset_name: str) -> None:
    presets = data["presets"]
    if preset_name not in presets:
        available = ", ".join(sorted(presets.keys()))
        raise ConfigError(f"Unknown preset {preset_name!r}. Available presets: {available}.")

    target_roles = _validate_roles_map(presets[preset_name]["roles"], f"presets.{preset_name}.roles")
    blocked = data["blocked_models"]
    for role, model in target_roles.items():
        hit = _matches_blocked(model, blocked)
        if hit is not None:
            raise ConfigError(
                f"Cannot switch to preset {preset_name!r}: role {role!r} model {model!r} "
                f"matches blocked pattern {hit!r}."
            )

    data["preset"] = preset_name
    data["roles"] = dict(target_roles)


def apply_set(data: dict[str, Any], assignments: list[str]) -> None:
    blocked = data["blocked_models"]
    pending_updates: list[tuple[str, str]] = []

    for item in assignments:
        role, sep, model = item.partition("=")
        if not sep:
            raise ConfigError(
                f"Invalid --set argument {item!r}. Expected format: <role>=<model>."
            )
        role_tok = _validate_nonempty_token(role, "role in --set")
        if role_tok not in REQUIRED_ROLES:
            raise ConfigError(
                f"Unknown role {role_tok!r}. Allowed roles: {', '.join(REQUIRED_ROLES)}."
            )
        model_tok = _validate_nonempty_token(model, f"model for role {role_tok!r}")
        norm_model = "inherit" if model_tok.lower() == "inherit" else model_tok
        hit = _matches_blocked(norm_model, blocked)
        if hit is not None:
            raise ConfigError(
                f"Cannot assign {norm_model!r} to role {role_tok!r}: "
                f"matches blocked pattern {hit!r}."
            )
        pending_updates.append((role_tok, norm_model))

    roles = dict(data["roles"])
    for role_tok, norm_model in pending_updates:
        roles[role_tok] = norm_model
    data["roles"] = roles

    current_preset = data.get("preset")
    presets = data.get("presets", {})
    if current_preset not in presets or roles != presets[current_preset]["roles"]:
        data["preset"] = "custom"


def apply_block(data: dict[str, Any], patterns: list[str]) -> None:
    blocked = list(data["blocked_models"])
    roles = data["roles"]

    for raw_pat in patterns:
        pat = _validate_nonempty_token(raw_pat, "block pattern")
        if fnmatch.fnmatchcase("inherit", pat.lower()):
            raise ConfigError(f"Refusing to block {pat!r}: 'inherit' cannot be blocked.")
        for role, model in roles.items():
            if model.lower() != "inherit" and fnmatch.fnmatchcase(model.lower(), pat.lower()):
                raise ConfigError(
                    f"Cannot block {pat!r}: active role {role!r} currently uses {model!r}."
                )
        if not any(existing.lower() == pat.lower() for existing in blocked):
            blocked.append(pat)

    data["blocked_models"] = blocked


def apply_unblock(data: dict[str, Any], patterns: list[str]) -> None:
    blocked = list(data["blocked_models"])
    for raw_pat in patterns:
        pat = _validate_nonempty_token(raw_pat, "unblock pattern")
        blocked = [existing for existing in blocked if existing.lower() != pat.lower()]
    data["blocked_models"] = blocked


def format_show_text(data: dict[str, Any], config_path: str) -> str:
    lines = [
        f"Config: {config_path}",
        f"Active Preset: {data['preset']}",
        f"Min Swarm Model: {data.get('min_swarm_model', '(unset)')}",
        f"Blocked Models: {', '.join(data['blocked_models']) if data['blocked_models'] else '(none)'}",
        "Role Bindings:",
    ]
    width = max(len(r) for r in REQUIRED_ROLES)
    for role in REQUIRED_ROLES:
        lines.append(f"  {role:<{width}} : {data['roles'][role]}")
    lines.append(f"Available Presets: {', '.join(data['presets'].keys())}")
    return "\n".join(lines)


def format_presets_text(data: dict[str, Any]) -> str:
    lines = [f"Active Preset: {data['preset']}", "Available Presets:"]
    for name, entry in data["presets"].items():
        marker = " *" if name == data["preset"] else ""
        desc = entry.get("description", "").strip()
        lines.append(f"  - {name}{marker}" + (f": {desc}" if desc else ""))
        for role in REQUIRED_ROLES:
            lines.append(f"      {role}: {entry['roles'][role]}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="configure_models.py",
        description="Inspect and configure agy-work-hub G0-G3 model routing (models.json).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to models.json (defaults to WORK_HUB_MODELS_FILE or plugin ../models.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show active preset, blocked models, and role bindings (default when no action given).",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="List all built-in presets and their role bindings.",
    )
    parser.add_argument(
        "--preset",
        default=None,
        help="Switch active preset (e.g. balanced, economy, max-quality, inherit-all).",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="ROLE=MODEL",
        help="Override a specific role binding (repeatable, e.g. --set planner=inherit).",
    )
    parser.add_argument(
        "--block",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Add a blocked model name or wildcard pattern (repeatable).",
    )
    parser.add_argument(
        "--unblock",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Remove a blocked model pattern (repeatable).",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate models.json schema, required roles, presets, and block-list conflicts.",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = resolve_config_path(args.config)
    data = load_config(config_path)

    mutated = False
    if args.preset is not None:
        preset_name = _validate_nonempty_token(args.preset, "preset name")
        apply_preset(data, preset_name)
        mutated = True

    if args.unblock:
        apply_unblock(data, args.unblock)
        mutated = True

    if args.block:
        apply_block(data, args.block)
        mutated = True

    if args.set:
        apply_set(data, args.set)
        mutated = True

    if mutated:
        validate_config(data)
        atomic_write_config(config_path, data)

    no_action = not (
        args.show
        or args.list_presets
        or args.validate
        or mutated
    )

    if args.list_presets:
        if args.json:
            print(
                json.dumps(
                    {
                        "config_path": config_path,
                        "active_preset": data["preset"],
                        "presets": data["presets"],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(format_presets_text(data))
        return 0

    if args.validate and not args.show and not mutated:
        if args.json:
            print(
                json.dumps(
                    {
                        "valid": True,
                        "config_path": config_path,
                        "preset": data["preset"],
                        "roles": data["roles"],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"OK: {config_path} is valid (preset={data['preset']}).")
        return 0

    if args.show or no_action or mutated:
        if args.json:
            payload = {
                "config_path": config_path,
                "preset": data["preset"],
                "min_swarm_model": data.get("min_swarm_model"),
                "blocked_models": data["blocked_models"],
                "roles": data["roles"],
                "available_presets": list(data["presets"].keys()),
            }
            if mutated:
                payload["updated"] = True
            if args.validate:
                payload["valid"] = True
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print(format_show_text(data, config_path))
        return 0

    return 0


def main() -> int:
    try:
        return run_cli()
    except (ConfigError, json.JSONDecodeError, OSError, UnicodeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
