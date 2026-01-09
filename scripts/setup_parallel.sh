#!/usr/bin/env bash
set -Eeuo pipefail

SYMBOL_1="${1:-LTCUSDT}"
SYMBOL_2="${2:-XRPUSDT}"

BASE_CONFIG="config_binance.json"
BOT_FILE="entry_and_manage.py"

mkdir -p bots/$SYMBOL_1 bots/$SYMBOL_2 scripts

cp $BOT_FILE bots/$SYMBOL_1/entry_and_manage.py
cp $BOT_FILE bots/$SYMBOL_2/entry_and_manage.py

cp $BASE_CONFIG bots/$SYMBOL_1/config_binance.json
cp $BASE_CONFIG bots/$SYMBOL_2/config_binance.json

python - <<PY
import json

def patch(path, symbol):
    with open(path) as f:
        cfg = json.load(f)
    cfg["symbol"] = symbol
    cfg["db_path"] = f"agent_{symbol}.db"
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)

patch("bots/$SYMBOL_1/config_binance.json", "$SYMBOL_1")
patch("bots/$SYMBOL_2/config_binance.json", "$SYMBOL_2")
PY