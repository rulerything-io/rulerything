"""
Benchmark — SQL 注入防护场景
"""

from scenarios import BenchmarkScene, register


class SecurityScene(BenchmarkScene):
    name = "SQL 注入防护"
    description = "实现一个安全的数据库查询函数，根据用户输入查询用户信息"
    difficulty = "easy"
    category = "security"

    def get_task_description(self) -> str:
        return (
            "实现一个 Python 函数 `get_user(username)`，根据用户名从 SQLite 数据库查询用户信息。\n"
            "要求：传入 username 参数，返回用户字典或 None。"
        )

    def get_naive_code(self) -> str:
        return '''import sqlite3

def get_user(username):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # 直接拼接 SQL — 存在 SQL 注入风险
    query = f"SELECT * FROM users WHERE username = '{username}'"
    cursor.execute(query)
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"id": row[0], "username": row[1], "email": row[2]}
    return None'''

    def get_improved_code(self) -> str:
        return '''import sqlite3
from contextlib import contextmanager

@contextmanager
def get_db():
    """安全的数据库连接上下文管理器。"""
    conn = sqlite3.connect("users.db", timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_user(username):
    """
    根据用户名查询用户信息。
    使用参数化查询防止 SQL 注入。
    """
    if not username or not isinstance(username, str):
        return None

    # 参数化查询 — 防止 SQL 注入
    query = "SELECT * FROM users WHERE username = ?"

    with get_db() as conn:
        cursor = conn.execute(query, (username,))
        row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row["id"],
        "username": row["username"],
        "email": row.get("email"),
        "created_at": row.get("created_at"),
    }

def get_user_safe(username: str) -> dict | None:
    """类型安全的包装器，带输入长度限制。"""
    MAX_LEN = 255
    if not username or len(username) > MAX_LEN:
        return None
    # 仅允许合法字符
    import re
    if not re.match(r'^[a-zA-Z0-9_@.\\-]+$', username):
        return None
    return get_user(username)'''

    def get_rule_queries(self):
        return ["SQL注入", "参数化查询", "输入验证"]

    def count_naive_bugs(self) -> list:
        return [
            "SQL 注入漏洞：直接拼接用户输入到 SQL 语句",
            "连接未使用上下文管理器，异常时连接泄漏",
            "无输入验证（类型、长度、字符集）",
            "异常处理缺失：SQL 错误会直接崩溃",
            "未设置 row_factory，按索引访问易出错",
            "无 timeout 设置，高并发时可能死锁",
            "未处理事务回滚",
        ]

    def count_prevented_bugs(self) -> list:
        return [
            "SQL 注入：参数化查询替代字符串拼接",
            "连接泄漏：contextmanager 确保 finally 关闭",
            "输入类型错误：isinstance 校验",
            "空输入崩溃：None/空字符串守卫",
            "事务不安全：try/except 确保 rollback",
            "过长的输入：MAX_LEN 限制",
            "特殊字符注入：正则白名单过滤",
        ]

    def count_best_practices(self) -> list:
        return [
            "参数化查询（Prepared Statement）",
            "上下文管理器管理资源",
            "最小权限原则（只查询需要字段）",
            "明确的异常处理与事务回滚",
            "输入白名单验证",
            "类型提示（Type Hint）",
            "行工厂模式（Row Factory）",
            "连接超时保护",
        ]

    def count_edge_cases(self) -> list:
        return [
            "username 为空字符串",
            "username 为 None",
            "username 类型错误（非 str）",
            "username 超长",
            "username 含特殊字符",
            "用户不存在（返回 None）",
            "数据库连接失败",
        ]


register(SecurityScene())
