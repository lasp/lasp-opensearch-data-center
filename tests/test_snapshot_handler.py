"""Tests for the snapshot Lambda's retention logic"""
# Standard
from datetime import datetime, timedelta, timezone
import importlib
import sys
from pathlib import Path
from unittest import mock
# Installed
import pytest

# The Lambda runtime is a separate package that is not installed alongside the construct library
sys.path.insert(0, str(Path(__file__).parent.parent / "lasp_opensearch_data_center" / "lambda"))

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
REPO_URL = "https://search.example.com/_snapshot/repo"


@pytest.fixture
def handler_module(monkeypatch):
    """Import the handler with the environment it reads at import time"""
    monkeypatch.setenv("OPEN_SEARCH_ENDPOINT", "https://search.example.com/")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.setenv("SNAPSHOT_REPO_NAME", "repo")
    monkeypatch.setenv("SNAPSHOT_S3_BUCKET", "bucket")
    monkeypatch.setenv("SNAPSHOT_ROLE_ARN", "arn:aws:iam::123456789012:role/snapshot")
    monkeypatch.setenv("SNAPSHOT_RETENTION_DAYS", "90")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    module = importlib.import_module("opensearch_data_center_lambda_runtime.snapshot_handler")
    return importlib.reload(module)


def _snap(name: str, days_old: float, state: str = "SUCCESS") -> dict:
    started = NOW - timedelta(days=days_old)
    return {"snapshot": name, "state": state, "start_time_in_millis": int(started.timestamp() * 1000)}


def _response(status: int = 200, body: dict = None) -> mock.Mock:
    response = mock.Mock(status_code=status, text="body")
    response.json.return_value = body or {}
    return response


def test_selects_only_expired_snapshots_oldest_first(handler_module):
    snapshots = [
        _snap("os_snapshot_b", 120),
        _snap("os_snapshot_new", 1),
        _snap("os_snapshot_a", 200),
        _snap("os_snapshot_edge", 89),
    ]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == ["os_snapshot_a", "os_snapshot_b"]


def test_keeps_newest_success_even_when_expired(handler_module):
    """A run of failures must never prune the repository down to nothing restorable."""
    snapshots = [
        _snap("os_snapshot_old", 300),
        _snap("os_snapshot_last_good", 200),
        _snap("os_snapshot_failed", 150, state="FAILED"),
    ]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == ["os_snapshot_old", "os_snapshot_failed"]


def test_ignores_snapshots_not_taken_by_this_handler(handler_module):
    snapshots = [_snap("manual-before-upgrade", 400), _snap("os_snapshot_new", 1)]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == []


def test_skips_in_progress_snapshots(handler_module):
    snapshots = [_snap("os_snapshot_running", 100, state="IN_PROGRESS"), _snap("os_snapshot_new", 1)]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == []


def test_caps_deletions_per_run(handler_module):
    snapshots = [_snap(f"os_snapshot_{i:03d}", 100 + i) for i in range(50)] + [_snap("os_snapshot_new", 1)]
    selected = handler_module.select_snapshots_to_delete(snapshots, NOW, 90, max_deletions=5)
    assert selected == [f"os_snapshot_{i:03d}" for i in (49, 48, 47, 46, 45)]


def test_prune_deletes_selected_snapshots(handler_module):
    listing = {"snapshots": [_snap("os_snapshot_old", 100), _snap("os_snapshot_new", 1)]}
    with mock.patch.object(handler_module.requests, "get", return_value=_response(body=listing)) as get, \
            mock.patch.object(handler_module.requests, "delete", return_value=_response()) as delete, \
            mock.patch.object(handler_module, "datetime", wraps=datetime) as fake_datetime:
        fake_datetime.now.return_value = NOW
        deleted = handler_module.prune_expired_snapshots(REPO_URL, 90, lambda: 900)
    assert deleted == ["os_snapshot_old"]
    assert get.call_args.args[0] == f"{REPO_URL}/_all"
    assert delete.call_args.args[0] == f"{REPO_URL}/os_snapshot_old"


