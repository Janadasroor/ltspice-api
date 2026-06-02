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
    $pipOutput = python -m pip install -r $req 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: pip install failed" -ForegroundColor Red
        Write-Host $pipOutput -ForegroundColor Red
        Write-Host ""
        Write-Host "Try installing manually:" -ForegroundColor Yellow
        Write-Host "  python -m pip install fastapi uvicorn numpy pywin32 python-telegram-bot matplotlib" -ForegroundColor Gray
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

# --- Install MCP ---
$mcpDir = Join-Path $PSScriptRoot "ltspice-mcp"
if (Test-Path $mcpDir) {
    Write-Host "Installing LTspice MCP package..." -ForegroundColor Yellow
    python -m pip install -e $mcpDir
    if ($LASTEXITCODE -eq 0) {
        # Find the actual executable path
        $mcpExe = Join-Path $env:APPDATA "Python\Python313\Scripts\ltspice-mcp.exe"
        if (-not (Test-Path $mcpExe)) {
            $mcpExe = (Get-Command ltspice-mcp -ErrorAction SilentlyContinue).Source
        }
        
        if ($mcpExe) {
            Write-Host "Configuring AI Agents..." -ForegroundColor Cyan
            
            # 1. Gemini CLI
            if (Get-Command gemini -ErrorAction SilentlyContinue) {
                Write-Host "  -> Registering with Gemini CLI (User Scope)..." -ForegroundColor Gray
                gemini mcp add --scope user --trust ltspice-mcp $mcpExe --api-url http://127.0.0.1:8000
            }

            # 2. Codex Agent
            if (Get-Command codex -ErrorAction SilentlyContinue) {
                Write-Host "  -> Registering with Codex Agent..." -ForegroundColor Gray
                codex mcp add ltspice-mcp $mcpExe -- --api-url http://127.0.0.1:8000
            }

            # 3. Claude Desktop
            $claudeConfig = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
            if (Test-Path (Split-Path $claudeConfig)) {
                Write-Host "  -> Registering with Claude Desktop..." -ForegroundColor Gray
                if (-not (Test-Path $claudeConfig)) {
                    '{"mcpServers": {}}' | Out-File -FilePath $claudeConfig -Encoding utf8
                }
                $configJson = Get-Content $claudeConfig | ConvertFrom-Json
                if (-not $configJson.mcpServers) { $configJson | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value @{} }
                
                $mcpEntry = @{
                    command = $mcpExe
                    args = @("--api-url", "http://127.0.0.1:8000")
                }
                
                if ($configJson.mcpServers.PSObject.Properties["ltspice-mcp"]) {
                    $configJson.mcpServers."ltspice-mcp" = $mcpEntry
                } else {
                    $configJson.mcpServers | Add-Member -MemberType NoteProperty -Name "ltspice-mcp" -Value $mcpEntry
                }
                $configJson | ConvertTo-Json -Depth 10 | Out-File -FilePath $claudeConfig -Encoding utf8
            }
        } else {
            Write-Host "WARNING: ltspice-mcp executable not found. Manual registration may be required." -ForegroundColor Yellow
        }
    }
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
