"""
Rulerything — 提案存储层（storage_v2 提案功能拆分）

包括：提案创建、获取、列表、状态更新
"""

import sqlite3
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from core.utils import _iso_now


class ProposalMixin:
    """storage_v2 提案功能混入类。"""

    # ── 提案管理 ────────────────────────────────────────

    def create_proposal(self, title: str, description: str,
                        module: str, dedup_key: str,
                        priority: int = 3,
                        risk_score: float = 0.0) -> str:
        """创建新提案，返回 proposal_id。"""
        proposal_id = f"{module}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        with self._lock:
            try:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT INTO proposals (id, title, description, module, dedup_key,
                            priority, risk_score, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """, (proposal_id, title, description, module, dedup_key,
                          priority, risk_score, _iso_now()))
                return proposal_id
            except sqlite3.Error:
                return ""

    def get_proposal(self, proposal_id: str) -> Optional[dict]:
        """获取提案详情。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
                ).fetchone()
        return dict(row) if row else None

    def list_proposals(self, status: Optional[str] = None,
                       module: Optional[str] = None,
                       limit: int = 100) -> List[dict]:
        """列出提案，可按状态/模块过滤。"""
        with self._lock:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                clauses = []
                params = []
                if status:
                    clauses.append("status = ?")
                    params.append(status)
                if module:
                    clauses.append("module = ?")
                    params.append(module)
                where = " AND ".join(clauses) if clauses else "1=1"
                rows = conn.execute(
                    f"SELECT * FROM proposals WHERE {where} ORDER BY created_at DESC LIMIT ?",
                    params + [limit],
                ).fetchall()
        return [dict(r) for r in rows]

    def update_proposal_status(self, proposal_id: str, status: str,
                               **extra):
        """更新提案状态和额外字段。"""
        allowed_statuses = {"pending", "running", "success", "failed", "rolled_back", "cancelled"}
        if status not in allowed_statuses:
            return False
        with self._lock:
            try:
                with self._connect() as conn:
                    sets = ["status = ?"]
                    vals = [status]
                    if status in ("success", "failed", "rolled_back"):
                        sets.append("executed_at = ?")
                        vals.append(_iso_now())
                    for key in ("snapshot_id", "result", "error_message"):
                        if key in extra:
                            sets.append(f"{key} = ?")
                            vals.append(extra[key])
                    vals.append(proposal_id)
                    conn.execute(
                        f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?",
                        vals,
                    )
                return True
            except sqlite3.Error:
                return False
