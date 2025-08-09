# #!/usr/bin/env python3
# """Monitor Redis queues and manage existing Fly.io machines via HTTP API.

# This script watches RQ queues and ensures a pool of pre-created Fly.io
# Machines are started when work arrives.

# Environment variables:
#     FLY_API_TOKEN          Fly API token for Machines API (required).
#     REDIS_URL              Redis connection URL.
#     POLL_INTERVAL_SEC      Seconds between queue checks (default: 30).
#     MAX_RUNNING_PER_TIER   Maximum running machines per tier (default: 1).

# Tier specific variables (repeat for tiers 1-3):
#     TIER{N}_APP            Fly app name (default: midjau-worker-tier{N}).
#     TIER{N}_MACHINE_IDS    Comma separated machine IDs for the tier.
#     TIER{N}_QUEUE_NAME     RQ queue name (default: Tier{N}).
# """

# from __future__ import annotations

# import json
# import os
# import requests
# import time
# from typing import Dict, Tuple

# from redis import Redis
# from rq import Queue

# REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# if not REDIS_URL:
#     raise RuntimeError("REDIS_URL is required (set it via fly secrets).")

# POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "30"))
# MAX_RUNNING = int(os.getenv("MAX_RUNNING_PER_TIER", "1"))
# API = "https://api.machines.dev"
# FLY_API_TOKEN = os.environ["FLY_API_TOKEN"]
# if not FLY_API_TOKEN:
#     raise RuntimeError("FLY_API_TOKEN is required (set it via fly secrets).")

# HDRS = {"Authorization": f"Bearer {FLY_API_TOKEN}"}





# def load_tiers() -> list[dict]:
#     """Load tier configuration from environment variables."""
#     tiers = []
#     for i in range(1, 4):
#         app = os.getenv(f"TIER{i}_APP", f"midjau-worker-tier{i}")
#         machine_ids = [m.strip() for m in os.getenv(f"TIER{i}_MACHINE_IDS", "").split(",") if m.strip()]
#         queue_name = os.getenv(f"TIER{i}_QUEUE_NAME", f"Tier{i}")
#         if not machine_ids:
#             continue
#         tiers.append({
#             "name": f"Tier{i}",
#             "app": app,
#             "machines": machine_ids,
#             "queue": queue_name,
#             "next_index": 0,
#         })
#     return tiers


# TIERS = load_tiers()


# def list_state(app: str, machine_ids: list[str]) -> Dict[str, str]:
#     """Return mapping of machine_id -> state for the provided IDs using the Machines API."""
#     states: Dict[str, str] = {}
#     for mid in machine_ids:
#         try:
#             r = requests.get(f"{API}/v1/apps/{app}/machines/{mid}", headers=HDRS, timeout=20)
#             if r.status_code == 404:
#                 states[mid] = "missing"
#                 continue
#             r.raise_for_status()
#             data = r.json()
#             states[mid] = (data.get("state") or "").lower()  # "started", "stopped", "stopping", "starting", "suspended"
#         except Exception as exc:
#             print(f"⚠️ failed to fetch state for {app}/{mid}: {exc}")
#             states[mid] = "unknown"
#     return states



# def start_machine(app: str, machine_id: str, state: str | None) -> None:
#     """Start a machine via Machines API. /start is idempotent for stopped/suspended."""
#     try:
#         r = requests.post(f"{API}/v1/apps/{app}/machines/{machine_id}/start", headers=HDRS, timeout=30)
#         # Accept success + already-running-ish cases
#         if r.status_code in (200, 202, 204, 409, 423):
#             print(f"Starting machine {machine_id} in {app} -> {r.status_code}")
#             return
#         r.raise_for_status()
#     except Exception as exc:
#         print(f"❌ start failed for {app}/{machine_id}: {exc}")



# def select_next_machine(tier: dict, states: Dict[str, str]) -> Tuple[str | None, str | None]:
#     """Round-robin selection of the next machine to start."""
#     machines = tier["machines"]
#     if not machines:
#         return None, None
#     for _ in machines:
#         idx = tier["next_index"] % len(machines)
#         tier["next_index"] += 1
#         machine_id = machines[idx]
#         state = states.get(machine_id)
#         if state != "started":
#             return machine_id, state
#     return None, None


# def monitor() -> None:
#     """Main monitoring loop."""
#     redis_conn = Redis.from_url(REDIS_URL)

