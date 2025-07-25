# Dockerfile
FROM python:3.11-slim

# 1. System basics (build tools if you need them later)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

# 2. Set workdir
WORKDIR /app

# 3. Copy dependency list first → leverage layer cache
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy the rest of the source code
COPY . .

# 5. Provide a non‑root user (optional but good practice)
RUN useradd -m runner
USER runner

# 6. Expose the port Gunicorn will bind to
EXPOSE 8000

# 7. Default command: 3 workers, each with 2 threads
CMD ["gunicorn", "-w", "3", "-k", "gevent", "-t", "120", \
     "-b", "0.0.0.0:8000", "wsgi:application"]
