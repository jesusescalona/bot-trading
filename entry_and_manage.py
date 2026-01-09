import os
import json
import time
import math
import signal
import logging
import traceback
import sys
from datetime import datetime, timezone

from binance.client import Client
from binance.enums import (
    SIDE_BUY, SIDE_SELL,
    ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT, ORDER_TYPE_STOP_MARKET,
    TIME_IN_FORCE_GTC
)
from binance.exceptions import BinanceAPIException

# ============================================================
# Logging (crítico para Fly)
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("bot")


# ============================================================
# Helpers
# ============================================================
def now_utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def clamp(x, a, b):
    return max(a, min(b, x))


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def round_step(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return math.floor(qty / step) * step


def round_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 12)


# ============================================================
# Telegram (opcional)
# ============================================================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()

def tg_send(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        import urllib.parse, urllib.request
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": TG_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True
        }).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception:
        pass


# ============================================================
# Config
# ============================================================
CONFIG_PATH = os.getenv("CONFIG_PATH", "config_binance.json")
if not os.path.exists(CONFIG_PATH):
    raise RuntimeError(f"❌ No existe {CONFIG_PATH} (CONFIG_PATH={CONFIG_PATH})")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CFG = json.load(f)

SYMBOL = CFG.get("symbol", "LTCUSDT")
TESTNET = bool(CFG.get("testnet", False))

LEVERAGE = int(CFG.get("leverage", 8))
MARGIN_TYPE = str(CFG.get("margin_type", "ISOLATED")).upper()

CAPITAL = float(CFG.get("capital", 50))
CAPITAL_DYNAMIC = bool(CFG.get("capital_dynamic", False))
CAPITAL_RESERVE_PCT = float(CFG.get("capital_reserve_pct", 0.0))
RISK_PER_TRADE_PCT = float(CFG.get("risk_per_trade_pct", 0.0))

POLL_SEC = float(CFG.get("poll_sec", 1))

# Señal Order Flow
OF_LOOKBACK = int(CFG.get("of_lookback", 3))
OF_VOLUME_MULT = float(CFG.get("of_volume_mult", 1.3))
OF_BODY_RATIO = float(CFG.get("of_body_ratio", 0.6))
TREND_TF = CFG.get("trend_timeframe", "1m")

# Filtro volatilidad
VOL_FILTER_LOOKBACK = int(CFG.get("vol_filter_lookback", 10))
VOL_FILTER_MULT = float(CFG.get("vol_filter_mult", 1.15))

# ATR / SL
ATR_TF = CFG.get("atr_tf", "1h")
ATR_PERIOD = int(CFG.get("atr_period", 14))
MIN_SL_DISTANCE_PCT = float(CFG.get("min_sl_distance_pct", 0.006))

# TP Ladder
TP_MIN_PROFIT_USD = float(CFG.get("tp_min_profit_usd", 2.0))
TP_LADDER = CFG.get("tp_ladder", [4.0, 7.0, 12.0])
TP_LADDER_PCT = CFG.get("tp_ladder_pct", [0.3, 0.3, 0.4])

# Cooldown
COOLDOWN_POST_SL_SEC = int(CFG.get("cooldown_post_sl_sec", 180))

TG_HEARTBEAT_MIN = int(CFG.get("tg_heartbeat_min", 20))


# ============================================================
# Binance credentials
# ============================================================
BINANCE_KEY = os.getenv("BINANCE_KEY", "").strip()
BINANCE_SECRET = os.getenv("BINANCE_SECRET", "").strip()
if not BINANCE_KEY or not BINANCE_SECRET:
    raise RuntimeError("❌ Faltan credenciales BINANCE_KEY / BINANCE_SECRET (env)")

client = Client(BINANCE_KEY, BINANCE_SECRET)

if TESTNET:
    try:
        client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
        client.FUTURES_DATA_URL = "https://testnet.binancefuture.com/fapi"
        log.info("✅ Configurado FUTURES testnet endpoint")
    except Exception:
        log.warning("⚠️ No pude configurar endpoints de testnet (continuo).")


