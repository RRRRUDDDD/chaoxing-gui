"""Integrated AI Question Bank, Study Graph, Practice, and AI Tutor subsystem."""
from __future__ import annotations

import threading
from typing import Optional

from api.ai.config import AIConfigStore
from api.ai.db import AIDB
from api.ai.engine import AIEngine
from api.ai.ocs_server import LocalOcsServer
from api.ai.routes import create_ai_blueprint

_lock = threading.RLock()
_db: Optional[AIDB] = None
_config: Optional[AIConfigStore] = None
_engine: Optional[AIEngine] = None
_server: Optional[LocalOcsServer] = None


def get_ai_db() -> AIDB:
    global _db
    with _lock:
        if _db is None:
            _db = AIDB()
        return _db


def get_ai_config() -> AIConfigStore:
    global _config
    with _lock:
        if _config is None:
            _config = AIConfigStore()
        return _config


def get_ai_engine() -> AIEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = AIEngine(get_ai_db(), get_ai_config())
        return _engine


def get_ai_server() -> LocalOcsServer:
    global _server
    with _lock:
        if _server is None:
            _server = LocalOcsServer(get_ai_engine(), get_ai_config())
        return _server


def init_ai_runtime() -> None:
    """Auto-start local OCS HTTP server on backend startup if configured."""
    cfg = get_ai_config().load()
    if cfg["engine"].get("localServerEnabled"):
        get_ai_server().start(cfg["engine"].get("localServerPort"))


def shutdown_ai_runtime() -> None:
    with _lock:
        if _server is not None:
            _server.stop()


def ai_blueprint(get_chaoxing_bridge=None):
    return create_ai_blueprint(
        get_ai_db,
        get_ai_config,
        get_ai_engine,
        get_ai_server,
        get_chaoxing_bridge=get_chaoxing_bridge,
    )
