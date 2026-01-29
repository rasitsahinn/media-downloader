import os
import sys
import time
import subprocess
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Media Downloader", layout="wide")

st.title("Media Downloader (Images + Videos)")

# --- Inputs ---
url = st.text_input("URL", placeholder="https://...")

out_dir = st.text_input("Output folder", value=str(Path("./downloads").resolve()))

col1, col2, col3 = st.columns(3)
with col1:
    run_images = st.checkbox("Download images", value=True)
with col2:
    run_videos = st.checkbox("Download videos", value=True)
with col3:
    rate = st.number_input("Rate (req/s)", min_value=0.1, value=2.0, step=0.1)

# optional flags
parse_css = st.checkbox("Parse CSS (images)", value=True)
compress = st.checkbox("Compress images", value=False)
phash = st.checkbox("Perceptual hash (images)", value=False)

st.divider()

# --- helpers ---
def run_command(cmd, label, output_box):
    """
    Runs a command and streams stdout/stderr lines into output_box.
    Returns (exit_code, all_text).
    """
    output_box.markdown(f"### {label}\n`{' '.join(cmd)}`")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    lines = []
    for line in proc.stdout:
        lines.append(line)
        # streamlit "live" text update
        output_box.code("".join(lines)[-8000:])  # keep last ~8k chars
    proc.wait()
    return proc.returncode, "".join(lines)

def ensure_out_dirs(base):
    base = Path(base)
    (base / "images").mkdir(parents=True, exist_ok=True)
    (base / "videos").mkdir(parents=True, exist_ok=True)
    return str((base / "images").resolve()), str((base / "videos").resolve())

# --- Run button ---
if st.button("Run", type="primary", disabled=not url.strip()):
    images_out, videos_out = ensure_out_dirs(out_dir)

    # show logs side by side
    left, right = st.columns(2)
    img_box = left.empty()
    vid_box = right.empty()

    python_exe = sys.executable  # venv python

    # 1) IMAGES
    if run_images:
        cmd = [python_exe, "grab_images.py", url, "--out", images_out, "--depth", "0", "--max-pages", "50", "--rate", str(rate)]
        if parse_css:
            cmd.append("--parse-css")
        if compress:
            cmd.append("--compress")
        if phash:
            cmd.append("--perceptual-hash")

        code, _ = run_command(cmd, "Images job", img_box)
        if code != 0:
            st.error(f"Images job failed (exit code {code}). Check logs.")
            st.stop()
    else:
        img_box.info("Images disabled.")

    # 2) VIDEOS
    if run_videos:
        cmd = [python_exe, "video_downloader.py", url, "--out", videos_out, "--rate", str(rate)]
        code, _ = run_command(cmd, "Videos job", vid_box)
        if code != 0:
            st.error(f"Videos job failed (exit code {code}). Check logs.")
            st.stop()
    else:
        vid_box.info("Videos disabled.")

    st.success("Done.")
    st.write("Saved to:", out_dir)