"""Vercel entrypoint for the public deployment (DEPLOY_MODE=public): the FastAPI app from api/main.py."""

from api.main import app  # noqa: F401
