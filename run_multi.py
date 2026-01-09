#!/usr/bin/env python3
import subprocess
import sys
import json
import time
from datetime import datetime
from pathlib import Path
import logging
import signal
import os
import threading

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('run_multi.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class ProcessManager:
    def __init__(self):
        self.processes = {}
        self.lock = threading.Lock()
        self.running = True
        
    def add_process(self, name, process):
        with self.lock:
            self.processes[name] = process
            logger.info(f"Process {name} added with PID {process.pid}")
    
    def remove_process(self, name):
        with self.lock:
            if name in self.processes:
                del self.processes[name]
                logger.info(f"Process {name} removed")
    
    def get_processes(self):
        with self.lock:
            return dict(self.processes)
    
    def terminate_all(self):
        with self.lock:
            for name, process in self.processes.items():
                try:
                    process.terminate()
                    logger.info(f"Terminated process {name}")
                except Exception as e:
                    logger.error(f"Error terminating {name}: {e}")

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}. Shutting down gracefully...")
    manager.running = False
    manager.terminate_all()
    sys.exit(0)

def load_config(config_file='config.json'):
    try:
        with open(config_file, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file {config_file} not found")
        return None
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in {config_file}")
        return None

def validate_config(config):
    required_keys = ['trading_pairs', 'strategies']
    
    if not config:
        return False
    
    for key in required_keys:
        if key not in config:
            logger.error(f"Missing required config key: {key}")
            return False
    
    if not isinstance(config['trading_pairs'], list) or len(config['trading_pairs']) == 0:
        logger.error("trading_pairs must be a non-empty list")
        return False
    
    if not isinstance(config['strategies'], list) or len(config['strategies']) == 0:
        logger.error("strategies must be a non-empty list")
        return False
    
    return True

def start_strategy_instance(config, pair_index, strategy_index, manager):
    pair = config['trading_pairs'][pair_index]
    strategy = config['strategies'][strategy_index]
    
    instance_name = f"{pair}_{strategy['name']}"
    
    try:
        env = os.environ.copy()
        env['TRADING_PAIR'] = pair
        env['STRATEGY_NAME'] = strategy['name']
        env['STRATEGY_CONFIG'] = json.dumps(strategy)
        
        process = subprocess.Popen(
            [sys.executable, 'entry_and_manage.py'],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        
        manager.add_process(instance_name, process)
        logger.info(f"Started {instance_name} with PID {process.pid}")
        
        return True
    except Exception as e:
        logger.error(f"Failed to start {instance_name}: {e}")
        return False

def monitor_processes(manager):
    while manager.running:
        processes = manager.get_processes()
        for name, process in processes.items():
            if process.poll() is not None:
                logger.warning(f"Process {name} (PID {process.pid}) has exited")
                manager.remove_process(name)
                try:
                    stdout, stderr = process.communicate(timeout=1)
                    if stdout:
                        logger.info(f"[{name}] stdout: {stdout}")
                    if stderr:
                        logger.error(f"[{name}] stderr: {stderr}")
                except Exception as e:
                    logger.error(f"Error reading process output for {name}: {e}")
        
        time.sleep(5)

def main():
    logger.info("Starting Bot Trading Multi-Instance Manager")
    logger.info(f"Current time: {datetime.now()}")
    
    # Load and validate configuration
    config = load_config()
    if not validate_config(config):
        logger.error("Configuration validation failed")
        sys.exit(1)
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Initialize process manager
    global manager
    manager = ProcessManager()
    
    # Start monitoring thread
    monitor_thread = threading.Thread(target=monitor_processes, args=(manager,), daemon=True)
    monitor_thread.start()
    logger.info("Process monitor thread started")
    
    # Start strategy instances
    num_pairs = len(config['trading_pairs'])
    num_strategies = len(config['strategies'])
    total_instances = num_pairs * num_strategies
    
    logger.info(f"Starting {total_instances} strategy instances ({num_pairs} pairs × {num_strategies} strategies)")
    
    for pair_idx in range(num_pairs):
        for strategy_idx in range(num_strategies):
            if not manager.running:
                break
            start_strategy_instance(config, pair_idx, strategy_idx, manager)
            time.sleep(1)  # Stagger instance startup
        
        if not manager.running:
            break
    
    logger.info("All strategy instances started. Running...")
    
    # Keep the main process alive
    try:
        while manager.running:
            processes = manager.get_processes()
            if len(processes) == 0:
                logger.warning("No active processes. Exiting...")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    finally:
        manager.running = False
        manager.terminate_all()
        monitor_thread.join(timeout=5)
        logger.info("Bot Trading Multi-Instance Manager stopped")

if __name__ == "__main__":
    main_script = 'entry_and_manage.py'
    main()