# ============================================================
# Exchange filters (tick/step)
# ============================================================
def load_symbol_filters(symbol: str):
    info = client.futures_exchange_info()
    for s in info.get("symbols", []):
        if s.get("symbol") == symbol:
            tick = 0.0
            step = 0.0
            min_qty = 0.0
            for f in s.get("filters", []):
                if f.get("filterType") == "PRICE_FILTER":
                    tick = safe_float(f.get("tickSize"), 0.0)
                if f.get("filterType") == "LOT_SIZE":
                    step = safe_float(f.get("stepSize"), 0.0)
                    min_qty = safe_float(f.get("minQty"), 0.0)
            return tick, step, min_qty
    raise RuntimeError(f"No encontré filtros de símbolo para {symbol}")

TICK_SIZE, STEP_SIZE, MIN_QTY = load_symbol_filters(SYMBOL)


# ============================================================
# Setup Binance (FIX -4046 aquí, NO en top-level)
# ============================================================
def setup_futures():
    client.ping()
    log.info("✅ Conectado a Binance correctamente")

    # Leverage
    try:
        client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        log.info(f"✅ Leverage seteado: {LEVERAGE}x")
    except Exception as e:
        log.warning(f"⚠️ No se pudo ajustar leverage (continuo): {e}")

    # Margin type (FIX -4046)
    try:
        client.futures_change_margin_type(symbol=SYMBOL, marginType=MARGIN_TYPE)
        log.info(f"✅ Margin type seteado: {MARGIN_TYPE}")
    except BinanceAPIException as e:
        if getattr(e, "code", None) == -4046 or "-4046" in str(e):
            log.info(f"✅ Margin type ya estaba en {MARGIN_TYPE} (ok)")
        else:
            raise


# ============================================================
# Market data
# ============================================================
def get_mark_price() -> float:
    mp = client.futures_mark_price(symbol=SYMBOL)
    return float(mp["markPrice"])

def get_klines(tf: str, limit: int):
    return client.futures_klines(symbol=SYMBOL, interval=tf, limit=limit)

def candle_stats(k):
    o = float(k[1]); h = float(k[2]); l = float(k[3]); c = float(k[4]); v = float(k[5])
    rng = max(h - l, 1e-12)
    body = abs(c - o)
    body_ratio = body / rng
    return o, h, l, c, v, rng, body_ratio

def atr(tf: str, period: int) -> float:
    kl = get_klines(tf, limit=period + 2)
    if len(kl) < period + 2:
        return 0.0
    trs = []
    prev_close = float(kl[0][4])
    for k in kl[1:]:
        _, h, l, c, _, _, _ = candle_stats(k)
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    window = trs[-period:]
    return sum(window) / max(len(window), 1)

def volatility_expansion_ok(tf: str, lookback: int, mult: float):
    kl = get_klines(tf, limit=max(lookback + 1, 12))
    ranges = []
    for k in kl[-lookback-1:-1]:
        _, h, l, _, _, rng, _ = candle_stats(k)
        ranges.append(rng)
    if not ranges:
        return False, 0.0, 0.0
    avgR = sum(ranges) / len(ranges)
    _, h, l, _, _, lastR, _ = candle_stats(kl[-1])
    ok = lastR >= avgR * mult
    return ok, avgR, lastR


# ============================================================
# Positions / Orders
# ============================================================
def get_position():
    ps = client.futures_position_information(symbol=SYMBOL)
    for p in ps:
        amt = float(p["positionAmt"])
        if abs(amt) > 0:
            return p
    return None

def cancel_all_open_orders():
    try:
        client.futures_cancel_all_open_orders(symbol=SYMBOL)
    except Exception:
        pass

def qty_from_capital(price: float) -> float:
    if RISK_PER_TRADE_PCT and RISK_PER_TRADE_PCT > 0:
        use = CAPITAL
        try:
            bal = client.futures_account_balance()
            usdt = 0.0
            for b in bal:
                if b.get("asset") == "USDT":
                    usdt = float(b.get("balance", 0))
                    break
            use = usdt * (RISK_PER_TRADE_PCT / 100.0)
        except Exception:
            use = CAPITAL
    else:
        use = CAPITAL

    if CAPITAL_DYNAMIC:
        use = use * (1.0 - clamp(CAPITAL_RESERVE_PCT, 0.0, 0.95))

    notional = use * LEVERAGE
    raw_qty = notional / max(price, 1e-9)
    qty = round_step(raw_qty, STEP_SIZE)

    if qty < MIN_QTY:
        qty = MIN_QTY

    return qty

