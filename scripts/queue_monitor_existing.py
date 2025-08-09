#!/usr/bin/env python3
"""Monitor Redis queues and manage existing Fly.io machines.

This script watches RQ queues and ensures a pool of pre-created Fly.io
Machines are started when work arrives.

Environment variables:
    REDIS_URL              Redis connection URL.
    POLL_INTERVAL_SEC      Seconds between queue checks (default: 30).
    MAX_RUNNING_PER_TIER   Maximum running machines per tier (default: 1).

Tier specific variables (repeat for tiers 1-3):
    TIER{N}_APP            Fly app name (default: midjau-worker-tier{N}).
    TIER{N}_MACHINE_IDS    Comma separated machine IDs for the tier.
    TIER{N}_QUEUE_NAME     RQ queue name (default: Tier{N}).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Dict, Tuple

from redis import Redis
from rq import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "30"))
MAX_RUNNING = int(os.getenv("MAX_RUNNING_PER_TIER", "1"))


def _run_flyctl(*args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a flyctl command and return the CompletedProcess."""
    cmd = ["fly"] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True)
    except Exception as exc:  # pragma: no cover - best effort logging
        print(f"flyctl command failed: {' '.join(cmd)} -> {exc}")
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stdout)
            print(exc.stderr)
        return None


def load_tiers() -> list[dict]:
    """Load tier configuration from environment variables."""
    tiers = []
    for i in range(1, 4):
        app = os.getenv(f"TIER{i}_APP", f"midjau-worker-tier{i}")
        machine_ids = [m.strip() for m in os.getenv(f"TIER{i}_MACHINE_IDS", "").split(",") if m.strip()]
        queue_name = os.getenv(f"TIER{i}_QUEUE_NAME", f"Tier{i}")
        if not machine_ids:
            continue
        tiers.append({
            "name": f"Tier{i}",
            "app": app,
            "machines": machine_ids,
            "queue": queue_name,
            "next_index": 0,
        })
    return tiers


TIERS = load_tiers()


def list_state(app: str) -> Dict[str, str]:
    """Return mapping of machine_id -> state for the Fly app."""
    result = _run_flyctl("machines", "list", "--app", app, "--json")
    if not result:
        return {}
    try:
        machines = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        machines = []
    return {m.get("id"): m.get("state") for m in machines}


def start_machine(app: str, machine_id: str, state: str | None) -> None:
    """Start or resume a machine based on its current state."""
    cmd = "resume" if state == "suspended" else "start"
    print(f"{cmd.capitalize()}ing machine {machine_id} in {app}")
    _run_flyctl("machines", cmd, machine_id, "--app", app)


def select_next_machine(tier: dict, states: Dict[str, str]) -> Tuple[str | None, str | None]:
    """Round-robin selection of the next machine to start."""
    machines = tier["machines"]
    if not machines:
        return None, None
    for _ in machines:
        idx = tier["next_index"] % len(machines)
        tier["next_index"] += 1
        machine_id = machines[idx]
        state = states.get(machine_id)
        if state != "started":
            return machine_id, state
    return None, None


def monitor() -> None:
    """Main monitoring loop."""
    redis_conn = Redis.from_url(REDIS_URL)

    while True:
        for tier in TIERS:
            queue = Queue(name=tier["queue"], connection=redis_conn)
            q_len = queue.count
            app = tier["app"]
            states = list_state(app)
            running_ids = [mid for mid in tier["machines"] if states.get(mid) == "started"]
            running_count = len(running_ids)

            if q_len > 0:
                desired = min(len(tier["machines"]), MAX_RUNNING, q_len)
                to_start = desired - running_count
                for _ in range(max(0, to_start)):
                    machine_id, state = select_next_machine(tier, states)
                    if machine_id:
                        start_machine(app, machine_id, state)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":  # pragma: no cover - manual run
    monitor()
