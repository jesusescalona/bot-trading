#!/usr/bin/env python3
"""
Multi-Symbol Bot Launcher
Runs multiple bot instances concurrently, one for each trading symbol.
Supports reading symbols from environment variables or config file.
Includes proper signal handling for graceful shutdown.
"""

import json
import os
import sys
import signal
import logging
import multiprocessing
import subprocess
import time
from pathlib import Path
from typing import List, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_multi.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class MultiSymbolBotLauncher:
    """Manages multiple bot instances for different trading symbols"""

    def __init__(self):
        self.processes = {}
        self.symbols = []
        self.shutdown_event = multiprocessing.Event()
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        logger.info("Signal handlers configured for SIGINT and SIGTERM")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        sig_name = signal.Signals(signum).name
        logger.warning(f"Received signal {sig_name} ({signum}), initiating graceful shutdown...")
        self.shutdown_event.set()
        self.stop_all_bots()
        sys.exit(0)

    def load_symbols(self) -> List[str]:
        """
        Load trading symbols from environment variable or config file.
        Priority:
        1. SYMBOLS environment variable (comma-separated)
        2. config_binance.json file
        3. Empty list if neither available
        """
        symbols = []

        # Check environment variable first
        env_symbols = os.getenv('SYMBOLS')
        if env_symbols:
            symbols = [s.strip().upper() for s in env_symbols.split(',')]
            logger.info(f"Loaded {len(symbols)} symbols from SYMBOLS environment variable: {symbols}")
            return symbols

        # Check config file
        config_path = Path('config_binance.json')
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                    if isinstance(config, dict):
                        symbols = config.get('symbols', [])
                    elif isinstance(config, list):
                        symbols = config
                    
                    symbols = [s.strip().upper() for s in symbols if s]
                    logger.info(f"Loaded {len(symbols)} symbols from config_binance.json: {symbols}")
                    return symbols
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing config_binance.json: {e}")
            except IOError as e:
                logger.error(f"Error reading config_binance.json: {e}")

        logger.warning("No symbols found in SYMBOLS environment variable or config_binance.json")
        return symbols

    def start_bot(self, symbol: str) -> Optional[subprocess.Popen]:
        """
        Start a bot instance for a specific symbol.
        Runs main.py with SYMBOL environment variable set.
        """
        try:
            env = os.environ.copy()
            env['SYMBOL'] = symbol
            
            # Determine the script to run
            main_script = 'main.py'
            if not Path(main_script).exists():
                logger.error(f"main.py not found in current directory")
                return None

            logger.info(f"Starting bot for symbol: {symbol}")
            process = subprocess.Popen(
                [sys.executable, main_script],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            self.processes[symbol] = process
            logger.info(f"Bot process started for {symbol} with PID: {process.pid}")
            return process

        except Exception as e:
            logger.error(f"Failed to start bot for symbol {symbol}: {e}")
            return None

    def monitor_processes(self):
        """Monitor running bot processes and restart if they crash"""
        while not self.shutdown_event.is_set():
            try:
                for symbol, process in list(self.processes.items()):
                    if process.poll() is not None:
                        # Process has terminated
                        returncode = process.returncode
                        logger.warning(f"Bot process for {symbol} terminated with code {returncode}")
                        
                        if not self.shutdown_event.is_set():
                            logger.info(f"Restarting bot for {symbol}...")
                            time.sleep(2)  # Wait before restart to avoid rapid restart loops
                            self.start_bot(symbol)
                
                time.sleep(5)  # Check every 5 seconds
            except Exception as e:
                logger.error(f"Error monitoring processes: {e}")
                time.sleep(5)

    def start_all_bots(self) -> bool:
        """Start bot instances for all loaded symbols"""
        if not self.symbols:
            logger.error("No symbols loaded. Cannot start bots.")
            return False

        logger.info(f"Starting {len(self.symbols)} bot instances...")
        started_count = 0

        for symbol in self.symbols:
            if self.start_bot(symbol):
                started_count += 1
            time.sleep(1)  # Stagger bot starts by 1 second

        logger.info(f"Successfully started {started_count}/{len(self.symbols)} bots")
        return started_count > 0

    def stop_all_bots(self):
        """Stop all running bot instances gracefully"""
        logger.info("Stopping all bot instances...")
        
        # First, send SIGTERM for graceful shutdown
        for symbol, process in self.processes.items():
            if process and process.poll() is None:
                try:
                    logger.info(f"Sending SIGTERM to {symbol} (PID: {process.pid})")
                    process.terminate()
                except Exception as e:
                    logger.error(f"Error terminating process for {symbol}: {e}")

        # Wait for graceful shutdown (30 seconds)
        timeout = 30
        start_time = time.time()
        while time.time() - start_time < timeout:
            if all(p.poll() is not None for p in self.processes.values() if p):
                logger.info("All bots stopped gracefully")
                return
            time.sleep(1)

        # Force kill any remaining processes
        logger.warning(f"Force killing remaining processes after {timeout}s timeout")
        for symbol, process in self.processes.items():
            if process and process.poll() is None:
                try:
                    logger.warning(f"Force killing {symbol} (PID: {process.pid})")
                    process.kill()
                except Exception as e:
                    logger.error(f"Error killing process for {symbol}: {e}")

        # Wait for killed processes
        for symbol, process in self.processes.items():
            if process:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.error(f"Failed to kill process for {symbol}")

    def get_status(self) -> dict:
        """Get status of all bot instances"""
        status = {
            'timestamp': datetime.utcnow().isoformat(),
            'total_symbols': len(self.symbols),
            'processes': {}
        }

        for symbol, process in self.processes.items():
            if process:
                returncode = process.poll()
                status['processes'][symbol] = {
                    'pid': process.pid,
                    'running': returncode is None,
                    'returncode': returncode
                }

        return status

    def run(self):
        """Main execution loop"""
        logger.info("=" * 60)
        logger.info("Multi-Symbol Bot Launcher Starting")
        logger.info("=" * 60)

        # Load symbols
        self.symbols = self.load_symbols()
        if not self.symbols:
            logger.error("No symbols configured. Exiting.")
            sys.exit(1)

        logger.info(f"Configured symbols: {self.symbols}")

        # Start all bots
        if not self.start_all_bots():
            logger.error("Failed to start any bots. Exiting.")
            sys.exit(1)

        # Monitor processes in main thread
        try:
            logger.info("Monitoring bot processes. Press Ctrl+C to stop.")
            self.monitor_processes()
        except Exception as e:
            logger.error(f"Error in monitoring loop: {e}")
        finally:
            self.stop_all_bots()
            logger.info("=" * 60)
            logger.info("Multi-Symbol Bot Launcher Stopped")
            logger.info("=" * 60)


def main():
    """Entry point"""
    launcher = MultiSymbolBotLauncher()
    launcher.run()


if __name__ == '__main__':
    main()
