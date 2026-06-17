#!/usr/bin/env python3
"""
Rulerything — Rule Knowledge Base CLI for Claude Code.

A self-evolving rule knowledge base engine: stores, indexes, retrieves,
and autonomously evolves programming rules across 30+ tech categories.

Designed as a deterministic knowledge layer for AI coding assistants.

Usage:
    python rule_helper.py smart <query>             # v2.0 semantic + category search
    python rule_helper.py search <query> [options]  # v1.0 keyword search
    python rule_helper.py list [--cat CAT]          # list rules by category
    python rule_helper.py get <rule_id>             # view rule details
    python rule_helper.py start                     # start the rule server
    python rule_helper.py stop                      # stop the rule server
    python rule_helper.py restart                   # restart the rule server
    python rule_helper.py status                    # check server status with Phases info
    python rule_helper.py ai-pending                # view pending delegated AI queries
    python rule_helper.py ai-respond <id> [--file]  # respond to a delegated query

Environment variables:
    RULERYTHING_DIR          Path to rulerything rule system (default: ../rulerything)
    RULERYTHING_PORT         API port (default: 8001)
    RULERYTHING_AUTO_START   Auto-start server on query if not running (default: true)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error


def _ensure_utf8_stdout():
    """Ensure stdout uses UTF-8 encoding to prevent garbled output."""
    try:
        if sys.stdout.encoding and sys.stdout.encoding.upper() not in ("UTF-8", "UTF8"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _safe(text: str, maxlen: int = 0) -> str:
    """Filter non-printable characters and optionally truncate."""
    if maxlen:
        text = text[:maxlen]
    return text


# ── Configuration ──────────────────────────────────────────────────────

RULERYTHING_DIR = os.environ.get(
    "RULERYTHING_DIR",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "rulerything"))
)
API_PORT = int(os.environ.get("RULERYTHING_PORT", "8001"))
API_BASE = f"http://127.0.0.1:{API_PORT}"
AUTO_START = os.environ.get("RULERYTHING_AUTO_START", "true").lower() == "true"
RULE_TRIGGER_LOG = os.environ.get(
    "RULERYTHING_LOG",
    os.path.join(RULERYTHING_DIR, "logs", "rule_triggers.log")
)

# ── API helpers ─────────────────────────────────────────────────────────


def _api(method: str, path: str, body: dict = None) -> dict:
    """Make an HTTP request to the rulerything API."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.URLError:
        return None
    except Exception as e:
        return {"_error": str(e)}


# ── Server lifecycle ────────────────────────────────────────────────────


