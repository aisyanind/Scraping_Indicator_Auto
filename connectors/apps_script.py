import json
import threading
import time
from typing import Any

import requests

from config import (
    APPS_SCRIPT_CONCURRENCY,
    APPS_SCRIPT_TOKEN,
    APPS_SCRIPT_URL,
    REQUEST_HEADERS,
)

_APPS_SEMAPHORE = threading.BoundedSemaphore(APPS_SCRIPT_CONCURRENCY)


MAX_RETRIES = 4
GET_TIMEOUT = 60
POST_TIMEOUT = 90


class AppsScriptError(Exception):
    """
    Error deterministik dari server (mis. Unknown action, Unauthorized,
    Job_ID tidak ditemukan). Tidak perlu di-retry.
    """


def _require_config():
    if not APPS_SCRIPT_URL:
        raise RuntimeError("APPS_SCRIPT_URL belum diisi di .env")

    if not APPS_SCRIPT_TOKEN:
        raise RuntimeError("APPS_SCRIPT_TOKEN belum diisi di .env")


def _headers():
    headers = dict(REQUEST_HEADERS)
    headers.update({
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    return headers


def _json_or_error(response: requests.Response) -> dict[str, Any]:
    text = (response.text or "").strip()

    try:
        data = response.json()
    except Exception:
        preview = text[:300].replace("\n", " ")
        raise RuntimeError(
            f"Apps Script tidak mengembalikan JSON. "
            f"HTTP {response.status_code}. Response: {preview}"
        )

    if not isinstance(data, dict):
        raise RuntimeError(
            f"Response Apps Script bukan object JSON: {type(data).__name__}"
        )

    return data


def _follow_contentservice_redirect(
    response: requests.Response,
    timeout: int,
) -> requests.Response:
    """
    Google Apps Script ContentService sering mengembalikan 302/303 ke
    script.googleusercontent.com.

    Redirect URL tersebut bersifat sementara. Kita follow satu kali secara
    manual. Kalau URL transient itu 404/timeout, caller akan retry ke URL
    Apps Script asli, bukan mengulang URL redirect yang sudah basi.
    """
    if response.status_code not in {301, 302, 303, 307, 308}:
        return response

    location = response.headers.get("Location", "").strip()

    if not location:
        return response

    redirected = requests.get(
        location,
        headers=_headers(),
        timeout=timeout,
        allow_redirects=True,
    )

    return redirected


def _request_json(
    method: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    timeout: int = 60,
) -> dict:
    _require_config()

    # Batasi panggilan Apps Script bersamaan agar tidak kena kuota.
    with _APPS_SEMAPHORE:
        return _request_json_unsafe(
            method,
            params=params,
            payload=payload,
            timeout=timeout,
        )


def _request_json_unsafe(
    method: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    timeout: int = 60,
) -> dict:

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method.upper() == "GET":
                response = requests.get(
                    APPS_SCRIPT_URL,
                    params=params,
                    headers=_headers(),
                    timeout=timeout,
                    allow_redirects=False,
                )
            elif method.upper() == "POST":
                response = requests.post(
                    APPS_SCRIPT_URL,
                    json=payload,
                    headers=_headers(),
                    timeout=timeout,
                    allow_redirects=False,
                )
            else:
                raise ValueError(f"HTTP method tidak didukung: {method}")

            response = _follow_contentservice_redirect(
                response,
                timeout=timeout,
            )

            response.raise_for_status()

            data = _json_or_error(response)

            if not data.get("success"):
                raise AppsScriptError(
                    data.get("error", "Apps Script mengembalikan success=false")
                )

            return data

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
            RuntimeError,
        ) as error:
            last_error = error

            if attempt >= MAX_RETRIES:
                break

            wait_seconds = min(2 ** (attempt - 1), 8)

            print(
                f"[APPS SCRIPT] Percobaan {attempt}/{MAX_RETRIES} gagal: "
                f"{str(error).splitlines()[0][:220]}"
            )
            print(
                f"[APPS SCRIPT] Retry dari URL utama dalam "
                f"{wait_seconds} detik..."
            )

            time.sleep(wait_seconds)

    raise RuntimeError(
        f"Apps Script gagal setelah {MAX_RETRIES} percobaan: {last_error}"
    )


def get_pending_jobs():
    data = _request_json(
        "GET",
        params={
            "action": "jobs",
            "token": APPS_SCRIPT_TOKEN,
            # cache-buster agar polling tidak menerima redirect/cache lama
            "_ts": str(int(time.time() * 1000)),
        },
        timeout=GET_TIMEOUT,
    )

    return data.get("data", [])


def get_existing_results(kode):
    data = _request_json(
        "GET",
        params={
            "action": "existing_results",
            "token": APPS_SCRIPT_TOKEN,
            "kode": str(kode or "").strip(),
            "_ts": str(int(time.time() * 1000)),
        },
        timeout=GET_TIMEOUT,
    )

    return data.get("data", [])


def update_job(
    job_id,
    status,
    progress=None,
    error="",
    diagnostics=None,
):
    return _request_json(
        "POST",
        payload={
            "action": "update_job",
            "token": APPS_SCRIPT_TOKEN,
            "job_id": job_id,
            "status": status,
            "progress": progress,
            "error": error,
            "diagnostics": diagnostics or [],
        },
        timeout=POST_TIMEOUT,
    )


def save_results(results):
    if not results:
        return {
            "success": True,
            "total": 0,
        }

    return _request_json(
        "POST",
        payload={
            "action": "save_results",
            "token": APPS_SCRIPT_TOKEN,
            "results": results,
        },
        timeout=120,
    )



def get_job_status(job_id):
    data = _request_json(
        "GET",
        params={
            "action": "job_status",
            "token": APPS_SCRIPT_TOKEN,
            "job_id": str(job_id),
            "_ts": str(int(time.time() * 1000)),
        },
        timeout=30,
    )

    return str(
        data.get("status", "")
    ).strip().upper()


def request_cancel(job_id):
    return _request_json(
        "POST",
        payload={
            "action": "cancel_job",
            "token": APPS_SCRIPT_TOKEN,
            "job_id": str(job_id),
        },
        timeout=POST_TIMEOUT,
    )


def send_heartbeat():
    try:
        _request_json(
            "GET",
            params={
                "action": "heartbeat",
                "token": APPS_SCRIPT_TOKEN,
                "_ts": str(int(time.time() * 1000)),
            },
            timeout=15,
        )
    except Exception as error:
        print(
            f"[HEARTBEAT] Gagal: "
            f"{str(error).splitlines()[0][:200]}"
        )
