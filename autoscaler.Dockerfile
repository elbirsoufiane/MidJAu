# autoscaler.Dockerfile
FROM python:3.11-slim

# 1. Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc && \
    rm -rf /var/lib/apt/lists/*

# 2. Set working directory
WORKDIR /app

# 3. Copy queue_monitor_existing.py script
COPY scripts/queue_monitor_existing.py /app/queue_monitor_existing.py

# 4. Install Python dependencies
RUN pip install --no-cache-dir redis rq

# 5. Set environment variables
ENV PYTHONUNBUFFERED=1

# 6. Run queue_monitor_existing.py
CMD ["python", "queue_monitor_existing.py"]