def _kill_port(port: int):
    """Free the given port by killing the owning process."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if s.connect_ex(("127.0.0.1", port)) != 0:
        s.close()
        return  # Port already free
    s.close()

    system = sys.platform
    try:
        if system == "win32":
            r = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=5
            )
            for line in r.stdout.splitlines():
                if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    os.kill(int(pid), 9)
                    print(f"  Freed port {port} (PID: {pid})")
                    return
        else:
            r = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True, timeout=5
            )
            if r.stdout.strip():
                pid = r.stdout.strip().splitlines()[0]
                os.kill(int(pid), 9)
                print(f"  Freed port {port} (PID: {pid})")
    except Exception:
        pass


def cmd_start():
    """Start the rulerything server (background)."""
    # Check if already running
    status = _api("GET", "/health")
    if status and status.get("status") == "ok":
        print(f"Server already running (port {API_PORT})")
        return

    # Verify rule system directory
    main_py = os.path.join(RULERYTHING_DIR, "main.py")
    if not os.path.exists(main_py):
        print(f"Error: rulerything directory not found at {RULERYTHING_DIR}")
        print(f"Set RULERYTHING_DIR environment variable or clone the repo:")
        print(f"  git clone https://github.com/rulerything-io/rulerything.git {RULERYTHING_DIR}")
        return

    _kill_port(API_PORT)

    # Start uvicorn
    cmd = [
        sys.executable, "-m", "uvicorn", "main:app",
        "--host", "127.0.0.1", "--port", str(API_PORT),
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd, cwd=RULERYTHING_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Wait for startup
    for _ in range(10):
        time.sleep(0.5)
        if _api("GET", "/health"):
            print(f"Rulerything server started (port {API_PORT}, PID: {proc.pid})")
            return

    # Fallback: try platform-specific launcher
    if sys.platform == "win32":
        bat_path = os.path.join(RULERYTHING_DIR, "start.bat")
        if os.path.exists(bat_path):
            print("Direct start failed, trying start.bat...")
            subprocess.Popen(
                [bat_path], cwd=RULERYTHING_DIR,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(15):
                time.sleep(0.5)
                if _api("GET", "/health"):
                    print(f"Rulerything server started (port {API_PORT})")
                    return
    elif sys.platform != "win32":
        sh_path = os.path.join(RULERYTHING_DIR, "start_server.sh")
        if os.path.exists(sh_path):
            print("Direct start failed, trying start_server.sh...")
            subprocess.Popen(
                ["bash", sh_path], cwd=RULERYTHING_DIR,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(15):
                time.sleep(0.5)
                if _api("GET", "/health"):
                    print(f"Rulerything server started (port {API_PORT})")
                    return

    print(f"Server start failed. Try manually:")
    print(f"  cd {RULERYTHING_DIR} && python -m uvicorn main:app --host 127.0.0.1 --port {API_PORT}")


def _ensure_server() -> bool:
    """Ensure the rule server is running; auto-start if enabled. Returns True if ready."""
    status = _api("GET", "/health")
    if status and status.get("status") == "ok":
        return True
    if not AUTO_START:
        return False
    cmd_start()
    for _ in range(10):
        time.sleep(0.5)
        if _api("GET", "/health"):
            return True
    return False


def cmd_restart():
    """Restart the rulerything server."""
    result = _api("POST", "/restart")
    if result is None:
        print("Server not running, attempting to start...")
        cmd_start()
    else:
        print(f"Server restarting: {result.get('message', '')}")


def cmd_stop():
    """Stop the rulerything server."""
    _kill_port(API_PORT)
    _kill_port(8000)  # Also try default port
    print(f"Rulerything server stopped (port {API_PORT})")


def cmd_status():
    """Check server status."""
    status = _api("GET", "/health")
    if status and status.get("status") == "ok":
        stats = _api("GET", "/stats") or {}
        stg = stats.get("storage", {})

        # Check Phase 3 status
        p3 = _api("GET", "/status")
        phases = ["[P1] Entropy Engine"]
        if p3 and "error" not in p3:
            phases.append("[P2] Immune System")
            phases.append("[P3] Adaptive System")
        else:
            phases.append("[P2] Immune System")
            p3_status = _api("GET", "/phase3/status")
            if p3_status and "error" not in p3_status:
                phases.append("[P3] Adaptive System")
            else:
                phases.append("[P3] Adaptive System (inactive)")

        print(f"Status: running (port {API_PORT})")
        print(f"Version: {status.get('version', '?')}")
        print(f"Uptime: {status.get('uptime_seconds', 0)}s")
        print(f"Rules: {stg.get('active_rules', '?')}/{stg.get('total_rules', '?')} active")
        print(f"Categories: {', '.join(stg.get('categories', []))}")
        print(f"Modules: {' | '.join(phases)}")
        print(f"Index: v{status.get('index_version', '?')}")
        print(f"Hot cache: {status.get('hot_cache_size', 0)} entries")
        print(f"Total searches: {status.get('total_searches', 0)}")
    else:
        print("Server not running. Start with: python rule_helper.py start")


# ── Smart search ────────────────────────────────────────────────────────

# Tech-stack keywords → rule category mapping
TECH_CATEGORY_MAP = {
    "python": "python", "django": "python", "fastapi": "python", "flask": "python",
    "go": "go", "golang": "go",
    "react": "react", "jsx": "react", "tsx": "react",
    "vue": "vue",
    "typescript": "typescript", "ts": "typescript",
    "javascript": "javascript", "js": "javascript", "node": "nodejs",
    "node.js": "nodejs", "express": "nodejs", "bun": "nodejs", "deno": "nodejs",
    "rust": "rust",
    "ruby": "ruby", "rails": "ruby",
    "erlang": "erlang", "elixir": "elixir",
    "lua": "lua",
    "zig": "zig",
    "docker": "docker", "container": "docker",
    "kubernetes": "docker", "k8s": "docker",
    "devops": "devops", "ci/cd": "devops", "github actions": "devops",
    "security": "security", "auth": "security", "oauth": "security",
    "jwt": "security", "xss": "security", "csrf": "security", "注入": "security",
    "api": "api", "rest": "api", "graphql": "api", "grpc": "api",
    "css": "css", "tailwind": "css", "style": "css",
    "git": "git",
    "shell": "shell", "bash": "shell", "zsh": "shell", "unix": "shell", "linux": "shell",
    "performance": "performance", "perf": "performance", "optimization": "performance",
    "dotnet": "dotnet", "c#": "dotnet", "csharp": "dotnet", ".net": "dotnet", "asp.net": "dotnet",
    "java": "java", "spring": "java", "jvm": "java",
    "c++": "cpp", "cpp": "cpp",
    "php": "php", "laravel": "php",
    "database": "database", "sql": "database", "postgresql": "database",
    "mysql": "database", "query": "database", "orm": "database",
    "mobile": "mobile", "android": "mobile", "ios": "mobile",
    "flutter": "mobile", "swift": "mobile",
    "embedded": "embedded", "firmware": "embedded", "iot": "embedded", "rtos": "embedded",
    "blockchain": "blockchain", "web3": "blockchain", "solidity": "blockchain",
    "design": "pattern", "architecture": "pattern", "refactor": "pattern",
    "testing": "test", "test": "test", "pytest": "test", "jest": "test",
}


def _detect_categories(query: str) -> list:
    """Detect relevant rule categories from a query by tech-stack keywords."""
    q = query.lower()
    matched = []
    for keyword, cat in TECH_CATEGORY_MAP.items():
        if keyword in q:
            matched.append(cat)
    seen = set()
    return [c for c in matched if not (c in seen or seen.add(c))] or ["all"]


def _try_v2_query(query_text: str, sort_by: str = "confidence",
                  use_semantic: bool = True, limit: int = 8):
    """Try Phase 3 /query endpoint. Returns (results, error_code)."""
    try:
        url = f"{API_BASE}/query"
        body = {
            "query_text": query_text, "sort_by": sort_by,
            "use_semantic": use_semantic, "limit": limit,
        }
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, e.code
    except urllib.error.URLError:
        return None, -1


def _display_v2_results(results: list):
    """Display Phase 3 query results."""
    for r in results[:8]:
        tags = ", ".join(r.get("tags", []))
        conf = r.get("confidence", 0)
        hits = r.get("hit_count", 0)
        title = _safe(r['title'])
        print(f"[{r['id']}] {title}  (confidence: {conf:.2f} | hits: {hits})")
        print(f"    Category: {r['category']} | Tags: {_safe(tags)}")
        content = _safe(r.get("content", "").replace("\n", " "), 180)
        print(f"    {content}")
        print()


def _display_v1_results(results: list):
    """Display v1.0 search results."""
    seen = set()
    unique = []
    for r in sorted(results, key=lambda x: x["confidence"], reverse=True):
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)

    for r in unique[:8]:
        tags = ", ".join(r.get("tags", []))
        title = _safe(r['title'])
        print(f"[{r['id']}] {title}  (confidence: {r['confidence']:.2f})")
        print(f"    Category: {r['category']} | Tags: {_safe(tags)}")
        content = _safe(r.get("content", "").replace("\n", " "), 180)
        print(f"    {content}")
        print()


def cmd_smart_search(query: str, force_v1: bool = False):
    """
    Smart search: v2.0 semantic + category hybrid by default,
    --v1 flag falls back to v1.0 keyword search.
    """
    if not _ensure_server():
        print("(Server start failed)")
        return

    cats = _detect_categories(query)
    is_all = cats == ["all"]

    # ── v2.0 Phase 3 path ──
    if not force_v1:
        result, err = _try_v2_query(query, sort_by="confidence",
                                    use_semantic=True, limit=15)
        if result and result.get("results"):
            results = result["results"]
            if not is_all:
                matched = [r for r in results if r.get("category") in cats]
                others = [r for r in results if r.get("category") not in cats]
                results = matched + others

            if results:
                label = f"v2.0 semantic + category search"
                if not is_all:
                    label += f" (detected: {', '.join(cats)})"
                print(f"{label} — {len(results)} results\n")
                display_rules = results[:8]
                _display_v2_results(display_rules, query)
                _print_rule_summary(display_rules)
                return

        if err:
            print(f"v2.0 query unavailable (error: {err}), falling back to v1.0...")
        else:
            print("v2.0 returned no results, falling back to v1.0...")

    # ── v1.0 fallback path ──
    if is_all:
        cats = []

    print(f"v1.0 keyword search — detected categories: {', '.join(cats) if cats else 'all'}\n")

    all_results = []
    for search_type in ("exact", "prefix", "tag"):
        targets = cats or [None]
        for cat in targets:
            params = {"query": query, "search_type": search_type, "limit": 3}
            if cat:
                params["category"] = cat
            result = _api("POST", "/search", params)
            if result and result.get("results"):
                all_results.extend(result["results"])

    if not all_results:
        for search_type in ("exact", "prefix", "tag"):
            params = {"query": query, "search_type": search_type, "limit": 5}
            result = _api("POST", "/search", params)
            if result and result.get("results"):
                all_results.extend(result["results"])

    if not all_results:
        import re
        keywords = re.findall(r'[a-zA-Z_+#.]+', query)
        for kw in keywords[:5]:
            if len(kw) < 2:
                continue
            for search_type in ("prefix", "tag"):
                params = {"query": kw, "search_type": search_type, "limit": 3}
                result = _api("POST", "/search", params)
                if result and result.get("results"):
                    all_results.extend(result["results"])

    if not all_results:
        print("(No matching rules)")
        return

    seen = set()
    unique = []
    for r in sorted(all_results, key=lambda x: x["confidence"], reverse=True):
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)
    _display_v1_results(all_results)
    _print_rule_summary(unique[:8])


# ── Standard search ─────────────────────────────────────────────────────


def cmd_search(query: str, search_type: str = "prefix", category: str = "all"):
    """Search and print matching rules."""
    if not _ensure_server():
        print("(Server start failed)")
        return
    result = _api("POST", "/search", {
        "query": query,
        "search_type": search_type,
        "category": category,
    })
    if result is None:
        print("(Server not running, skipping rule query)")
        return []

    results = result.get("results", [])
    if not results:
        return []

    for r in results:
        tags = ", ".join(r.get("tags", []))
        print(f"[{r['id']}] {r['title']}")
        print(f"    Category: {r['category']} | Confidence: {r['confidence']} | Tags: {tags}")
        content = r.get("content", "")
        if len(content) > 200:
            content = content[:200] + "..."
        print(f"    Content: {content.replace(chr(10), ' ')}")
        print()
    return results


def cmd_list(category: str = None):
    """List rules, optionally filtered by category."""
    if not _ensure_server():
        print("(Server start failed)")
        return
    path = "/rules"
    if category:
        path += f"?category={category}"
    rules = _api("GET", path)
    if rules is None:
        print("(Server not running)")
        return
    if not rules:
        print("(No rules)")
        return

    by_cat = {}
    for r in rules:
        by_cat.setdefault(r["category"], []).append(r)

    total = len(rules)
    print(f"Total: {total} rules\n")
    for cat, items in sorted(by_cat.items()):
        print(f"── {cat} ({len(items)}) ──")
        for r in items:
            bar = _bar(r.get("confidence", 0))
            print(f"  {r['id']:<25} {r['title']:<35} {bar} {r['confidence']:.2f}  hits:{r.get('hit_count', 0)}")
        print()


def cmd_get(rule_id: str):
    """Get a single rule's full details."""
    if not _ensure_server():
        print("(Server start failed)")
        return
    rules = _api("GET", "/rules")
    if not rules:
        print("(Server not running or no rules)")
        return
    for r in rules:
        if r["id"] == rule_id:
            print(f"{r['id']} — {r['title']}")
            print(f"{'=' * 50}")
            print(f"  Category: {r['category']}")
            print(f"  Tags: {', '.join(r.get('tags', []))}")
            print(f"  Confidence: {_bar(r.get('confidence', 0))} {r.get('confidence', 0):.2f}")
            print(f"  Version: v{r.get('version', 1)}")
            print(f"  Hits: {r.get('hit_count', 0)}")
            print(f"\n  Content:")
            for line in r.get("content", "").split("\n"):
                print(f"    {line}")
            return
    print(f"Rule not found: {rule_id}")


