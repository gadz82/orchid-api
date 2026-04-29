"""Tests for the bearer-token redacting log formatter wired in ``main.py``."""

from __future__ import annotations

import io
import logging

import pytest

from orchid_api.main import _RedactingFormatter


@pytest.fixture
def formatter():
    return _RedactingFormatter("%(message)s")


def _record(msg, *args):
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_redacts_bearer_in_message(formatter):
    out = formatter.format(_record("Authorization: Bearer eyJhbGciOi.fooBAR-_=.sigs"))
    assert "eyJhbGciOi.fooBAR-_=.sigs" not in out
    assert "Bearer ****" in out


def test_redacts_bearer_in_args(formatter):
    out = formatter.format(_record("got header %s", "Bearer abc123.def-456"))
    assert "abc123.def-456" not in out
    assert "Bearer ****" in out


def test_passthrough_when_no_token(formatter):
    out = formatter.format(_record("nothing to redact here %d", 42))
    assert out == "nothing to redact here 42"


def test_redacts_in_exception_traceback(formatter):
    try:
        raise RuntimeError("upstream said: Bearer leaked.token.value")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="boom",
            args=(),
            exc_info=sys.exc_info(),
        )
    out = formatter.format(record)
    assert "leaked.token.value" not in out
    assert "Bearer ****" in out


def test_case_insensitive(formatter):
    out = formatter.format(_record("bearer myJwt.signed.payload"))
    assert "myJwt.signed.payload" not in out
    assert "ear" in out.lower()  # ``Bearer ****`` survived


def test_redactor_is_installed_on_root_handlers():
    """``main.py`` must wire at least one root handler with the redactor so
    standard-logger output is scrubbed before reaching stdout/stderr.

    Pytest's own ``LogCaptureHandler`` is also attached to the root logger
    during the test run, so we look for *any* handler whose formatter is
    the redactor — not ``all``.
    """
    root = logging.getLogger()
    assert root.handlers, "main.py is expected to install at least one root handler"
    redacting = [h for h in root.handlers if isinstance(h.formatter, _RedactingFormatter)]
    assert redacting, (
        "no root handler is using _RedactingFormatter — main.py must install one. "
        f"Saw: {[(type(h).__name__, type(h.formatter).__name__) for h in root.handlers]}"
    )


def test_redactor_handles_dict_args():
    """``logger.info('%(x)s', {'x': 'Bearer leak'})`` should still get scrubbed.

    ``logging.LogRecord`` accepts ``args`` as either a tuple or a single
    mapping; testing through a real logger keeps that detail out of the
    test and exercises the full format path.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(_RedactingFormatter("%(message)s"))

    logger = logging.getLogger("orchid_api.tests.redactor_dict_args")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("msg=%(x)s", {"x": "Bearer leak.token"})

    out = buf.getvalue()
    assert "leak.token" not in out
    assert "Bearer ****" in out


def test_redactor_emits_through_real_handler():
    """End-to-end: bearer token must not survive through a configured handler."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(_RedactingFormatter("%(message)s"))

    logger = logging.getLogger("orchid_api.tests.redactor_real_handler")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("Authorization=Bearer secrettoken.value.123")

    out = buf.getvalue()
    assert "secrettoken.value.123" not in out
    assert "Bearer ****" in out
