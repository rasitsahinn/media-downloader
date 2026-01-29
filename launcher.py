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
        input("\nPress Enter to exit...")
        sys.exit(1)
    
    print(f"OK - Found ui.py at: {ui_path}")
    
    port = find_free_port()
    print(f"OK - Port selected: {port}")
    print()
    print("Starting Streamlit server...")
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
    
    print("Command:", " ".join(cmd))
    print()
    print("-" * 60)
    print("STREAMLIT OUTPUT:")
    print("-" * 60)
    print()
    
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        streamlit_started = False
        output_lines = []
        
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            
            print(line.rstrip())
            output_lines.append(line)
            
            if "You can now view" in line or "Local URL" in line:
                streamlit_started = True
                break
        
        exit_code = proc.poll()
        
        if exit_code is not None:
            print()
            print("-" * 60)
            print(f"ERROR: Streamlit exited with code {exit_code}")
            print("-" * 60)
            print()
            print("Full output:")
            print("".join(output_lines))
            print()
            input("Press Enter to exit...")
            sys.exit(1)
        
        if streamlit_started:
            print()
            print("-" * 60)
            print("SUCCESS - STREAMLIT STARTED!")
            print("-" * 60)
            print()
            
            time.sleep(2)
            url = f"http://localhost:{port}"
            print(f"Opening browser: {url}")
            webbrowser.open(url)
            
            print()
            print("Browser should open now.")
            print("Close this window to stop Streamlit.")
            print()
            
            proc.wait()
        else:
            print()
            print("WARNING: Streamlit didn't show startup message")
            print("Trying to open browser anyway...")
            print()
            
            time.sleep(5)
            url = f"http://localhost:{port}"
            webbrowser.open(url)
            proc.wait()
        
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        proc.terminate()
        proc.wait()
        
    except Exception as e:
        print(f"\n\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        input("\nPress Enter to exit...")
        sys.exit(1)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