# ── AI delegation ───────────────────────────────────────────────────────


def cmd_ai_pending():
    """View pending queries delegated to the parent AI."""
    if not _ensure_server():
        print("(Server start failed)")
        return
    try:
        data = _api("GET", "/ai/pending?limit=20")
    except Exception as e:
        print(f"Failed to fetch: {e}")
        return
    queries = data.get("queries", [])
    counts = data.get("counts", {})
    print(f"Delegated queries — pending: {counts.get('pending', 0)} / answered: {counts.get('answered', 0)} / total: {counts.get('total', 0)}")
    if not queries:
        print("No pending queries.")
        return
    print()
    for q in queries:
        qid = q.get("id", "?")
        qtext = q.get("query", "")[:80]
        ts = q.get("created_at", "")[11:22]
        print(f"  [{ts}] {qid}")
        print(f"    Question: {qtext}")
        print()


def cmd_ai_respond(query_id: str, response_file: str = None):
    """Answer a delegated query."""
    if not query_id or query_id == "?":
        print("Usage: python rule_helper.py ai-respond <query_id> [--file response.txt]")
        return
    if response_file:
        try:
            with open(response_file, "r", encoding="utf-8") as f:
                response = f.read()
        except FileNotFoundError:
            print(f"File not found: {response_file}")
            return
    else:
        print("Enter response (Ctrl+D / Ctrl+Z to finish):")
        response = sys.stdin.read()
    if not response.strip():
        print("Response cannot be empty.")
        return
    try:
        result = _api("POST", "/ai/respond", {"query_id": query_id, "response": response})
    except Exception as e:
        print(f"Submit failed: {e}")
        return
    if result and result.get("status") == "ok":
        print(f"Response submitted (query_id={query_id})")
    else:
        err = (result or {}).get("_error", str(result))
        print(f"Submit failed: {err}")
        print("Tip: ensure server is running and query_id is valid")


