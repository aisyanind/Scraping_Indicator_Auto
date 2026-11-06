import json

import pytest

from connectors import apps_script


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload
        self.text = json.dumps(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_apps_script_retries_lock_timeout(monkeypatch):
    responses = iter([
        FakeResponse({
            "success": False,
            "error": (
                "Exception: Batas waktu penguncian habis: "
                "proses lain menahan kunci terlalu lama."
            ),
        }),
        FakeResponse({"success": True}),
    ])
    sleeps = []

    monkeypatch.setattr(apps_script, "APPS_SCRIPT_URL", "https://example.test")
    monkeypatch.setattr(
        apps_script.requests,
        "post",
        lambda *_args, **_kwargs: next(responses),
    )
    monkeypatch.setattr(apps_script.time, "sleep", sleeps.append)

    result = apps_script._request_json_unsafe(
        "POST",
        payload={"action": "update_job"},
    )

    assert result == {"success": True}
    assert sleeps == [1]


def test_apps_script_does_not_retry_permanent_server_errors(monkeypatch):
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(True)
        return FakeResponse({
            "success": False,
            "error": "Unauthorized",
        })

    monkeypatch.setattr(apps_script, "APPS_SCRIPT_URL", "https://example.test")
    monkeypatch.setattr(apps_script.requests, "post", fake_post)
    monkeypatch.setattr(
        apps_script.time,
        "sleep",
        lambda _seconds: pytest.fail("Permanent errors must not be retried"),
    )

    with pytest.raises(apps_script.AppsScriptError, match="Unauthorized"):
        apps_script._request_json_unsafe(
            "POST",
            payload={"action": "update_job"},
        )

    assert len(calls) == 1
