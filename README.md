# MidJAu

## Local Docker run
```bash
docker build -t midj-app .
docker run --rm -p 8000:8000 midj-app
```

## Deploying to Fly.io

Two Fly apps are used: one for the web interface and one for the RQ worker.

### Web process
The default `fly.toml` deploys the web UI using `Dockerfile`.
Deploy it with:
```bash
fly deploy -c fly.toml
```

### Worker process
`fly.worker.toml` builds `worker.dockerfile` and runs the RQ worker.
Create a separate Fly app (for example `my-app-worker`) and deploy:
```bash
fly apps create my-app-worker    # only once
fly deploy -c fly.worker.toml --app my-app-worker
```

### Autoscaler process
`fly.autoscaler.toml` builds `autoscaler.Dockerfile` and runs
`queue_monitor_existing.py` to scale worker machines up or down.
Deploy it with:
```bash
fly deploy -c fly.autoscaler.toml
```

### Required environment variables
Both apps require the following environment variables (usually set as Fly
secrets):
- `REDIS_URL` – connection string to your Redis instance
- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
- `AWS_ENDPOINT_URL_S3`, `BUCKET_NAME` and `AWS_REGION`
- other application settings like `FLASK_SECRET_KEY` and
  `LICENSE_VALIDATION_URL`

Set them with `fly secrets set` before deploying.


### Commands to deploy app and worker

fly deploy --app midjau-web


fly deploy --config fly.worker.tier1.toml --dockerfile worker.tier1.dockerfile --app midjau-worker-tier1 --no-cache

fly deploy --config fly.worker.tier2.toml --dockerfile worker.tier2.dockerfile --app midjau-worker-tier2 --no-cache

fly deploy --config fly.worker.tier3.toml --dockerfile worker.tier3.dockerfile --app midjau-worker-tier3 --no-cache

fly deploy -c fly.autoscaler.toml

### Commands to show logs
fly logs --app midjau-web
fly logs --app midjau-worker

fly logs --app midjau-worker-tier1
fly logs --app midjau-worker-tier2
fly logs --app midjau-worker-tier3
fly logs --app midjau-autoscaler

### show set secrets

fly secrets list --app midjau-web
fly secrets list --app midjau-worker-tier1
fly secrets list --app midjau-worker-tier2
fly secrets list --app midjau-worker-tier3

### How much time it takes to run one prompt
U1 mode: ~42 seconds per prompt (on average)
All mode: ~58 seconds per prompt (on average)


# Typical one-time run for each new worker app
# Example for Tier1
fly apps create midjau-worker-tier1   # only if you haven’t already

fly secrets set \
  AWS_ACCESS_KEY_ID=tid_lxK_TuVAJPM_cidrIwgJHSKCErCiueNQVcNTUjnHMQkQKcynGB \
  AWS_ENDPOINT_URL_S3=https://fly.storage.tigris.dev \
  AWS_REGION=auto \
  AWS_SECRET_ACCESS_KEY=tsec_oTaRILcVC8o8Uv6sKUi865HfpOF9++yG5zJ8_p_mmK3EK1PUiCkjKr5yVs0ZRam2yLCqv7 \
  BUCKET_NAME=nameless-butterfly-6759 \
  FLASK_SECRET_KEY=dev_key123 \
  LICENSE_VALIDATION_URL=https://script.google.com/macros/s/AKfycbyMueLJfhU8U_Gu25S4MWxSa4eYLYy7Q_Y6fI0p4eq90cW0GY2yUjK9OAdWelpI1enj/exec \
  REDIS_URL=redis://default:d36a19f8a3a34402945eee9a59de3ab3@fly-midjau-redis.upstash.io:6379 \
  -a midjau-worker-tier1


fly secrets set \
  AWS_ACCESS_KEY_ID=tid_lxK_TuVAJPM_cidrIwgJHSKCErCiueNQVcNTUjnHMQkQKcynGB \
  AWS_ENDPOINT_URL_S3=https://fly.storage.tigris.dev \
  AWS_REGION=auto \
  AWS_SECRET_ACCESS_KEY=tsec_oTaRILcVC8o8Uv6sKUi865HfpOF9++yG5zJ8_p_mmK3EK1PUiCkjKr5yVs0ZRam2yLCqv7 \
  BUCKET_NAME=nameless-butterfly-6759 \
  FLASK_SECRET_KEY=dev_key123 \
  LICENSE_VALIDATION_URL=https://script.google.com/macros/s/AKfycbyMueLJfhU8U_Gu25S4MWxSa4eYLYy7Q_Y6fI0p4eq90cW0GY2yUjK9OAdWelpI1enj/exec \
  REDIS_URL=redis://default:d36a19f8a3a34402945eee9a59de3ab3@fly-midjau-redis.upstash.io:6379 \
  -a midjau-web