# ── Utilities ───────────────────────────────────────────────────────────


def _print_rule_summary(rule_ids: list):
    """Print a one-line rule trigger summary."""
    if not rule_ids:
        return
    ids = [r.get("id", r) if isinstance(r, dict) else r for r in rule_ids]
    print()
    print(f"Triggered rules: {' · '.join(ids)}")


def _bar(conf: float, width: int = 10) -> str:
    filled = int(conf * width)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _background_start():
    """Start the rule server in a background thread (non-blocking)."""
    import threading

    def _start():
        status = _api("GET", "/health")
        if status and status.get("status") == "ok":
            return
        cmd_start()

    t = threading.Thread(target=_start, daemon=True)
    t.start()


# ═══════════════════════════════════════════════════════════════════════
# Auto-start on import (when running as a module or via Claude Code hooks)
# ═══════════════════════════════════════════════════════════════════════
if AUTO_START:
    _background_start()


# ── CLI entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    _ensure_utf8_stdout()
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "restart":
        cmd_restart()
    elif cmd == "status":
        cmd_status()
    elif cmd == "search":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        search_type = "prefix"
        category = "all"
        for i, a in enumerate(sys.argv[3:]):
            if a == "--type" and i + 4 < len(sys.argv):
                search_type = sys.argv[i + 4]
            if a == "--cat" and i + 4 < len(sys.argv):
                category = sys.argv[i + 4]
        cmd_search(query, search_type, category)
    elif cmd == "smart":
        query = sys.argv[2] if len(sys.argv) > 2 else input("Query: ")
        force_v1 = "--v1" in sys.argv
        cmd_smart_search(query, force_v1=force_v1)
    elif cmd == "list":
        cat = None
        for i, a in enumerate(sys.argv[2:]):
            if a == "--cat" and i + 3 < len(sys.argv):
                cat = sys.argv[i + 3]
        cmd_list(cat)
    elif cmd == "get":
        if len(sys.argv) > 2:
            cmd_get(sys.argv[2])
        else:
            print("Usage: python rule_helper.py get <rule_id>")
    elif cmd == "ai-pending":
        cmd_ai_pending()
    elif cmd == "ai-respond":
        qid = sys.argv[2] if len(sys.argv) > 2 else "?"
        resp_file = None
        for i, a in enumerate(sys.argv[3:]):
            if a == "--file" and i + 4 < len(sys.argv):
                resp_file = sys.argv[i + 4]
        cmd_ai_respond(qid, resp_file)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
