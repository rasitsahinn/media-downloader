#!/usr/bin/env python3
"""
video_downloader.py - Standalone video downloader from web pages
Downloads MP4 videos and converts HLS streams (.m3u8) to MP4 from a given URL.
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
VIDEO_EXTENSIONS = {'.mp4', '.m3u8'}
NOISE_PATTERNS = ['icon', 'sprite', 'favicon', 'logo', 'button', 'arrow']
MIN_VIDEO_SIZE = 50 * 1024  # 50KB
HLS_TIMEOUT = 600  # 10 minutes for HLS conversion

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
            logger.warning("HLS streams will only be detected and logged, not converted to MP4")
            logger.warning("Install FFmpeg to enable automatic HLS to MP4 conversion")
        
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
            'failed': 0,
            'robots_blocked': 0
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
        
        # HLS URLs file
        self.hls_file_path = self.output_dir / 'hls_urls.txt'

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
        return urlunparse(parsed._replace(fragment=''))

    def is_video_url(self, url: str) -> bool:
        """Check if URL looks like a video"""
        url_lower = url.lower()
        # Check extension
        path = urlparse(url).path.lower()
        if any(path.endswith(ext) for ext in VIDEO_EXTENSIONS):
            return True
        # Check in full URL (with query string)
        if any(ext in url_lower for ext in VIDEO_EXTENSIONS):
            return True
        return False

    def is_noise(self, url: str) -> bool:
        """Filter out common UI assets unless they're video files"""
        if self.is_video_url(url):
            return False
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in NOISE_PATTERNS)

    def discover_from_html(self, html: str, base_url: str) -> Set[str]:
        """Parse HTML and extract video URLs with advanced techniques"""
        videos = set()
        
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            # 1. Check <video> and <source> tags
            for tag in soup.find_all(['video', 'source']):
                if tag.get('src'):
                    url = urljoin(base_url, tag['src'])
                    if self.is_video_url(url):
                        videos.add(url)
            
            # 2. Scan all attributes for video URLs
            video_attrs = [
                'data-oembed-url', 'data-src', 'data-video', 'data-url',
                'data-embed-url', 'content', 'href', 'data-video-src',
                'data-mp4', 'data-hls', 'data-file', 'data-playlist'
            ]
            
            for tag in soup.find_all(True):
                for attr, value in tag.attrs.items():
                    if isinstance(value, str):
                        if 'http' in value and self.is_video_url(value):
                            url = urljoin(base_url, value)
                            videos.add(url)
                    elif isinstance(value, list):
                        for v in value:
                            if isinstance(v, str) and 'http' in v and self.is_video_url(v):
                                url = urljoin(base_url, v)
                                videos.add(url)
            
            # 3. Parse <script> tags for embedded video URLs
            for script in soup.find_all('script'):
                script_text = script.string or ''
                
                # Advanced patterns for video URLs in JavaScript
                patterns = [
                    r'["\'](https?://[^"\']*\.m3u8[^"\']*)["\']',
                    r'["\'](https?://[^"\']*\.mp4[^"\']*)["\']',
                    r'videoUrl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                    r'hlsUrl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                    r'src["\']?\s*[:=]\s*["\']([^"\']+\.mp4[^"\']*)["\']',
                    r'file["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                    r'playlist["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                    r'source["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                    r'mp4["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                    r'hls["\']?\s*[:=]\s*["\']([^"\']+)["\']',
                ]
                
                for pattern in patterns:
                    for match in re.findall(pattern, script_text, re.IGNORECASE):
                        if self.is_video_url(match):
                            full_url = urljoin(base_url, match)
                            videos.add(full_url)
            
            # 4. Parse JSON-LD structured data
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)
                    # Handle both single objects and arrays
                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        if isinstance(item, dict):
                            # Check common video properties
                            for key in ['contentUrl', 'embedUrl', 'url', 'videoUrl']:
                                if key in item and self.is_video_url(str(item[key])):
                                    videos.add(item[key])
                except Exception as e:
                    logger.debug(f"Failed to parse JSON-LD: {e}")
        
        except Exception as e:
            logger.error(f"Error parsing HTML: {e}")
        
        # 5. Regex scan for video URLs in raw HTML
        pattern = r'https?://[^\s"\'>]+\.(?:mp4|m3u8)(?:\?[^\s"\'>]*)?'
        for match in re.finditer(pattern, html, re.IGNORECASE):
            url = match.group(0)
            videos.add(url)
        
        return videos

    def discover_with_selenium(self, url: str) -> Set[str]:
        """Use Selenium to capture network requests"""
        if not self.selenium_available or not self.chromedriver_path:
            logger.warning("Selenium not available, skipping JS rendering")
            return set()
        
        videos = set()
        
        try:
            logger.info("Starting Selenium (Chrome)...")
            
            # Chrome options
            chrome_options = Options()
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--log-level=3')
            chrome_options.add_argument(f'--user-agent={USER_AGENT}')
            
            # Enable performance logging
            chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
            
            service = Service(self.chromedriver_path)
            driver = webdriver.Chrome(service=service, options=chrome_options)
            
            logger.info(f"Loading page with Chrome: {url}")
            driver.get(url)
            
            # Wait for page to load (adjustable)
            wait_time = self.args.js_wait if hasattr(self.args, 'js_wait') else 5
            time.sleep(wait_time)
            
            # Get performance logs
            logs = driver.get_log('performance')
            logger.info(f"Processing {len(logs)} network log entries...")
            
            for entry in logs:
                try:
                    log = json.loads(entry['message'])['message']
                    
                    # Look for Network.responseReceived events
                    if log.get('method') == 'Network.responseReceived':
                        params = log.get('params', {})
                        response = params.get('response', {})
                        response_url = response.get('url', '')
                        mime_type = response.get('mimeType', '')
                        
                        # Check if it's a video URL
                        if self.is_video_url(response_url):
                            videos.add(response_url)
                        # Check by content type
                        elif 'video' in mime_type or 'mpegurl' in mime_type or 'application/vnd.apple' in mime_type:
                            videos.add(response_url)
                
                except Exception as e:
                    logger.debug(f"Failed to parse log entry: {e}")
            
            driver.quit()
            logger.info(f"Selenium found {len(videos)} video URLs")
        
        except Exception as e:
            logger.error(f"Selenium error: {e}")
            logger.error("Make sure Chrome browser is installed on this system")
        
        return videos

    def discover_with_playwright(self, url: str) -> Set[str]:
        """Use Playwright to capture network requests"""
        if not self.playwright_available:
            logger.warning("Playwright not available, skipping JS rendering")
            return set()
        
        videos = set()
        
        try:
            logger.info("Starting Playwright (Chromium)...")
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent=USER_AGENT)
                page = context.new_page()
                
                # Capture network responses
                def handle_response(response):
                    try:
                        url = response.url
                        if self.is_video_url(url):
                            videos.add(url)
                        else:
                            content_type = response.headers.get('content-type', '')
                            if content_type.startswith('video/') or \
                               content_type.startswith('application/vnd.apple.mpegurl'):
                                videos.add(url)
                    except Exception:
                        pass
                
                page.on('response', handle_response)
                page.goto(url, wait_until='networkidle', timeout=30000)
                
                browser.close()
            
            logger.info(f"Playwright found {len(videos)} video URLs")
        
        except Exception as e:
            logger.error(f"Playwright error: {e}")
        
        return videos

    def check_robots(self, url: str, is_media_file: bool = False) -> bool:
        """
        Check if URL is allowed by robots.txt
        
        Args:
            url: URL to check
            is_media_file: True if this is a direct media file (video/audio), not a page to crawl
        
        Returns:
            True if allowed, False if blocked
        """
        if self.args.ignore_robots:
            return True
        
        # robots.txt is designed for web crawling/scraping prevention
        # Direct media file downloads are not crawling - they are resource fetching
        # This is similar to how browsers load videos - they don't check robots.txt for media
        if is_media_file:
            # Check if it's actually a media file by extension
            media_extensions = ['.mp4', '.m3u8', '.ts', '.mp3', '.webm', '.avi', '.mov', '.flv', '.mkv']
            if any(ext in url.lower() for ext in media_extensions):
                logger.debug(f"Direct media file - bypassing robots.txt check: {url}")
                return True
        
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        parser = self.robots_cache.get_parser(base_url)
        
        allowed = parser.can_fetch(USER_AGENT, url)
        if not allowed:
            logger.warning(f"Blocked by robots.txt: {url}")
            self.stats['robots_blocked'] += 1
        return allowed

    def get_output_path(self, video_url: str, source_url: str, force_mp4: bool = False) -> Path:
        """Generate output path for video"""
        parsed = urlparse(video_url)
        domain = parsed.netloc.replace(':', '_')
        
        # Get first 3 path segments
        path_parts = [p for p in parsed.path.split('/') if p][:3]
        subdir = '_'.join(path_parts) if path_parts else 'root'
        
        # Create directory
        out_dir = self.output_dir / domain / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate filename
        path = parsed.path
        if path.endswith('.mp4'):
            filename = Path(path).name
        elif path.endswith('.m3u8') and force_mp4:
            # Convert .m3u8 to .mp4 for HLS downloads
            filename = Path(path).stem + '.mp4'
        elif path.endswith('.m3u8'):
            filename = Path(path).name
        else:
            # Generate from URL hash
            url_hash = hashlib.md5(video_url.encode()).hexdigest()[:8]
            filename = f"video_{url_hash}.mp4"
        
        # Sanitize filename
        filename = re.sub(r'[^\w\-_\.]', '_', filename)
        
        # Avoid overwriting
        base_path = out_dir / filename
        if base_path.exists() and base_path not in self.downloaded_urls:
            stem = base_path.stem
            ext = base_path.suffix
            counter = 1
            while (out_dir / f"{stem}_{counter}{ext}").exists():
                counter += 1
            base_path = out_dir / f"{stem}_{counter}{ext}"
        
        return base_path

    def download_mp4(self, url: str, output_path: Path) -> Tuple[bool, str]:
        """Download MP4 file with streaming and resume support"""
        domain = urlparse(url).netloc
        self.rate_limiter.wait(domain)
        
        # Check for partial download
        resume_pos = 0
        mode = 'wb'
        headers = {
            'Referer': self.source_url  # Add referer - look like browser request
        }
        
        if output_path.exists():
            resume_pos = output_path.stat().st_size
            headers['Range'] = f'bytes={resume_pos}-'
            mode = 'ab'
            logger.info(f"Resuming download from byte {resume_pos}")
        
        for attempt in range(self.args.retries):
            try:
                response = self.session.get(
                    url,
                    stream=True,
                    timeout=self.args.timeout,
                    auth=self.auth,
                    headers=headers
                )
                
                # Check if resume was accepted
                if resume_pos > 0 and response.status_code != 206:
                    logger.warning("Server doesn't support resume, starting over")
                    resume_pos = 0
                    mode = 'wb'
                    headers = {'Referer': self.source_url}
                    response = self.session.get(
                        url,
                        stream=True,
                        timeout=self.args.timeout,
                        auth=self.auth,
                        headers=headers
                    )
                
                response.raise_for_status()
                
                # Check Content-Length
                content_length = response.headers.get('Content-Length')
                if content_length:
                    total_size = int(content_length)
                    if resume_pos == 0 and total_size < MIN_VIDEO_SIZE:
                        return False, f"File too small: {total_size} bytes"
                
                # Download with streaming
                with open(output_path, mode) as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                logger.info(f"Downloaded: {output_path}")
                return True, "success"
            
            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                if attempt == self.args.retries - 1:
                    return False, str(e)
                time.sleep(2 ** attempt)
        
        return False, "Max retries exceeded"

    def download_hls_with_ffmpeg(self, m3u8_url: str, output_path: Path) -> Tuple[bool, str]:
        """Download and convert HLS stream to MP4 using FFmpeg"""
        if not self.ffmpeg_available:
            return False, "FFmpeg not available"
        
        logger.info(f"Converting HLS to MP4: {m3u8_url}")
        logger.info(f"Output: {output_path}")
        
        # Build FFmpeg command
        cmd = [
            self.ffmpeg_path,
            '-i', m3u8_url,
            '-c', 'copy',  # Copy streams without re-encoding (fast)
            '-bsf:a', 'aac_adtstoasc',  # Fix AAC headers
            '-y',  # Overwrite output file
            '-loglevel', 'warning',  # Less verbose
            str(output_path)
        ]
        
        # Add headers if needed (referer and cookies)
        header_string = f'Referer: {self.source_url}\r\n'
        if self.args.cookies:
            header_string += f'Cookie: {self.args.cookies}\r\n'
        
        cmd.insert(1, '-headers')
        cmd.insert(2, header_string)
        
        try:
            logger.debug(f"FFmpeg command: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=HLS_TIMEOUT,
                text=True
            )
            
            if result.returncode == 0:
                # Check if file was created and has reasonable size
                if output_path.exists() and output_path.stat().st_size > MIN_VIDEO_SIZE:
                    logger.info(f"HLS conversion successful: {output_path}")
                    return True, "converted"
                else:
                    logger.error(f"FFmpeg completed but output file is invalid")
                    return False, "Invalid output file"
            else:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                logger.error(f"FFmpeg failed: {error_msg}")
                return False, f"FFmpeg error: {error_msg[:100]}"
        
        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg timeout after {HLS_TIMEOUT}s")
            # Try to kill ffmpeg process
            try:
                if output_path.exists():
                    output_path.unlink()
            except Exception:
                pass
            return False, f"Timeout after {HLS_TIMEOUT}s"
        
        except Exception as e:
            logger.error(f"FFmpeg exception: {e}")
            return False, str(e)

    def log_to_csv(self, source_url: str, video_url: str, local_path: str, status: str, note: str = ''):
        """Write entry to CSV log"""
        self.csv_writer.writerow([source_url, video_url, local_path, status, note])
        self.csv_file.flush()

    def save_hls_url(self, url: str):
        """Save HLS URL to text file"""
        with open(self.hls_file_path, 'a', encoding='utf-8') as f:
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
        
        # Determine if HLS or MP4
        is_hls = '.m3u8' in normalized.lower()
        
        if is_hls:
            if self.ffmpeg_available:
                # Convert HLS to MP4
                output_path = self.get_output_path(normalized, source_url, force_mp4=True)
                logger.info(f"Converting HLS: {normalized}")
                
                success, note = self.download_hls_with_ffmpeg(normalized, output_path)
                
                if success:
                    self.stats['hls_converted'] += 1
                    self.log_to_csv(source_url, normalized, str(output_path), 'converted', note)
                else:
                    self.stats['failed'] += 1
                    # Fallback: save URL to text file
                    self.save_hls_url(normalized)
                    self.log_to_csv(source_url, normalized, '', 'conversion_failed', note)
            else:
                # Just log HLS URLs (FFmpeg not available)
                logger.info(f"HLS detected (FFmpeg not available): {normalized}")
                self.save_hls_url(normalized)
                self.log_to_csv(source_url, normalized, '', 'hls_detected', 'FFmpeg not available')
                self.stats['hls_detected'] += 1
        else:
            # Download MP4
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
        
        # Strategy 1: Advanced HTML parsing (always)
        html_videos = self.discover_from_html(html, url)
        videos.update(html_videos)
        logger.info(f"HTML parsing found {len(html_videos)} video URLs")
        
        # Strategy 2: JavaScript rendering (if requested)
        if self.args.render_js:
            # Try Selenium first (better for offline/standalone)
            if self.selenium_available:
                logger.info("Using Selenium for JavaScript rendering...")
                selenium_videos = self.discover_with_selenium(url)
                videos.update(selenium_videos)
                logger.info(f"Selenium found {len(selenium_videos)} additional videos")
            # Fallback to Playwright if available
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
        print(f"HLS detected:      {self.stats['hls_detected']}")
        print(f"Failed:            {self.stats['failed']}")
        print(f"Robots blocked:    {self.stats['robots_blocked']}")
        print("="*60)
        
        if self.stats['hls_detected'] > 0:
            print(f"\nHLS URLs saved to: {self.hls_file_path}")
            if not self.ffmpeg_available:
                print("\nNote: FFmpeg is not installed or not in PATH")
                print("Install FFmpeg to enable automatic HLS to MP4 conversion")
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
        description='Download videos from web pages (MP4 + HLS conversion)',
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
