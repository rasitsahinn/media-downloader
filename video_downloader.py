#!/usr/bin/env python3
"""
video_downloader.py - Professional Video Downloader
- Supports: MP4, HLS (.m3u8), DASH (.mpd), Dailymotion
- Network logging via Selenium performance logs
- Robust multi-strategy extraction
- EXE-ready with comprehensive error handling
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
import sys
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
    from selenium.webdriver.common.by import By
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
VIDEO_EXTENSIONS = {'.mp4', '.m3u8', '.mpd', '.m4s'}
NOISE_PATTERNS = ['icon', 'sprite', 'favicon', 'logo', 'button', 'arrow']
MIN_VIDEO_SIZE = 50 * 1024  # 50KB
STREAM_TIMEOUT = 600  # 10 minutes

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
        self.rate = rate
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
        self.source_url = args.url
        
        # Check dependencies
        self.ffmpeg_path = self.find_ffmpeg()
        self.ffmpeg_available = self.ffmpeg_path is not None
        if not self.ffmpeg_available:
            logger.warning("⚠ FFmpeg not found - stream conversion disabled")
        
        self.selenium_available = SELENIUM_AVAILABLE
        self.chromedriver_path = None
        self.chrome_binary_path = args.chrome_binary if hasattr(args, 'chrome_binary') and args.chrome_binary else None
        
        if self.selenium_available:
            self.chromedriver_path = self.find_chromedriver()
            if not self.chromedriver_path:
                logger.warning("⚠ ChromeDriver not found")
            
            if args.render_js:
                self._setup_selenium()
        
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
        
        # Setup output
        self.output_dir = Path(args.out)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Debug output
        self.debug_dir = self.output_dir / '_debug'
        if args.verbose:
            self.debug_dir.mkdir(exist_ok=True)
        
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
        if getattr(sys, 'frozen', False):
            bundle_dir = Path(sys.executable).parent
            ffmpeg_exe = bundle_dir / 'ffmpeg.exe'
            if ffmpeg_exe.exists():
                return str(ffmpeg_exe)
        
        script_dir = Path(__file__).parent if not getattr(sys, 'frozen', False) else Path(sys.executable).parent
        ffmpeg_exe = script_dir / 'ffmpeg.exe'
        if ffmpeg_exe.exists():
            return str(ffmpeg_exe)
        
        if shutil.which('ffmpeg'):
            return 'ffmpeg'
        
        return None

    def find_chrome_binary(self) -> Optional[str]:
        """Find Chrome binary in standard locations"""
        # If user provided path, use it
        if self.chrome_binary_path and os.path.exists(self.chrome_binary_path):
            return self.chrome_binary_path
        
        username = os.getenv('USERNAME') or os.getenv('USER') or 'User'
        
        chrome_paths = [
            # Windows standard paths
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            f"C:\\Users\\{username}\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe",
            
            # Mac paths
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            f"/Users/{username}/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            
            # Linux paths
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
        
        for path in chrome_paths:
            if os.path.exists(path):
                return path
        
        return None
    
    def find_chromedriver(self) -> Optional[str]:
        """Find ChromeDriver executable"""
        if getattr(sys, 'frozen', False):
            bundle_dir = Path(sys.executable).parent
            chromedriver_exe = bundle_dir / 'chromedriver.exe'
            if chromedriver_exe.exists():
                return str(chromedriver_exe)
        
        script_dir = Path(__file__).parent if not getattr(sys, 'frozen', False) else Path(sys.executable).parent
        chromedriver_exe = script_dir / 'chromedriver.exe'
        if chromedriver_exe.exists():
            return str(chromedriver_exe)
        
        if shutil.which('chromedriver'):
            return 'chromedriver'
        
        return None
    
    def _setup_selenium(self):
        """Setup Selenium with Chrome binary detection"""
        try:
            options = Options()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            
            # Find Chrome binary
            chrome_binary = self.find_chrome_binary()
            
            if not chrome_binary:
                logger.warning("❌ Chrome not found in standard locations")
                logger.warning("Selenium requires Chrome to be installed")
                logger.warning("Download Chrome: https://www.google.com/chrome/")
                logger.warning("Or use --chrome-binary to specify path")
                self.selenium_available = False
                return
            
            options.binary_location = chrome_binary
            logger.info(f"✓ Chrome found: {chrome_binary}")
            
            # Enable performance logging
            options.set_capability('goog:loggingPrefs', {
                'performance': 'ALL',
                'browser': 'ALL'
            })
            
            # Stealth mode
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Test if Chrome can start
            if self.chromedriver_path:
                service = Service(executable_path=self.chromedriver_path)
                test_driver = webdriver.Chrome(service=service, options=options)
            else:
                test_driver = webdriver.Chrome(options=options)
            
            test_driver.quit()
            
            logger.info("✓ Selenium initialized successfully")
            
        except Exception as e:
            logger.warning(f"⚠ Selenium setup failed: {e}")
            logger.warning("Video extraction will use HTML parsing only")
            self.selenium_available = False

    def normalize_url(self, url: str) -> str:
        """Normalize URL"""
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ''))

    def check_robots(self, url: str, is_media_file: bool = False) -> bool:
        """Check robots.txt"""
        if self.args.ignore_robots or is_media_file:
            return True
        
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        
        try:
            parser = self.robots_cache.get_parser(base_url)
            can_fetch = parser.can_fetch(USER_AGENT, url)
            if not can_fetch:
                logger.warning(f"🚫 Blocked by robots.txt: {url}")
                self.stats['robots_blocked'] += 1
            return can_fetch
        except Exception as e:
            logger.warning(f"robots.txt check failed: {e}")
            return True

    def is_noise(self, url: str) -> bool:
        """Check if URL is noise"""
        url_lower = url.lower()
        return any(pattern in url_lower for pattern in NOISE_PATTERNS)

    def extract_dailymotion_video_url(self, embed_url: str) -> Optional[str]:
        """
        Extract Dailymotion video URL - Multi-strategy approach
        """
        try:
            # Extract video ID
            match = re.search(r'video[=/]([a-zA-Z0-9]+)', embed_url, re.IGNORECASE)
            if not match:
                logger.warning(f"Could not extract Dailymotion video ID from: {embed_url}")
                return None
            
            video_id = match.group(1)
            logger.info(f"🎬 Dailymotion video ID: {video_id}")
            
            # Fetch embed page
            embed_page_url = f"https://www.dailymotion.com/embed/video/{video_id}"
            
            try:
                response = self.session.get(embed_page_url, timeout=15)
                response.raise_for_status()
                html = response.text
                
                # Save debug HTML
                if self.args.verbose:
                    debug_file = self.debug_dir / f'dailymotion_{video_id}.html'
                    with open(debug_file, 'w', encoding='utf-8') as f:
                        f.write(html)
                    logger.debug(f"📝 Debug HTML saved: {debug_file}")
                
            except Exception as e:
                logger.error(f"Failed to fetch Dailymotion embed: {e}")
                return None
            
            # ==========================================
            # STRATEGY 1: Parse __PLAYER_CONFIG__ JSON
            # ==========================================
            logger.debug("Strategy 1: __PLAYER_CONFIG__ JSON")
            
            config_pattern = r'window\.__PLAYER_CONFIG__\s*=\s*(\{.+?\});?\s*(?:</script>|var )'
            config_match = re.search(config_pattern, html, re.DOTALL)
            
            if config_match:
                json_str = config_match.group(1).strip()
                
                # Save debug JSON
                if self.args.verbose:
                    debug_json = self.debug_dir / f'dailymotion_{video_id}_config.json'
                    with open(debug_json, 'w', encoding='utf-8') as f:
                        f.write(json_str)
                    logger.debug(f"📝 Debug JSON saved: {debug_json}")
                
                try:
                    config = json.loads(json_str)
                    logger.debug(f"✓ JSON parsed, keys: {list(config.keys())}")
                    
                    # Navigate to manifestUrl
                    critical_metadata = config.get('criticalMetadata', {})
                    manifest_url = critical_metadata.get('manifestUrl')
                    
                    if manifest_url:
                        logger.info(f"✓ Strategy 1 SUCCESS: {manifest_url[:70]}...")
                        self.stats['dailymotion_extracted'] += 1
                        return manifest_url
                    else:
                        logger.debug(f"criticalMetadata keys: {list(critical_metadata.keys())}")
                    
                except json.JSONDecodeError as e:
                    logger.debug(f"JSON parse error: {str(e)[:100]}")
            
            # ==========================================
            # STRATEGY 2: Direct manifestUrl field
            # ==========================================
            logger.debug("Strategy 2: Direct manifestUrl extraction")
            
            manifest_pattern = r'"manifestUrl"\s*:\s*"(https://[^"]+)"'
            manifest_match = re.search(manifest_pattern, html)
            
            if manifest_match:
                manifest_url = manifest_match.group(1)
                logger.info(f"✓ Strategy 2 SUCCESS: {manifest_url[:70]}...")
                self.stats['dailymotion_extracted'] += 1
                return manifest_url
            
            # ==========================================
            # STRATEGY 3: Any .m3u8 URL
            # ==========================================
            logger.debug("Strategy 3: Scan for .m3u8 URLs")
            
            m3u8_urls = re.findall(r'(https://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
            
            if m3u8_urls:
                best_url = max(m3u8_urls, key=lambda x: (
                    'cdndirector' in x,
                    'manifest' in x,
                    len(x)
                ))
                logger.info(f"✓ Strategy 3 SUCCESS: {best_url[:70]}...")
                self.stats['dailymotion_extracted'] += 1
                return best_url
            
            # ==========================================
            # STRATEGY 4: Any .mpd URL (DASH)
            # ==========================================
            logger.debug("Strategy 4: Scan for .mpd URLs")
            
            mpd_urls = re.findall(r'(https://[^\s"\'<>]+\.mpd[^\s"\'<>]*)', html)
            
            if mpd_urls:
                best_url = max(mpd_urls, key=lambda x: (
                    'cdndirector' in x,
                    'manifest' in x,
                    len(x)
                ))
                logger.info(f"✓ Strategy 4 SUCCESS: {best_url[:70]}...")
                self.stats['dailymotion_extracted'] += 1
                return best_url
            
            # ==========================================
            # STRATEGY 5: Construct from .m4s
            # ==========================================
            logger.debug("Strategy 5: Construct from .m4s segments")
            
            m4s_urls = re.findall(r'(https://[^\s"\'<>]+/video/\d+\.m4s[^\s"\'<>]*)', html)
            
            if m4s_urls:
                m4s_url = m4s_urls[0]
                mpd_url = re.sub(r'/video/\d+\.m4s.*', '/manifest.mpd', m4s_url)
                logger.info(f"✓ Strategy 5 SUCCESS: {mpd_url[:70]}...")
                self.stats['dailymotion_extracted'] += 1
                return mpd_url
            
            # ==========================================
            # STRATEGY 6: Direct MP4
            # ==========================================
            logger.debug("Strategy 6: Look for MP4")
            
            mp4_urls = re.findall(r'(https://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', html)
            video_mp4s = [url for url in mp4_urls if 'poster' not in url and 'thumb' not in url]
            
            if video_mp4s:
                best_url = max(video_mp4s, key=len)
                logger.info(f"✓ Strategy 6 SUCCESS: {best_url[:70]}...")
                self.stats['dailymotion_extracted'] += 1
                return best_url
            
            # ==========================================
            # ALL FAILED
            # ==========================================
            logger.warning(f"❌ All strategies failed for Dailymotion {video_id}")
            logger.debug(f"HTML length: {len(html)} bytes")
            logger.debug(f"HTML preview: {html[:500]}")
            
            return None
            
        except Exception as e:
            logger.error(f"Dailymotion extraction error: {e}")
            if self.args.verbose:
                import traceback
                logger.debug(traceback.format_exc())
            return None

    def discover_from_html(self, html: str, page_url: str) -> Set[str]:
        """Discover video URLs from HTML"""
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
        
        # 2. <iframe> embeds (Dailymotion)
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src')
            if not src:
                continue
            
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
        
        # 4. Scan all URLs
        all_urls = re.findall(r'https?://[^\s"\'<>]+', html)
        for url in all_urls:
            url_lower = url.lower()
            if any(ext in url_lower for ext in VIDEO_EXTENSIONS):
                videos.add(url)
        
        # 5. DASH manifests
        mpd_urls = re.findall(r'(https?://[^\s"\'<>]+\.mpd(?:\?[^\s"\'<>]*)?)', html, re.IGNORECASE)
        videos.update(mpd_urls)
        
        return videos

    def extract_video_urls_from_network(self, driver) -> Set[str]:
        """Extract video URLs from Chrome performance logs"""
        video_urls = set()
        
        try:
            logs = driver.get_log('performance')
            
            for entry in logs:
                try:
                    log = json.loads(entry['message'])
                    message = log.get('message', {})
                    method = message.get('method', '')
                    
                    if method == 'Network.responseReceived':
                        response = message.get('params', {}).get('response', {})
                        url = response.get('url', '')
                        mime_type = response.get('mimeType', '')
                        
                        # Filter video URLs
                        if any(ext in url.lower() for ext in ['.m3u8', '.mpd', '.m4s', '.mp4', '.webm']):
                            video_urls.add(url)
                            logger.debug(f"📡 Network: {url[:80]}")
                        
                        # Check MIME type
                        if any(mime in mime_type for mime in ['video/', 'application/vnd.apple.mpegurl', 'application/dash+xml']):
                            video_urls.add(url)
                            logger.debug(f"📡 Network (MIME): {url[:80]}")
                
                except (json.JSONDecodeError, KeyError):
                    continue
        
        except Exception as e:
            logger.warning(f"Failed to extract from network logs: {e}")
        
        return video_urls

    def discover_with_selenium(self, page_url: str) -> Set[str]:
        """Discover videos using Selenium with network logging"""
        videos = set()
        
        if not self.selenium_available:
            return videos
        
        driver = None
        try:
            options = Options()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            
            # Enable performance logging
            options.set_capability('goog:loggingPrefs', {
                'performance': 'ALL',
                'browser': 'ALL'
            })
            
            # Stealth mode
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            if self.chromedriver_path:
                service = Service(executable_path=self.chromedriver_path)
                driver = webdriver.Chrome(service=service, options=options)
            else:
                driver = webdriver.Chrome(options=options)
            
            logger.info("Loading page with Selenium...")
            driver.get(page_url)
            time.sleep(self.args.js_wait)
            
            # Strategy 1: Network logs
            logger.info("Extracting from network logs...")
            network_videos = self.extract_video_urls_from_network(driver)
            videos.update(network_videos)
            logger.info(f"📡 Network: {len(network_videos)} video URLs")
            
            # Strategy 2: Main frame DOM
            logger.info("Extracting from main frame DOM...")
            main_html = driver.page_source
            main_videos = self.discover_from_html(main_html, page_url)
            videos.update(main_videos)
            logger.info(f"📄 Main frame: {len(main_videos)} video URLs")
            
            # Strategy 3: Iframes
            try:
                iframes = driver.find_elements(By.TAG_NAME, 'iframe')
                logger.info(f"Found {len(iframes)} iframes")
                
                for i, iframe in enumerate(iframes):
                    try:
                        iframe_src = iframe.get_attribute('src')
                        if iframe_src:
                            logger.debug(f"Iframe {i}: {iframe_src[:60]}")
                        
                        driver.switch_to.frame(iframe)
                        time.sleep(1)
                        
                        iframe_html = driver.page_source
                        iframe_videos = self.discover_from_html(iframe_html, page_url)
                        
                        if iframe_videos:
                            videos.update(iframe_videos)
                            logger.info(f"🖼 Iframe {i}: {len(iframe_videos)} video URLs")
                        
                        driver.switch_to.default_content()
                        
                    except Exception as e:
                        logger.debug(f"Iframe {i} failed: {e}")
                        try:
                            driver.switch_to.default_content()
                        except:
                            pass
            
            except Exception as e:
                logger.warning(f"Iframe processing failed: {e}")
            
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
        
        filename = re.sub(r'[^\w\-.]', '_', filename)
        
        if not filename or filename == '_':
            url_hash = hashlib.md5(video_url.encode()).hexdigest()[:8]
            filename = f"video_{url_hash}.mp4"
        
        if force_mp4 and not filename.endswith('.mp4'):
            filename = os.path.splitext(filename)[0] + '.mp4'
        
        output_path = self.output_dir / filename
        
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
        """Convert HLS/DASH stream to MP4"""
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
        """Write to CSV log"""
        self.csv_writer.writerow([source_url, video_url, local_path, status, note])
        self.csv_file.flush()

    def save_stream_url(self, url: str, stream_type: str = ""):
        """Save stream URL to file"""
        with open(self.stream_file_path, 'a', encoding='utf-8') as f:
            if stream_type:
                f.write(f"[{stream_type}] {url}\n")
            else:
                f.write(f"{url}\n")

    def process_video(self, video_url: str, source_url: str):
        """Process a single video URL"""
        normalized = self.normalize_url(video_url)
        
        if normalized.startswith('data:'):
            return
        
        if normalized in self.downloaded_urls:
            return
        
        self.downloaded_urls.add(normalized)
        self.stats['found'] += 1
        
        if not self.check_robots(normalized, is_media_file=True):
            self.log_to_csv(source_url, normalized, '', 'robots_blocked', '')
            return
        
        url_lower = normalized.lower()
        is_hls = '.m3u8' in url_lower
        is_dash = '.mpd' in url_lower or '.m4s' in url_lower
        
        if is_dash:
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
        
        videos = set()
        
        # Strategy 1: HTML parsing (always)
        html_videos = self.discover_from_html(html, url)
        videos.update(html_videos)
        logger.info(f"📄 HTML parsing: {len(html_videos)} video URLs")
        
        # Strategy 2: JavaScript rendering
        if self.args.render_js:
            if self.selenium_available:
                logger.info("Using Selenium for JS rendering...")
                selenium_videos = self.discover_with_selenium(url)
                videos.update(selenium_videos)
                logger.info(f"🌐 Selenium: {len(selenium_videos)} additional videos")
            elif self.playwright_available:
                logger.info("Using Playwright for JS rendering...")
                playwright_videos = self.discover_with_playwright(url)
                videos.update(playwright_videos)
                logger.info(f"🎭 Playwright: {len(playwright_videos)} additional videos")
            else:
                logger.warning("⚠ --render-js specified but no JS engine available")
        
        # Filter noise
        videos = {v for v in videos if not self.is_noise(v)}
        logger.info(f"After noise filtering: {len(videos)} total videos")
        
        # Check if no videos found
        if not videos:
            logger.error("\n" + "="*60)
            logger.error("❌ NO VIDEOS FOUND")
            logger.error("="*60)
            logger.error("\nPossible reasons:")
            logger.error("  1. Page has no videos")
            logger.error("  2. Videos are loaded with JavaScript")
            logger.error("  3. Videos are in iframes not detected")
            
            if not self.args.render_js:
                logger.error("\n💡 Try with --render-js to enable JavaScript rendering")
            elif not self.selenium_available:
                logger.error("\n💡 Selenium not available:")
                logger.error("  - Chrome not installed")
                logger.error("  - Download: https://www.google.com/chrome/")
                logger.error("  - Or use: --chrome-binary /path/to/chrome")
            
            logger.error("="*60 + "\n")
            return
        
        # Process videos
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
                print("\n⚠ FFmpeg not found - install it to enable automatic conversion")

    def cleanup(self):
        """Cleanup"""
        self.csv_file.close()


def setup_logging(verbose: bool = False):
    """Setup logging"""
    level = logging.DEBUG if verbose else logging.INFO
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    console_handler.setFormatter(console_format)
    
    file_handler = logging.FileHandler('video_downloader.log', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    file_handler.setFormatter(file_format)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def main():
    parser = argparse.ArgumentParser(
        description='Professional Video Downloader (MP4 + HLS + DASH + Dailymotion)',
        epilog='Example: %(prog)s "https://example.com/video" --render-js --verbose'
    )
    parser.add_argument('url', help='Page URL')
    parser.add_argument('--out', default='./downloads', help='Output directory (default: ./downloads)')
    parser.add_argument('--rate', type=float, default=2.0, help='Rate limit (req/s, default: 2.0)')
    parser.add_argument('--retries', type=int, default=3, help='Retries (default: 3)')
    parser.add_argument('--timeout', type=int, default=20, help='Timeout (seconds, default: 20)')
    parser.add_argument('--render-js', action='store_true', help='Use Selenium/Playwright')
    parser.add_argument('--js-wait', type=int, default=5, help='JS wait time (seconds, default: 5)')
    parser.add_argument('--ignore-robots', action='store_true', help='Ignore robots.txt')
    parser.add_argument('--cookies', help='Cookies (format: "k1=v1; k2=v2")')
    parser.add_argument('--auth-user', help='Basic auth username')
    parser.add_argument('--auth-pass', help='Basic auth password')
    parser.add_argument('--chrome-binary', help='Path to chrome.exe (for Selenium)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging + debug files')
    
    args = parser.parse_args()
    
    setup_logging(args.verbose)
    
    downloader = VideoDownloader(args)
    try:
        downloader.run()
    finally:
        downloader.cleanup()


if __name__ == '__main__':
    main()