#     while True:
#         for tier in TIERS:
#             queue = Queue(name=tier["queue"], connection=redis_conn)
#             q_len = queue.count
#             app = tier["app"]
#             states = list_state(app, tier["machines"])
#             running_ids = [mid for mid in tier["machines"] if states.get(mid) == "started"]
#             running_count = len(running_ids)

#             if q_len > 0:
#                 desired = min(len(tier["machines"]), MAX_RUNNING, q_len)
#                 to_start = desired - running_count
#                 for _ in range(max(0, to_start)):
#                     machine_id, state = select_next_machine(tier, states)
#                     if machine_id:
#                         start_machine(app, machine_id, state)
#         time.sleep(POLL_INTERVAL)


# if __name__ == "__main__":  # pragma: no cover - manual run
#     monitor()


#!/usr/bin/env python3
"""Monitor Redis queues and manage existing Fly.io machines via HTTP API.

This script watches RQ queues and ensures a pool of pre-created Fly.io
Machines are started when work arrives.

Env:
    FLY_API_TOKEN          Fly API token for Machines API (required).
    REDIS_URL              Redis connection URL.
    POLL_INTERVAL_SEC      Seconds between queue checks (default: 30).
    MAX_RUNNING_PER_TIER   Maximum running machines per tier (default: 1).

Tier envs (repeat for tiers 1-3):
    TIER{N}_APP            Fly app name (default: midjau-worker-tier{N}).
    TIER{N}_MACHINE_IDS    Comma-separated machine IDs for the tier.
    TIER{N}_QUEUE_NAME     RQ queue name (default: Tier{N}).
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from redis import Redis
from rq import Queue

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
if not REDIS_URL:
    raise RuntimeError("REDIS_URL is required (set it via fly secrets).")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "30"))
MAX_RUNNING = int(os.getenv("MAX_RUNNING_PER_TIER", "1"))

API = os.getenv("FLY_API_HOSTNAME", "https://api.machines.dev")

FLY_API_TOKEN = os.getenv("FLY_API_TOKEN")
if not FLY_API_TOKEN:
    raise RuntimeError("FLY_API_TOKEN is required (set it via fly secrets).")

# Session with retries for transient errors
session = requests.Session()
session.headers.update({"Authorization": f"Bearer {FLY_API_TOKEN}"})
retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
session.mount("https://", HTTPAdapter(max_retries=retries))
session.mount("http://", HTTPAdapter(max_retries=retries))


def load_tiers() -> list[dict]:
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


def list_state(app: str, machine_ids: list[str]) -> Dict[str, str]:
    """Return mapping of machine_id -> state for the provided IDs using the Machines API."""
    states: Dict[str, str] = {}
    for mid in machine_ids:
        try:
            r = session.get(f"{API}/v1/apps/{app}/machines/{mid}", timeout=20)
            if r.status_code == 404:
                states[mid] = "missing"
                continue
            r.raise_for_status()
            data = r.json()
            states[mid] = (data.get("state") or "").lower()  # started/stopped/stopping/starting/suspended
        except Exception as exc:
            print(f"⚠️ failed to fetch state for {app}/{mid}: {exc}")
            states[mid] = "unknown"
    return states


def start_machine(app: str, machine_id: str, state: str | None) -> None:
    """Start a machine via Machines API. /start is idempotent for stopped/suspended."""
    try:
        r = session.post(f"{API}/v1/apps/{app}/machines/{machine_id}/start", timeout=30)
        if r.status_code in (200, 202, 204, 409, 423):
            print(f"Starting machine {machine_id} in {app} -> {r.status_code}")
            return
        r.raise_for_status()
    except Exception as exc:
        print(f"❌ start failed for {app}/{machine_id}: {exc}")


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
    redis_conn = Redis.from_url(REDIS_URL)

    while True:
        for tier in TIERS:
            queue = Queue(name=tier["queue"], connection=redis_conn)
            q_len = queue.count
            app = tier["app"]

            states = list_state(app, tier["machines"])
            running_ids = [mid for mid in tier["machines"] if states.get(mid) == "started"]
            running_count = len(running_ids)

            desired = min(len(tier["machines"]), MAX_RUNNING, q_len)
            to_start = desired - running_count
            print(f"[{tier['name']}] q_len={q_len} running={running_count} desired={desired} to_start={to_start}")

            for _ in range(max(0, to_start)):
                machine_id, state = select_next_machine(tier, states)
                if machine_id:
                    start_machine(app, machine_id, state)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":  # pragma: no cover - manual run
    monitor()
