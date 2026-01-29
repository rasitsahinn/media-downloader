#!/usr/bin/env python3
"""GUI Launcher for Media Downloader - Multi-layer protection against infinite loops"""

import sys
import subprocess
import time
import webbrowser
from pathlib import Path
import socket
import multiprocessing
import os

def find_free_port():
    """Find an available port for Streamlit"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def main():
    # ═══════════════════════════════════════════════════════════
    # LAYER 1: Subprocess Detection
    # Eğer subprocess içindeysek, çık (sonsuz döngüyü önle)
    # ═══════════════════════════════════════════════════════════
    if os.environ.get('MEDIA_DOWNLOADER_SUBPROCESS'):
        return
    
    print("=" * 60)
    print("  MEDIA DOWNLOADER")
    print("  Starting Web Interface...")
    print("=" * 60)
    print()
    
    # ═══════════════════════════════════════════════════════════
    # Find ui.py location
    # ═══════════════════════════════════════════════════════════
    if getattr(sys, 'frozen', False):
        # Running as EXE (PyInstaller)
        app_dir = Path(sys._MEIPASS)
    else:
        # Running as script
        app_dir = Path(__file__).parent
    
    ui_path = app_dir / "ui.py"
    
    if not ui_path.exists():
        print(f"ERROR: ui.py not found at {app_dir}")
        print("Make sure ui.py is included in the package")
        input("\nPress Enter to exit...")
        sys.exit(1)
    
    # ═══════════════════════════════════════════════════════════
    # Find available port
    # ═══════════════════════════════════════════════════════════
    port = find_free_port()
    print(f"Port: {port}")
    print("Starting Streamlit server...")
    print("Browser will open automatically in 5 seconds...")
    print()
    
    # ═══════════════════════════════════════════════════════════
    # LAYER 2: Environment Variable Protection
    # Subprocess'e flag gönder
    # ═══════════════════════════════════════════════════════════
    env = os.environ.copy()
    env['MEDIA_DOWNLOADER_SUBPROCESS'] = '1'
    
    # Streamlit command
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
        # ═══════════════════════════════════════════════════════════
        # Start Streamlit subprocess with protection
        # ═══════════════════════════════════════════════════════════
        if sys.platform == 'win32':
            # Windows: Hide console window for subprocess
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                startupinfo=startupinfo
            )
        else:
            # Mac/Linux
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env
            )
        
        # Wait for Streamlit to start
        print("Waiting for Streamlit to initialize...")
        time.sleep(5)
        
        # Open browser
        url = f"http://localhost:{port}"
        print(f"Opening browser at {url}")
        webbrowser.open(url)
        
        print()
        print("=" * 60)
        print("  ✓ WEB INTERFACE IS RUNNING")
        print("=" * 60)
        print()
        print("  Browser should open automatically.")
        print("  If not, manually open: " + url)
        print()
        print("  To stop the server:")
        print("    - Close this window, or")
        print("    - Press Ctrl+C")
        print()
        print("=" * 60)
        print()
        
        # Keep process alive
        proc.wait()
        
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        proc.terminate()
        proc.wait()
        print("Server stopped.")
        
    except Exception as e:
        print(f"\nERROR: {e}")
        print("\nPlease report this issue on GitHub")
        input("\nPress Enter to exit...")
        sys.exit(1)

if __name__ == "__main__":
    # ═══════════════════════════════════════════════════════════
    # LAYER 3: Multiprocessing Freeze Support
    # PyInstaller compatibility
    # ═══════════════════════════════════════════════════════════
    multiprocessing.freeze_support()
    main()
