# App image for the seed job and the API (FastAPI + UI). Python 3.11, deps from uv.lock.
FROM python:3.11.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY ingest ingest
COPY graph graph
COPY agent agent
COPY api api
COPY ui ui
COPY tests tests
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
