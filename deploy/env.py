"""Read deployment settings from .env without printing them (values never logged)."""

import os

KEYS = ("FALKORDB_HOST", "FALKORDB_PORT", "FALKORDB_USERNAME", "FALKORDB_PASSWORD", "FALKORDB_TLS")


def load(path=".env", keys=KEYS):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        k, sep, v = line.strip().partition("=")
        if sep and k in keys:
            os.environ[k] = v.strip().strip('"').strip("'")
