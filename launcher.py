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
    print("  MEDIA DOWNLOADER")
    print("  Starting Web Interface...")
    print("=" * 60)
    print()
    
    # Dosya yollarını bul
    if getattr(sys, 'frozen', False):
        # EXE olarak çalışıyorsa
        app_dir = Path(sys._MEIPASS)  # PyInstaller temp klasörü
    else:
        # Script olarak çalışıyorsa
        app_dir = Path(__file__).parent
    
    ui_path = app_dir / "ui.py"
    
    if not ui_path.exists():
        print(f"ERROR: ui.py not found at {ui_path}")
        print("Make sure ui.py is in the same folder as this executable")
        input("\nPress Enter to exit...")
        sys.exit(1)
    
    # Port bul
    port = find_free_port()
    print(f"Starting Streamlit on port {port}...")
    print("Browser will open automatically in a few seconds...")
    print()
    
    # Streamlit komutunu hazırla
    cmd = [
        sys.executable,
        "-m", "streamlit", "run",
        str(ui_path),
        "--server.port", str(port),
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType", "none",
        "--theme.base", "light"
    ]
    
    try:
        # Streamlit'i başlat
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        # Streamlit'in başlamasını bekle
        print("Waiting for Streamlit to start...")
        time.sleep(5)
        
        # Browser'ı aç
        url = f"http://localhost:{port}"
        print(f"Opening browser at {url}")
        webbrowser.open(url)
        
        print()
        print("✓ Web interface is running!")
        print("✓ You can now use the application in your browser")
        print()
        print("To stop the server:")
        print("  - Close this window, or")
        print("  - Press Ctrl+C")
        print()
        
        # Process'i canlı tut
        proc.wait()
        
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        proc.terminate()
        proc.wait()
        print("Server stopped.")
    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nIf you see this error, please report it.")
        input("\nPress Enter to exit...")
        sys.exit(1)

if __name__ == "__main__":
    main()