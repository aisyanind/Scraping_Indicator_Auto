import multiprocessing as mp
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from config import (
    JOB_TIMEOUT_SECONDS,
    PER_DOMAIN_LIMIT,
    POLL_SECONDS,
    WORKER_CONCURRENCY,
)
from connectors.apps_script import (
    get_job_status,
    get_pending_jobs,
    save_results,
    send_heartbeat,
    update_job,
)
from engine.domain_limiter import (
    DomainGate,
    extract_domains_from_job,
)
from engine.run_indicator import process_indicator_job
from models import IndicatorJob


CANCEL_POLL_SECONDS = 2


def _job_child(raw_job, output_queue):
    """
    Scraping dijalankan di child process.

    Keuntungan:
    jika user menekan Batalkan, parent worker dapat menghentikan
    child process tanpa menunggu request/PDF/Selenium selesai.
    """
    try:
        job = IndicatorJob.from_dict(
            raw_job
        )

        output = process_indicator_job(
            job
        )

        output_queue.put({
            "ok": True,
            "output": output,
        })

    except Exception as error:
        output_queue.put({
            "ok": False,
            "error": (
                f"{type(error).__name__}: "
                f"{str(error).splitlines()[0][:500]}"
            ),
            "traceback": traceback.format_exc(),
        })


def _safe_job_status(job_id):
    try:
        return get_job_status(
            job_id
        )
    except Exception as error:
        print(
            f"[CANCEL CHECK WARNING] "
            f"Tidak dapat membaca status job: {error}"
        )
        return ""


def _terminate_process(process):
    if not process.is_alive():
        return

    process.terminate()
    process.join(timeout=5)

    if process.is_alive():
        try:
            process.kill()
        except Exception:
            pass

        process.join(timeout=3)


# Registry child process yang sedang berjalan supaya bisa dihentikan
# saat worker berhenti (KeyboardInterrupt) atau saat terjadi error.
_ACTIVE_PROCESSES = set()
_PROCESSES_LOCK = threading.Lock()


def _register_process(process):
    with _PROCESSES_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister_process(process):
    with _PROCESSES_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def _terminate_all_processes():
    with _PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES)

    for process in processes:
        try:
            _terminate_process(process)
        except Exception as error:
            print(
                f"[WORKER] Gagal menghentikan child process: {error}"
            )


