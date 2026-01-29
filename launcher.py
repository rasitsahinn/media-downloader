#!/usr/bin/env python3
"""GUI Launcher for Media Downloader"""

import sys
import subprocess
import time
import webbrowser
from pathlib import Path
import socket
import multiprocessing

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def main():
    print("=" * 60)
    print("  MEDIA DOWNLOADER")
    print("  Starting Web Interface...")
    print("=" * 60)
    print()
    
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys._MEIPASS)
    else:
        app_dir = Path(__file__).parent
    
    ui_path = app_dir / "ui.py"
    
    if not ui_path.exists():
        print(f"ERROR: ui.py not found")
        input("\nPress Enter to exit...")
        sys.exit(1)
    
    port = find_free_port()
    print(f"Starting Streamlit on port {port}...")
    print("Browser will open automatically in 5 seconds...")
    print()
    
    cmd = [
        sys.executable,
        "-m", "streamlit", "run",
        str(ui_path),
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType", "none",
        "--server.enableXsrfProtection", "false",
        "--server.enableCORS", "false"
    ]
    
    try:
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                startupinfo=startupinfo
            )
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
        
        time.sleep(5)
        url = f"http://localhost:{port}"
        print(f"Opening browser at {url}")
        webbrowser.open(url)
        
        print("\n✓ Web interface is running!")
        print("✓ Browser should open automatically")
        print("\nTo stop: Close this window or press Ctrl+C\n")
        
        proc.wait()
        
    except KeyboardInterrupt:
        print("\nShutting down...")
        proc.terminate()
        proc.wait()
    except Exception as e:
        print(f"\nERROR: {e}")
        input("\nPress Enter to exit...")
        sys.exit(1)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()