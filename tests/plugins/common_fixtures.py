"""Generic fixtures without a specific home"""
# Installed
import pytest


@pytest.fixture(scope='session')
def monkeypatch_session():
    """Provides a monkeypatch that applies for an entire pytest session (saves time)"""
    from _pytest.monkeypatch import MonkeyPatch
    m = MonkeyPatch()
    yield m
    m.undo()
