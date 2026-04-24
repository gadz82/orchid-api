"""Tests for orchid_api.mcp_gateway — resolver + router."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from orchid_ai.config import (
    OrchidAgentsConfig,
    OrchidMCPGatewayConfig,
    OrchidMCPGatewayPrompt,
    OrchidMCPGatewayToolOverride,
)
from orchid_ai.core.state import OrchidAuthContext

from orchid_api.mcp_gateway import (
    OrchidMCPGatewayConfigError,
    resolve_mcp_gateway_config,
)
from orchid_api.routers.mcp_gateway import get_mcp_gateway_config


# ── resolve_mcp_gateway_config ───────────────────────────────────


class TestResolverTools:
    def test_no_env_returns_copy_of_base(self):
        base = OrchidMCPGatewayConfig(
            tools={"orchid_ask": OrchidMCPGatewayToolOverride(title="YAML Title")},
        )
        resolved = resolve_mcp_gateway_config(base, env={})
        assert resolved.tools["orchid_ask"].title == "YAML Title"
        # Different object — caller may mutate without affecting base.
        assert resolved.tools["orchid_ask"] is not base.tools["orchid_ask"]

    def test_env_title_overrides_yaml(self):
        base = OrchidMCPGatewayConfig(
            tools={"orchid_ask": OrchidMCPGatewayToolOverride(title="YAML")},
        )
        resolved = resolve_mcp_gateway_config(
            base,
            env={"ORCHID_MCP_GATEWAY_TOOL_ORCHID_ASK_TITLE": "ENV"},
        )
        assert resolved.tools["orchid_ask"].title == "ENV"

    def test_env_description_overrides_yaml(self):
        base = OrchidMCPGatewayConfig(
            tools={
                "orchid_ask": OrchidMCPGatewayToolOverride(
                    title="Keep",
                    description="YAML desc",
                )
            },
        )
        resolved = resolve_mcp_gateway_config(
            base,
            env={"ORCHID_MCP_GATEWAY_TOOL_ORCHID_ASK_DESCRIPTION": "ENV desc"},
        )
        assert resolved.tools["orchid_ask"].title == "Keep"  # untouched
        assert resolved.tools["orchid_ask"].description == "ENV desc"

    def test_env_creates_missing_tool_entry(self):
        base = OrchidMCPGatewayConfig()  # empty
        resolved = resolve_mcp_gateway_config(
            base,
            env={"ORCHID_MCP_GATEWAY_TOOL_ORCHID_NEW_CHAT_TITLE": "Fresh"},
        )
        assert resolved.tools["orchid_new_chat"].title == "Fresh"

    def test_env_lowercases_tool_name(self):
        # The env var convention uses uppercase.  The canonical tool name
        # is lowercase.
        base = OrchidMCPGatewayConfig()
        resolved = resolve_mcp_gateway_config(
            base,
            env={"ORCHID_MCP_GATEWAY_TOOL_ORCHID_UPLOAD_FILE_TITLE": "Up"},
        )
        assert "orchid_upload_file" in resolved.tools
        assert resolved.tools["orchid_upload_file"].title == "Up"

    def test_ignores_unrelated_env_vars(self):
        base = OrchidMCPGatewayConfig()
        resolved = resolve_mcp_gateway_config(
            base,
            env={
                "ORCHID_MCP_GATEWAY_TOOL_X_TITLE": "fine",
                "ORCHID_MCP_SOMETHING_ELSE": "ignored",
                "PATH": "/usr/bin",
                "ORCHID_MCP_GATEWAY_TOOL_X_WEIRD": "ignored",  # not TITLE/DESCRIPTION
            },
        )
        assert set(resolved.tools.keys()) == {"x"}

    def test_does_not_mutate_base(self):
        base = OrchidMCPGatewayConfig(
            tools={"orchid_ask": OrchidMCPGatewayToolOverride(title="keep")},
        )
        resolve_mcp_gateway_config(
            base,
            env={"ORCHID_MCP_GATEWAY_TOOL_ORCHID_ASK_TITLE": "new"},
        )
        assert base.tools["orchid_ask"].title == "keep"


class TestResolverPromptsFile:
    def test_no_file_keeps_base_prompts(self):
        base = OrchidMCPGatewayConfig(
            prompts=[OrchidMCPGatewayPrompt(name="p", template="x")],
        )
        resolved = resolve_mcp_gateway_config(base, env={})
        assert len(resolved.prompts) == 1
        assert resolved.prompts[0].name == "p"

    def test_file_replaces_yaml_prompts(self, tmp_path: Path):
        base = OrchidMCPGatewayConfig(
            prompts=[OrchidMCPGatewayPrompt(name="yaml_prompt", template="old")],
        )
        path = tmp_path / "prompts.yml"
        path.write_text(
            yaml.safe_dump(
                [
                    {"name": "file_prompt", "template": "new {{x}}."},
                ]
            )
        )
        resolved = resolve_mcp_gateway_config(
            base,
            env={"ORCHID_MCP_GATEWAY_PROMPTS_FILE": str(path)},
        )
        assert [p.name for p in resolved.prompts] == ["file_prompt"]
        assert resolved.prompts[0].template == "new {{x}}."

    def test_file_accepts_top_level_prompts_key(self, tmp_path: Path):
        path = tmp_path / "prompts.yml"
        path.write_text(
            yaml.safe_dump(
                {
                    "prompts": [{"name": "p", "template": "t"}],
                }
            )
        )
        resolved = resolve_mcp_gateway_config(
            OrchidMCPGatewayConfig(),
            env={"ORCHID_MCP_GATEWAY_PROMPTS_FILE": str(path)},
        )
        assert resolved.prompts[0].name == "p"

    def test_file_missing_raises_config_error(self, tmp_path: Path):
        missing = tmp_path / "nope.yml"
        with pytest.raises(OrchidMCPGatewayConfigError, match="does not exist"):
            resolve_mcp_gateway_config(
                OrchidMCPGatewayConfig(),
                env={"ORCHID_MCP_GATEWAY_PROMPTS_FILE": str(missing)},
            )

    def test_file_invalid_yaml_raises(self, tmp_path: Path):
        path = tmp_path / "bad.yml"
        path.write_text("not: valid: yaml: here: [\n")
        with pytest.raises(OrchidMCPGatewayConfigError, match="Failed to parse"):
            resolve_mcp_gateway_config(
                OrchidMCPGatewayConfig(),
                env={"ORCHID_MCP_GATEWAY_PROMPTS_FILE": str(path)},
            )

    def test_file_wrong_shape_raises(self, tmp_path: Path):
        path = tmp_path / "odd.yml"
        path.write_text(yaml.safe_dump({"something_else": 42}))
        with pytest.raises(OrchidMCPGatewayConfigError, match="top-level list"):
            resolve_mcp_gateway_config(
                OrchidMCPGatewayConfig(),
                env={"ORCHID_MCP_GATEWAY_PROMPTS_FILE": str(path)},
            )


class TestResolverCombined:
    def test_env_tool_and_prompts_file_apply_together(self, tmp_path: Path):
        path = tmp_path / "p.yml"
        path.write_text(yaml.safe_dump([{"name": "file", "template": "t"}]))
        base = OrchidMCPGatewayConfig(
            tools={"orchid_ask": OrchidMCPGatewayToolOverride(title="yaml")},
            prompts=[OrchidMCPGatewayPrompt(name="yaml_p", template="x")],
        )
        resolved = resolve_mcp_gateway_config(
            base,
            env={
                "ORCHID_MCP_GATEWAY_TOOL_ORCHID_ASK_TITLE": "env",
                "ORCHID_MCP_GATEWAY_PROMPTS_FILE": str(path),
            },
        )
        assert resolved.tools["orchid_ask"].title == "env"
        assert [p.name for p in resolved.prompts] == ["file"]


# ── Router (handler) ─────────────────────────────────────────────


@pytest.fixture
def auth():
    return OrchidAuthContext(access_token="tok", tenant_key="t1", user_id="u1")


class TestRouter:
    @pytest.mark.asyncio
    async def test_returns_resolved_config(self, auth, monkeypatch):
        monkeypatch.delenv("ORCHID_MCP_GATEWAY_PROMPTS_FILE", raising=False)
        # Remove any tool-override env vars in the parent shell so the test
        # is deterministic.
        for key in list(monkeypatch._setenv.keys() if hasattr(monkeypatch, "_setenv") else []):
            if key.startswith("ORCHID_MCP_GATEWAY_TOOL_"):
                monkeypatch.delenv(key, raising=False)

        agents_config = OrchidAgentsConfig(
            mcp_gateway={
                "tools": {"orchid_ask": {"title": "YAML"}},
                "prompts": [{"name": "p", "template": "t"}],
            }
        )
        result = await get_mcp_gateway_config(_auth=auth, agents_config=agents_config)
        assert isinstance(result, OrchidMCPGatewayConfig)
        assert result.tools["orchid_ask"].title == "YAML"
        assert result.prompts[0].name == "p"

    @pytest.mark.asyncio
    async def test_surfaces_resolver_errors_as_500(self, auth, tmp_path, monkeypatch):
        missing = tmp_path / "nope.yml"
        monkeypatch.setenv("ORCHID_MCP_GATEWAY_PROMPTS_FILE", str(missing))

        agents_config = OrchidAgentsConfig()
        with pytest.raises(HTTPException) as exc:
            await get_mcp_gateway_config(_auth=auth, agents_config=agents_config)
        assert exc.value.status_code == 500
