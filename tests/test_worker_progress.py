import queue
from types import SimpleNamespace

from jobs import worker


def test_drain_progress_coalesces_messages_and_throttles_updates(monkeypatch):
    sent = []
    now = [100.0]
    progress_queue = queue.Queue()
    progress_state = {"last_sent": 90.0, "latest": None}

    monkeypatch.setattr(
        worker,
        "_mark_job",
        lambda job_id, status, **kwargs: sent.append(
            (job_id, status, kwargs)
        ),
    )
    monkeypatch.setattr(
        worker,
        "time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )

    progress_queue.put({"progress": 10, "message": "first"})
    progress_queue.put({"progress": 20, "message": "latest"})
    worker._drain_progress(progress_queue, "job-1", progress_state)

    assert sent == [
        ("job-1", "PROCESSING", {"progress": 20, "error": "latest"}),
    ]

    now[0] = 102.0
    progress_queue.put({"progress": 30, "message": "pending"})
    worker._drain_progress(progress_queue, "job-1", progress_state)

    assert len(sent) == 1
    assert progress_state["latest"] == {
        "progress": 30,
        "message": "pending",
    }

    now[0] = 105.0
    worker._drain_progress(progress_queue, "job-1", progress_state)

    assert sent[-1] == (
        "job-1",
        "PROCESSING",
        {"progress": 30, "error": "pending"},
    )
