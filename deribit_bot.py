
"""
╔══════════════════════════════════════════════════════╗
║        Bitcoin Futures Bot — Deribit Edition         ║
║                                                      ║
║  Logs written to:                                    ║
║    C:\\temp\\deribit_bot.log   ← human readable      ║
║    C:\\temp\\trade_history.csv ← open in Excel       ║
╚══════════════════════════════════════════════════════╝
"""

import time
import logging
import os
import csv
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import ccxt

# ─────────────────────────────────────────────────────
#  YOUR SETTINGS
# ─────────────────────────────────────────────────────
CONFIG = {
    "short_trigger_pct":  3.0,
    "take_profit_pct":    5.0,
    "stop_loss_pct":      50.0,
    "contracts":          10,
    "check_interval_sec": 60,
    "dry_run":            True,
    "symbol":             "BTC/USD:BTC",
    "log_dir":            "/home/pi/deribit-bot/logs",
    "lookback_hours":     12,
    "max_trades_per_day": 10,       # Maximum positions to open per day
}
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
#   DAILY TRADE COUNTER
# - Limits the amount of positions the bot can open on a daily basis.
# ─────────────────────────────────────────────────────
trade_counter = {
    "date":  None,   # tracks which date the counter belongs to
    "count": 0,      # how many trades opened today
}

def check_trade_limit() -> bool:
    """
    Returns True if we are allowed to open a new trade today.
    Resets the counter automatically at midnight.
    """
    today = datetime.now().date()

    # If it's a new day, reset the counter
    if trade_counter["date"] != today:
        trade_counter["date"]  = today
        trade_counter["count"] = 0
        log.info(f"New day — trade counter reset (limit: {CONFIG['max_trades_per_day']})")

    remaining = CONFIG["max_trades_per_day"] - trade_counter["count"]

    if trade_counter["count"] >= CONFIG["max_trades_per_day"]:
        log.info(
            f"Daily trade limit reached "
            f"({trade_counter['count']}/{CONFIG['max_trades_per_day']}) — "
            f"no new positions until midnight"
        )
        return False

    log.info(f"  Trades today: {trade_counter['count']}/{CONFIG['max_trades_per_day']} ({remaining} remaining)")
    return True

def increment_trade_counter():
    """Call this every time a new short is successfully opened."""
    trade_counter["count"] += 1
    log.info(f"Trade counter: {trade_counter['count']}/{CONFIG['max_trades_per_day']} today")

# ─────────────────────────────────────────────────────
#  LOGGING SETUP — creates C:\temp if it doesn't exist
# ─────────────────────────────────────────────────────
def setup_logging():
    """
    Sets up two log outputs:
      1. deribit_bot.log  — full activity log, human readable
      2. trade_history.csv — one row per trade, open in Excel

    Also creates C:\\temp automatically if it doesn't exist.
    """
    log_dir  = Path(CONFIG["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)  # creates C:\temp if missing

    log_file = log_dir / "deribit_bot.log"
    csv_file = log_dir / "trade_history.csv"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

    log = logging.getLogger(__name__)
    log.info(f"Activity log : {log_file}")
    log.info(f"Trade history: {csv_file}")

    # Write CSV header if the file is new
    if not csv_file.exists():
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Date",
                "Time",
                "Action",           # OPEN_SHORT or CLOSE_SHORT
                "Reason",           # SIGNAL / TAKE_PROFIT / STOP_LOSS
                "BTC Price (USD)",
                "Contracts",
                "Exposure (USD)",
                "Entry Price",
                "Exit Price",
                "Profit %",
                "Profit USD",
                "Mode",             # TESTNET or LIVE
            ])

    return log, csv_file


