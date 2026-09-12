"""Temporary E2E probe: submits a job and polls it, logging to /tmp/e2e_progress.txt."""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
LOG = "/tmp/e2e_progress.txt"


def log(line: str) -> None:
    with open(LOG, "a") as f:
        f.write(line + "\n")


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


def post(path: str, payload: dict):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def retry(fn, attempts=30, delay=5):
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - probe helper
            last = e
            time.sleep(delay)
    raise last


if __name__ == "__main__":
    job = retry(lambda: post("/api/jobs", {"youtube_url": URL}))
    job_id = job["id"]
    log(f"job_id={job_id}")
    deadline = time.time() + 60 * 20  # 20 min cap
    while time.time() < deadline:
        d = get(f"/api/jobs/{job_id}")
        log(
            f"{time.strftime('%H:%M:%S')} status={d['status']} msg={d.get('progress_message') or ''} clips={len(d['clips'])}"
        )
        if d["status"] in ("done", "failed"):
            log("=== FINAL ===")
            log(json.dumps(d, indent=2))
            break
        time.sleep(10)
    log("DONE-POLLING")
    sys.exit(0)