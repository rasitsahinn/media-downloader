#!/usr/bin/env python3
"""
video_downloader.py - Standalone video downloader from web pages
Downloads MP4 videos and converts HLS (.m3u8) and DASH (.mpd) streams to MP4
NOW WITH DAILYMOTION DASH SUPPORT!
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

# Optional Selenium import
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Optional Playwright import (legacy support)
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Constants
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
VIDEO_EXTENSIONS = {'.mp4', '.m3u8', '.mpd', '.m4s'}  # Added DASH support
NOISE_PATTERNS = ['icon', 'sprite', 'favicon', 'logo', 'button', 'arrow']
MIN_VIDEO_SIZE = 50 * 1024  # 50KB
STREAM_TIMEOUT = 600  # 10 minutes for stream conversion

# Setup logging
logger = logging.getLogger(__name__)


class RobotsCache:
    """Simple TTL cache for robots.txt parsers"""
    def __init__(self, ttl: int = 3600):
        self.cache: Dict[str, Tuple[RobotFileParser, float]] = {}
        self.ttl = ttl

    def get_parser(self, base_url: str) -> RobotFileParser:
        now = time.time()
        if base_url in self.cache:
            parser, timestamp = self.cache[base_url]
            if now - timestamp < self.ttl:
                return parser
        
        parser = RobotFileParser()
        robots_url = urljoin(base_url, '/robots.txt')
        try:
            parser.set_url(robots_url)
            parser.read()
            self.cache[base_url] = (parser, now)
        except Exception as e:
            logger.warning(f"Could not read robots.txt from {robots_url}: {e}")
        return parser


class RateLimiter:
    """Domain-based rate limiter"""
    def __init__(self, rate: float):
        self.rate = rate  # requests per second
        self.last_request: Dict[str, float] = {}

    def wait(self, domain: str):
        if domain in self.last_request:
            elapsed = time.time() - self.last_request[domain]
            delay = (1.0 / self.rate) - elapsed
            if delay > 0:
                time.sleep(delay)
        self.last_request[domain] = time.time()


class VideoDownloader:
    def __init__(self, args):
        self.args = args
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        
        if args.cookies:
            self.session.headers.update({'Cookie': args.cookies})
        
        self.auth = None
        if args.auth_user and args.auth_pass:
            self.auth = (args.auth_user, args.auth_pass)
        
        self.robots_cache = RobotsCache()
        self.rate_limiter = RateLimiter(args.rate)
        self.downloaded_urls: Set[str] = set()
        
        # Store source URL for referer
        self.source_url = args.url
        
        # Check FFmpeg availability
        self.ffmpeg_path = self.find_ffmpeg()
        self.ffmpeg_available = self.ffmpeg_path is not None
        if not self.ffmpeg_available:
            logger.warning("FFmpeg not found in PATH")
            logger.warning("Streaming videos will only be detected and logged, not converted to MP4")
            logger.warning("Install FFmpeg to enable automatic conversion")
        
        # Check Selenium availability
        self.selenium_available = SELENIUM_AVAILABLE
        self.chromedriver_path = None
        if self.selenium_available:
            self.chromedriver_path = self.find_chromedriver()
            if not self.chromedriver_path:
                logger.warning("Selenium installed but ChromeDriver not found")
                logger.warning("Place chromedriver.exe in the same folder or install Chrome")
                self.selenium_available = False
        
        # Check Playwright availability
        self.playwright_available = PLAYWRIGHT_AVAILABLE
        
        # Stats
        self.stats = {
            'found': 0,
            'mp4_downloaded': 0,
            'hls_detected': 0,
            'hls_converted': 0,
            'dash_detected': 0,
            'dash_converted': 0,
            'failed': 0,
            'robots_blocked': 0,
            'dailymotion_extracted': 0
        }
        
        # Setup output directory
        self.output_dir = Path(args.out)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # CSV log
        self.csv_path = self.output_dir / 'video_download_log.csv'
        self.csv_file = open(self.csv_path, 'a', newline='', encoding='utf-8')
        self.csv_writer = csv.writer(self.csv_file)
        if self.csv_path.stat().st_size == 0:
            self.csv_writer.writerow(['source_page', 'video_url', 'local_path', 'status', 'note'])
        
        # Stream URLs file
        self.stream_file_path = self.output_dir / 'stream_urls.txt'

    def find_ffmpeg(self) -> Optional[str]:
        """Find FFmpeg executable"""
        import sys
        
        # 1. Check in bundle (PyInstaller)
        if getattr(sys, 'frozen', False):
            bundle_dir = Path(sys.executable).parent
            ffmpeg_exe = bundle_dir / 'ffmpeg.exe'
            if ffmpeg_exe.exists():
                return str(ffmpeg_exe)
        
        # 2. Check in script directory
        script_dir = Path(__file__).parent if not getattr(sys, 'frozen', False) else Path(sys.executable).parent
        ffmpeg_exe = script_dir / 'ffmpeg.exe'
        if ffmpeg_exe.exists():
            return str(ffmpeg_exe)
        
        # 3. Check in PATH
        if shutil.which('ffmpeg'):
            return 'ffmpeg'
        
        return None

    def find_chromedriver(self) -> Optional[str]:
        """Find ChromeDriver executable"""
        import sys
        
        # 1. Check in bundle (PyInstaller)
        if getattr(sys, 'frozen', False):
            bundle_dir = Path(sys.executable).parent
            chromedriver_exe = bundle_dir / 'chromedriver.exe'
            if chromedriver_exe.exists():
                return str(chromedriver_exe)
        
        # 2. Check in script directory
        script_dir = Path(__file__).parent if not getattr(sys, 'frozen', False) else Path(sys.executable).parent
        chromedriver_exe = script_dir / 'chromedriver.exe'
        if chromedriver_exe.exists():
            return str(chromedriver_exe)
        
        # 3. Check in PATH
        if shutil.which('chromedriver'):
            return 'chromedriver'
        
        return None

    def normalize_url(self, url: str) -> str:
        """Normalize URL: remove fragment, keep query string"""
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ''))

    def check_robots(self, url: str, is_media_file: bool = False) -> bool:
        """Check robots.txt (skip for direct media files)"""
        if self.args.ignore_robots:
            return True
        
        # Always allow direct media files
        if is_media_file:
            return True
        
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        try:
            parser = self.robots_cache.get_parser(base_url)
            can_fetch = parser.can_fetch(USER_AGENT, url)
            if not can_fetch:
                logger.warning(f"Blocked by robots.txt: {url}")
                self.stats['robots_blocked'] += 1
            return can_fetch
        except Exception as e:
            logger.warning(f"robots.txt check failed: {e}")
            return True

    def is_noise(self, url: str) -> bool:
        """Check if URL looks like a noise file"""
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in NOISE_PATTERNS)

    def extract_dailymotion_video_url(self, embed_url: str) -> Optional[str]:
        """
        Extract real video URL from Dailymotion embed
        Supports: HLS (.m3u8), DASH (.mpd), MP4
        """
        try:
            # Extract video ID
            match = re.search(r'video[=/]([a-zA-Z0-9]+)', embed_url)
            if not match:
                logger.warning(f"Could not extract Dailymotion video ID from: {embed_url}")
                return None
            
            video_id = match.group(1)
            logger.info(f"🎬 Dailymotion video ID: {video_id}")
            
            # Fetch embed page
            embed_page_url = f"https://www.dailymotion.com/embed/video/{video_id}"
            response = self.session.get(embed_page_url, timeout=10)
            response.raise_for_status()
            
            # Strategy 1: Look for HLS (.m3u8)
            m3u8_match = re.search(r'"(https://[^"]+\.m3u8[^"]*)"', response.text)
            if m3u8_match:
                video_url = m3u8_match.group(1).replace('\\/', '/')
                logger.info(f"✓ Found Dailymotion HLS: {video_url[:60]}...")
                self.stats['dailymotion_extracted'] += 1
                return video_url
            
            # Strategy 2: Look for DASH manifest (.mpd)
            mpd_match = re.search(r'"(https://[^"]+\.mpd[^"]*)"', response.text)
            if mpd_match:
                video_url = mpd_match.group(1).replace('\\/', '/')
                logger.info(f"✓ Found Dailymotion DASH: {video_url[:60]}...")
                self.stats['dailymotion_extracted'] += 1
                return video_url
            
            # Strategy 3: Look for .m4s segments and construct .mpd URL
            m4s_match = re.search(r'"(https://[^"]+/video/\d+\.m4s[^"]*)"', response.text)
            if m4s_match:
                m4s_url = m4s_match.group(1).replace('\\/', '/')
                # Convert: .../video/1719.m4s → .../manifest.mpd
                mpd_url = re.sub(r'/video/\d+\.m4s.*', '/manifest.mpd', m4s_url)
                logger.info(f"✓ Found Dailymotion DASH (via .m4s): {mpd_url[:60]}...")
                self.stats['dailymotion_extracted'] += 1
                return mpd_url
            
            # Strategy 4: Look for MP4 (fallback)
            mp4_match = re.search(r'"(https://[^"]+\.mp4[^"]*)"', response.text)
            if mp4_match:
                video_url = mp4_match.group(1).replace('\\/', '/')
                logger.info(f"✓ Found Dailymotion MP4: {video_url[:60]}...")
                self.stats['dailymotion_extracted'] += 1
                return video_url
            
            logger.warning(f"Could not extract video URL for Dailymotion {video_id}")
            return None
            
        except Exception as e:
            logger.error(f"Dailymotion extraction error: {e}")
            return None

    def discover_from_html(self, html: str, page_url: str) -> Set[str]:
        """
        Discover video URLs from HTML
        Supports: Direct videos, HLS, DASH, Dailymotion embeds
        """
        videos = set()
        soup = BeautifulSoup(html, 'html.parser')
        
        # 1. <video> tags
        for video in soup.find_all('video'):
            src = video.get('src')
            if src:
                videos.add(urljoin(page_url, src))
            
            for source in video.find_all('source'):
                src = source.get('src')
                if src:
                    videos.add(urljoin(page_url, src))
        
        # 2. <iframe> embeds (Dailymotion extraction)
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if not src:
                continue
            
            # Check if Dailymotion
            if 'dailymotion.com' in src or 'geo.dailymotion.com' in src:
                logger.info(f"🔍 Found Dailymotion iframe: {src[:60]}...")
                real_url = self.extract_dailymotion_video_url(src)
                if real_url:
                    videos.add(real_url)
        
        # 3. data-src attributes
        for tag in soup.find_all(attrs={'data-src': True}):
            src = tag['data-src']
            if any(ext in src.lower() for ext in VIDEO_EXTENSIONS):
                videos.add(urljoin(page_url, src))
        
        # 4. Scan all URLs in page for video extensions
        all_urls = re.findall(r'https?://[^\s"\'<>]+', html)
        for url in all_urls:
            url_lower = url.lower()
            if any(ext in url_lower for ext in VIDEO_EXTENSIONS):
                videos.add(url)
        
        # 5. Look specifically for DASH manifests (.mpd)
        mpd_pattern = r'https?://[^\s"\'<>]+\.mpd(?:\?[^\s"\'<>]*)?'
        mpd_urls = re.findall(mpd_pattern, html, re.IGNORECASE)
        for mpd_url in mpd_urls:
            videos.add(mpd_url)
        
        return videos

    def discover_with_selenium(self, page_url: str) -> Set[str]:
        """Discover videos using Selenium"""
        videos = set()
        
        if not self.selenium_available:
            return videos
        
        driver = None
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            
            if self.chromedriver_path:
                service = Service(executable_path=self.chromedriver_path)
                driver = webdriver.Chrome(service=service, options=options)
            else:
                driver = webdriver.Chrome(options=options)
            
            driver.get(page_url)
            time.sleep(self.args.js_wait)
            
            rendered_html = driver.page_source
            videos = self.discover_from_html(rendered_html, page_url)
            
        except Exception as e:
            logger.error(f"Selenium error: {e}")
        finally:
            if driver:
                driver.quit()
        
        return videos

    def discover_with_playwright(self, page_url: str) -> Set[str]:
        """Discover videos using Playwright"""
        videos = set()
        
        if not self.playwright_available:
            return videos
        
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(page_url)
                page.wait_for_timeout(self.args.js_wait * 1000)
                
                rendered_html = page.content()
                videos = self.discover_from_html(rendered_html, page_url)
                
                browser.close()
        except Exception as e:
            logger.error(f"Playwright error: {e}")
        
        return videos

    def get_output_path(self, video_url: str, source_url: str, force_mp4: bool = False) -> Path:
        """Generate output filename"""
        parsed = urlparse(video_url)
        filename = os.path.basename(parsed.path)
        
        # Clean filename
        filename = re.sub(r'[^\w\-.]', '_', filename)
        
        if not filename or filename == '_':
            url_hash = hashlib.md5(video_url.encode()).hexdigest()[:8]
            filename = f"video_{url_hash}.mp4"
        
        if force_mp4 and not filename.endswith('.mp4'):
            filename = os.path.splitext(filename)[0] + '.mp4'
        
        output_path = self.output_dir / filename
        
        # Handle duplicates
        counter = 1
        while output_path.exists():
            name, ext = os.path.splitext(filename)
            output_path = self.output_dir / f"{name}_{counter}{ext}"
            counter += 1
        
        return output_path

    def download_mp4(self, video_url: str, output_path: Path) -> Tuple[bool, str]:
        """Download MP4 video"""
        try:
            domain = urlparse(video_url).netloc
            self.rate_limiter.wait(domain)
            
            headers = {
                'Referer': self.source_url,
                'User-Agent': USER_AGENT
            }
            
            for attempt in range(self.args.retries):
                try:
                    response = self.session.get(
                        video_url,
                        headers=headers,
                        timeout=self.args.timeout,
                        stream=True,
                        auth=self.auth
                    )
                    response.raise_for_status()
                    
                    with open(output_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    
                    size = output_path.stat().st_size
                    if size < MIN_VIDEO_SIZE:
                        output_path.unlink()
                        return False, f'File too small ({size} bytes)'
                    
                    logger.info(f"✓ Downloaded: {output_path.name} ({size / 1024 / 1024:.1f} MB)")
                    return True, f'{size} bytes'
                    
                except Exception as e:
                    if attempt == self.args.retries - 1:
                        raise
                    time.sleep(2 ** attempt)
            
            return False, 'Max retries exceeded'
            
        except Exception as e:
            if output_path.exists():
                output_path.unlink()
            return False, str(e)

    def download_stream_with_ffmpeg(self, stream_url: str, output_path: Path, stream_type: str = "HLS") -> Tuple[bool, str]:
        """
        Convert HLS or DASH stream to MP4
        Works for both .m3u8 (HLS) and .mpd (DASH)
        """
        if not self.ffmpeg_available:
            return False, 'FFmpeg not available'
        
        try:
            cmd = [
                self.ffmpeg_path,
                '-i', stream_url,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                '-y',
                str(output_path)
            ]
            
            logger.info(f"Converting {stream_type} stream...")
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=STREAM_TIMEOUT
            )
            
            if result.returncode == 0 and output_path.exists():
                size = output_path.stat().st_size
                if size < MIN_VIDEO_SIZE:
                    output_path.unlink()
                    return False, f'File too small ({size} bytes)'
                
                logger.info(f"✓ Converted {stream_type}: {output_path.name} ({size / 1024 / 1024:.1f} MB)")
                return True, f'{size} bytes'
            else:
                error_output = result.stderr.decode('utf-8', errors='ignore')
                logger.error(f"FFmpeg failed: {error_output[:200]}")
                return False, 'FFmpeg conversion failed'
                
        except subprocess.TimeoutExpired:
            return False, 'FFmpeg timeout'
        except Exception as e:
            if output_path.exists():
                output_path.unlink()
            return False, str(e)

    def log_to_csv(self, source_url: str, video_url: str, local_path: str, status: str, note: str = ''):
        """Write entry to CSV log"""
        self.csv_writer.writerow([source_url, video_url, local_path, status, note])
        self.csv_file.flush()

    def save_stream_url(self, url: str, stream_type: str = ""):
        """Save stream URL to text file"""
        with open(self.stream_file_path, 'a', encoding='utf-8') as f:
            if stream_type:
                f.write(f"[{stream_type}] {url}\n")
            else:
                f.write(f"{url}\n")

    def process_video(self, video_url: str, source_url: str):
        """Process a single video URL"""
        normalized = self.normalize_url(video_url)
        
        # Skip data URLs
        if normalized.startswith('data:'):
            return
        
        # Skip if already processed
        if normalized in self.downloaded_urls:
            return
        
        self.downloaded_urls.add(normalized)
        self.stats['found'] += 1
        
        # Check robots.txt - mark as media file to bypass for direct video URLs
        if not self.check_robots(normalized, is_media_file=True):
            self.log_to_csv(source_url, normalized, '', 'robots_blocked', '')
            return
        
        # Determine video type
        url_lower = normalized.lower()
        is_hls = '.m3u8' in url_lower
        is_dash = '.mpd' in url_lower or '.m4s' in url_lower
        
        if is_dash:
            # DASH stream
            if self.ffmpeg_available:
                output_path = self.get_output_path(normalized, source_url, force_mp4=True)
                logger.info(f"Converting DASH: {normalized}")
                
                success, note = self.download_stream_with_ffmpeg(normalized, output_path, "DASH")
                
                if success:
                    self.stats['dash_converted'] += 1
                    self.log_to_csv(source_url, normalized, str(output_path), 'converted_dash', note)
                else:
                    self.stats['failed'] += 1
                    self.save_stream_url(normalized, "DASH")
                    self.log_to_csv(source_url, normalized, '', 'conversion_failed', note)
            else:
                logger.info(f"DASH detected (FFmpeg not available): {normalized}")
                self.save_stream_url(normalized, "DASH")
                self.log_to_csv(source_url, normalized, '', 'dash_detected', 'FFmpeg not available')
                self.stats['dash_detected'] += 1
        
        elif is_hls:
            # HLS stream
            if self.ffmpeg_available:
                output_path = self.get_output_path(normalized, source_url, force_mp4=True)
                logger.info(f"Converting HLS: {normalized}")
                
                success, note = self.download_stream_with_ffmpeg(normalized, output_path, "HLS")
                
                if success:
                    self.stats['hls_converted'] += 1
                    self.log_to_csv(source_url, normalized, str(output_path), 'converted_hls', note)
                else:
                    self.stats['failed'] += 1
                    self.save_stream_url(normalized, "HLS")
                    self.log_to_csv(source_url, normalized, '', 'conversion_failed', note)
            else:
                logger.info(f"HLS detected (FFmpeg not available): {normalized}")
                self.save_stream_url(normalized, "HLS")
                self.log_to_csv(source_url, normalized, '', 'hls_detected', 'FFmpeg not available')
                self.stats['hls_detected'] += 1
        
        else:
            # Direct MP4
            output_path = self.get_output_path(normalized, source_url)
            logger.info(f"Downloading MP4: {normalized}")
            
            success, note = self.download_mp4(normalized, output_path)
            
            if success:
                self.stats['mp4_downloaded'] += 1
                self.log_to_csv(source_url, normalized, str(output_path), 'downloaded', note)
            else:
                self.stats['failed'] += 1
                self.log_to_csv(source_url, normalized, '', 'failed', note)

    def run(self):
        """Main execution"""
        url = self.args.url
        logger.info(f"Starting video discovery for: {url}")
        
        # Check robots.txt for source page (not media file)
        if not self.check_robots(url, is_media_file=False):
            logger.error(f"Source page blocked by robots.txt: {url}")
            return
        
        # Fetch HTML
        try:
            response = self.session.get(url, timeout=self.args.timeout, auth=self.auth)
            response.raise_for_status()
            html = response.text
            logger.info(f"Fetched HTML ({len(html)} bytes)")
        except Exception as e:
            logger.error(f"Failed to fetch page: {e}")
            return
        
        # Discover videos using multiple strategies
        videos = set()
        
        # Strategy 1: Advanced HTML parsing (always) - WITH DAILYMOTION & DASH!
        html_videos = self.discover_from_html(html, url)
        videos.update(html_videos)
        logger.info(f"HTML parsing found {len(html_videos)} video URLs")
        
        # Strategy 2: JavaScript rendering (if requested)
        if self.args.render_js:
            if self.selenium_available:
                logger.info("Using Selenium for JavaScript rendering...")
                selenium_videos = self.discover_with_selenium(url)
                videos.update(selenium_videos)
                logger.info(f"Selenium found {len(selenium_videos)} additional videos")
            elif self.playwright_available:
                logger.info("Using Playwright for JavaScript rendering...")
                playwright_videos = self.discover_with_playwright(url)
                videos.update(playwright_videos)
                logger.info(f"Playwright found {len(playwright_videos)} additional videos")
            else:
                logger.warning("--render-js specified but no JavaScript engine available")
                logger.warning("Install Selenium (pip install selenium) or Playwright (pip install playwright)")
        
        # Filter noise
        videos = {v for v in videos if not self.is_noise(v)}
        logger.info(f"After noise filtering: {len(videos)} total videos")
        
        # Process each video
        for video_url in sorted(videos):
            self.process_video(video_url, url)
        
        # Print summary
        print("\n" + "="*60)
        print("VIDEO DOWNLOAD SUMMARY")
        print("="*60)
        print(f"Videos found:      {self.stats['found']}")
        print(f"MP4 downloaded:    {self.stats['mp4_downloaded']}")
        print(f"HLS converted:     {self.stats['hls_converted']}")
        print(f"DASH converted:    {self.stats['dash_converted']}")
        print(f"HLS detected:      {self.stats['hls_detected']}")
        print(f"DASH detected:     {self.stats['dash_detected']}")
        print(f"Dailymotion:       {self.stats['dailymotion_extracted']}")
        print(f"Failed:            {self.stats['failed']}")
        print(f"Robots blocked:    {self.stats['robots_blocked']}")
        print("="*60)
        
        total_detected = self.stats['hls_detected'] + self.stats['dash_detected']
        if total_detected > 0:
            print(f"\nStream URLs saved to: {self.stream_file_path}")
            if not self.ffmpeg_available:
                print("\nNote: FFmpeg is not installed or not in PATH")
                print("Install FFmpeg to enable automatic stream conversion")
                print("  - Windows: Download from https://ffmpeg.org/ and add to PATH")
                print("  - macOS: brew install ffmpeg")
                print("  - Linux: sudo apt install ffmpeg (or equivalent)")

    def cleanup(self):
        """Cleanup resources"""
        self.csv_file.close()


def setup_logging(verbose: bool = False):
    """Setup logging configuration"""
    level = logging.DEBUG if verbose else logging.INFO
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    console_handler.setFormatter(console_format)
    
    # File handler
    file_handler = logging.FileHandler('video_downloader.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    file_handler.setFormatter(file_format)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def main():
    parser = argparse.ArgumentParser(
        description='Download videos from web pages (MP4 + HLS + DASH + Dailymotion)',
        epilog='Example: %(prog)s "https://example.com/video-page" --render-js'
    )
    parser.add_argument('url', help='Page URL to scan for videos')
    parser.add_argument('--out', default='./downloads', help='Output directory (default: ./downloads)')
    parser.add_argument('--rate', type=float, default=2.0, help='Request rate limit per domain (req/s, default: 2.0)')
    parser.add_argument('--retries', type=int, default=3, help='Number of retries (default: 3)')
    parser.add_argument('--timeout', type=int, default=20, help='Request timeout in seconds (default: 20)')
    parser.add_argument('--render-js', action='store_true', help='Use JavaScript rendering (Selenium/Playwright)')
    parser.add_argument('--js-wait', type=int, default=5, help='Seconds to wait for JS rendering (default: 5)')
    parser.add_argument('--ignore-robots', action='store_true', help='Ignore robots.txt completely (for page and media)')
    parser.add_argument('--cookies', help='Cookies to send (format: "k1=v1; k2=v2")')
    parser.add_argument('--auth-user', help='Basic auth username')
    parser.add_argument('--auth-pass', help='Basic auth password')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    
    downloader = VideoDownloader(args)
    try:
        downloader.run()
    finally:
        downloader.cleanup()


if __name__ == '__main__':
    main()
