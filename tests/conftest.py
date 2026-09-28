"""Shared test settings: a synthetic ``[tool.outlooks]`` config for every test.

The suite must run the same way from any directory, so every test gets a
fixed, fictitious mailbox and archive location via ``OUTLOOKS_*`` env vars
rather than whichever ``pyproject.toml`` encloses it. A test that needs a
different value (its own mailbox, its own archive root, no mailbox at all)
overrides it with the same ``monkeypatch`` fixture — this fixture and the
test body share one ``monkeypatch`` instance, so an explicit ``setenv``/
``delenv`` later in the test wins.
"""

from __future__ import annotations

import pytest

MAILBOX = "desk@example.org"
OWN_ADDRESSES = "desk@example.org,events@example.org"
SYSTEM_SENDERS = "notices@system.example.org"
DECISION_TAG = "[Decision]"
TIMEZONE = "America/Los_Angeles"


@pytest.fixture(autouse=True)
def outlooks_settings(monkeypatch, tmp_path):
    """Pin ``[tool.outlooks]`` for every test via ``OUTLOOKS_*`` env vars.

    Archive, capture, and scratch directories default to this test's own
    ``tmp_path``, so no test touches a real archive or a real repo's
    ``pyproject.toml`` table by accident. The working directory is
    ``tmp_path`` too: a key with no env var here (``profile``) and the root
    relative paths resolve against would otherwise come from whichever
    ``pyproject.toml`` encloses the directory the suite is run from.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OUTLOOKS_MAILBOX", MAILBOX)
    monkeypatch.setenv("OUTLOOKS_OWN_ADDRESSES", OWN_ADDRESSES)
    monkeypatch.setenv("OUTLOOKS_SYSTEM_SENDERS", SYSTEM_SENDERS)
    monkeypatch.setenv("OUTLOOKS_DECISION_TAG", DECISION_TAG)
    monkeypatch.setenv("OUTLOOKS_TIMEZONE", TIMEZONE)
    monkeypatch.setenv("OUTLOOKS_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("OUTLOOKS_CAPTURED_DIR", str(tmp_path / "captured"))
    monkeypatch.setenv("OUTLOOKS_SCRATCH_DIR", str(tmp_path / "scratch"))
    monkeypatch.delenv("OUTLOOKS_PROFILE", raising=False)
    monkeypatch.delenv("OUTLOOKS_RENDER_LABEL", raising=False)
    # Where Claude Code would save an oversized result: a spill note is
    # followed only into this directory's projects/.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    yield