def place_sl_tp(side: str, entry_price: float, qty: float):
    a = atr(ATR_TF, ATR_PERIOD)
    atr_dist = a * 0.6 if a > 0 else 0.0
    pct_dist = entry_price * MIN_SL_DISTANCE_PCT
    sl_dist = max(atr_dist, pct_dist)

    if side == "LONG":
        sl_price = entry_price - sl_dist
        sl_side = SIDE_SELL
        tp_side = SIDE_SELL
    else:
        sl_price = entry_price + sl_dist
        sl_side = SIDE_BUY
        tp_side = SIDE_BUY

    sl_price = round_tick(sl_price, TICK_SIZE)

    # SL protección (cierra toda la posición)
    client.futures_create_order(
        symbol=SYMBOL,
        side=sl_side,
        type=ORDER_TYPE_STOP_MARKET,
        stopPrice=sl_price,
        closePosition=True,
        workingType="MARK_PRICE",
    )
    log.info(f"🟥 STOP LOSS {side} colocado | stop={sl_price}")
    tg_send(f"🟥 STOP LOSS {side} colocado | stop={sl_price}")

    ladder = list(TP_LADDER)
    pcts = list(TP_LADDER_PCT)

    if ladder and ladder[0] < TP_MIN_PROFIT_USD:
        ladder[0] = TP_MIN_PROFIT_USD

    if len(pcts) != len(ladder):
        p = 1.0 / max(len(ladder), 1)
        pcts = [p for _ in ladder]

    s = sum(pcts) if pcts else 1.0
    pcts = [x / s for x in pcts]

    remaining = qty
    for i, (tp_usd, pct) in enumerate(zip(ladder, pcts), start=1):
        tp_qty = round_step(qty * pct, STEP_SIZE)
        if i == len(ladder):
            tp_qty = round_step(remaining, STEP_SIZE)

        if tp_qty <= 0:
            continue

        if side == "LONG":
            tp_price = entry_price + float(tp_usd)
        else:
            tp_price = entry_price - float(tp_usd)

        tp_price = round_tick(tp_price, TICK_SIZE)

        client.futures_create_order(
            symbol=SYMBOL,
            side=tp_side,
            type=ORDER_TYPE_LIMIT,
            quantity=tp_qty,
            price=tp_price,
            timeInForce=TIME_IN_FORCE_GTC,
            reduceOnly=True
        )

        remaining = max(0.0, remaining - tp_qty)

        log.info(f"🎯 TP{i} {side} | +{tp_usd} USD | qty={tp_qty} | price={tp_price}")
        tg_send(f"🎯 TP{i} {side} | +{tp_usd} USD | qty={tp_qty} | price={tp_price}")

def open_market(side: str):
    price = get_mark_price()
    qty = round_step(qty_from_capital(price), STEP_SIZE)

    if qty < MIN_QTY:
        log.warning(f"Qty muy baja ({qty}). Revisa capital/leverage.")
        return

    cancel_all_open_orders()

    if side == "LONG":
        o_side = SIDE_BUY
    else:
        o_side = SIDE_SELL

    log.info(f"🚀 ENTRY {side} | qty={qty}")
    tg_send(f"🚀 ENTRY {side} | qty={qty}")

    client.futures_create_order(
        symbol=SYMBOL,
        side=o_side,
        type=ORDER_TYPE_MARKET,
        quantity=qty
    )

    time.sleep(0.6)
    p = get_position()
    if not p:
        log.warning("No se detectó posición luego del entry.")
        return

    entry_price = float(p["entryPrice"])
    pos_qty = abs(float(p["positionAmt"]))

    place_sl_tp(side, entry_price, pos_qty)


