#!/usr/bin/env python3
"""Simple RQ queue monitor for auto-scaling Fly.io Machines.

This script watches the Redis queues used by the application and ensures
there are enough Fly Machines running to handle the load.  When a queue has
jobs pending it clones additional workers, and after a period of inactivity
it stops surplus machines to save resources.

Environment variables:
    REDIS_URL               Redis connection URL.
    TIER1_APP               Fly app name for Tier1 workers (default: midjau-worker-tier1).
    TIER2_APP               Fly app name for Tier2 workers (default: midjau-worker-tier2).
    TIER3_APP               Fly app name for Tier3 workers (default: midjau-worker-tier3).
    TIER1_TEMPLATE_MACHINE  Machine ID to clone for Tier1 workers.
    TIER2_TEMPLATE_MACHINE  Machine ID to clone for Tier2 workers.
    TIER3_TEMPLATE_MACHINE  Machine ID to clone for Tier3 workers.
    QUEUE_MONITOR_INTERVAL  Seconds between queue checks (default: 30).
    INACTIVITY_TIMEOUT      Seconds of empty queue before stopping workers (default: 300).
    MAX_WORKERS_PER_TIER    Upper limit of machines per tier (default: 3).
    JOBS_PER_WORKER         Estimated number of jobs a single worker handles at once
                            (default: 1).
    QUEUE_THRESHOLD         Queue length required to trigger scaling (default: 0).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from math import ceil

from redis import Redis
from rq import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TIERS = ["Tier1", "Tier2", "Tier3"]
TIER_APPS = {
    "Tier1": os.getenv("TIER1_APP", "midjau-worker-tier1"),
    "Tier2": os.getenv("TIER2_APP", "midjau-worker-tier2"),
    "Tier3": os.getenv("TIER3_APP", "midjau-worker-tier3"),
}
TIER_TEMPLATE = {
    "Tier1": os.getenv("TIER1_TEMPLATE_MACHINE"),
    "Tier2": os.getenv("TIER2_TEMPLATE_MACHINE"),
    "Tier3": os.getenv("TIER3_TEMPLATE_MACHINE"),
}
POLL_INTERVAL = int(os.getenv("QUEUE_MONITOR_INTERVAL", "30"))
INACTIVITY_TIMEOUT = int(os.getenv("INACTIVITY_TIMEOUT", "300"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS_PER_TIER", "3"))
JOBS_PER_WORKER = int(os.getenv("JOBS_PER_WORKER", "1"))
QUEUE_THRESHOLD = int(os.getenv("QUEUE_THRESHOLD", "0"))


def _run_flyctl(*args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a flyctl command and return the CompletedProcess.

    Any errors are logged but do not raise exceptions so the monitor keeps running.
    """
    cmd = ["fly"] + list(args)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result
    except Exception as exc:  # pragma: no cover - best effort logging
        print(f"flyctl command failed: {' '.join(cmd)} -> {exc}")
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stdout)
            print(exc.stderr)
        return None


def list_running_machines(app: str) -> list[dict]:
    """Return list of running machines for the Fly app."""
    result = _run_flyctl("machines", "list", "--app", app, "--json")
    if not result:
        return []
    try:
        machines = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        machines = []
    return [m for m in machines if m.get("state") in {"started", "created"}]


def clone_machine(app: str, template_id: str | None) -> None:
    """Clone a new machine from the template ID."""
    if not template_id:
        print(f"No template machine ID configured for {app}; cannot clone.")
        return
    _run_flyctl("machines", "clone", template_id, "--app", app)


def stop_machine(app: str, machine_id: str) -> None:
    """Stop a running Fly machine."""
    _run_flyctl("machines", "stop", machine_id, "--app", app)


def monitor() -> None:
    """Main monitoring loop."""
    redis_conn = Redis.from_url(REDIS_URL)
    last_activity = {tier: time.time() for tier in TIERS}

    while True:
        for tier in TIERS:
            queue = Queue(name=tier, connection=redis_conn)
            q_len = queue.count
            app = TIER_APPS[tier]
            running = list_running_machines(app)
            running_count = len(running)

            if q_len > QUEUE_THRESHOLD:
                last_activity[tier] = time.time()
                desired = min(MAX_WORKERS, ceil(q_len / JOBS_PER_WORKER))
                if running_count < desired:
                    for _ in range(desired - running_count):
                        clone_machine(app, TIER_TEMPLATE[tier])
            else:
                idle_for = time.time() - last_activity[tier]
                if running_count > 0 and idle_for > INACTIVITY_TIMEOUT:
                    for machine in running:
                        stop_machine(app, machine.get("id"))
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":  # pragma: no cover - manual run
    monitor()
