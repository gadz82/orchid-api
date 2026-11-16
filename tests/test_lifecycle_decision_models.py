"""The API consumes decision configuration through the facade, not its own schema."""

from __future__ import annotations

from orchid_ai.config.loader import load_config


def test_api_config_loader_accepts_a_decision_provider(tmp_path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        """
agents:
  helper:
    description: "Answers questions."
    prompt: "You are a helper."
decision_models:
  stub:
    protocol: stub
"""
    )
    config = load_config(str(path))
    assert config.decision_models["stub"].protocol == "stub"
    assert config.decisions.resolve("routing") is None
