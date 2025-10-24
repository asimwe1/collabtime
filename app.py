#!/usr/bin/env python3
"""
Warehouse DSM Visualization Dashboard

Usage:
    python app.py              # Normal mode
    python app.py --lf         # With Lingua Franca coordination
    python app.py --port 8080  # Custom port
"""

import argparse
import threading
import logging
import subprocess
import sys
import time
import atexit
from pathlib import Path

from dashboard import create_app
from dashboard.state import simulation_loop, use_lf, LF_AVAILABLE


def check_lf_server_running(host='127.0.0.1', port=9001):
    """Check if LF server is already running."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False


def start_lf_coordinator():
    """Start LF coordinator as subprocess if not already running."""
    if check_lf_server_running():
        print("LF coordinator already running, connecting to existing instance...")
        return True
    
    lf_dir = Path(__file__).parent / 'lf'
    coordinator_script = lf_dir / 'src-gen' / 'coordinator' / 'coordinator.py'
    
    if not coordinator_script.exists():
        print(f"Error: LF coordinator not compiled. Please run:")
        print(f"  cd {lf_dir}")
        print(f"  lfc coordinator.lf")
        return None
    
    print("Starting LF coordinator...")
    proc = subprocess.Popen(
        [sys.executable, str(coordinator_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(lf_dir)
    )
    
    # Wait for server to start
    time.sleep(2)
    
    if proc.poll() is not None:
        print("Error: LF coordinator failed to start")
        return None
    
    print(f"LF coordinator started (PID: {proc.pid})")
    
    def cleanup():
        print("Stopping LF coordinator...")
        proc.terminate()
        proc.wait(timeout=5)
    
    atexit.register(cleanup)
    
    return proc


def main():
    parser = argparse.ArgumentParser(description='Warehouse DSM Visualization Dashboard')
    parser.add_argument('--lf', action='store_true', 
                       help='Use Lingua Franca for deterministic timing (auto-starts coordinator)')
    parser.add_argument('--port', type=int, default=8050, 
                       help='Port to run dashboard on (default: 8050)')
    args = parser.parse_args()
    
    # Start LF coordinator if requested
    lf_proc = None
    if args.lf:
        if not LF_AVAILABLE:
            print("Error: lf.bridge module not available")
            print("Falling back to internal timing")
            use_lf.set(False)
        else:
            lf_result = start_lf_coordinator()
            if lf_result is None or lf_result is False:
                print("Warning: Failed to start/connect to LF coordinator")
                print("Falling back to internal timing")
                use_lf.set(False)
            else:
                lf_proc = lf_result if lf_result is not True else None
                use_lf.set(True)
    else:
        use_lf.set(False)
    
    # Start background simulation thread
    sim_thread = threading.Thread(target=simulation_loop, daemon=True)
    sim_thread.start()
    
    # Create and run Dash app
    app = create_app()
    
    print(f"\n{'='*60}")
    print(f"  Warehouse DSM Visualization Dashboard")
    print(f"{'='*60}")
    print(f"  URL: http://localhost:{args.port}")
    print(f"  Mode: {'LF-coordinated' if use_lf.get() else 'Internal timing'}")
    print(f"{'='*60}\n")
    
    app.run(debug=False, host='0.0.0.0', port=args.port)  # Disable debug to prevent hot-reload


if __name__ == '__main__':
    main()
