import threading
from urllib.parse import urlparse


def extract_domain(url):
    try:
        parsed = urlparse(str(url or ""))
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def extract_domains_from_job(job):
    domains = set()
    for sumber in (job.sumber_1, job.sumber_2, job.sumber_3):
        domain = extract_domain(sumber)
        if domain:
            domains.add(domain)
    return domains


class ThreadDomainLimiter:
    def __init__(self, per_domain_limit=1):
        self._limit = per_domain_limit
        self._semaphores = {}
        self._lock = threading.Lock()

    def acquire(self, domain):
        if self._limit < 1:
            return
        sem = self._semaphores.get(domain)
        if sem is None:
            with self._lock:
                sem = self._semaphores.get(domain)
                if sem is None:
                    sem = threading.BoundedSemaphore(self._limit)
                    self._semaphores[domain] = sem
        sem.acquire(blocking=True)

    def release(self, domain):
        if self._limit < 1:
            return
        sem = self._semaphores.get(domain)
        if sem:
            sem.release()

    def __call__(self, url):
        return _LimiterContext(self, extract_domain(url))


class _LimiterContext:
    def __init__(self, limiter, domain):
        self.limiter = limiter
        self.domain = domain

    def __enter__(self):
        if self.domain:
            self.limiter.acquire(self.domain)
        return self

    def __exit__(self, *args):
        if self.domain:
            self.limiter.release(self.domain)


class DomainGate:
    """
    Gate non-blocking antar-job: pastikan tidak lebih dari `per_domain_limit`
    job yang memegang domain yang sama secara bersamaan.

    Berbeda dengan ThreadDomainLimiter (blocking semaphore untuk intra-job),
    gate ini mengembalikan False bila domain sudah penuh, sehingga job bisa
    ditunda ke polling berikutnya tanpa membuang thread.
    """

    def __init__(self, per_domain_limit=1):
        self._limit = per_domain_limit
        self._counts = {}
        self._lock = threading.Lock()

    def acquire(self, domains):
        if self._limit < 1 or not domains:
            return True

        domains = set(domains)

        with self._lock:
            for domain in domains:
                if self._counts.get(domain, 0) >= self._limit:
                    return False

            for domain in domains:
                self._counts[domain] = (
                    self._counts.get(domain, 0) + 1
                )

            return True

    def release(self, domains):
        if self._limit < 1 or not domains:
            return

        domains = set(domains)

        with self._lock:
            for domain in domains:
                if domain not in self._counts:
                    continue

                self._counts[domain] -= 1

                if self._counts[domain] <= 0:
                    del self._counts[domain]