"""
Benchmark — 数据库索引优化场景
"""

from scenarios import BenchmarkScene, register


class PerformanceScene(BenchmarkScene):
    name = "数据库索引优化"
    description = "为高并发订单系统设计数据库查询，优化慢查询"
    difficulty = "medium"
    category = "performance"

    def get_task_description(self) -> str:
        return (
            "有一个订单表 orders(id, user_id, status, created_at, total_amount)，"
            "现有 500 万行数据。\n"
            "实现一个函数 `query_orders(user_id, status, page, page_size)` 支持"
            "按用户和状态分页查询，要求性能最优。"
        )

    def get_naive_code(self) -> str:
        return '''import sqlite3

def query_orders(user_id, status, page=1, page_size=20):
    conn = sqlite3.connect("orders.db")
    cursor = conn.cursor()
    offset = (page - 1) * page_size
    # 无索引意识、SELECT *、OFFSET 分页
    cursor.execute(f"""
        SELECT * FROM orders
        WHERE user_id = {user_id} AND status = '{status}'
        ORDER BY created_at DESC
        LIMIT {page_size} OFFSET {offset}
    """)
    rows = cursor.fetchall()
    conn.close()
    return [{"id": r[0], "amount": r[4]} for r in rows]'''

    def get_improved_code(self) -> str:
        return '''import sqlite3
from typing import Optional

# 建议索引：
#   CREATE INDEX idx_orders_user_status ON orders(user_id, status, created_at DESC);
#   CREATE INDEX idx_orders_status_date ON orders(status, created_at DESC);
# 覆盖索引可避免回表查询

def query_orders(
    user_id: int,
    status: str,
    page: int = 1,
    page_size: int = 20,
    db_path: str = "orders.db",
) -> list[dict]:
    """
    高性能分页查询订单。

    使用 Keyset Pagination（游标分页）代替 OFFSET 分页，
    避免大偏移量时的全表扫描。
    """
    valid_statuses = {"pending", "paid", "shipped", "completed", "cancelled"}
    if status not in valid_statuses:
        raise ValueError(f"无效状态: {status}，可选: {valid_statuses}")

    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("page >= 1, page_size 1-100")

    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row

    try:
        # Keyset Pagination：基于游标而非偏移量
        # 利用复合索引 idx_orders_user_status 避免排序
        query = """
            SELECT id, user_id, status, created_at, total_amount
            FROM orders
            WHERE user_id = ?
              AND status = ?
              AND created_at < COALESCE(?, '9999-12-31')
            ORDER BY created_at DESC
            LIMIT ?
        """
        cursor = conn.execute(query, (user_id, status, None, page_size + 1))
        rows = cursor.fetchall()

        has_next = len(rows) > page_size
        if has_next:
            rows = rows[:page_size]

        # 仅查询需要的列，避免 SELECT *
        results = [
            {
                "id": row["id"],
                "user_id": row["user_id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "total_amount": row["total_amount"],
            }
            for row in rows
        ]

        return results, has_next

    finally:
        conn.close()


# For comparison: traditional OFFSET approach (slower on large offsets)
def query_orders_offset(
    user_id: int,
    status: str,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], bool]:
    """
    OFFSET 分页（适合小数据集，大数据量时性能差）。
    保留此实现以便对比。
    """
    valid_statuses = {"pending", "paid", "shipped", "completed", "cancelled"}
    if status not in valid_statuses:
        raise ValueError(f"无效状态: {status}")

    offset = (page - 1) * page_size
    query = """
        SELECT id, user_id, status, created_at, total_amount
        FROM orders
        WHERE user_id = ? AND status = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """
    conn = sqlite3.connect("orders.db", timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        # 多取一条判断是否有下一页
        cursor = conn.execute(query, (user_id, status, page_size + 1, offset))
        rows = cursor.fetchall()
        has_next = len(rows) > page_size
        if has_next:
            rows = rows[:page_size]
        return [dict(r) for r in rows], has_next
    finally:
        conn.close()'''

    def get_rule_queries(self) -> list:
        return ["数据库索引优化", "SQL 性能优化", "分页查询"]

    def count_naive_bugs(self) -> list:
        return [
            "SQL 注入：直接拼接 user_id 和 status",
            "SELECT * 查询多余列浪费 IO",
            "OFFSET 分页在深分页时性能灾难",
            "无状态校验：status 任意字符串",
            "无参数校验：page/page_size 负数或极大",
            "connection 未用 try/finally，异常时泄漏",
            "按数字索引访问列，表结构变更后静默出错",
        ]

    def count_prevented_bugs(self) -> list:
        return [
            "SQL 注入：参数化查询",
            "无效状态值：白名单校验",
            "页码溢出：page/page_size 范围校验",
            "深分页性能：Keyset Pagination",
            "IO浪费：只查询需要的列",
            "连接泄漏：try/finally 确保关闭",
            "复合索引建议：显式文档说明",
        ]

    def count_best_practices(self) -> list:
        return [
            "参数化查询",
            "覆盖索引设计",
            "只查询必要列（避免 SELECT *）",
            "输入白名单验证",
            "Keyset Pagination",
            "显式类型提示",
            "资源安全管理",
            "返回值中包含分页状态（has_next）",
        ]

    def count_edge_cases(self) -> list:
        return [
            "status 为无效值",
            "page 为 0 或负数",
            "page_size 超过上限",
            "user_id 不存在（返回空列表）",
            "无数据时仍正确返回",
            "深分页（第 10000 页）",
            "最后一页不足 page_size",
        ]


register(PerformanceScene())
