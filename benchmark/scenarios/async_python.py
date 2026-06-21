"""
Benchmark — Python 异步编程场景
"""

from scenarios import BenchmarkScene, register


class AsyncPythonScene(BenchmarkScene):
    name = "Python 异步编程"
    description = "实现一个异步 Web 爬虫，并发抓取多个 URL 并处理结果"
    difficulty = "medium"
    category = "performance"

    def get_task_description(self) -> str:
        return (
            "实现一个异步函数 `fetch_urls(urls)`，并发抓取多个 URL 的内容。\n"
            "要求：控制并发数、超时处理、错误隔离。"
        )

    def get_naive_code(self) -> str:
        return '''import asyncio
import aiohttp

async def fetch_urls(urls):
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in urls:
            # 无限制并发 — 可能耗尽连接池
            tasks.append(session.get(url))
        results = await asyncio.gather(*tasks)
        return [await r.text() for r in results]'''

    def get_improved_code(self) -> str:
        return '''import asyncio
import logging
from typing import Optional

import aiohttp

# 配置日志
logger = logging.getLogger(__name__)

# 合理的超时和并发控制
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
MAX_CONCURRENT = 10
MAX_RETRIES = 2
USER_AGENT = "BenchmarkBot/1.0"


class FetchResult:
    """统一的结果容器。"""

    def __init__(self, url: str, success: bool, data: Optional[str] = None,
                 status: Optional[int] = None, error: Optional[str] = None):
        self.url = url
        self.success = success
        self.data = data
        self.status = status
        self.error = error


async def fetch_single(
    session: aiohttp.ClientSession,
    url: str,
    semaphore: asyncio.Semaphore,
    timeout: aiohttp.ClientTimeout,
) -> FetchResult:
    """抓取单个 URL，带重试和错误处理。"""
    async with semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(url, timeout=timeout,
                                        headers={"User-Agent": USER_AGENT}) as resp:
                    text = await resp.text()
                    return FetchResult(
                        url=url, success=True,
                        data=text[:10000], status=resp.status,
                    )
            except asyncio.TimeoutError:
                logger.warning("超时 (attempt %d/%d): %s", attempt, MAX_RETRIES, url)
                if attempt == MAX_RETRIES:
                    return FetchResult(url=url, success=False, error="timeout")
                await asyncio.sleep(1 * attempt)  # 退避
            except aiohttp.ClientError as e:
                logger.warning("请求失败 (attempt %d/%d): %s - %s",
                               attempt, MAX_RETRIES, url, e)
                if attempt == MAX_RETRIES:
                    return FetchResult(url=url, success=False, error=str(e))
                await asyncio.sleep(1 * attempt)
            except Exception as e:
                logger.exception("未知错误: %s", url)
                return FetchResult(url=url, success=False, error=str(e))
    return FetchResult(url=url, success=False, error="unknown")


async def fetch_urls(
    urls: list[str],
    max_concurrent: int = MAX_CONCURRENT,
    timeout: Optional[aiohttp.ClientTimeout] = None,
) -> list[FetchResult]:
    """
    并发抓取多个 URL。

    特性：
    - 信号量控制并发数
    - 超时保护
    - 自动重试（指数退避）
    - 错误隔离（单 URL 失败不影响其他）
    - 响应大小限制
    """
    if not urls:
        return []

    timeout = timeout or DEFAULT_TIMEOUT
    semaphore = asyncio.Semaphore(max_concurrent)
    connector = aiohttp.TCPConnector(limit=max_concurrent)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    ) as session:
        tasks = [
            fetch_single(session, url, semaphore, timeout)
            for url in urls
        ]
        results = await asyncio.gather(*tasks)

    success_count = sum(1 for r in results if r.success)
    logger.info("抓取完成: %d/%d 成功", success_count, len(urls))
    return results


# 使用示例
async def demo():
    urls = [
        "https://httpbin.org/delay/1",
        "https://httpbin.org/delay/2",
        "https://httpbin.org/status/500",  # 会触发重试
    ]
    results = await fetch_urls(urls, max_concurrent=5)
    for r in results:
        print(f"{r.url}: {'OK' if r.success else 'FAIL'} ({r.status or r.error})")'''

    def get_rule_queries(self) -> list:
        return ["异步编程", "并发控制", "错误处理"]

    def count_naive_bugs(self) -> list:
        return [
            "无并发限制：可能耗尽连接池",
            "无超时：慢请求阻塞所有任务",
            "无错误处理：单个 URL 失败导致整体崩溃",
            "无重试机制：临时故障导致失败",
            "无 User-Agent：可能被服务器拒绝",
            "响应体无大小限制：内存可能耗尽",
            "无结果隔离：无法区分成功/失败",
            "异常泄漏：asyncio.gather 默认模式不隔离异常",
        ]

    def count_prevented_bugs(self) -> list:
        return [
            "并发数控制：Semaphore 限制最大 10",
            "超时保护：全局 30s + 连接 10s",
            "错误隔离：每个 URL 单独 try/except",
            "自动重试：最多 2 次 + 指数退避",
            "连接池限制：TCPConnector(limit=N)",
            "响应裁剪：最大 10000 字符防 OOM",
            "统一结果类型：FetchResult 区分成功/失败",
            "User-Agent 防封禁",
        ]

    def count_best_practices(self) -> list:
        return [
            "信号量并发控制",
            "超时设置（连接超时 + 总超时）",
            "指数退避重试",
            "错误隔离（不崩整体）",
            "资源管理（ClientSession context manager）",
            "结构化日志",
            "统一结果模型",
            "显式类型提示",
            "连接池限制",
        ]

    def count_edge_cases(self) -> list:
        return [
            "空 URL 列表",
            "无效 URL 格式",
            "DNS 解析失败",
            "服务器返回 5xx",
            "请求超时",
            "响应体过大",
            "并发数超过系统限制",
            "所有 URL 都失败",
        ]


register(AsyncPythonScene())
