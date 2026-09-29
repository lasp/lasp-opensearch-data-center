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


def _name(days_old: float) -> str:
    """The name the handler gives a snapshot started `days_old` days before NOW"""
    return "os_snapshot_" + (NOW - timedelta(days=days_old)).strftime("%Y-%m-%d-%H:%M:%S")


def _snap(days_old: float, state: str = "SUCCESS", name: str = None) -> dict:
    """An entry as the non-verbose listing returns it: the name carries the start time"""
    return {"snapshot": name or _name(days_old), "state": state}


def _response(status: int = 200, body: dict = None) -> mock.Mock:
    response = mock.Mock(status_code=status, text="body")
    response.json.return_value = body or {}
    return response


def test_selects_only_expired_snapshots_oldest_first(handler_module):
    snapshots = [_snap(120), _snap(1), _snap(200), _snap(89)]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == [_name(200), _name(120)]


def test_keeps_newest_success_even_when_expired(handler_module):
    """A run of failures must never prune the repository down to nothing restorable."""
    snapshots = [_snap(300), _snap(200), _snap(150, state="FAILED")]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == [_name(300), _name(150)]


@pytest.mark.parametrize("name", [
    "manual-before-upgrade",
    "os_snapshot_manual",
    "my_snapshot_2026-01-01-00:00:00",  # same length as the handler's prefix, so only the prefix check rejects it
    "os_snapshot_2026-1-1-1:1:1",
    "os_snapshot_2026-01-01-00:00:00-copy",
    "os_snapshot_2026-01-01",
])
def test_ignores_snapshots_not_named_by_this_handler(handler_module, name):
    snapshots = [_snap(400, name=name), _snap(1)]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == []


def test_keeps_snapshots_at_the_cutoff(handler_module):
    assert handler_module.select_snapshots_to_delete([_snap(90), _snap(1)], NOW, 90) == []


def test_keeps_snapshots_with_no_recorded_state(handler_module):
    """The listing omits the state when the repository has none; such a snapshot must not be deleted blind."""
    snapshots = [{"snapshot": _name(200)}, _snap(1)]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == []


def test_a_future_dated_name_does_not_displace_the_newest_success(handler_module):
    snapshots = [_snap(-3650), _snap(200), _snap(150, state="FAILED")]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == [_name(150)]


def test_recognises_the_names_it_generates(handler_module):
    """The name the handler writes and the name pruning accepts must stay in step."""
    name = handler_module.snapshot_name(NOW)
    assert name == "os_snapshot_2026-09-28-09:00:00"
    assert handler_module.is_handler_snapshot_name(name)


def test_skips_in_progress_snapshots(handler_module):
    """Deleting an in-progress snapshot aborts it."""
    snapshots = [_snap(100, state="IN_PROGRESS"), _snap(1)]
    assert handler_module.select_snapshots_to_delete(snapshots, NOW, 90) == []


def test_caps_deletions_per_run(handler_module):
    snapshots = [_snap(100 + i) for i in range(50)] + [_snap(1)]
    selected = handler_module.select_snapshots_to_delete(snapshots, NOW, 90, max_deletions=5)
    assert selected == [_name(100 + i) for i in (49, 48, 47, 46, 45)]


def _prune(handler_module, listing: dict, delete_result=None):
    """Run pruning at NOW against a mocked listing, returning the result and the mocked requests"""
    delete_kwargs = {"side_effect": delete_result} if isinstance(delete_result, Exception) else \
        {"return_value": delete_result or _response()}
    with mock.patch.object(handler_module.requests, "get", return_value=_response(body=listing)) as get, \
            mock.patch.object(handler_module.requests, "delete", **delete_kwargs) as delete, \
            mock.patch.object(handler_module, "datetime", wraps=datetime) as fake_datetime:
        fake_datetime.now.return_value = NOW
        fake_datetime.strptime = datetime.strptime
        result = handler_module.prune_expired_snapshots(REPO_URL, 90)
    return result, get, delete


def test_prune_lists_without_reading_each_snapshot(handler_module):
    """A verbose listing fails outright when any snapshot's metadata file is missing."""
    _, get, _ = _prune(handler_module, {"snapshots": [_snap(1)]})
    assert get.call_args.args[0] == f"{REPO_URL}/_all"
    assert get.call_args.kwargs["params"]["verbose"] == "false"
    assert set(get.call_args.kwargs["params"]["filter_path"].split(",")) == {"snapshots.snapshot", "snapshots.state"}
    assert get.call_args.kwargs["timeout"] == 120


def test_prune_deletes_selected_snapshots_in_one_request(handler_module):
    deleted, _, delete = _prune(handler_module, {"snapshots": [_snap(100), _snap(200), _snap(1)]})
    assert deleted == [_name(200), _name(100)]
    delete.assert_called_once()
    assert delete.call_args.args[0] == f"{REPO_URL}/{_name(200)},{_name(100)}"
    # A deletion queued behind the day's snapshot does not answer for a long time, so the wait must be bounded
    assert delete.call_args.kwargs["timeout"] == 60


def test_prune_deletes_at_most_20_per_run(handler_module):
    deleted, _, delete = _prune(handler_module, {"snapshots": [_snap(100 + i) for i in range(30)] + [_snap(1)]})
    assert deleted == [_name(100 + i) for i in range(29, 9, -1)]
    delete.assert_called_once()


