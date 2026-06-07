@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   Rule System API Server (port 8001)
echo ============================================

REM Auto-kill any existing process on port 8001
python -c "
import socket, os, signal
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
if s.connect_ex(('127.0.0.1', 8001)) == 0:
    s.close()
    import subprocess
    r = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if '127.0.0.1:8001' in line and 'LISTENING' in line:
            pid = line.strip().split()[-1]
            os.kill(int(pid), 9)
            print('  [OK] Freed port 8001')
            break
else:
    s.close()
" 2>&1

echo.
echo   Dashboard : http://localhost:8001
echo   API Docs  : http://localhost:8001/docs
echo   Ctrl+C to stop
echo.

python -m uvicorn main:app --host 127.0.0.1 --port 8001 --log-level warning

pause
