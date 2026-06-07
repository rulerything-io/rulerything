#!/bin/bash
# 启动规则系统 API 服务（后台运行）
# 用法: bash start_server.sh [start|stop|status]

PORT=${RULES_PORT:-8321}
PID_FILE="/tmp/rules-server.pid"
LOG_DIR="logs"

case "${1:-start}" in
  start)
    mkdir -p "$LOG_DIR"
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Server already running (PID $(cat "$PID_FILE"))"
      exit 0
    fi
    cd "$(dirname "$0")"
    nohup python -m uvicorn main:app \
      --host 127.0.0.1 \
      --port "$PORT" \
      --log-level warning \
      >> "$LOG_DIR/server.log" 2>&1 &
    echo "$!" > "$PID_FILE"
    # Wait briefly then check
    sleep 2
    if kill -0 "$!" 2>/dev/null; then
      echo "Rule server started on port $PORT (PID $!)"
    else
      echo "Server failed to start. Check logs/server.log"
      rm -f "$PID_FILE"
    fi
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      kill "$(cat "$PID_FILE")" 2>/dev/null
      rm -f "$PID_FILE"
      echo "Server stopped"
    else
      echo "No server running"
    fi
    ;;
  status)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Server running (PID $(cat "$PID_FILE"))"
    else
      echo "Server not running"
    fi
    ;;
  *)
    echo "Usage: $0 [start|stop|status]"
    ;;
esac
