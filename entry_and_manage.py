import os
import json
import time
import math
import logging
import traceback
import sys
from datetime import datetime

from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

# ---------------------------------------------------------------------
# LOGGING (CRÍTICO PARA FLY.IO)
# ---------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ---------------------------------------------------------------------
# CARGA CONFIG
# ---------------------------------------------------------------------
CONFIG_PATH = os.getenv("CONFIG_PATH", "config_binance.json")

if not os.path.exists(CONFIG_PATH):
    raise RuntimeError(f"No existe {CONFIG_PATH}")

with open(CONFIG_PATH, "r") as f:
    CFG = json.load(f)

SYMBOL = CFG["symbol"]
LEVERAGE = CFG["leverage"]
CAPITAL = CFG["capital"]
TP_LADDER = CFG["tp_ladder"]
TP_LADDER_PCT = CFG["tp_ladder_pct"]
MIN_SL_PCT = CFG["min_sl_distance_pct"]
POLL_SEC = CFG.get("poll_sec", 1)

# ---------------------------------------------------------------------
# CREDENCIALES BINANCE (DESDE ENV)
# ---------------------------------------------------------------------
BINANCE_KEY = os.getenv("BINANCE_KEY")
BINANCE_SECRET = os.getenv("BINANCE_SECRET")

if not BINANCE_KEY or not BINANCE_SECRET:
    raise RuntimeError("Faltan credenciales de Binance (BINANCE_KEY / BINANCE_SECRET)")

# ---------------------------------------------------------------------
# CLIENTE BINANCE
# ---------------------------------------------------------------------
client = Client(BINANCE_KEY, BINANCE_SECRET)

# Ping explícito (para ver errores de región / restricción)
client.ping()
logging.info("Conectado a Binance correctamente")

# ---------------------------------------------------------------------
# SETUP FUTURES
# ---------------------------------------------------------------------
client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
client.futures_change_margin_type(symbol=SYMBOL, marginType="ISOLATED")

# ---------------------------------------------------------------------
# UTILIDADES
# ---------------------------------------------------------------------
def get_price():
    return float(client.futures_mark_price(symbol=SYMBOL)["markPrice"])

def get_position():
    positions = client.futures_position_information(symbol=SYMBOL)
    for p in positions:
        if abs(float(p["positionAmt"])) > 0:
            return p
    return None

def cancel_all_orders():
    try:
        client.futures_cancel_all_open_orders(symbol=SYMBOL)
    except Exception:
        pass

# ---------------------------------------------------------------------
# ENTRY DE PRUEBA (NEUTRO)
# ---------------------------------------------------------------------
def should_enter():
    """
    Placeholder de señal.
    Devuelve 'LONG', 'SHORT' o None
    """
    return None  # <-- aquí conectas tu lógica real

# ---------------------------------------------------------------------
# EXECUCIÓN DE ORDEN
# ---------------------------------------------------------------------
def open_position(side):
    price = get_price()
    qty = round((CAPITAL * LEVERAGE) / price, 3)

    logging.info(f"ENTRY {side} | qty {qty}")

    order = client.futures_create_order(
        symbol=SYMBOL,
        side=SIDE_BUY if side == "LONG" else SIDE_SELL,
        type=ORDER_TYPE_MARKET,
        quantity=qty
    )

    time.sleep(0.5)
    place_sl_tp(side)

# ---------------------------------------------------------------------
# STOP LOSS + TAKE PROFITS
# ---------------------------------------------------------------------
def place_sl_tp(side):
    pos = get_position()
    if not pos:
        return

    entry_price = float(pos["entryPrice"])
    qty = abs(float(pos["positionAmt"]))

    if side == "LONG":
        sl_price = entry_price * (1 - MIN_SL_PCT)
    else:
        sl_price = entry_price * (1 + MIN_SL_PCT)

    client.futures_create_order(
        symbol=SYMBOL,
        side=SIDE_SELL if side == "LONG" else SIDE_BUY,
        type=ORDER_TYPE_STOP_MARKET,
        stopPrice=round(sl_price, 4),
        closePosition=True
    )

    logging.info(f"SL colocado en {round(sl_price,4)}")

    # Take Profits escalonados
    for tp, pct in zip(TP_LADDER, TP_LADDER_PCT):
        tp_qty = round(qty * pct, 3)

        if side == "LONG":
            tp_price = entry_price + tp
            tp_side = SIDE_SELL
        else:
            tp_price = entry_price - tp
            tp_side = SIDE_BUY

        client.futures_create_order(
            symbol=SYMBOL,
            side=tp_side,
            type=ORDER_TYPE_LIMIT,
            quantity=tp_qty,
            price=round(tp_price, 4),
            timeInForce=TIME_IN_FORCE_GTC,
            reduceOnly=True
        )

        logging.info(f"TP {tp} USD | qty {tp_qty}")

# ---------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------
def main():
    logging.info("Bot iniciado")

    while True:
        try:
            pos = get_position()

            if not pos:
                signal = should_enter()
                if signal:
                    open_position(signal)

            time.sleep(POLL_SEC)

        except BinanceAPIException as e:
            logging.error(f"Binance API error: {e}")
            time.sleep(5)

        except Exception as e:
            logging.error("Error inesperado")
            traceback.print_exc()
            time.sleep(5)

# ---------------------------------------------------------------------
# ENTRYPOINT
# ---------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.critical("ERROR FATAL DEL BOT")
        logging.critical(str(e))
        traceback.print_exc()
        sys.exit(1)
