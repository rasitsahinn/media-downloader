#!/usr/bin/env python3
"""GUI Launcher for Media Downloader"""

import sys
import subprocess
import time
import webbrowser
from pathlib import Path
import socket

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def main():
    print("=" * 60)
    print("  MEDIA DOWNLOADER - Starting...")
    print("=" * 60)
    
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys.executable).parent
    else:
        app_dir = Path(__file__).parent
    
    ui_path = app_dir / "ui.py"
    
    if not ui_path.exists():
        print(f"ERROR: ui.py not found")
        input("Press Enter to exit...")
        sys.exit(1)
    
    port = find_free_port()
    print(f"Starting on port {port}...")
    
    cmd = [
        sys.executable,
        "-m", "streamlit", "run",
        str(ui_path),
        "--server.port", str(port),
        "--server.headless", "true"
    ]
    
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(3)
        url = f"http://localhost:{port}"
        webbrowser.open(url)
        print("\nWeb interface running!")
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()

if __name__ == "__main__":
    main()