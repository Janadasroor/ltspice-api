import sys, os

# Load TELEGRAM_BOT_TOKEN from persistent user env var
_token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not _token:
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "[Environment]::GetEnvironmentVariable('TELEGRAM_BOT_TOKEN', 'User')"],
            capture_output=True, text=True, timeout=5
        )
        _token = result.stdout.strip()
        if _token:
            os.environ["TELEGRAM_BOT_TOKEN"] = _token
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import uvicorn
from api.server import app

if __name__ == "__main__":
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
