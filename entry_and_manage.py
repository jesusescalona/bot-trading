#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BOT BINANCE FUTURES – ORDER FLOW PROXY (TERMUX SAFE)
Incluye:
- Entrada por agresión real (orderflow proxy)
- Filtro de volatilidad (anti-chop)
- TP / SL monetarios
- Cooldown post-SL
- Sin pandas
"""

import os, json, time, math, requests
from binance.client import Client

CFG_PATH = "config_binance.json"
CFG = {}

BINANCE_KEY = os.getenv("BINANCE_KEY") or os.getenv("BINANCE_API_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET_KEY") or os.getenv("BINANCE_API_SECRET")
TG_TOKEN = os.getenv("TG_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")

if not BINANCE_KEY or not BINANCE_SECRET:
    raise RuntimeError("Faltan credenciales de Binance")

client = Client(BINANCE_KEY, BINANCE_SECRET)

state = {
    "last_signal": None,
    "shutdown": False,
    "cooldown_until": 0,
    "last_vol_block_ts": 0
}

# -------------------------
# Telegram
# -------------------------
def tg(msg):
    if not TG_TOKEN or not TG_CHAT_ID:
        print(msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10
        )
    except:
        pass

# -------------------------
# Market utils
# -------------------------
def mark_price(sym):
    return float(client.futures_mark_price(symbol=sym)["markPrice"])

def get_step(sym):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == sym:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return float(f["stepSize"])
    return 0.001

def round_qty(qty, step):
    return math.floor(qty / step) * step

# -------------------------
# Data
# -------------------------
def fetch_1m(symbol, limit=30):
    kl = client.futures_klines(
        symbol=symbol,
        interval=Client.KLINE_INTERVAL_1MINUTE,
        limit=limit
    )
    candles = []
    for k in kl:
        candles.append({
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "taker_buy": float(k[9])
        })
    return candles

# -------------------------
# Orderflow proxy
# -------------------------
def orderflow_signal(candles, lookback, vol_mult, body_ratio):
    if len(candles) < lookback + 2:
        return None

    recent = candles[-(lookback+1):-1]  # velas cerradas
    last = recent[-1]

    avg_vol = sum(c["volume"] for c in recent) / len(recent)
    if last["volume"] < avg_vol * vol_mult:
        return None

    buy = sum(c["taker_buy"] for c in recent)
    sell = sum(c["volume"] for c in recent) - buy
    delta = buy - sell

    rng = last["high"] - last["low"]
    if rng <= 0:
        return None

    body = abs(last["close"] - last["open"])
    body_strength = body / rng

    if delta > 0 and last["close"] > last["open"] and body_strength >= body_ratio:
        return "LONG"

    if delta < 0 and last["close"] < last["open"] and body_strength >= body_ratio:
        return "SHORT"

    return None

# -------------------------
# Volatility filter (anti-chop)
# -------------------------
def passes_volatility_filter(candles, lookback, range_mult, min_avg_range_pct, price):
    """
    - avg_range: promedio (high-low) de últimas lookback velas cerradas
    - last_range: rango de última vela cerrada
    Reglas:
      1) avg_range >= price * min_avg_range_pct
      2) last_range >= avg_range * range_mult
    """
    if len(candles) < lookback + 3:
        return False, 0.0, 0.0

    recent = candles[-(lookback+1):-1]  # velas cerradas
    ranges = [(c["high"] - c["low"]) for c in recent]
    avg_range = sum(ranges) / len(ranges)
    last_range = ranges[-1]

    if avg_range <= 0 or last_range <= 0:
        return False, avg_range, last_range

    # piso mínimo de volatilidad (evita mercado muerto)
    if avg_range < price * min_avg_range_pct:
        return False, avg_range, last_range

    # expansión (evita chop)
    if last_range < avg_range * range_mult:
        return False, avg_range, last_range

    return True, avg_range, last_range

# -------------------------
# Position / PnL
# -------------------------
def get_position(symbol):
    pos = client.futures_position_information(symbol=symbol)
    for p in pos:
        amt = float(p["positionAmt"])
        if abs(amt) > 0:
            return {
                "amt": amt,
                "side": "LONG" if amt > 0 else "SHORT",
                "pnl": float(p["unRealizedProfit"])
            }
    return None

def close_position(symbol, side, qty):
    client.futures_create_order(
        symbol=symbol,
        side="SELL" if side == "LONG" else "BUY",
        type="MARKET",
        quantity=abs(qty),
        reduceOnly=True
    )

# -------------------------
# Entry
# -------------------------
def enter_market(symbol, side):
    px = mark_price(symbol)
    step = get_step(symbol)

    capital = float(CFG["capital"])
    leverage = int(CFG["leverage"])

    qty = round_qty((capital * leverage) / px, step)
    if qty <= 0:
        return

    client.futures_create_order(
        symbol=symbol,
        side="BUY" if side == "LONG" else "SELL",
        type="MARKET",
        quantity=qty
    )
    tg(f"🚀 ENTRY {side} | qty {qty}")

# -------------------------
# Main loop
# -------------------------
def main():
    global CFG
    with open(CFG_PATH) as f:
        CFG = json.load(f)

    tg("▶️ Bot ORDER FLOW iniciado")

    while not state["shutdown"]:
        try:
            now = time.time()

            # Cooldown post-SL
            if state["cooldown_until"] > now:
                time.sleep(1)
                continue

            # Gestión TP/SL
            pos = get_position(CFG["symbol"])
            if pos:
                pnl = pos["pnl"]

                if pnl >= float(CFG["tp_min_profit_usd"]):
                    tg(f"🎯 TAKE PROFIT {pos['side']} | PnL {pnl:.2f}")
                    close_position(CFG["symbol"], pos["side"], pos["amt"])
                    time.sleep(3)
                    continue

                if pnl <= -float(CFG["sl_max_loss_usd"]):
                    tg(f"🟥 STOP LOSS {pos['side']} | PnL {pnl:.2f}")
                    close_position(CFG["symbol"], pos["side"], pos["amt"])

                    cooldown = int(CFG.get("cooldown_after_sl_sec", 180))
                    state["cooldown_until"] = time.time() + cooldown
                    tg(f"⏸️ Cooldown post-SL activado ({cooldown}s)")

                    time.sleep(3)
                    continue

                time.sleep(1)
                continue

            # --- Buscar entrada ---
            candles = fetch_1m(CFG["symbol"], limit=int(CFG.get("data_klines_limit", 30)))
            price = candles[-2]["close"] if len(candles) >= 2 else mark_price(CFG["symbol"])

            # Filtro de volatilidad (anti-chop)
            vol_ok, avg_r, last_r = passes_volatility_filter(
                candles,
                lookback=int(CFG.get("vol_lookback", 14)),
                range_mult=float(CFG.get("vol_range_mult", 1.15)),
                min_avg_range_pct=float(CFG.get("min_avg_range_pct", 0.0012)),
                price=price
            )

            if not vol_ok:
                # Mensaje cada X segundos para no spamear
                notify_every = int(CFG.get("vol_block_notify_sec", 600))
                if now - state["last_vol_block_ts"] >= notify_every:
                    tg(f"⛔ Vol filter: sin expansión | avgR={avg_r:.4f} lastR={last_r:.4f}")
                    state["last_vol_block_ts"] = now
                time.sleep(1)
                continue

            # Señal orderflow
            signal = orderflow_signal(
                candles,
                int(CFG["of_lookback"]),
                float(CFG["of_volume_mult"]),
                float(CFG["of_body_ratio"])
            )

            if signal and signal != state["last_signal"]:
                enter_market(CFG["symbol"], signal)
                state["last_signal"] = signal

            time.sleep(int(CFG.get("poll_sec", 1)))

        except Exception as e:
            print("Loop error:", e)
            time.sleep(5)

if __name__ == "__main__":
    main()
