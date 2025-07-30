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

### Required environment variables
Both apps require the following environment variables (usually set as Fly
secrets):
- `REDIS_URL` – connection string to your Redis instance
- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
- `AWS_ENDPOINT_URL_S3`, `BUCKET_NAME` and `AWS_REGION`
- other application settings like `FLASK_SECRET_KEY` and
  `LICENSE_VALIDATION_URL`

Set them with `fly secrets set` before deploying.
