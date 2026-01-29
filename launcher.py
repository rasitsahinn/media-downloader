#!/usr/bin/env python3
"""GUI Launcher for Media Downloader"""

import sys
import subprocess
import time
import webbrowser
from pathlib import Path
import socket
import multiprocessing
import os

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def main():
    if os.environ.get('MEDIA_DOWNLOADER_SUBPROCESS'):
        return
    
    print("=" * 60)
    print("  MEDIA DOWNLOADER")
    print("=" * 60)
    print()
    
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys._MEIPASS)
    else:
        app_dir = Path(__file__).parent
    
    ui_path = app_dir / "ui.py"
    
    if not ui_path.exists():
        print(f"ERROR: ui.py not found at {app_dir}")
        print(f"Looking in: {app_dir}")
        input("\nPress Enter to exit...")
        sys.exit(1)
    
    print(f"✓ Found ui.py at: {ui_path}")
    
    port = find_free_port()
    print(f"✓ Port selected: {port}")
    print()
    print("Starting Streamlit server...")
    print("This may take 10-15 seconds...")
    print()
    
    env = os.environ.copy()
    env['MEDIA_DOWNLOADER_SUBPROCESS'] = '1'
    
    cmd = [
        sys.executable,
        "-m", "streamlit", "run",
        str(ui_path),
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType", "none"
    ]
    
    try:
        # Streamlit'i başlat - OUTPUT'U GÖSTER
        proc = subprocess.Popen(
            cmd,
            env=env,
            # STDOUT/STDERR'i console'a yönlendir
            stdout=None,  # Console'a yazdır
            stderr=None   # Console'a yazdır
        )
        
        # Streamlit'in başlamasını bekle
        print("Waiting for Streamlit...")
        for i in range(15):
            time.sleep(1)
            print(".", end="", flush=True)
            
            # Crash oldu mu?
            if proc.poll() is not None:
                print("\n\nERROR: Streamlit exited unexpectedly!")
                print(f"Exit code: {proc.returncode}")
                input("\nPress Enter to exit...")
                sys.exit(1)
        
        print("\n")
        
        # Browser'ı aç
        url = f"http://localhost:{port}"
        print(f"Opening browser: {url}")
        webbrowser.open(url)
        
        print()
        print("=" * 60)
        print("  ✓ STREAMLIT RUNNING")
        print("=" * 60)
        print()
        print(f"  URL: {url}")
        print()
        print("  - If browser shows error, wait 5 more seconds")
        print("  - Then refresh the page")
        print()
        print("  - To stop: Press Ctrl+C or close this window")
        print()
        print("  DO NOT CLOSE THIS WINDOW!")
        print("  Streamlit output will appear below:")
        print()
        print("=" * 60)
        print()
        
        # Process'i canlı tut - ÇIKMA!
        try:
            proc.wait()
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            proc.terminate()
            proc.wait()
            print("Stopped.")
        
    except Exception as e:
        print(f"\n\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