def test_prune_stops_before_the_reserved_time(handler_module):
    """No deletion may start, or be allowed to run, into the time reserved for taking the snapshot."""
    listing = {"snapshots": [_snap(f"os_snapshot_{i}", 100 + i) for i in range(3)] + [_snap("os_snapshot_new", 1)]}
    # One reading for the listing, then one before each delete
    remaining = iter([900, 900, handler_module.RESERVED_SECONDS_FOR_SNAPSHOT])
    with mock.patch.object(handler_module.requests, "get", return_value=_response(body=listing)), \
            mock.patch.object(handler_module.requests, "delete", return_value=_response()) as delete, \
            mock.patch.object(handler_module, "datetime", wraps=datetime) as fake_datetime:
        fake_datetime.now.return_value = NOW
        deleted = handler_module.prune_expired_snapshots(REPO_URL, 90, lambda: next(remaining))
    assert deleted == ["os_snapshot_2"]
    assert delete.call_count == 1
    assert delete.call_args.kwargs["timeout"] == 900 - handler_module.RESERVED_SECONDS_FOR_SNAPSHOT


def test_prune_bounds_the_listing_by_the_reserved_time(handler_module):
    """A slow listing on a large repository must not consume the time reserved for the snapshot."""
    listing = {"snapshots": [_snap("os_snapshot_new", 1)]}
    with mock.patch.object(handler_module.requests, "get", return_value=_response(body=listing)) as get:
        handler_module.prune_expired_snapshots(REPO_URL, 90, lambda: 900)
    assert get.call_args.kwargs["timeout"] == 900 - handler_module.RESERVED_SECONDS_FOR_SNAPSHOT

    with mock.patch.object(handler_module.requests, "get") as get:
        assert handler_module.prune_expired_snapshots(
            REPO_URL, 90, lambda: handler_module.RESERVED_SECONDS_FOR_SNAPSHOT
        ) == []
    get.assert_not_called()


def test_prune_raises_when_a_delete_fails(handler_module):
    listing = {"snapshots": [_snap("os_snapshot_old", 100), _snap("os_snapshot_new", 1)]}
    with mock.patch.object(handler_module.requests, "get", return_value=_response(body=listing)), \
            mock.patch.object(handler_module.requests, "delete", return_value=_response(status=500)), \
            mock.patch.object(handler_module, "datetime", wraps=datetime) as fake_datetime:
        fake_datetime.now.return_value = NOW
        with pytest.raises(Exception, match="os_snapshot_old"):
            handler_module.prune_expired_snapshots(REPO_URL, 90, lambda: 900)


def test_handler_takes_the_snapshot_even_when_pruning_fails(handler_module):
    """Pruning is housekeeping; losing the day's snapshot to it would be worse than the failure itself."""
    context = mock.Mock(get_remaining_time_in_millis=mock.Mock(return_value=900_000))
    with mock.patch.object(handler_module, "register_repo", return_value=_response()), \
            mock.patch.object(handler_module, "prune_expired_snapshots", side_effect=RuntimeError("listing failed")), \
            mock.patch.object(handler_module, "take_snapshot", return_value=_response()) as take:
        with pytest.raises(RuntimeError, match="listing failed"):
            handler_module.handler({}, context)
    take.assert_called_once()


def test_handler_skips_pruning_when_retention_is_unset(handler_module, monkeypatch):
    monkeypatch.setattr(handler_module, "snapshot_retention_days", "")
    context = mock.Mock(get_remaining_time_in_millis=mock.Mock(return_value=900_000))
    with mock.patch.object(handler_module, "register_repo", return_value=_response()), \
            mock.patch.object(handler_module, "prune_expired_snapshots") as prune, \
            mock.patch.object(handler_module, "take_snapshot", return_value=_response()) as take:
        handler_module.handler({}, context)
    prune.assert_not_called()
    take.assert_called_once()
