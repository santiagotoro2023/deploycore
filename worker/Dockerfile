FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    genisoimage \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend /backend
COPY worker/pyproject.toml ./
COPY worker/worker ./worker
RUN pip install --no-cache-dir -e /backend && pip install --no-cache-dir -e .

CMD ["arq", "worker.main.WorkerSettings"]
