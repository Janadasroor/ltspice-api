param(
    [string]$LtspicePath = "",
    [switch]$NoPrompt
)

Write-Host "=== LTspice Automation Stack Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- Find LTspice ---
if (-not $LtspicePath) {
    $candidates = @(
        "${env:LOCALAPPDATA}\Programs\ADI\LTspice\LTspice.exe",
        "${env:ProgramFiles}\ADI\LTspice\LTspice.exe",
        "${env:ProgramFiles(x86)}\ADI\LTspice\LTspice.exe",
        "${env:ProgramFiles}\LTC\LTspiceXVII\XVIIx64.exe",
        "${env:ProgramFiles(x86)}\LTC\LTspiceXVII\XVIIx64.exe"
    )
    $found = $false
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            $LtspicePath = $c
            $found = $true
            break
        }
    }
    if (-not $found) {
        $LtspicePath = Read-Host "LTspice not found in default locations. Enter path to LTspice.exe"
    }
}

if (-not (Test-Path $LtspicePath)) {
    Write-Host "ERROR: LTspice not found at: $LtspicePath" -ForegroundColor Red
    exit 1
}
Write-Host "LTspice: $LtspicePath" -ForegroundColor Green

# --- Check Python ---
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "ERROR: Python not found. Install Python 3.10+ first." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $($py.Source)" -ForegroundColor Green

# --- Install deps ---
$req = Join-Path $PSScriptRoot "requirements.txt"
if (Test-Path $req) {
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    python -m pip install -r $req
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install failed" -ForegroundColor Red
        exit 1
    }
}

# --- Create .env ---
$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    "LTSPICE_EXE=$LtspicePath" | Out-File -FilePath $envFile -Encoding utf8
    "# TELEGRAM_BOT_TOKEN=your_token_here" | Out-File -FilePath $envFile -Encoding utf8 -Append
    Write-Host "Created .env (edit to add your Telegram bot token)" -ForegroundColor Green
} else {
    Write-Host ".env already exists, skipping" -ForegroundColor Yellow
}

# --- Verify ---
Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan
python -c "import ltspice; print('Library OK, version:', ltspice.__version__)" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Library import: OK" -ForegroundColor Green
} else {
    Write-Host "Library import: FAILED (run from project root)" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Quick start:" -ForegroundColor White
Write-Host "  python -m uvicorn api.server:app --host 0.0.0.0 --port 8000" -ForegroundColor Gray
Write-Host "  python test_api.py" -ForegroundColor Gray
