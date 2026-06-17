# ─────────────────────────────────────────────────────────────────────────
# Rulerything — Post-session-start hook for Claude Code (Windows/PowerShell)
#
# Automatically starts the rule server in the background when a Claude Code
# session begins.
#
# Installation:
#   In Claude Code settings (settings.json), add:
#     "hooks": {
#       "PostSessionStart": "powershell -File C:\path\to\rulerything-skill\hooks\post-session-start.ps1"
#     }
# ─────────────────────────────────────────────────────────────────────────

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$skillDir = Split-Path -Parent $scriptDir
$rulerythingDir = [Environment]::GetEnvironmentVariable("RULERYTHING_DIR", "User")
if (-not $rulerythingDir) { $rulerythingDir = Join-Path (Split-Path -Parent $skillDir) "rulerything" }
$port = [Environment]::GetEnvironmentVariable("RULERYTHING_PORT", "User")
if (-not $port) { $port = "8001" }

# Check if server is already running
try {
    $req = [System.Net.WebRequest]::Create("http://127.0.0.1:$port/health")
    $req.Timeout = 2000
    $resp = $req.GetResponse()
    $resp.Close()
    exit 0  # Already running
} catch {
    # Server not running, continue to start
}

# Start the server
$mainPy = Join-Path $rulerythingDir "main.py"
if (Test-Path $mainPy) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "python"
    $psi.Arguments = "-m uvicorn main:app --host 127.0.0.1 --port $port --log-level warning"
    $psi.WorkingDirectory = $rulerythingDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
}
