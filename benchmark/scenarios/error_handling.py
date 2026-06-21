"""
Benchmark — 错误处理模式场景
"""

from scenarios import BenchmarkScene, register


class ErrorHandlingScene(BenchmarkScene):
    name = "错误处理模式"
    description = "实现一个文件处理服务，含读取、解析、写入操作，需要完善的错误处理"
    difficulty = "medium"
    category = "error-handling"

    def get_task_description(self) -> str:
        return (
            "实现一个 CSV 文件处理器：读取 CSV 文件，解析数据，写入到 SQLite 数据库。\n"
            "要求：完善的错误处理、日志记录、数据完整性。"
        )

    def get_naive_code(self) -> str:
        return '''import csv
import sqlite3

def import_csv_to_db(csv_path, db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cursor.execute(
                "INSERT INTO data VALUES (?, ?, ?)",
                (row["id"], row["name"], row["value"])
            )
    conn.commit()
    conn.close()
    print("导入完成")'''

    def get_improved_code(self) -> str:
        return '''import csv
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ImportError(Exception):
    """CSV 导入相关错误的基类。"""


class FileNotFound(ImportError):
    """文件不存在。"""


class EmptyFile(ImportError):
    """文件为空。"""


class InvalidFormat(ImportError):
    """CSV 格式无效。"""


class DatabaseError(ImportError):
    """数据库操作失败。"""


@dataclass
class ImportResult:
    """导入结果报告。"""
    total_rows: int = 0
    imported: int = 0
    skipped: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def success_rate(self) -> float:
        if self.total_rows == 0:
            return 1.0
        return self.imported / self.total_rows


def validate_csv_path(csv_path: str) -> Path:
    """验证 CSV 文件路径。"""
    path = Path(csv_path).resolve()
    if not path.exists():
        raise FileNotFound(f"文件不存在: {csv_path}")
    if not path.is_file():
        raise ImportError(f"路径不是文件: {csv_path}")
    if path.stat().st_size == 0:
        raise EmptyFile(f"文件为空: {csv_path}")
    if path.suffix.lower() not in (".csv", ".tsv"):
        logger.warning("文件后缀非常规: %s", path.suffix)
    return path


def validate_row(row: dict, expected_fields: set, row_num: int) -> Optional[str]:
    """验证单行数据，返回错误描述或 None。"""
    if not row:
        return f"第 {row_num} 行为空"

    missing = expected_fields - set(row.keys())
    if missing:
        return f"第 {row_num} 行缺少字段: {missing}"

    # 字段值校验
    for key, val in row.items():
        if val is None:
            return f"第 {row_num} 行 {key} 为空"
        if len(str(val)) > 1024:
            return f"第 {row_num} 行 {key} 超长"

    return None


def import_csv_to_db(
    csv_path: str,
    db_path: str,
    table_name: str = "data",
    batch_size: int = 100,
) -> ImportResult:
    """
    将 CSV 文件导入到 SQLite 数据库。

    特性：
    - 事务性：全部成功才提交，失败回滚
    - 逐行校验：跳过无效行并记录
    - 批处理：控制内存使用
    - 编码检测：自动处理 UTF-8/GBK
    """
    result = ImportResult()

    # 1. 验证输入
    path = validate_csv_path(csv_path)

    # 2. 检测编码
    import chardet
    raw = path.read_bytes()
    detected = chardet.detect(raw)
    encoding = detected.get("encoding", "utf-8") or "utf-8"
    logger.info("检测到编码: %s (置信度: %.0f%%)", encoding, detected.get("confidence", 0) * 100)

    # 3. 连接数据库
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    try:
        # 4. 读取并校验 CSV
        try:
            content = raw.decode(encoding)
            reader = csv.DictReader(content.splitlines())
        except (UnicodeDecodeError, csv.Error) as e:
            raise InvalidFormat(f"CSV 解析失败: {e}")

        expected_fields = set(reader.fieldnames or [])
        if not expected_fields:
            raise InvalidFormat("CSV 文件无表头或列为空")

        # 5. 逐行处理
        placeholders = ", ".join("?" for _ in expected_fields)
        columns = ", ".join(expected_fields)
        insert_sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"

        batch = []
        for row_num, row in enumerate(reader, start=2):  # 从第 2 行开始（第 1 行是表头）
            result.total_rows += 1
            err = validate_row(row, expected_fields, row_num)
            if err:
                result.skipped += 1
                result.errors.append(err)
                continue

            batch.append(tuple(row.get(f, "") for f in expected_fields))

            if len(batch) >= batch_size:
                try:
                    conn.executemany(insert_sql, batch)
                    batch = []
                except sqlite3.Error as e:
                    raise DatabaseError(f"批量插入失败: {e}")

        # 剩余批次
        if batch:
            try:
                conn.executemany(insert_sql, batch)
            except sqlite3.Error as e:
                raise DatabaseError(f"批量插入失败: {e}")

        conn.commit()
        result.imported = result.total_rows - result.skipped
        logger.info("导入完成: %d 行导入, %d 行跳过, %d 错误",
                     result.imported, result.skipped, len(result.errors))

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return result'''

    def get_rule_queries(self) -> list:
        return ["错误处理", "文件处理", "Python 数据库"]

    def count_naive_bugs(self) -> list:
        return [
            "文件不存在时抛出 FileNotFoundError 未处理",
            "空文件时 CSV 解析器行为未定义",
            "编码假设：默认 UTF-8，遇到 GBK 崩溃",
            "无字段校验：CSV 列名不匹配时 KeyError",
            "无行校验：空值/异常值直接入库",
            "单条插入：大数据量性能极差",
            "无事务保护：中间失败部分数据已写入",
            "无日志：静默失败难排查",
            "未指定 PRAGMA，默认配置性能差",
            "错误信息不足：无行号，无上下文",
        ]

    def count_prevented_bugs(self) -> list:
        return [
            "文件存在性校验",
            "文件为空检测",
            "编码自动检测（chardet）",
            "字段完整性校验",
            "逐行空值校验",
            "字段长度越界检测",
            "批量插入性能优化",
            "事务性：回滚保证数据一致性",
            "结构化日志记录",
            "自定义异常层次，上层可精确捕获",
        ]

    def count_best_practices(self) -> list:
        return [
            "防御性编程（前置校验）",
            "自定义异常层次",
            "事务性操作",
            "批量插入优化",
            "编码检测（chardet）",
            "结构化日志",
            "数据校验隔离（validate_row 纯函数）",
            "WAL 模式 + synchronous=NORMAL",
            "返回详细报告而非简单状态",
            "明确的资源管理（try/finally close）",
        ]

    def count_edge_cases(self) -> list:
        return [
            "文件不存在",
            "空文件",
            "文件后缀非 .csv",
            "UTF-8 编码文件",
            "GBK 编码文件",
            "字段缺失（列名不匹配）",
            "包含空值的行",
            "超长字段",
            "空行",
            "100 万行大数据量",
        ]


register(ErrorHandlingScene())