def test_prune_sends_no_delete_when_nothing_has_expired(handler_module):
    deleted, _, delete = _prune(handler_module, {"snapshots": [_snap(1)]})
    assert deleted == []
    delete.assert_not_called()


def test_prune_leaves_a_slow_deletion_running(handler_module):
    """The deletion is queued behind the snapshot just requested, so not finishing in time is expected."""
    deleted, _, _ = _prune(
        handler_module, {"snapshots": [_snap(100), _snap(1)]}, handler_module.requests.exceptions.ReadTimeout()
    )
    assert deleted == [_name(100)]


def test_prune_raises_when_it_cannot_connect_to_delete(handler_module):
    """A connect timeout means the deletion was never sent, so it must not be reported as running."""
    with pytest.raises(handler_module.requests.exceptions.ConnectTimeout):
        _prune(
            handler_module, {"snapshots": [_snap(100), _snap(1)]}, handler_module.requests.exceptions.ConnectTimeout()
        )


def test_prune_treats_an_already_deleted_snapshot_as_benign(handler_module):
    """Another deletion finishing first makes OpenSearch reject the batch; the next run selects the rest again."""
    deleted, _, _ = _prune(handler_module, {"snapshots": [_snap(100), _snap(1)]}, _response(status=404))
    assert deleted == []


def test_prune_leaves_a_deletion_the_endpoint_timed_out_running(handler_module):
    deleted, _, _ = _prune(handler_module, {"snapshots": [_snap(100), _snap(1)]}, _response(status=504))
    assert deleted == [_name(100)]


def test_prune_raises_when_a_delete_fails(handler_module):
    with pytest.raises(Exception, match=_name(100)):
        _prune(handler_module, {"snapshots": [_snap(100), _snap(1)]}, _response(status=500))


def test_prune_raises_when_the_listing_fails(handler_module):
    with mock.patch.object(handler_module.requests, "get", return_value=_response(status=500)):
        with pytest.raises(Exception, match="Listing snapshots failed"):
            handler_module.prune_expired_snapshots(REPO_URL, 90)


def _run_handler(handler_module):
    """Run the handler with its requests mocked, recording the order of the snapshot and pruning calls"""
    context = mock.Mock(get_remaining_time_in_millis=mock.Mock(return_value=900_000))
    calls = mock.Mock()
    calls.take.return_value = _response()
    with mock.patch.object(handler_module, "register_repo", return_value=_response()), \
            mock.patch.object(handler_module, "prune_expired_snapshots", calls.prune), \
            mock.patch.object(handler_module, "take_snapshot", calls.take):
        handler_module.handler({}, context)
    return calls


def test_handler_requests_the_snapshot_before_pruning(handler_module):
    """A deletion still running on the cluster must never be in the way of the day's snapshot."""
    calls = _run_handler(handler_module)
    assert [c[0] for c in calls.mock_calls] == ["take", "prune"]


def test_handler_reports_a_pruning_failure_after_taking_the_snapshot(handler_module):
    """Pruning is housekeeping; losing the day's snapshot to it would be worse than the failure itself."""
    context = mock.Mock(get_remaining_time_in_millis=mock.Mock(return_value=900_000))
    with mock.patch.object(handler_module, "register_repo", return_value=_response()), \
            mock.patch.object(handler_module, "prune_expired_snapshots", side_effect=RuntimeError("listing failed")), \
            mock.patch.object(handler_module, "take_snapshot", return_value=_response()) as take:
        with pytest.raises(RuntimeError, match="listing failed"):
            handler_module.handler({}, context)
    take.assert_called_once()


@pytest.mark.parametrize("days", ["0", "-5"])
def test_handler_refuses_a_retention_below_one_day_after_taking_the_snapshot(handler_module, monkeypatch, days):
    monkeypatch.setattr(handler_module, "snapshot_retention_days", days)
    context = mock.Mock(get_remaining_time_in_millis=mock.Mock(return_value=900_000))
    with mock.patch.object(handler_module, "register_repo", return_value=_response()), \
            mock.patch.object(handler_module, "prune_expired_snapshots") as prune, \
            mock.patch.object(handler_module, "take_snapshot", return_value=_response()) as take:
        with pytest.raises(ValueError, match="SNAPSHOT_RETENTION_DAYS"):
            handler_module.handler({}, context)
    take.assert_called_once()
    prune.assert_not_called()


def test_handler_prunes_with_the_configured_retention(handler_module):
    calls = _run_handler(handler_module)
    calls.prune.assert_called_once_with("https://search.example.com/_snapshot/repo", 90)


def test_handler_does_not_prune_when_the_snapshot_fails(handler_module):
    context = mock.Mock(get_remaining_time_in_millis=mock.Mock(return_value=900_000))
    with mock.patch.object(handler_module, "register_repo", return_value=_response()), \
            mock.patch.object(handler_module, "prune_expired_snapshots") as prune, \
            mock.patch.object(handler_module, "take_snapshot", return_value=_response(status=500)):
        with pytest.raises(Exception, match="500"):
            handler_module.handler({}, context)
    prune.assert_not_called()


def test_handler_skips_pruning_when_retention_is_unset(handler_module, monkeypatch):
    monkeypatch.setattr(handler_module, "snapshot_retention_days", "")
    calls = _run_handler(handler_module)
    calls.prune.assert_not_called()
    calls.take.assert_called_once()
