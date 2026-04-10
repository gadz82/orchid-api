"""Tests for orchid_api.tracing — LangSmith configuration."""

from __future__ import annotations

import os
from unittest.mock import patch

from orchid_api.tracing import configure_tracing


class TestConfigureTracing:
    def test_disabled_clears_env(self):
        """Disabled tracing removes LANGCHAIN_TRACING_V2."""
        with patch.dict(os.environ, {"LANGCHAIN_TRACING_V2": "true"}, clear=False):
            configure_tracing(enabled=False, api_key="key")
            assert "LANGCHAIN_TRACING_V2" not in os.environ

    def test_disabled_when_no_api_key(self):
        """Tracing is disabled even if enabled=True but no API key."""
        with patch.dict(os.environ, {}, clear=False):
            configure_tracing(enabled=True, api_key="")
            assert os.environ.get("LANGCHAIN_TRACING_V2") is None

    def test_enabled_sets_env_vars(self):
        """Enabled tracing sets all required env vars."""
        with patch.dict(os.environ, {}, clear=False):
            configure_tracing(enabled=True, api_key="lsv2_test", project="my-project")
            assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
            assert os.environ["LANGCHAIN_API_KEY"] == "lsv2_test"
            assert os.environ["LANGCHAIN_PROJECT"] == "my-project"

    def test_default_project_name(self):
        """Default project is 'orchid'."""
        with patch.dict(os.environ, {}, clear=False):
            configure_tracing(enabled=True, api_key="key")
            assert os.environ["LANGCHAIN_PROJECT"] == "orchid"
