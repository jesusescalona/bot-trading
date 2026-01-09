#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import math
import traceback
import signal
from datetime import datetime, timezone

import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException

# =========================
# Files / Env
# =========================
CONFIG_FILE = os.getenv("CONFIG_FILE", "config_binance.json")
STATE_FILE = os.getenv("BOT_STATE_FILE", "bot_state.json")

BINANCE_KEY = os.getenv("BINANCE_KEY", "").strip()
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "").strip()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
TG_API = f"https://api.telegram.org/bot{TG_BOT_TOKEN}" if TG_BOT_TOKEN else ""

# Telegram networking timeouts (robust for Fly)
TG_CONNECT_TIMEOUT = float(os.getenv("TG_CONNECT_TIMEOUT", "3"))
TG_READ_TIMEOUT = float(os.getenv("TG_READ_TIMEOUT", "10"))

START_TS = time.time()


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "paused": False,
            "paused_at": None,
            "tg_offset": 0,
            "cooldown_until": 0,
            "last_vol_block_notify": 0,
            "last_error_notify": 0,
            "last_entry": None,  # {"side": "...", "price":..., "qty":..., "ts":...}
        }


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


STATE = load_state()


# =========================
# Telegram helpers
# =========================
def tg_send(text: str):
    """Never kill the bot due to Telegram issues."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=(TG_CONNECT_TIMEOUT, TG_READ_TIMEOUT),
        )
    except Exception:
        return


def tg_poll_commands(client: Client, cfg: dict):
    """
    Poll Telegram getUpdates and process:
    /pause, /resume, /status, /help, /close, /close yes
    Only accepts messages from TG_CHAT_ID
    """
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    offset = int(STATE.get("tg_offset", 0))
    try:
        r = requests.get(
            f"{TG_API}/getUpdates",
            params={"timeout": 0, "offset": offset},
            timeout=(TG_CONNECT_TIMEOUT, TG_READ_TIMEOUT),
        )
        data = r.json()
        if not data.get("ok"):
            return

        for upd in data.get("result", []):
            upd_id = upd.get("update_id", 0)
            STATE["tg_offset"] = max(STATE.get("tg_offset", 0), upd_id + 1)

            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue

            chat_id = str((msg.get("chat") or {}).get("id", ""))
            if chat_id != str(TG_CHAT_ID):
                continue  # only authorized chat

            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue

            parts = text.split()
            cmd = parts[0].lower()
            arg = parts[1].lower() if len(parts) > 1 else ""

            if cmd == "/pause":
                if not STATE.get("paused", False):
                    STATE["paused"] = True
                    STATE["paused_at"] = int(time.time())
                    save_state(STATE)
                    tg_send("⏸️ Trading PAUSADO. No se abrirán nuevas entradas.")
                else:
                    tg_send("⏸️ Ya estaba pausado.")

            elif cmd == "/resume":
                if STATE.get("paused", False):
                    STATE["paused"] = False
                    STATE["paused_at"] = None
                    save_state(STATE)
                    tg_send("▶️ Trading REANUDADO. Se permiten nuevas entradas.")
                else:
                    tg_send("▶️ Ya estaba activo (no pausado).")

            elif cmd == "/status":
                paused = STATE.get("paused", False)
                cd = int(STATE.get("cooldown_until", 0))
                now = int(time.time())
                cd_left = max(0, cd - now)
                up = int(now - START_TS)

                pos = get_position_info(client, cfg["symbol"])
                pos_line = "sin posición"
                if pos and abs(pos["amt"]) > 0:
                    pos_line = f'{pos["side"]} amt={pos["amt"]} entry={pos["entry"]} uPnL={pos["upnl"]}'

                tg_send(
                    "📊 STATUS\n"
                    f"- paused: {paused}\n"
                    f"- cooldown_sec: {cd_left}\n"
                    f"- symbol: {cfg['symbol']}\n"
                    f"- pos: {pos_line}\n"
                    f"- uptime_sec: {up}"
                )

            elif cmd == "/help":
                tg_send(
                    "🤖 Comandos:\n"
                    "/pause  - Pausar nuevas entradas\n"
                    "/resume - Reanudar trading\n"
                    "/status - Estado\n"
                    "/close  - Solicita cierre (requiere confirmación)\n"
                    "/close yes - Cierra posición a MARKET\n"
                    "/help   - Ayuda"
                )

            elif cmd == "/close":
                if arg != "yes":
                    tg_send("⚠️ Para cerrar, confirma con: /close yes")
                else:
                    closed = close_position_market(client, cfg["symbol"])
                    if closed:
                        tg_send("🧯 CLOSE ejecutado: posición cerrada a MARKET.")
                    else:
                        tg_send("ℹ️ CLOSE: no había posición abierta o no se pudo cerrar.")

        save_state(STATE)

    except KeyboardInterrupt:
        # Fly signals can interrupt requests; exit clean
        raise
    except Exception:
        # Don't kill main loop
        return


# =========================
# Binance helpers
# =========================
def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_api(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except BinanceAPIException as e:
        # ignore: "No need to change margin type." (-4046)
        if getattr(e, "code", None) == -4046:
            return None
        raise


def get_symbol_filters(client: Client, symbol: str):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            f = {x["filterType"]: x for x in s["filters"]}
            step = float(f["LOT_SIZE"]["stepSize"])
            min_qty = float(f["LOT_SIZE"]["minQty"])
            tick = float(f["PRICE_FILTER"]["tickSize"])
            return step, min_qty, tick
    raise RuntimeError(f"No exchange info for {symbol}")


def floor_to_step(x: float, step: float):
    return math.floor(x / step) * step


def round_to_tick(price: float, tick: float):
    return math.floor(price / tick) * tick


def get_mark_price(client: Client, symbol: str) -> float:
    mp = client.futures_mark_price(symbol=symbol)
    return float(mp["markPrice"])


def get_klines(client: Client, symbol: str, tf: str, limit: int):
    return client.futures_klines(symbol=symbol, interval=tf, limit=limit)


def candle_metrics(k):
    o = float(k[1]); h = float(k[2]); l = float(k[3]); c = float(k[4]); v = float(k[5])
    body = abs(c - o)
    rng = max(1e-12, (h - l))
    body_ratio = body / rng
    return o, h, l, c, v, body_ratio, rng


def avg(vals):
    return sum(vals) / max(1, len(vals))


def get_position_info(client: Client, symbol: str):
    try:
        acc = client.futures_account()
        for p in acc.get("positions", []):
            if p.get("symbol") != symbol:
                continue
            amt = float(p.get("positionAmt", 0))
            entry = float(p.get("entryPrice", 0))
            upnl = float(p.get("unRealizedProfit", 0))
            if amt == 0:
                return {"amt": 0.0, "entry": entry, "upnl": upnl, "side": "NONE"}
            side = "LONG" if amt > 0 else "SHORT"
            return {"amt": amt, "entry": entry, "upnl": upnl, "side": side}
        return {"amt": 0.0, "entry": 0.0, "upnl": 0.0, "side": "NONE"}
    except Exception:
        return None


def cancel_open_orders(client: Client, symbol: str):
    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
        return True
    except Exception:
        return False


def close_position_market(client: Client, symbol: str) -> bool:
    # cancel orders first so they don't re-open / conflict
    cancel_open_orders(client, symbol)

    pos = get_position_info(client, symbol)
    if not pos or abs(pos["amt"]) == 0:
        return False

    amt = abs(pos["amt"])
    side = "SELL" if pos["side"] == "LONG" else "BUY"

    try:
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=amt,
            reduceOnly=True,
        )
        return True
    except Exception:
        return False


def set_leverage_and_margin(client: Client, symbol: str, leverage: int, margin_type: str):
    safe_api(client.futures_change_leverage, symbol=symbol, leverage=leverage)
    safe_api(client.futures_change_margin_type, symbol=symbol, marginType=margin_type)


# =========================
# Strategy logic
# =========================
def check_volume_expansion(cfg, klines):
    vb = int(cfg.get("vol_lookback", 14))
    mult = float(cfg.get("vol_range_mult", 1.15))
    min_avg = float(cfg.get("min_avg_range_pct", 0.0012))

    if len(klines) < vb + 1:
        return False, 0.0, 0.0

    ranges = []
    for k in klines[-(vb+1):-1]:
        _, h, l, c, _, _, _ = candle_metrics(k)
        ranges.append((h - l) / max(1e-12, c))

    avgR = avg(ranges)

    last = klines[-1]
    _, h, l, c, _, _, _ = candle_metrics(last)
    lastR = (h - l) / max(1e-12, c)

    ok = (avgR >= min_avg) and (lastR >= avgR * mult)
    return ok, avgR, lastR


def signal_orderflow(cfg, klines):
    lb = int(cfg.get("of_lookback", 3))
    v_mult = float(cfg.get("of_volume_mult", 1.3))
    body_min = float(cfg.get("of_body_ratio", 0.6))

    if len(klines) < lb + 1:
        return None

    vols = []
    for k in klines[-(lb+1):-1]:
        *_, v, __, ___ = candle_metrics(k)
        vols.append(v)

    avg_vol = avg(vols)

    last = klines[-1]
    o, _, _, c, v, body_ratio, _ = candle_metrics(last)

    if avg_vol <= 0:
        return None
    if v < avg_vol * v_mult:
        return None
    if body_ratio < body_min:
        return None

    if c > o:
        return "LONG"
    if c < o:
        return "SHORT"
    return None


def calc_qty(cfg, price: float, step: float, min_qty: float):
    capital = float(cfg.get("capital", 50))
    leverage = int(cfg.get("leverage", 8))
    notional = capital * leverage
    qty = notional / max(1e-12, price)
    qty = floor_to_step(qty, step)
    if qty < min_qty:
        return 0.0
    return qty


def place_protection_orders(client: Client, cfg: dict, symbol: str, side: str, entry_price: float, qty: float, tick: float):
    sl_usd = float(cfg.get("sl_max_loss_usd", 1.5))
    min_sl_pct = float(cfg.get("min_sl_distance_pct", 0.006))
    tp_ladder = cfg.get("tp_ladder", [4.0, 7.0, 12.0])
    tp_ladder_pct = cfg.get("tp_ladder_pct", [0.3, 0.3, 0.4])

    # Cancel any leftover orders
    cancel_open_orders(client, symbol)

    # USD-based SL distance => dist = $loss / qty
    sl_dist = sl_usd / max(1e-12, qty)
    min_dist = entry_price * min_sl_pct
    sl_dist = max(sl_dist, min_dist)

    if side == "LONG":
        sl_price = entry_price - sl_dist
        sl_side = "SELL"
    else:
        sl_price = entry_price + sl_dist
        sl_side = "BUY"

    sl_price = round_to_tick(sl_price, tick)

    # SL (close entire position)
    client.futures_create_order(
        symbol=symbol,
        side=sl_side,
        type="STOP_MARKET",
        stopPrice=sl_price,
        closePosition=True,
        workingType="MARK_PRICE",
    )

    # TP ladder (LIMIT reduceOnly)
    n = min(len(tp_ladder), len(tp_ladder_pct))
    if n <= 0:
        tg_send(f"🛡️ SL colocado @ {sl_price} | (sin TP ladder)")
        return

    remain_qty = qty

    for i in range(n):
        target_usd = float(tp_ladder[i])
        pct = float(tp_ladder_pct[i])

        part_qty = qty * pct
        if i == n - 1:
            part_qty = remain_qty
        else:
            remain_qty -= part_qty

        if part_qty <= 0:
            continue

        # For each TP, distance in price = $target / part_qty
        dist = target_usd / max(1e-12, part_qty)

        if side == "LONG":
            tp_price = entry_price + dist
            tp_side = "SELL"
        else:
            tp_price = entry_price - dist
            tp_side = "BUY"

        tp_price = round_to_tick(tp_price, tick)

        client.futures_create_order(
            symbol=symbol,
            side=tp_side,
            type="LIMIT",
            timeInForce="GTC",
            price=tp_price,
            quantity=part_qty,
            reduceOnly=True,
        )

    tg_send(f"🛡️ SL @ {sl_price} | 🎯 TP ladder colocado | entry={entry_price} qty={qty}")


def enter_trade(client: Client, cfg: dict, symbol: str, side: str, step: float, min_qty: float, tick: float):
    price = get_mark_price(client, symbol)
    qty = calc_qty(cfg, price, step, min_qty)

    if qty <= 0:
        tg_send("⚠️ Qty inválida (revisa capital/leverage/minQty).")
        return False

    order_side = "BUY" if side == "LONG" else "SELL"

    client.futures_create_order(
        symbol=symbol,
        side=order_side,
        type="MARKET",
        quantity=qty,
    )

    time.sleep(0.6)
    pos = get_position_info(client, symbol)
    entry_price = float(pos["entry"]) if pos else price

    STATE["last_entry"] = {"side": side, "price": entry_price, "qty": qty, "ts": int(time.time())}
    save_state(STATE)

    tg_send(f"🚀 ENTRY {side} | qty={qty} | entry={entry_price}")

    place_protection_orders(client, cfg, symbol, side, entry_price, qty, tick)
    return True


# =========================
# Main loop
# =========================
def main():
    cfg = load_config()
    symbol = cfg["symbol"]
    tf = cfg.get("trend_timeframe", "1m")
    poll_sec = float(cfg.get("poll_sec", 1))
    kl_limit = int(cfg.get("data_klines_limit", 30))
    vol_block_notify_sec = int(cfg.get("vol_block_notify_sec", 60))

    if not BINANCE_KEY or not BINANCE_SECRET:
        raise RuntimeError("Faltan credenciales Binance (BINANCE_KEY/BINANCE_SECRET)")

    client = Client(BINANCE_KEY, BINANCE_SECRET, testnet=bool(cfg.get("testnet", False)))

    # handle SIGTERM/SIGINT clean
    def _handle_term(signum, frame):
        tg_send("🛑 Bot detenido por señal (SIGTERM).")
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_term)
    signal.signal(signal.SIGINT, _handle_term)

    # init
    set_leverage_and_margin(client, symbol, int(cfg.get("leverage", 8)), cfg.get("margin_type", "ISOLATED"))
    step, min_qty, tick = get_symbol_filters(client, symbol)

    tg_send(f"✅ Bot iniciado | {symbol} | {now_utc()}")
    tg_send("ℹ️ Usa /help para comandos")

    while True:
        try:
            # Telegram control (each loop)
            tg_poll_commands(client, cfg)

            now = int(time.time())

            # Pause: do not open new trades
            if STATE.get("paused", False):
                time.sleep(min(poll_sec, 2))
                continue

            # If position open: do nothing (exchange handles SL/TP)
            pos = get_position_info(client, symbol)
            if pos and abs(pos["amt"]) > 0:
                time.sleep(poll_sec)
                continue

            # Evaluate signal
            kl = get_klines(client, symbol, tf, kl_limit)

            ok_vol, avgR, lastR = check_volume_expansion(cfg, kl)
            if not ok_vol:
                last_n = int(STATE.get("last_vol_block_notify", 0))
                if now - last_n >= vol_block_notify_sec:
                    tg_send(f"⛔ Vol filter: sin expansión | avgR={avgR:.4f} lastR={lastR:.4f}")
                    STATE["last_vol_block_notify"] = now
                    save_state(STATE)
                time.sleep(poll_sec)
                continue

            side = signal_orderflow(cfg, kl)
            if not side:
                time.sleep(poll_sec)
                continue

            enter_trade(client, cfg, symbol, side, step, min_qty, tick)
            time.sleep(poll_sec)

        except KeyboardInterrupt:
            # clean exit
            tg_send("🛑 Bot detenido (KeyboardInterrupt).")
            break

        except BinanceAPIException as e:
            # throttle errors
            now = int(time.time())
            last_err = int(STATE.get("last_error_notify", 0))
            if now - last_err >= 15:
                tg_send(f"❌ Binance error: {str(e)}")
                STATE["last_error_notify"] = now
                save_state(STATE)
            time.sleep(2)

        except Exception as ex:
            now = int(time.time())
            last_err = int(STATE.get("last_error_notify", 0))
            if now - last_err >= 15:
                tg_send(f"❌ Error loop: {ex}")
                STATE["last_error_notify"] = now
                save_state(STATE)
            # avoid tight loop
            time.sleep(2)


if __name__ == "__main__":
    main()
