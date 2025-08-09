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