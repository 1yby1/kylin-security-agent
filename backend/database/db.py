from __future__ import annotations

import os
from pathlib import Path

from backend.audit.store import get_audit_store
from backend.config import get_audit_settings

DB_PATH = Path(os.getenv("AGENT_DB_PATH", str(Path(__file__).resolve().parent / "app.db")))


def init_db() -> None:
    # 应用级 SQLite（保留路径常量供其它用途）。
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # 审计表 schema 由 AuditStore 负责；启动时构造一次共享 store 即建表。
    get_audit_store(get_audit_settings().db_path)
