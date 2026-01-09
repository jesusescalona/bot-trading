# 🚀 Implementación paralela de trading bot en Fly.io (SIN modificar entry_and_manage.py)

## Objetivo
Implementar una arquitectura para ejecutar **2 símbolos en paralelo** en Fly.io:

- SIN modificar `entry_and_manage.py`
- Usando **carpetas separadas por símbolo**
- Con **un solo deploy**
- Que quede **24/7 sin prender el computador**
- Compatible con GitHub Actions (CI/CD)
- Lista para ser ejecutada automáticamente por GitHub Copilot

---

## 📌 Contexto
- App Fly: `bot-trading-hidden-sky-3395`
- El bot actualmente se ejecuta con:
  ```
  python entry_and_manage.py
  ```
- El bot **no soporta override de config por ENV**, por lo tanto:
  - Se usan **carpetas independientes por símbolo**
  - Cada carpeta tiene su propio `config_binance.json` y DB

---

## 📁 Estructura final esperada del repositorio

```
/
├─ entry_and_manage.py
├─ config_binance.json
├─ requirements.txt
├─ Dockerfile
├─ fly.toml
├─ run_multi.py
├─ scripts/
│   ├─ setup_parallel.sh
│   └─ set_symbols.sh
└─ bots/
   ├─ LTCUSDT/
   │  ├─ entry_and_manage.py
   │  └─ config_binance.json
   └─ XRPUSDT/
      ├─ entry_and_manage.py
      └─ config_binance.json
```

---

## PASO 1 — Script setup_parallel.sh

```bash
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
```

---

## PASO 2 — run_multi.py

```python
import subprocess
import time
import sys

BOTS = [
    ("LTCUSDT", "bots/LTCUSDT"),
    ("XRPUSDT", "bots/XRPUSDT"),
]

def main():
    procs = []
    for tag, folder in BOTS:
        p = subprocess.Popen([sys.executable, "entry_and_manage.py"], cwd=folder)
        procs.append((tag, p))
        time.sleep(0.3)

    while True:
        for tag, p in procs:
            if p.poll() is not None:
                for _, other in procs:
                    if other.poll() is None:
                        other.terminate()
                return 1
        time.sleep(1)

if __name__ == "__main__":
    main()
```

---

## PASO 3 — Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "run_multi.py"]
```

---

## PASO 4 — fly.toml

```toml
app = "bot-trading-hidden-sky-3395"

[http_service]
  internal_port = 8080
  auto_start_machines = true
  auto_stop_machines = "off"
  min_machines_running = 1

[processes]
  app = "python run_multi.py"
```

---

## PASO 5 — GitHub Actions

```yaml
name: Deploy to Fly
on:
  push:
    branches: ["main"]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: superfly/flyctl-actions/setup-flyctl@master
      - run: flyctl deploy --remote-only -a bot-trading-hidden-sky-3395
        env:
          FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}
```

---

## PASO 6 — Secrets Fly

```bash
fly secrets set -a bot-trading-hidden-sky-3395 BINANCE_KEY=TU_KEY
fly secrets set -a bot-trading-hidden-sky-3395 BINANCE_SECRET=TU_SECRET
fly secrets set -a bot-trading-hidden-sky-3395 TG_BOT_TOKEN=TU_TOKEN
fly secrets set -a bot-trading-hidden-sky-3395 TG_CHAT_ID=TU_CHAT_ID
```

---

## PASO 7 — Commit & Deploy

```bash
git add .
git commit -m "Parallel bots via folder workers"
git push
```