fly secrets set FLY_API_TOKEN="xfbenfsd-dkjheb-ere45964i5-dfjfbbfdhjfb-3jdheyufb" --app midjau-web

fly deploy --config fly.worker.tier1.toml --dockerfile worker.tier1.dockerfile --app midjau-worker-tier1 --no-cache




git add .
git commit -m "Explain what you changed"
git push
git pull


### Autoscaller set up
Autoscaler (queue_monitor_existing.py)
queue_monitor_existing.py uses the following environment variables:

Variable	Purpose
REDIS_URL	URL to the shared Redis instance.
POLL_INTERVAL_SEC	Seconds between queue checks (default 30).
MAX_RUNNING_PER_TIER	Max machines to run per tier (default 1).
TIER{N}_APP	Fly app name for tier N (default midjau-worker-tierN).
TIER{N}_MACHINE_IDS	Comma‑separated machine IDs for tier N.
TIER{N}_QUEUE_NAME	Redis queue name for tier N (default TierN).
For each tier that you want the autoscaler to manage, set TIER{N}_MACHINE_IDS to the IDs of your pre‑created machines (e.g., abcd123,efgh456). You can override app names or queue names if they differ.

Set these as secrets for the autoscaler app:

fly secrets set \
  REDIS_URL=redis://... \
  POLL_INTERVAL_SEC=30 \
  MAX_RUNNING_PER_TIER=1 \
  TIER1_APP=midjau-worker-tier1 \
  TIER1_MACHINE_IDS=... \
  TIER1_QUEUE_NAME=Tier1 \
  TIER2_APP=midjau-worker-tier2 \
  TIER2_MACHINE_IDS=... \
  TIER2_QUEUE_NAME=Tier2 \
  TIER3_APP=midjau-worker-tier3 \
  TIER3_MACHINE_IDS=... \
  TIER3_QUEUE_NAME=Tier3 \
  -a midjau-autoscaler



  Get machine IDs for each tier
  fly machines list -a midjau-worker-tier1
  fly machines list -a midjau-worker-tier2
  fly machines list -a midjau-worker-tier3


Copy the ID column values for the machines you want the autoscaler to manage.
If multiple machines in a tier should be started/stopped, separate the IDs with commas:


### Example:

fly secrets set \
  FLY_API_TOKEN="FlyV1 fm2_lJPECAAAAAAACZFPxBBzJdYlqJiWA4K+lsXAmQtSwrVodHRwczovL2FwaS5mbHkuaW8vdjGUAJLOABJl9h8Lk7lodHRwczovL2FwaS5mbHkuaW8vYWFhL3YxxDybeLc0M3BrrXzIkL5Ck0fPTaBW75Fpc9DIdhZLa7/i3KOLuX8qnYdKTw4nlR1Lp05iCvvbHc76ZWdgsMXETiSVQMOID4KWuq82pbR1MlyD5kEa0OpUSWorafeVIMaX0ojeKcOA84gCbHU90z6OPHCBAkinZtU6A5OBdS8ZVFKxE8V3LCZolmLl31M0j8Qg/S/KZZk5eak5ksbgx3JlwxWBYXt3M8P2RoWe78lW8s4=,fm2_lJPETiSVQMOID4KWuq82pbR1MlyD5kEa0OpUSWorafeVIMaX0ojeKcOA84gCbHU90z6OPHCBAkinZtU6A5OBdS8ZVFKxE8V3LCZolmLl31M0j8QQP1X0gfDhxQfT9LxTXG+KEsO5aHR0cHM6Ly9hcGkuZmx5LmlvL2FhYS92MZgEks5ol4OfzwAAAAEkj6G9F84AEa7xCpHOABGu8QzEEJqdrLlLRSsPlPQNwZHMGTDEIEuCqJ3hvWQucaVZapK7uOmf2LyAhkIcmSD3nX9QQN83" \
  REDIS_URL=redis://default:d36a19f8a3a34402945eee9a59de3ab3@fly-midjau-redis.upstash.io:6379 \
  POLL_INTERVAL_SEC=10 \
  MAX_RUNNING_PER_TIER=1 \
  TIER1_APP=midjau-worker-tier1 \
  TIER1_MACHINE_IDS=90805536be6958,9080553ea9e0d8 \
  TIER1_QUEUE_NAME=Tier1 \
  -a midjau-autoscaler


  fly deploy --config fly.autoscaler.toml --dockerfile autoscaler.Dockerfile --app midjau-autoscaler --no-cache