# ─────────────────────────────────────────────────────
#  CSV WRITER — called every time a trade opens or closes
# ─────────────────────────────────────────────────────
def log_trade(csv_file: Path, action: str, reason: str, price: float,
              entry_price: float = None, profit_pct: float = None):
    
    
    """
    Appends one row to trade_history.csv.

    action      : "OPEN_SHORT" or "CLOSE_SHORT"
    reason      : "SIGNAL", "TAKE_PROFIT", or "STOP_LOSS"
    price       : current BTC price at time of trade
    entry_price : original short entry price (only filled on close)
    profit_pct  : % gain/loss (only filled on close)
    """
    now        = datetime.now()
    contracts  = CONFIG["contracts"]
    exposure   = contracts * 10   # $10 per contract on Deribit
    mode       = "TESTNET" if CONFIG["dry_run"] else "LIVE"

    # Calculate dollar profit if we have enough info
    profit_usd = ""
    if profit_pct is not None and entry_price is not None:
        profit_usd = round(exposure * (profit_pct / 100), 2)

    row = [
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S"),
        action,
        reason,
        f"{price:,.2f}",
        contracts,
        f"${exposure}",
        f"{entry_price:,.2f}" if entry_price else "",
        f"{price:,.2f}"       if action == "CLOSE_SHORT" else "",
        f"{profit_pct:+.2f}%" if profit_pct is not None else "",
        f"${profit_usd}"      if profit_usd != "" else "",
        mode,
    ]

    with open(csv_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


# ─────────────────────────────────────────────────────
#  CONNECT TO DERIBIT
# ─────────────────────────────────────────────────────
def init_exchange(log):
    load_dotenv("/home/pi/deribit-bot/Keys.env")

    client_id     = os.getenv("DERIBIT_CLIENT_ID")
    client_secret = os.getenv("DERIBIT_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError(
            "Missing API credentials! "
            "Add DERIBIT_CLIENT_ID and DERIBIT_CLIENT_SECRET to your .env file."
        )

    exchange = ccxt.deribit({
        "apiKey": client_id,
        "secret": client_secret,
    })

    exchange.set_sandbox_mode(CONFIG["dry_run"])

    mode = "TESTNET (test.deribit.com)" if CONFIG["dry_run"] else "⚠️  LIVE (deribit.com)"
    log.info(f"Connected to Deribit — {mode}")

    return exchange


# ─────────────────────────────────────────────────────
#  MARKET DATA
# ─────────────────────────────────────────────────────
def get_market_data(exchange) -> dict:
    # Get current price from ticker
    ticker = exchange.fetch_ticker(CONFIG["symbol"])
    price  = ticker["last"]

    # Fetch hourly candles going back lookback_hours
    # Each candle = [timestamp, open, high, low, close, volume]
    candles = exchange.fetch_ohlcv(
        CONFIG["symbol"],
        timeframe="1h",
        limit=CONFIG["lookback_hours"] + 2
    )

    oldest_candle = candles[0]
    newest_candle = candles[-1]
    price_then    = oldest_candle[4]  # [4] = close price of oldest candle
    price_now     = newest_candle[4]  # [4] = close price of newest candle

    log.info(
        f"  📊 Debug — "
        f"Price {CONFIG['lookback_hours']}h ago: ${price_then:,.2f} | "
        f"Price now: ${price_now:,.2f}"
    )

    # Calculate % change
    if price_then and price_then > 0:
        change_pct = (price_now - price_then) / price_then * 100
    else:
        change_pct = 0.0

    return {
        "price":      price,
        "change_pct": change_pct,
    }

# ─────────────────────────────────────────────────────
#  POSITION HELPERS
# ─────────────────────────────────────────────────────
def get_open_short(exchange) -> dict | None:
    """Returns the open short position, or None if there isn't one."""
    positions = exchange.fetch_positions([CONFIG["symbol"]])

    # Debug — log everything Deribit returns so we can see the format
    log.info(f"  🔍 Positions returned: {len(positions)}")
    for i, p in enumerate(positions):
        log.info(f"  🔍 Position {i}: side={p.get('side')} | contracts={p.get('contracts')} | symbol={p.get('symbol')}")

    for p in positions:
        if p["side"] == "short" and float(p.get("contracts", 0)) > 0:
            return p
    return None

def calc_profit_pct(position: dict) -> float:
    """
    Calculates unrealised P&L for our short.
    Positive = profit (price fell), Negative = loss (price rose).

    Formula: (entry - current) / entry * 100
    """
    entry   = float(position["entryPrice"])
    current = float(position["markPrice"])
    return (entry - current) / entry * 100


# ─────────────────────────────────────────────────────
#  TRADE ACTIONS
# ─────────────────────────────────────────────────────
def open_short(exchange, log, csv_file, price: float):
    """Opens a short by placing a SELL market order."""
    log.info(
        f"Opening SHORT — "
        f"{CONFIG['contracts']} contracts @ ${price:,.2f} "
        f"(${CONFIG['contracts'] * 10} exposure)"
    )
    try:
        order = exchange.create_order(
            symbol=CONFIG["symbol"],
            type="market",
            side="sell",
            amount=CONFIG["contracts"],
            params={"reduceOnly": False}
        )
        log.info(f"Short OPENED! Order ID: {order['id']}")
        log_trade(csv_file, action="OPEN_SHORT", reason="SIGNAL", price=price)
        increment_trade_counter()  
        return order

    except ccxt.InsufficientFunds:
        log.error("Insufficient funds — add BTC to your testnet account at test.deribit.com")
    except ccxt.ExchangeError as e:
        log.error(f"Exchange error opening short: {e}")

    return None


def close_short(exchange, log, csv_file, position: dict, reason: str, price: float):
    """Closes our short by placing a BUY market order."""
    contracts   = float(position["contracts"])
    entry_price = float(position["entryPrice"])
    profit_pct  = calc_profit_pct(position)

    log.info(
        f"Closing SHORT ({reason}) | "
        f"Entry: ${entry_price:,.2f} → Exit: ${price:,.2f} | "
        f"P&L: {profit_pct:+.2f}%"
    )

    try:
        order = exchange.create_order(
            symbol=CONFIG["symbol"],
            type="market",
            side="buy",
            amount=contracts,
            params={"reduceOnly": True}
        )
        log.info(f"Short CLOSED! Order ID: {order['id']}")
        log_trade(
            csv_file,
            action="CLOSE_SHORT",
            reason=reason,
            price=price,
            entry_price=entry_price,
            profit_pct=profit_pct,
        )
        return order

    except ccxt.ExchangeError as e:
        log.error(f"Exchange error closing short: {e}")

    return None


# ─────────────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────────────
def run():
    log, csv_file = setup_logging()

    log.info("=" * 55)
    log.info("Deribit BTC Futures Bot starting...")
    log.info(f"   Short trigger : +{CONFIG['short_trigger_pct']}% {CONFIG['lookback_hours']}h rise")
    log.info(f"   Take profit   : +{CONFIG['take_profit_pct']}%")
    log.info(f"   Stop loss     : -{CONFIG['stop_loss_pct']}%")
    log.info(f"   Contracts     : {CONFIG['contracts']} (${CONFIG['contracts'] * 10} USD)")
    log.info(f"   Check every   : {CONFIG['check_interval_sec']}s")
    log.info("=" * 55)

    exchange = init_exchange(log)

    while True:
        try:
            market        = get_market_data(exchange)
            price         = market["price"]
            change_pct    = market["change_pct"]
            open_position = get_open_short(exchange)

            log.info(
                f"BTC: ${price:,.2f} | "
                f"{CONFIG['lookback_hours']}h: {change_pct:+.2f}% | "
                f"Short open: {'YES' if open_position else 'NO'}"
            )

            # ── Manage open position ──────────────────────────
            if open_position:
                profit_pct  = calc_profit_pct(open_position)
                entry_price = float(open_position["entryPrice"])

                log.info(
                    f"  └─ Entry: ${entry_price:,.2f} | "
                    f"Mark: ${price:,.2f} | "
                    f"P&L: {profit_pct:+.2f}%"
                )

                if profit_pct >= CONFIG["take_profit_pct"]:
                    log.info(f"  └─ Take profit triggered! ({profit_pct:+.2f}%)")
                    close_short(exchange, log, csv_file, open_position, "TAKE_PROFIT", price)

                elif profit_pct <= -CONFIG["stop_loss_pct"]:
                    log.info(f"  └─ Stop loss triggered! ({profit_pct:+.2f}%)")
                    close_short(exchange, log, csv_file, open_position, "STOP_LOSS", price)

                else:
                    log.info(
                        f"  └─ Holding position "
                        f"(TP: +{CONFIG['take_profit_pct']}% | "
                        f"SL: -{CONFIG['stop_loss_pct']}%)"
                    )

            # ── Look for entry signal ─────────────────────────
            else:
                if change_pct >= CONFIG["short_trigger_pct"]:
                    log.info(f"  └─ 🚨 Entry signal! BTC up {change_pct:+.2f}% in 6h")
                    if check_trade_limit():
                        open_short(exchange, log, csv_file, price)
                else:
                    needed = CONFIG["short_trigger_pct"] - change_pct
                    log.info(
                        f"  └─ No signal yet. "
                        f"Need {needed:.2f}% more rise to trigger "
                        f"(currently {change_pct:+.2f}%)"
                    )

        except ccxt.NetworkError as e:
            log.warning(f"⚠️  Network error (will retry next cycle): {e}")

        except ccxt.AuthenticationError as e:
            log.error(f"🔑 Authentication failed — check your .env file: {e}")
            break

        except ccxt.ExchangeError as e:
            log.error(f"❌ Exchange error: {e}")

        except KeyboardInterrupt:
            log.info("👋 Bot stopped by user (Ctrl+C)")
            break

        except Exception as e:
            log.exception(f"💥 Unexpected error: {e}")

        log.info(f"  💤 Next check in {CONFIG['check_interval_sec']}s...\n")
        time.sleep(CONFIG["check_interval_sec"])


if __name__ == "__main__":
    run()
