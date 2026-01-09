#!/usr/bin/env python3
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
