"""
Resolve the effective :class:`OrchidMCPGatewayConfig` at request time.

The framework library ships an :class:`OrchidMCPGatewayConfig` loaded
from ``agents.yaml`` (or set programmatically).  This module layers two
runtime override sources on top of that base config, both driven from
the process environment:

* **Per-tool title / description env vars** — pattern::

      ORCHID_MCP_GATEWAY_TOOL_<TOOL_NAME_UPPER>_<TITLE|DESCRIPTION>

  e.g. ``ORCHID_MCP_GATEWAY_TOOL_ORCHID_ASK_TITLE``.  Captured tool
  name is lowercased to get the canonical name the gateway uses
  (``orchid_ask``).  Env-var values override any YAML values for the
  same tool/field.

* **External prompts YAML file** — pointed to by
  ``ORCHID_MCP_GATEWAY_PROMPTS_FILE``.  When set, the prompts list in
  the base config is **replaced** by the file's contents — not merged
  — so an operator can shrink or reorder the prompt set without
  editing ``agents.yaml``.  The file contains either a top-level list
  of prompt objects, or a dict with a ``prompts:`` key.

Precedence (highest → lowest): env vars > prompts file > YAML / code.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

import yaml

from orchid_ai.config import (
    OrchidMCPGatewayConfig,
    OrchidMCPGatewayPrompt,
    OrchidMCPGatewayToolOverride,
)

_TOOL_ENV_RE = re.compile(
    r"^ORCHID_MCP_GATEWAY_TOOL_([A-Z0-9_]+)_(TITLE|DESCRIPTION)$",
)
_PROMPTS_FILE_ENV = "ORCHID_MCP_GATEWAY_PROMPTS_FILE"


class OrchidMCPGatewayConfigError(RuntimeError):
    """Raised when env-var / file overrides can't be resolved."""


def resolve_mcp_gateway_config(
    base: OrchidMCPGatewayConfig,
    env: Mapping[str, str] | None = None,
) -> OrchidMCPGatewayConfig:
    """Return a new :class:`OrchidMCPGatewayConfig` with env + file overrides applied.

    The ``base`` argument is not mutated — tool overrides and prompts
    are deep-copied before any modifications.
    """
    env_map = env if env is not None else os.environ

    # Deep-copy tool overrides so we don't mutate the framework's config.
    tools: dict[str, OrchidMCPGatewayToolOverride] = {
        name: override.model_copy() for name, override in base.tools.items()
    }

    for key, value in env_map.items():
        match = _TOOL_ENV_RE.match(key)
        if match is None:
            continue
        tool_upper, field_upper = match.groups()
        tool_name = tool_upper.lower()
        current = tools.get(tool_name, OrchidMCPGatewayToolOverride())
        if field_upper == "TITLE":
            tools[tool_name] = current.model_copy(update={"title": value})
        else:
            tools[tool_name] = current.model_copy(update={"description": value})

    # Prompts file override (if set) — replaces, does not merge.
    prompts: list[OrchidMCPGatewayPrompt]
    prompts_file = env_map.get(_PROMPTS_FILE_ENV)
    if prompts_file:
        prompts = _load_prompts_file(Path(prompts_file))
    else:
        prompts = [p.model_copy(deep=True) for p in base.prompts]

    return OrchidMCPGatewayConfig(tools=tools, prompts=prompts)


def _load_prompts_file(path: Path) -> list[OrchidMCPGatewayPrompt]:
    """Load an external YAML file and coerce it into ``[OrchidMCPGatewayPrompt]``.

    Accepts either a top-level list of prompt objects or a dict with a
    ``prompts:`` key (which mirrors the ``mcp_gateway.prompts`` block
    in ``agents.yaml``).
    """
    if not path.exists():
        raise OrchidMCPGatewayConfigError(
            f"{_PROMPTS_FILE_ENV} points to a file that does not exist: {path}",
        )
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise OrchidMCPGatewayConfigError(
            f"Failed to parse {path} as YAML: {exc}",
        ) from exc

    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict) and isinstance(raw.get("prompts"), list):
        items = raw["prompts"]
    else:
        raise OrchidMCPGatewayConfigError(
            f"{path}: must contain a top-level list or a dict with a 'prompts' key",
        )

    return [OrchidMCPGatewayPrompt(**item) for item in items]
