# LTspice Automation Stack

Python library + FastAPI REST API + Telegram bot for automating LTspice simulations over LAN.

## Quick Start

```bash
pip install -r requirements.txt
set TELEGRAM_BOT_TOKEN=your_token_here
python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

## Components

### `ltspice/` — Python library
- `Netlist` / `Circuit` — build SPICE netlists programmatically
- `run_netlist(text)` — simulate from netlist string
- `run_simulation(path)` — simulate from file
- `RawFile` — parse binary `.raw` files
- `SimulationResult` — access signals, measurements, FFT, summary

### `api/` — FastAPI REST API
- `POST /simulate` — simulate a netlist
- `POST /circuit` — simulate from component definition
- `POST /netlist` — generate netlist only
- `GET /results/{id}` — result metadata
- `GET /results/{id}/data?var=X` — signal data
- `GET /results/{id}/fft?var=X` — FFT analysis
- `GET /results/{id}/measurements` — .meas results
- `GET /results/{id}/summary` — formatted summary (used by bot)
- `GET /results/{id}/log` — raw LTspice log
- `GET /results/{id}/netlist` — original netlist
- `DELETE /results/{id}` — free server resources
- `GET /telegram/status` — bot status

### `api/telegram_bot.py` — Telegram bot (@LtspiceRunnerBot)
- Send a SPICE netlist as text or file (`.txt`, `.cir`, `.net`, `.sp`, `.asc`, `.raw`)
- `/avg [var]` — average value (empty = all signals)
- `/rms [var]` — RMS value (empty = all signals)
- `/img [var]` — plot image (empty = all signals as album)
- `/status` — bot status

Results are stored per user for 5 hours. Sending a new netlist replaces the previous result immediately.

## Network

Bind to `0.0.0.0` for LAN access. Ensure Windows Firewall allows inbound on port `8000`.

## Tests

```bash
python test_api.py
```
