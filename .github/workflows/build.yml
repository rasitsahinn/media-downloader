#!/usr/bin/env python3
"""
Media Downloader - Tkinter GUI
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import subprocess
import threading
import sys
from pathlib import Path

class MediaDownloaderGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Media Downloader v1.0")
        self.root.geometry("900x700")
        
        if getattr(sys, 'frozen', False):
            self.exe_dir = Path(sys.executable).parent
        else:
            self.exe_dir = Path(__file__).parent
        
        self.setup_ui()
        self.running = False
    
    def setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#2c3e50", height=80)
        header.pack(fill="x")
        tk.Label(header, text="MEDIA DOWNLOADER", font=("Arial", 24, "bold"),
                bg="#2c3e50", fg="white").pack(pady=20)
        
        # Main
        main = tk.Frame(self.root, padx=20, pady=20)
        main.pack(fill="both", expand=True)
        
        # URL
        url_frame = ttk.LabelFrame(main, text="URL", padding=10)
        url_frame.pack(fill="x", pady=(0, 10))
        self.url_entry = ttk.Entry(url_frame, font=("Arial", 11))
        self.url_entry.pack(fill="x")
        self.url_entry.insert(0, "https://")
        
        # Options
        opts = ttk.LabelFrame(main, text="Options", padding=10)
        opts.pack(fill="x", pady=(0, 10))
        
        self.download_images = tk.BooleanVar(value=True)
        self.download_videos = tk.BooleanVar(value=True)
        self.ignore_robots = tk.BooleanVar(value=False)
        self.render_js = tk.BooleanVar(value=False)
        self.compress_images = tk.BooleanVar(value=False)
        
        ttk.Checkbutton(opts, text="Download Images", 
                       variable=self.download_images).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        ttk.Checkbutton(opts, text="Download Videos", 
                       variable=self.download_videos).grid(row=0, column=1, sticky="w", padx=5, pady=5)
        ttk.Checkbutton(opts, text="Ignore robots.txt", 
                       variable=self.ignore_robots).grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Checkbutton(opts, text="Render JavaScript", 
                       variable=self.render_js).grid(row=1, column=1, sticky="w", padx=5, pady=5)
        ttk.Checkbutton(opts, text="Compress Images", 
                       variable=self.compress_images).grid(row=2, column=0, sticky="w", padx=5, pady=5)
        
        # Output dir
        out_frame = ttk.LabelFrame(main, text="Output Directory", padding=10)
        out_frame.pack(fill="x", pady=(0, 10))
        out_inner = tk.Frame(out_frame)
        out_inner.pack(fill="x")
        
        self.output_entry = ttk.Entry(out_inner, font=("Arial", 10))
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.output_entry.insert(0, str(Path.home() / "Downloads" / "media_downloader"))
        
        ttk.Button(out_inner, text="Browse...", 
                  command=self.browse_output).pack(side="right", padx=(5, 0))
        
        # Run button
        self.run_button = tk.Button(main, text="▶ RUN", font=("Arial", 14, "bold"),
                                    bg="#27ae60", fg="white", height=2,
                                    command=self.run_download, cursor="hand2")
        self.run_button.pack(fill="x", pady=(0, 10))
        
        # Log
        log_frame = ttk.LabelFrame(main, text="Output", padding=10)
        log_frame.pack(fill="both", expand=True)
        
        self.output_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD,
                                                     font=("Consolas", 9),
                                                     bg="#1e1e1e", fg="#00ff00")
        self.output_text.pack(fill="both", expand=True)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status = tk.Label(self.root, textvariable=self.status_var,
                         relief=tk.SUNKEN, anchor="w",
                         bg="#34495e", fg="white", font=("Arial", 9))
        status.pack(side="bottom", fill="x")
    
    def browse_output(self):
        folder = filedialog.askdirectory(initialdir=self.output_entry.get())
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)
    
    def log(self, message, level="INFO"):
        self.output_text.insert(tk.END, f"[{level}] {message}\n")
        self.output_text.see(tk.END)
        self.root.update()
    
    def run_download(self):
        if self.running:
            messagebox.showwarning("Running", "Download already in progress!")
            return
        
        url = self.url_entry.get().strip()
        if not url or url == "https://":
            messagebox.showerror("Error", "Please enter a valid URL")
            return
        
        if not self.download_images.get() and not self.download_videos.get():
            messagebox.showerror("Error", "Select at least one option")
            return
        
        self.running = True
        self.run_button.config(state="disabled", bg="#7f8c8d")
        self.status_var.set("Running...")
        
        threading.Thread(target=self.download_worker, args=(url,), daemon=True).start()
    
    def download_worker(self, url):
        try:
            output_dir = Path(self.output_entry.get())
            output_dir.mkdir(parents=True, exist_ok=True)
            
            self.log("=" * 60)
            self.log(f"URL: {url}")
            self.log(f"Output: {output_dir}")
            self.log("=" * 60)
            
            # Images
            if self.download_images.get():
                self.log("\n>>> DOWNLOADING IMAGES...")
                self.status_var.set("Downloading images...")
                
                img_dir = output_dir / "images"
                img_dir.mkdir(exist_ok=True)
                
                cmd = [
                    str(self.exe_dir / "grab_images.exe"),
                    url, "--out", str(img_dir),
                    "--depth", "0", "--max-pages", "50"
                ]
                
                if self.compress_images.get():
                    cmd.append("--compress")
                
                self.run_command(cmd)
            
            # Videos
            if self.download_videos.get():
                self.log("\n>>> DOWNLOADING VIDEOS...")
                self.status_var.set("Downloading videos...")
                
                vid_dir = output_dir / "videos"
                vid_dir.mkdir(exist_ok=True)
                
                cmd = [
                    str(self.exe_dir / "video_downloader.exe"),
                    url, "--out", str(vid_dir)
                ]
                
                if self.ignore_robots.get():
                    cmd.append("--ignore-robots")
                if self.render_js.get():
                    cmd.append("--render-js")
                
                self.run_command(cmd)
            
            self.log("\n" + "=" * 60)
            self.log("COMPLETE!", "SUCCESS")
            self.log("=" * 60)
            self.status_var.set("Complete!")
            
            messagebox.showinfo("Success", f"Download complete!\n\nSaved to:\n{output_dir}")
            
        except Exception as e:
            self.log(f"\nERROR: {e}", "ERROR")
            self.status_var.set("Error")
            messagebox.showerror("Error", str(e))
        finally:
            self.running = False
            self.run_button.config(state="normal", bg="#27ae60")
            self.status_var.set("Ready")
    
    def run_command(self, cmd):
        self.log(f"Command: {' '.join(cmd)}", "DEBUG")
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
        
        for line in iter(proc.stdout.readline, ''):
            if line:
                self.log(line.rstrip())
        
        proc.wait()
        if proc.returncode != 0:
            self.log(f"Exit code: {proc.returncode}", "WARNING")
    
    def start(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = MediaDownloaderGUI()
    app.start()
