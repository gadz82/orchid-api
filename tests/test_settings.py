"""Tests for orchid_api.settings — configuration and YAML overlay."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import yaml

from orchid_api.settings import Settings, _YAML_TO_ENV, _apply_yaml_config


class TestSettings:
    def test_default_model(self):
        s = Settings()
        assert s.litellm_model == "ollama/llama3.2"

    def test_default_storage_class(self):
        s = Settings()
        assert s.chat_storage_class == "orchid_ai.persistence.sqlite.SQLiteChatStorage"

    def test_default_storage_dsn(self):
        s = Settings()
        assert s.chat_db_dsn == "~/.orchid/chats.db"

    def test_default_vector_backend(self):
        s = Settings()
        assert s.vector_backend == "qdrant"

    def test_default_agents_config_path(self):
        s = Settings()
        assert s.agents_config_path == "agents.yaml"

    def test_default_dev_auth_bypass(self):
        s = Settings()
        assert s.dev_auth_bypass is False

    def test_default_langsmith_tracing(self):
        s = Settings()
        assert s.langsmith_tracing is False

    def test_default_langsmith_project(self):
        s = Settings()
        assert s.langsmith_project == "agents"

    def test_default_upload_max_size(self):
        s = Settings()
        assert s.upload_max_size_mb == 20

    def test_default_chunk_settings(self):
        s = Settings()
        assert s.chunk_size == 1000
        assert s.chunk_overlap == 200


class TestApplyYamlConfig:
    def test_missing_file_is_silent(self):
        """Missing YAML file doesn't raise, just logs a warning."""
        with patch.dict(os.environ, {"ORCHID_CONFIG": "/nonexistent/path.yml"}, clear=False):
            _apply_yaml_config()  # should not raise

    def test_yaml_values_applied_to_env(self):
        """Values from orchid.yml are exported as env vars."""
        config = {
            "llm": {"model": "openai/gpt-4o"},
            "rag": {"vector_backend": "null"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            f.flush()
            # Clear any existing env vars that would take priority
            env_patch = {"ORCHID_CONFIG": f.name}
            for key in ("LITELLM_MODEL", "VECTOR_BACKEND"):
                env_patch[key] = ""
            with patch.dict(os.environ, env_patch, clear=False):
                # Remove the keys so YAML can set them
                os.environ.pop("LITELLM_MODEL", None)
                os.environ.pop("VECTOR_BACKEND", None)
                _apply_yaml_config()
                assert os.environ.get("LITELLM_MODEL") == "openai/gpt-4o"
                assert os.environ.get("VECTOR_BACKEND") == "null"
        os.unlink(f.name)

    def test_env_overrides_yaml(self):
        """Existing env vars are NOT overwritten by YAML."""
        config = {"llm": {"model": "should-not-apply"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            f.flush()
            with patch.dict(os.environ, {"ORCHID_CONFIG": f.name, "LITELLM_MODEL": "keep-this"}, clear=False):
                _apply_yaml_config()
                assert os.environ["LITELLM_MODEL"] == "keep-this"
        os.unlink(f.name)

    def test_unknown_yaml_keys_ignored(self):
        """YAML keys not in _YAML_TO_ENV are silently skipped."""
        config = {"unknown_section": {"unknown_key": "value"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            f.flush()
            with patch.dict(os.environ, {"ORCHID_CONFIG": f.name}, clear=False):
                _apply_yaml_config()  # should not raise
        os.unlink(f.name)


class TestYamlToEnvMapping:
    def test_all_sections_covered(self):
        """Verify the mapping covers expected sections."""
        sections = {k[0] for k in _YAML_TO_ENV.keys()}
        expected = {"agents", "llm", "auth", "startup", "rag", "upload", "storage", "mcp", "mcp_auth", "api", "tracing"}
        assert expected == sections

    def test_agents_config_path_mapped(self):
        assert _YAML_TO_ENV[("agents", "config_path")] == "AGENTS_CONFIG_PATH"

    def test_storage_class_mapped(self):
        assert _YAML_TO_ENV[("storage", "class")] == "CHAT_STORAGE_CLASS"
