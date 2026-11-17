# App image: Python 3.12 (standard library only, no pip packages) + Node.js 26 for Phase 3's test sandbox.
# Node 25+ is required: the sandbox relies on Node's permission model blocking network access by default.
FROM node:26-slim AS node

FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libstdc++6 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY . /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0
EXPOSE 8000 8001 8002
CMD ["python", "workflow.py", "up"]
