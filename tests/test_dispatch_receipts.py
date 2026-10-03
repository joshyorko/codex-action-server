import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


def test_receipt_reservation_survives_restart_and_does_not_reexecute(tmp_path):
    import dispatch_receipts as d

    with d.reserve(
        tmp_path, "job-1", {"target": "local", "text": "secret prompt"}
    ) as receipt:
        assert not receipt.replayed
        receipt.update(state="accepted", thread_id="t1", turn_id="u1")
    with d.reserve(
        tmp_path, "job-1", {"target": "local", "text": "secret prompt"}
    ) as receipt:
        assert receipt.replayed
        assert receipt.data["turn_id"] == "u1"
    assert "secret prompt" not in "".join(
        p.read_text() for p in tmp_path.glob("*.json")
    )


def test_reused_key_with_other_payload_fails(tmp_path):
    import dispatch_receipts as d

    with d.reserve(tmp_path, "job-1", {"text": "a"}):
        pass
    with pytest.raises(ValueError, match="request_id_conflict"):
        with d.reserve(tmp_path, "job-1", {"text": "b"}):
            pass


def test_unfinished_receipt_never_authorizes_retry(tmp_path):
    import dispatch_receipts as d

    with d.reserve(tmp_path, "job-1", {"text": "a"}):
        pass
    with d.reserve(tmp_path, "job-1", {"text": "a"}) as receipt:
        assert receipt.replayed
        assert receipt.data["state"] == "unknown"


def test_lock_prevents_concurrent_same_key_execution(tmp_path):
    import dispatch_receipts as d

    with d.reserve(tmp_path, "job-1", {"text": "a"}):
        with d.reserve(tmp_path, "job-1", {"text": "a"}) as receipt:
            assert receipt.replayed
            assert receipt.data["state"] == "in_progress"
