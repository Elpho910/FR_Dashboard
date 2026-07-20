"""Runtime role configuration for server and client deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

VALID_APP_ROLES = {"server", "client"}


@dataclass(frozen=True)
class RoleConfig:
    app_role: str
    host: str
    port: int
    flask_debug: bool
    browser_hard_refresh_seconds: int
    client_auth_max_skew_seconds: int
    client_sync_seconds: int
    server_base_url: str
    client_id: str
    client_secret: str
    client_cache_db_path: Path

    @property
    def is_server(self) -> bool:
        return self.app_role == "server"

    @property
    def is_client(self) -> bool:
        return self.app_role == "client"


def load_role_config() -> RoleConfig:
    app_role = (os.getenv("APP_ROLE", "server") or "server").strip().lower()
    if app_role not in VALID_APP_ROLES:
        raise ValueError(f"APP_ROLE must be one of {sorted(VALID_APP_ROLES)}")

    return RoleConfig(
        app_role=app_role,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "5000")),
        flask_debug=os.getenv("FLASK_DEBUG", "1") == "1",
        browser_hard_refresh_seconds=int(os.getenv("BROWSER_HARD_REFRESH_SECONDS", "0")),
        client_auth_max_skew_seconds=int(os.getenv("CLIENT_AUTH_MAX_SKEW_SECONDS", "60")),
        client_sync_seconds=int(os.getenv("CLIENT_SYNC_SECONDS", "30")),
        server_base_url=os.getenv("SERVER_BASE_URL", "").strip(),
        client_id=os.getenv("CLIENT_ID", "").strip(),
        client_secret=os.getenv("CLIENT_SECRET", "").strip(),
        client_cache_db_path=Path(os.getenv("CLIENT_CACHE_DB_PATH", "data/client_cache.sqlite3")),
    )
