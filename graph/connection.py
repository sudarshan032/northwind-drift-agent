"""One FalkorDB connection rule for the API, the loader and the deploy scripts.

Env: FALKORDB_HOST (localhost), FALKORDB_PORT (6379), FALKORDB_USERNAME,
FALKORDB_PASSWORD (both optional), FALKORDB_TLS (true/false; default true in
public mode, false otherwise). Credentials are never logged.
"""

import os

from falkordb import FalkorDB


def tls_default():
    return os.environ.get("DEPLOY_MODE", "local") == "public"


def connect(host=None, port=None, timeout=15):
    tls = os.environ.get("FALKORDB_TLS", "").strip().lower()
    return FalkorDB(host=host or os.environ.get("FALKORDB_HOST", "localhost"),
                    port=int(port or os.environ.get("FALKORDB_PORT", 6379)),
                    username=os.environ.get("FALKORDB_USERNAME") or None,
                    password=os.environ.get("FALKORDB_PASSWORD") or None,
                    ssl=(tls == "true") if tls else tls_default(),
                    socket_timeout=timeout, socket_connect_timeout=timeout)
