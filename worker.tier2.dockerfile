# worker.tier2.dockerfile
FROM python:3.11-slim

# 1. Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc && \
    rm -rf /var/lib/apt/lists/*

# 2. Set working directory
WORKDIR /app

# 3. Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy source code
COPY . .

# 5. Set environment variables (worker won't need port)
ENV PYTHONUNBUFFERED=1

# 6. Run the worker command
CMD ["rq", "worker", "Tier2"]
