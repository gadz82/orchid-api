"""Backwards-compatible re-export — the real implementation lives in
:mod:`orchid_ai.events.bootstrap` so both ``orchid-api`` and
``orchid-cli`` consume the same wiring helper.

Existing imports of ``orchid_api.events_bootstrap.start_events`` etc.
keep working unchanged.
"""

from __future__ import annotations

from orchid_ai.events.bootstrap import EventsRuntime, build_signal_source_registry, start_events, stop_events

__all__ = ["EventsRuntime", "build_signal_source_registry", "start_events", "stop_events"]
