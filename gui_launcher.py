#!/usr/bin/env python3
"""
Media Downloader - Tkinter GUI
Simple, lightweight, works as standalone EXE
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
        self.root.resizable(True, True)
        
        # Get executable directory
        if getattr(sys, 'frozen', False):
            self.exe_dir = Path(sys.executable).parent
        else:
            self.exe_dir = Path(__file__).parent
        
        self.setup_ui()
        self.running = False
    
    def setup_ui(self):
        # Header
        header_frame = tk.Frame(self.root, bg="#2c3e50", height=80)
        header_frame.pack(fill="x", side="top")
        
        title_label = tk.Label(
            header_frame,
            text="MEDIA DOWNLOADER",
            font=("Arial", 24, "bold"),
            bg="#2c3e50",
            fg="white"
        )
        title_label.pack(pady=20)
        
        # Main content
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(fill="both", expand=True)
        
        # URL Input
        url_frame = ttk.LabelFrame(main_frame, text="URL", padding=10)
        url_frame.pack(fill="x", pady=(0, 10))
        
        self.url_entry = ttk.Entry(url_frame, font=("Arial", 11))
        self.url_entry.pack(fill="x")
        self.url_entry.insert(0, "https://")
        
        # Options
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding=10)
        options_frame.pack(fill="x", pady=(0, 10))
        
        self.download_images = tk.BooleanVar(value=True)
        self.download_videos = tk.BooleanVar(value=True)
        self.ignore_robots = tk.BooleanVar(value=False)
        self.render_js = tk.BooleanVar(value=False)
        self.compress_images = tk.BooleanVar(value=False)
        
        ttk.Checkbutton(
            options_frame,
            text="Download Images",
            variable=self.download_images
        ).grid(row=0, column=0, sticky="w", padx=5, pady=5)
        
        ttk.Checkbutton(
            options_frame,
            text="Download Videos",
            variable=self.download_videos
        ).grid(row=0, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Checkbutton(
            options_frame,
            text="Ignore robots.txt",
            variable=self.ignore_robots
        ).grid(row=1, column=0, sticky="w", padx=5, pady=5)
        
        ttk.Checkbutton(
            options_frame,
            text="Render JavaScript (slower)",
            variable=self.render_js
        ).grid(row=1, column=1, sticky="w", padx=5, pady=5)
        
        ttk.Checkbutton(
            options_frame,
            text="Compress Images",
            variable=self.compress_images
        ).grid(row=2, column=0, sticky="w", padx=5, pady=5)
        
        # Output Directory
        output_frame = ttk.LabelFrame(main_frame, text="Output Directory", padding=10)
        output_frame.pack(fill="x", pady=(0, 10))
        
        output_inner = tk.Frame(output_frame)
        output_inner.pack(fill="x")
        
        self.output_entry = ttk.Entry(output_inner, font=("Arial", 10))
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.output_entry.insert(0, str(Path.home() / "Downloads" / "media_downloader"))
        
        ttk.Button(
            output_inner,
            text="Browse...",
            command=self.browse_output
        ).pack(side="right", padx=(5, 0))
        
        # Run Button
        self.run_button = tk.Button(
            main_frame,
            text="▶ RUN",
            font=("Arial", 14, "bold"),
            bg="#27ae60",
            fg="white",
            activebackground="#229954",
            activeforeground="white",
            command=self.run_download,
            height=2,
            cursor="hand2"
        )
        self.run_button.pack(fill="x", pady=(0, 10))
        
        # Output Log
        log_frame = ttk.LabelFrame(main_frame, text="Output", padding=10)
        log_frame.pack(fill="both", expand=True)
        
        self.output_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#00ff00",
            insertbackground="white"
        )
        self.output_text.pack(fill="both", expand=True)
        
        # Status Bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = tk.Label(
            self.root,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor="w",
            bg="#34495e",
            fg="white",
            font=("Arial", 9)
        )
        status_bar.pack(side="bottom", fill="x")
    
    def browse_output(self):
        folder = filedialog.askdirectory(
            title="Select Output Directory",
            initialdir=self.output_entry.get()
        )
        if folder:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, folder)
    
    def log(self, message, level="INFO"):
        self.output_text.insert(tk.END, f"[{level}] {message}\n")
        self.output_text.see(tk.END)
        self.root.update()
    
    def run_download(self):
        if self.running:
            messagebox.showwarning("Already Running", "Download is already in progress!")
            return
        
        url = self.url_entry.get().strip()
        if not url or url == "https://":
            messagebox.showerror("Error", "Please enter a valid URL")
            return
        
        if not self.download_images.get() and not self.download_videos.get():
            messagebox.showerror("Error", "Please select at least one option")
            return
        
        self.running = True
        self.run_button.config(state="disabled", bg="#7f8c8d")
        self.status_var.set("Running...")
        
        thread = threading.Thread(target=self.download_worker, args=(url,), daemon=True)
        thread.start()
    
    def download_worker(self, url):
        try:
            output_dir = Path(self.output_entry.get())
            output_dir.mkdir(parents=True, exist_ok=True)
            
            self.log("=" * 60)
            self.log(f"Starting download from: {url}")
            self.log(f"Output directory: {output_dir}")
            self.log("=" * 60)
            
            # Download Images
            if self.download_images.get():
                self.log("\n>>> DOWNLOADING IMAGES...", "IMAGE")
                self.status_var.set("Downloading images...")
                
                images_dir = output_dir / "images"
                images_dir.mkdir(exist_ok=True)
                
                cmd = [
                    str(self.exe_dir / "grab_images.exe"),
                    url,
                    "--out", str(images_dir),
                    "--depth", "0",
                    "--max-pages", "50"
                ]
                
                if self.compress_images.get():
                    cmd.append("--compress")