def _mark_job(job_id, status, progress=None, error="", diagnostics=None):
    """
    Update status job ke Apps Script, best-effort.

    Jika Apps Script tidak bisa dijangkau, jangan biarkan exception
    menggagalkan alur worker; job tetap dicoba ditandai FAILED.
    """
    try:
        update_job(
            job_id,
            status,
            progress=progress,
            error=error,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        print(
            f"[JOB] {job_id} -> GAGAL update status "
            f"{status}: {str(exc).splitlines()[0][:300]}"
        )


def process_one(raw_job):
    job = IndicatorJob.from_dict(
        raw_job
    )

    print(
        f"\n[JOB] {job.job_id} | "
        f"[{job.kode}] {job.nama_indikator}"
    )

    # Bisa saja job sudah dicancel setelah queue dibaca tetapi
    # sebelum worker mulai memproses.
    initial_status = _safe_job_status(
        job.job_id
    )

    if initial_status == "CANCELLED":
        print(
            f"[JOB] {job.job_id} -> CANCELLED "
            "(sebelum diproses)"
        )
        return

    try:
        update_job(
            job.job_id,
            "PROCESSING",
            progress=5,
        )
    except Exception as error:
        print(
            f"[JOB] {job.job_id} -> Gagal set PROCESSING: "
            f"{str(error).splitlines()[0][:300]}"
        )
        return

    output_queue = mp.Queue()

    process = mp.Process(
        target=_job_child,
        args=(
            raw_job,
            output_queue,
        ),
        daemon=False,
    )

    _register_process(process)
    process.start()

    cancel_requested = False
    timed_out = False
    start_time = time.monotonic()

    try:
        while process.is_alive():
            time.sleep(
                CANCEL_POLL_SECONDS
            )

            # Job yang menggantung tidak boleh mengunci thread worker
            # dan lease domain selamanya.
            if (
                time.monotonic() - start_time
                > JOB_TIMEOUT_SECONDS
            ):
                timed_out = True

                print(
                    f"[JOB] {job.job_id} -> timeout "
                    f"{JOB_TIMEOUT_SECONDS}s. "
                    "Menghentikan scraper..."
                )

                _terminate_process(
                    process
                )

                break

            status = _safe_job_status(
                job.job_id
            )

            if status in {
                "CANCEL_REQUESTED",
                "CANCELLED",
            }:
                cancel_requested = True

                print(
                    f"[JOB] {job.job_id} "
                    "-> pembatalan diterima. "
                    "Menghentikan scraper..."
                )

                _terminate_process(
                    process
                )

                break

    finally:
        process.join(timeout=2)
        _unregister_process(process)

    if timed_out:
        _mark_job(
            job.job_id,
            "FAILED",
            progress=100,
            error=(
                f"Job melebihi batas waktu "
                f"{JOB_TIMEOUT_SECONDS} detik."
            ),
        )

        print(
            f"[JOB] {job.job_id} -> FAILED (timeout)"
        )
        return

    if cancel_requested:
        _mark_job(
            job.job_id,
            "CANCELLED",
            progress=100,
            error="Dibatalkan pengguna.",
        )

        print(
            f"[JOB] {job.job_id} -> CANCELLED"
        )
        return

    # Cek terakhir untuk mencegah race:
    # user bisa klik cancel tepat ketika child selesai.
    final_remote_status = _safe_job_status(
        job.job_id
    )

    if final_remote_status in {
        "CANCEL_REQUESTED",
        "CANCELLED",
    }:
        _mark_job(
            job.job_id,
            "CANCELLED",
            progress=100,
            error="Dibatalkan pengguna.",
        )

        print(
            f"[JOB] {job.job_id} -> CANCELLED"
        )
        return

    try:
        child_result = output_queue.get_nowait()

    except queue.Empty:
        _mark_job(
            job.job_id,
            "FAILED",
            progress=100,
            error=(
                "Scraper berhenti tanpa mengembalikan hasil."
            ),
        )

        print(
            f"[JOB] {job.job_id} -> FAILED "
            "(child process no result)"
        )
        return

    if not child_result.get("ok"):
        error = child_result.get(
            "error",
            "Unknown scraper error",
        )

        _mark_job(
            job.job_id,
            "FAILED",
            progress=100,
            error=error,
        )

        print(
            f"[JOB] {job.job_id} -> FAILED | {error}"
        )
        return

    output = child_result["output"]

    # Cek sekali lagi SEBELUM save_results.
    before_save_status = _safe_job_status(
        job.job_id
    )

    if before_save_status in {
        "CANCEL_REQUESTED",
        "CANCELLED",
    }:
        _mark_job(
            job.job_id,
            "CANCELLED",
            progress=100,
            error="Dibatalkan pengguna sebelum hasil disimpan.",
        )

        print(
            f"[JOB] {job.job_id} -> CANCELLED"
        )
        return

    try:
        if output["results"]:
            save_results(
                output["results"]
            )

        final_status = output["status"]

        _mark_job(
            job.job_id,
            final_status,
            progress=100,
            diagnostics=output["diagnostics"],
        )

    except Exception as error:
        # Gagal simpan hasil tidak boleh membiarkan job menggantung
        # di status PROCESSING.
        _mark_job(
            job.job_id,
            "FAILED",
            progress=100,
            error=(
                f"Gagal menyimpan hasil: "
                f"{str(error).splitlines()[0][:300]}"
            ),
        )

        print(
            f"[JOB] {job.job_id} -> FAILED (penyimpanan): "
            f"{str(error).splitlines()[0][:300]}"
        )
        return

    print(
        f"[JOB] {job.job_id} -> "
        f"{final_status} | "
        f"results={len(output['results'])}"
    )

    if final_status != "SUCCESS":
        for diagnostic in output["diagnostics"][:5]:
            message = str(
                diagnostic.get("message") or diagnostic.get("status") or ""
            ).splitlines()[0][:200]
            print(
                f"    [DIAG] tahun={diagnostic.get('tahun')} "
                f"sumber={diagnostic.get('source')} "
                f"status={diagnostic.get('status')} "
                f"confidence={diagnostic.get('confidence')} | "
                f"{message}"
            )


def run_worker():
    mp.freeze_support()

    print(
        "AUTO SCRAPER WORKER aktif. "
        f"Polling tiap {POLL_SECONDS} detik."
    )

    gate = DomainGate(PER_DOMAIN_LIMIT)

    while True:
        try:
            send_heartbeat()

            jobs = get_pending_jobs()

            if jobs:
                print(
                    f"[QUEUE] {len(jobs)} job PENDING."
                )

            accepted = []
            deferred = []

            for raw_job in jobs:
                job = IndicatorJob.from_dict(
                    raw_job
                )
                domains = extract_domains_from_job(
                    job
                )

                if gate.acquire(domains):
                    accepted.append(
                        (raw_job, domains)
                    )
                else:
                    deferred.append(
                        (raw_job, domains)
                    )

            if deferred:
                print(
                    f"[QUEUE] {len(deferred)} job ditunda "
                    "(domain sedang dipakai job lain)."
                )

            with ThreadPoolExecutor(
                max_workers=max(
                    1,
                    WORKER_CONCURRENCY,
                )
            ) as pool:
                futures = [
                    pool.submit(
                        _run_job_guarded,
                        raw_job,
                        gate,
                        domains,
                    )
                    for raw_job, domains in accepted
                ]

                for future in futures:
                    future.result()

        except KeyboardInterrupt:
            print(
                "\nWorker dihentikan. "
                "Menghentikan proses scraper aktif..."
            )
            _terminate_all_processes()
            break

        except Exception as error:
            print(
                f"[WORKER ERROR] {error}"
            )

        time.sleep(POLL_SECONDS)


def _run_job_guarded(raw_job, gate, domains):
    try:
        process_one(raw_job)
    finally:
        gate.release(domains)


if __name__ == "__main__":
    run_worker()
