"""
╔══════════════════════════════════════════════════════╗
║        Deribit Bot — Web Dashboard API               ║
║                                                      ║
║  Run with:                                           ║
║    uvicorn api:app --host 0.0.0.0 --port 5000        ║
║                                                      ║
║  Access at:                                          ║
║    http://<YOUR_PI_IP>:5000                          ║
╚══════════════════════════════════════════════════════╝
"""

import os
import csv
import json
import subprocess
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import ccxt

# ─────────────────────────────────────────────────────
#  SETUP
# ─────────────────────────────────────────────────────
load_dotenv("/home/pi/deribit-bot/Keys.env")

app = FastAPI(title="Deribit Bot Dashboard")

# Allow React frontend to talk to this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the React frontend
dashboard_path = Path("/home/pi/deribit-bot/dashboard")
if dashboard_path.exists():
    app.mount("/static", StaticFiles(directory=str(dashboard_path)), name="static")

CONFIG = {
    "short_trigger_pct":  2.0,
    "take_profit_pct":    100.0,
    "stop_loss_pct":      90.0,
    "contracts":          10,
    "check_interval_sec": 60,
    "dry_run":            True,
    "symbol":             "BTC/USD:BTC",
    "log_dir":            "/home/pi/deribit-bot/logs",
    "lookback_hours":     1,
    "max_trades_per_day": 10,
}

LOG_DIR = Path(CONFIG["log_dir"])


# ─────────────────────────────────────────────────────
#  EXCHANGE CONNECTION
# ─────────────────────────────────────────────────────
def get_exchange():
    exchange = ccxt.deribit({
        "apiKey": os.getenv("DERIBIT_CLIENT_ID"),
        "secret": os.getenv("DERIBIT_CLIENT_SECRET"),
    })
    exchange.set_sandbox_mode(CONFIG["dry_run"])
    return exchange


# ─────────────────────────────────────────────────────
#  API ROUTES
# ─────────────────────────────────────────────────────

@app.get("/")
def serve_dashboard():
    """Serve the React dashboard."""
    index = dashboard_path / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Dashboard not found — make sure dashboard/index.html exists"}


@app.get("/api/status")
def get_status():
    """Returns whether the bot service is running."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "deribit-bot"],
            capture_output=True,
            text=True
        )
        running = result.stdout.strip() == "active"
    except Exception:
        running = False

    return {
        "running":     running,
        "status":      "Running" if running else "Stopped",
        "mode":        "TESTNET" if CONFIG["dry_run"] else "LIVE",
        "timestamp":   datetime.now().isoformat(),
    }


@app.get("/api/price")
def get_price():
    """Returns current BTC price and % change."""
    try:
        exchange = get_exchange()
        ticker   = exchange.fetch_ticker(CONFIG["symbol"])

        # Fetch candles for lookback period
        candles    = exchange.fetch_ohlcv(CONFIG["symbol"], "1h", limit=CONFIG["lookback_hours"] + 2)
        price_then = candles[0][4]
        price_now  = candles[-1][4]
        change_pct = (price_now - price_then) / price_then * 100 if price_then > 0 else 0.0

        return {
            "price":      ticker["last"],
            "change_pct": round(change_pct, 2),
            "high_24h":   ticker.get("high"),
            "low_24h":    ticker.get("low"),
            "symbol":     CONFIG["symbol"],
            "lookback_hours": CONFIG["lookback_hours"],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/position")
def get_position():
    """Returns the current open short position if any."""
    try:
        exchange  = get_exchange()
        positions = exchange.fetch_positions([CONFIG["symbol"]])

        for p in positions:
            contracts = float(p.get("contracts", 0))
            if contracts < 0:
                entry   = float(p.get("entryPrice", 0))
                mark    = float(p.get("markPrice", 0))
                leverage = float(p.get("leverage", 1))

                # Calculate ROI
                if entry > 0 and mark > 0:
                    price_change = (entry - mark) / entry * 100
                    roi = price_change * leverage
                else:
                    roi = 0.0

                return {
                    "open":             True,
                    "contracts":        abs(contracts),
                    "entry_price":      entry,
                    "mark_price":       mark,
                    "leverage":         leverage,
                    "roi":              round(roi, 2),
                    "take_profit":      CONFIG["take_profit_pct"],
                    "stop_loss":        CONFIG["stop_loss_pct"],
                    "trailing_stop_pct": CONFIG.get("trailing_stop_pct", 20.0),
                }

        return {"open": False}

    except Exception as e:
        return {"error": str(e)}


@app.get("/api/trades")
def get_trades():
    """Returns trade history from the CSV file."""
    csv_file = LOG_DIR / "trade_history.csv"

    if not csv_file.exists():
        return {"trades": []}

    trades = []
    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(row)
    except Exception as e:
        return {"error": str(e)}

    # Return most recent trades first
    trades.reverse()
    return {"trades": trades}


@app.get("/api/config")
def get_config():
    """Returns the current bot configuration."""
    return CONFIG

@app.get("/api/summary")
def get_summary():
    """Returns total profit/loss across all closed trades."""
    csv_file = LOG_DIR / "trade_history.csv"

    if not csv_file.exists():
        return {"total_usd": 0.0, "trade_count": 0, "wins": 0, "losses": 0}

    total_usd  = 0.0
    total_roi  = 0.0
    trade_count = 0
    wins       = 0
    losses     = 0

    try:
        with open(csv_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Action") != "CLOSE_SHORT":
                    continue

                trade_count += 1

                # Parse USD profit — strip $ sign
                usd_str = row.get("Profit USD", "").replace("$", "").strip()
                try:
                    usd = float(usd_str)
                    total_usd += usd
                    if usd >= 0:
                        wins += 1
                    else:
                        losses += 1
                except ValueError:
                    pass

                # Parse ROI % — strip % sign and +
                roi_str = row.get("Profit %", "").replace("%", "").replace("+", "").strip()
                try:
                    total_roi += float(roi_str)
                except ValueError:
                    pass

    except Exception as e:
        return {"error": str(e)}

    avg_roi = round(total_roi / trade_count, 2) if trade_count > 0 else 0.0

    return {
        "total_usd":   round(total_usd, 2),
        "avg_roi":     avg_roi,
        "trade_count": trade_count,
        "wins":        wins,
        "losses":      losses,
    }

@app.get("/api/logs")
def get_logs():
    """Returns the last 50 lines of the activity log."""
    log_file = LOG_DIR / "deribit_bot.log"

    if not log_file.exists():
        return {"lines": []}

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # Return last 50 lines, most recent first
        return {"lines": [l.strip() for l in lines[-50:]][::-1]}
    except Exception as e:
        return {"error": str(e)}