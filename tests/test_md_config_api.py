"""Tests for MD config integration with orchid-api."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import yaml

from orchid_api.settings import Settings, _apply_yaml_config


class TestSettingsMD:
    def test_orchid_config_format_defaults_to_auto(self):
        s = Settings()
        assert s.orchid_config_format == "auto"

    def test_orchid_reload_interval_defaults_to_30(self):
        s = Settings()
        assert s.orchid_reload_interval == 30

    def test_orchid_config_format_from_env(self):
        with patch.dict(os.environ, {"ORCHID_CONFIG_FORMAT": "md"}, clear=False):
            s = Settings()
            assert s.orchid_config_format == "md"

    def test_reload_interval_from_env(self):
        with patch.dict(os.environ, {"ORCHID_RELOAD_INTERVAL": "60"}, clear=False):
            s = Settings()
            assert s.orchid_reload_interval == 60

    def test_reload_interval_zero_disabled(self):
        with patch.dict(os.environ, {"ORCHID_RELOAD_INTERVAL": "0"}, clear=False):
            s = Settings()
            assert s.orchid_reload_interval == 0


class TestApplyYamlConfigMD:
    def test_md_file_skipped_does_not_set_env(self):
        """_apply_yaml_config skips .md files — no env vars set from YAML."""
        config = {"llm": {"model": "should-not-apply"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            yaml.dump(config, f)
            f.flush()
            os.environ.pop("LITELLM_MODEL", None)
            with patch.dict(os.environ, {"ORCHID_CONFIG": f.name}, clear=False):
                _apply_yaml_config()
                assert "LITELLM_MODEL" not in os.environ
        os.unlink(f.name)

    def test_yml_file_applies_env(self):
        """_apply_yaml_config processes .yml files."""
        config = {"llm": {"model": "test-from-yml"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            f.flush()
            os.environ.pop("LITELLM_MODEL", None)
            with patch.dict(os.environ, {"ORCHID_CONFIG": f.name}, clear=False):
                _apply_yaml_config()
                assert os.environ.get("LITELLM_MODEL") == "test-from-yml"
        os.unlink(f.name)