# ============================================================
# Señal: Order Flow simple
# ============================================================
def orderflow_signal():
    limit = max(OF_LOOKBACK + 2, 10)
    kl = get_klines(TREND_TF, limit=limit)
    if len(kl) < OF_LOOKBACK + 2:
        return None, {}

    prev = kl[-(OF_LOOKBACK+1):-1]
    last = kl[-1]

    prev_vols = [float(k[5]) for k in prev]
    avg_vol = sum(prev_vols) / max(len(prev_vols), 1)

    o, _, _, c, v, _, body_ratio = candle_stats(last)

    vol_ok = v >= avg_vol * OF_VOLUME_MULT
    body_ok = body_ratio >= OF_BODY_RATIO

    side = None
    if vol_ok and body_ok:
        if c > o:
            side = "LONG"
        elif c < o:
            side = "SHORT"

    debug = {
        "avg_vol": avg_vol,
        "last_vol": v,
        "body_ratio": body_ratio,
        "vol_ok": vol_ok,
        "body_ok": body_ok,
        "tf": TREND_TF
    }
    return side, debug


# ============================================================
# Loop control
# ============================================================
_last_heartbeat = 0
_last_vol_filter_msg = 0
_cooldown_until = 0

def heartbeat():
    global _last_heartbeat
    if TG_HEARTBEAT_MIN <= 0:
        return
    now = now_utc_ts()
    if now - _last_heartbeat >= TG_HEARTBEAT_MIN * 60:
        _last_heartbeat = now
        tg_send("▶️ Bot ORDER FLOW activo (heartbeat)")
        log.info("▶️ Heartbeat enviado")

def set_cooldown(seconds: int, reason: str):
    global _cooldown_until
    _cooldown_until = now_utc_ts() + max(0, seconds)
    msg = f"⏸️ Cooldown activado ({seconds}s) | {reason}"
    log.info(msg)
    tg_send(msg)

def in_cooldown() -> bool:
    return now_utc_ts() < _cooldown_until


def main_loop():
    global _last_vol_filter_msg

    tg_send("▶️ Bot ORDER FLOW iniciado")
    log.info("▶️ Bot ORDER FLOW iniciado")

    while True:
        try:
            heartbeat()

            if in_cooldown():
                time.sleep(1)
                continue

            pos = get_position()
            if pos:
                time.sleep(POLL_SEC)
                continue

            ok_vol, avgR, lastR = volatility_expansion_ok(
                tf=TREND_TF,
                lookback=VOL_FILTER_LOOKBACK,
                mult=VOL_FILTER_MULT
            )
            if not ok_vol:
                now = now_utc_ts()
                if now - _last_vol_filter_msg >= 60:
                    _last_vol_filter_msg = now
                    msg = f"⛔ Vol filter: sin expansión | avgR={avgR:.4f} lastR={lastR:.4f}"
                    log.info(msg)
                    tg_send(msg)
                time.sleep(POLL_SEC)
                continue

            side, dbg = orderflow_signal()
            if side:
                log.info(
                    f"✅ Señal {side} | vol={dbg.get('last_vol', 0):.2f} "
                    f"avg={dbg.get('avg_vol', 0):.2f} bodyR={dbg.get('body_ratio', 0):.2f} tf={dbg.get('tf')}"
                )
                open_market(side)

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            log.info("SIGINT/KeyboardInterrupt recibido, cerrando limpio.")
            tg_send("🛑 Bot detenido (SIGINT).")
            sys.exit(0)

        except BinanceAPIException as e:
            if getattr(e, "code", None) == -4046 or "-4046" in str(e):
                log.info("✅ Binance -4046 (no-op) ignorado")
                time.sleep(1)
                continue

            log.error(f"❌ BinanceAPIException: {e}")
            tg_send(f"❌ Binance API error: {e}")
            set_cooldown(10, "Binance API error")
            time.sleep(1)

        except Exception as e:
            log.error("❌ Error inesperado en loop")
            traceback.print_exc()
            tg_send(f"❌ Error loop: {e}")
            set_cooldown(10, "Error inesperado")
            time.sleep(1)


# ============================================================
# Entrypoint
# ============================================================
def main():
    def _handle_sigterm(signum, frame):
        log.info("SIGTERM recibido, cerrando limpio.")
        tg_send("🛑 Bot detenido (SIGTERM).")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    setup_futures()
    main_loop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt global, cierre limpio.")
        sys.exit(0)
    except Exception as e:
        log.critical("❌ ERROR FATAL DEL BOT")
        log.critical(str(e))
        traceback.print_exc()
        tg_send(f"❌ ERROR FATAL: {e}")
        sys.exit(1)
