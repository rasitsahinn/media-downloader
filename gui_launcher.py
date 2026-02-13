#!/usr/bin/env python3
"""MediaDownloader - Unified Single EXE"""
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import sys
from pathlib import Path
import io

class MediaDownloaderGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Media Downloader v3.0")
        self.root.geometry("900x700")
        self.setup_ui()
        self.running = False
    
    def setup_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#2c3e50", height=80)
        header.pack(fill="x", side="top")
        
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
        
        # Options
        opts = ttk.LabelFrame(main, text="Options", padding=10)
        opts.pack(fill="x", pady=(0, 10))
        
        self.dl_images = tk.BooleanVar(value=True)
        self.dl_videos = tk.BooleanVar(value=True)
        self.ignore_robots = tk.BooleanVar(value=False)
        
        ttk.Checkbutton(opts, text="Images", variable=self.dl_images).grid(row=0, column=0, sticky="w", padx=5)
        ttk.Checkbutton(opts, text="Videos", variable=self.dl_videos).grid(row=0, column=1, sticky="w", padx=5)
        ttk.Checkbutton(opts, text="Ignore robots.txt", variable=self.ignore_robots).grid(row=1, column=0, sticky="w", padx=5)
        
        # Output
        out_frame = ttk.LabelFrame(main, text="Output", padding=10)
        out_frame.pack(fill="x", pady=(0, 10))
        
        out_inner = tk.Frame(out_frame)
        out_inner.pack(fill="x")
        
        self.out_entry = ttk.Entry(out_inner)
        self.out_entry.pack(side="left", fill="x", expand=True)
        self.out_entry.insert(0, str(Path.home() / "Downloads"))
        
        ttk.Button(out_inner, text="Browse", command=self.browse).pack(side="right", padx=(5,0))
        
        # Run button
        self.run_btn = tk.Button(main, text="▶ RUN", font=("Arial", 14, "bold"),
                                 bg="#27ae60", fg="white", command=self.run, height=2)
        self.run_btn.pack(fill="x", pady=(0, 10))
        
        # Log
        log_frame = ttk.LabelFrame(main, text="Log", padding=10)
        log_frame.pack(fill="both", expand=True)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD,
                                                   font=("Consolas", 9), bg="#1e1e1e", fg="#00ff00")
        self.log_text.pack(fill="both", expand=True)
    
    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, folder)
    
    def log(self, msg):
        def _log():
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
        self.root.after(0, _log)
    
    def run(self):
        if self.running:
            return
        
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("Error", "Enter URL")
            return
        
        if not self.dl_images.get() and not self.dl_videos.get():
            messagebox.showerror("Error", "Select at least one option")
            return
        
        self.running = True
        self.run_btn.config(state="disabled")
        self.log_text.delete("1.0", tk.END)
        
        threading.Thread(target=self.worker, args=(url,), daemon=True).start()
    
    def worker(self, url):
        try:
            out = Path(self.out_entry.get())
            out.mkdir(parents=True, exist_ok=True)
            
            self.log(f"URL: {url}")
            self.log(f"Output: {out}")
            
            if self.dl_images.get():
                self.log("\n>>> Images...")
                self.run_images(url, out / "images")
            
            if self.dl_videos.get():
                self.log("\n>>> Videos...")
                self.run_videos(url, out / "videos")
            
            self.log("\n✓ Complete!")
            
            def success():
                messagebox.showinfo("Success", f"Saved to:\n{out}")
            self.root.after(0, success)
        
        except Exception as e:
            self.log(f"\nERROR: {e}")
            def error():
                messagebox.showerror("Error", str(e))
            self.root.after(0, error)
        
        finally:
            self.running = False
            self.root.after(0, lambda: self.run_btn.config(state="normal"))
    
    def run_images(self, url, out_dir):
        import grab_images
        
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Capture output
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        try:
            downloader = grab_images.ImageDownloader(
                url, str(out_dir), depth=0, max_pages=50, rate_limit=2.0,
                workers=4, ignore_robots=self.ignore_robots.get()
            )
            downloader.crawl()
            
            output = sys.stdout.getvalue()
            for line in output.split('\n'):
                if line.strip():
                    self.log(line)
        finally:
            sys.stdout = old_stdout
    
    def run_videos(self, url, out_dir):
        import video_downloader
        
        out_dir.mkdir(parents=True, exist_ok=True)
        
        class Args:
            pass
        
        args = Args()
        args.url = url
        args.out = str(out_dir)
        args.rate = 2.0
        args.retries = 3
        args.timeout = 20
        args.render_js = False
        args.js_wait = 5
        args.ignore_robots = self.ignore_robots.get()
        args.cookies = None
        args.auth_user = None
        args.auth_pass = None
        args.chrome_binary = None
        args.verbose = False
        args.force = False
        
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        try:
            downloader = video_downloader.VideoDownloader(args)
            downloader.run()
            downloader.cleanup()
            
            output = sys.stdout.getvalue()
            for line in output.split('\n'):
                if line.strip():
                    self.log(line)
        finally:
            sys.stdout = old_stdout
    
    def start(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = MediaDownloaderGUI()
    app.start()
