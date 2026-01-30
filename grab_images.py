#!/usr/bin/env python3
"""
Advanced Image Downloader with Smart Content Filtering
Features: Content filtering, robots.txt, retry logic, progress tracking, metadata extraction
"""

import os
import sys
import argparse
import logging
import time
import hashlib
import mimetypes
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib import robotparser
from typing import Set, Optional, Tuple, List, Dict, Callable
from datetime import datetime
import re

import requests
from bs4 import BeautifulSoup
from PIL import Image
import io

# Optional dependencies
try:
    import imagehash
    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# User-Agent rotation pool
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
]

# Regex patterns - FLAGS AT THE START!
CSS_URL_PATTERN = re.compile(
    r'(?i)url\s*\(\s*["\']?([^"\')]+)["\']?\s*\)',
    re.IGNORECASE | re.MULTILINE
)

INLINE_STYLE_PATTERN = re.compile(
    r'(?i)url\s*\(\s*["\']?([^"\')]+)["\']?\s*\)',
    re.IGNORECASE
)


class DownloadError(Exception):
    """Custom exception for download errors"""
    pass


class ImageDownloader:
    """Advanced image downloader with full feature set"""
    
    def __init__(
        self,
        output_dir: str = "./images",
        rate_limit: float = 2.0,
        timeout: int = 20,
        parse_css: bool = False,
        compress: bool = False,
        perceptual_hash: bool = False,
        max_size_mb: int = 50,
        min_width: int = 200,
        min_height: int = 150,
        content_only: bool = True,
        respect_robots: bool = True,
        organize_by_domain: bool = False,
        organize_by_date: bool = False,
        generate_thumbnails: bool = False,
        save_metadata: bool = False,
        max_retries: int = 3,
        progress_callback: Optional[Callable] = None
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.parse_css = parse_css
        self.compress = compress
        self.perceptual_hash = perceptual_hash
        self.max_size_mb = max_size_mb
        self.min_width = min_width
        self.min_height = min_height
        self.content_only = content_only
        self.respect_robots = respect_robots
        self.organize_by_domain = organize_by_domain
        self.organize_by_date = organize_by_date
        self.generate_thumbnails = generate_thumbnails
        self.save_metadata = save_metadata
        self.max_retries = max_retries
        self.progress_callback = progress_callback
        
        self.session = requests.Session()
        self.current_ua_index = 0
        
        self.visited_urls: Set[str] = set()
        self.downloaded_hashes: Set[str] = set()
        self.downloaded_phashes: Set[str] = set()
        self.downloaded_files: List[str] = []
        self.failed_urls: List[Dict] = []
        self.last_request_time = 0
        
        # Robots.txt cache
        self.robot_parsers: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        
        self.stats = {
            'pages_crawled': 0,
            'images_found': 0,
            'images_downloaded': 0,
            'duplicates_skipped': 0,
            'filtered_unwanted': 0,
            'filtered_size': 0,
            'errors_total': 0,
            'errors_timeout': 0,
            'errors_connection': 0,
            'errors_404': 0,
            'errors_403': 0,
            'errors_robots': 0,
        }
    
    def _get_user_agent(self) -> str:
        """Rotate user agents to avoid detection"""
        ua = USER_AGENTS[self.current_ua_index]
        self.current_ua_index = (self.current_ua_index + 1) % len(USER_AGENTS)
        return ua
    
    def _can_fetch(self, url: str) -> bool:
        """Check robots.txt before fetching"""
        if not self.respect_robots:
            return True
        
        try:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            
            # Check cache
            if base_url not in self.robot_parsers:
                rp = robotparser.RobotFileParser()
                rp.set_url(f"{base_url}/robots.txt")
                try:
                    rp.read()
                    self.robot_parsers[base_url] = rp
                    logger.debug(f"Loaded robots.txt from {base_url}")
                except Exception as e:
                    logger.debug(f"No robots.txt at {base_url}: {e}")
                    self.robot_parsers[base_url] = None
            
            rp = self.robot_parsers[base_url]
            if rp:
                can_fetch = rp.can_fetch("*", url)
                if not can_fetch:
                    logger.debug(f"Blocked by robots.txt: {url}")
                return can_fetch
            
            return True
        except Exception as e:
            logger.debug(f"Error checking robots.txt: {e}")
            return True
    
    def _rate_limit_wait(self):
        """Enforce rate limiting"""
        if self.rate_limit > 0:
            elapsed = time.time() - self.last_request_time
            wait_time = (1.0 / self.rate_limit) - elapsed
            if wait_time > 0:
                time.sleep(wait_time)
        self.last_request_time = time.time()
    
    def _normalize_url(self, url: str) -> str:
        """Normalize URL for comparison"""
        parsed = urlparse(url)
        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            '',
            '',
            ''
        ))
    
    def _is_valid_image_url(self, url: str) -> bool:
        """Check if URL is likely an image"""
        if not url:
            return False
        
        path = urlparse(url).path.lower()
        
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico'}
        if any(path.endswith(ext) for ext in image_extensions):
            return True
        
        if any(x in path for x in ['/image', '/img', '/photo', '/picture', '/thumb']):
            return True
        
        return False
    
    def _is_unwanted_image(self, img_tag, img_url: str) -> Tuple[bool, str]:
        """
        Multi-layer filtering for unwanted images
        Returns (is_unwanted, reason)
        """
        
        # 1. URL Pattern Blacklist
        url_lower = img_url.lower()
        url_blacklist = ['/logo', '/icon', '/sprite', '/ad', '/banner', 
                        '/placeholder', '/avatar', '/emoji', '/social']
        
        for pattern in url_blacklist:
            if pattern in url_lower:
                return True, f"url_pattern:{pattern}"
        
        # 2. Class Blacklist
        classes = img_tag.get('class', [])
        classes_str = ' '.join(classes).lower() if classes else ''
        
        class_blacklist = ['logo', 'icon', 'avatar', 'badge', 'emoji',
                          'ad', 'banner', 'widget', 'sidebar', 'nav',
                          'menu', 'footer', 'header', 'sponsor', 'advertisement']
        
        for unwanted in class_blacklist:
            if unwanted in classes_str:
                return True, f"class:{unwanted}"
        
        # 3. ID Blacklist
        img_id = (img_tag.get('id') or '').lower()
        for unwanted in class_blacklist:
            if unwanted in img_id:
                return True, f"id:{unwanted}"
        
        # 4. Parent Element Check
        parent = img_tag.find_parent()
        if parent:
            parent_class = ' '.join(parent.get('class', [])).lower() if parent.get('class') else ''
            parent_blacklist = ['header', 'footer', 'nav', 'sidebar', 'menu', 'ad', 'widget', 'advertisement']
            
            for unwanted in parent_blacklist:
                if unwanted in parent_class:
                    return True, f"parent:{unwanted}"
        
        # 5. Alt Text Check
        alt = (img_tag.get('alt') or '').lower()
        alt_blacklist = ['logo', 'icon', 'advertisement', 'ad', 'banner']
        
        for unwanted in alt_blacklist:
            if unwanted in alt:
                return True, f"alt:{unwanted}"
        
        # 6. Minimum Size Check
        width = img_tag.get('width')
        height = img_tag.get('height')
        
        if width and height:
            try:
                w, h = int(width), int(height)
                if w < self.min_width or h < self.min_height:
                    return True, f"size:{w}x{h}"
            except (ValueError, TypeError):
                pass
        
        return False, ""
    
    def _get_image_hash(self, content: bytes) -> str:
        """Calculate content hash"""
        return hashlib.md5(content).hexdigest()
    
    def _get_perceptual_hash(self, content: bytes) -> Optional[str]:
        """Calculate perceptual hash using imagehash"""
        if not IMAGEHASH_AVAILABLE:
            return None
        
        try:
            img = Image.open(io.BytesIO(content))
            phash = imagehash.phash(img)
            return str(phash)
        except Exception as e:
            logger.debug(f"Failed to calculate phash: {e}")
            return None
    
    def _should_download(self, content: bytes) -> Tuple[bool, str]:
        """Check if image should be downloaded (not duplicate)"""
        content_hash = self._get_image_hash(content)
        if content_hash in self.downloaded_hashes:
            return False, "duplicate_hash"
        
        if self.perceptual_hash and IMAGEHASH_AVAILABLE:
            phash = self._get_perceptual_hash(content)
            if phash and phash in self.downloaded_phashes:
                return False, "duplicate_phash"
            if phash:
                self.downloaded_phashes.add(phash)
        
        self.downloaded_hashes.add(content_hash)
        return True, ""
    
    def _extract_image_info(self, content: bytes) -> dict:
        """Extract image metadata"""
        try:
            img = Image.open(io.BytesIO(content))
            return {
                'format': img.format,
                'mode': img.mode,
                'size': list(img.size),
                'width': img.width,
                'height': img.height,
                'file_size': len(content)
            }
        except Exception as e:
            logger.debug(f"Failed to extract image info: {e}")
            return {}
    
    def _create_thumbnail(self, content: bytes, size=(200, 200)) -> Optional[bytes]:
        """Create thumbnail for preview"""
        try:
            img = Image.open(io.BytesIO(content))
            img.thumbnail(size, Image.Resampling.LANCZOS)
            
            thumb_io = io.BytesIO()
            img.save(thumb_io, format='JPEG', quality=85)
            return thumb_io.getvalue()
        except Exception as e:
            logger.debug(f"Failed to create thumbnail: {e}")
            return None
    
    def _compress_image(self, content: bytes) -> bytes:
        """Compress image to reduce file size"""
        try:
            img = Image.open(io.BytesIO(content))
            
            if img.mode == 'RGBA':
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            elif img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=85, optimize=True)
            return output.getvalue()
        except Exception as e:
            logger.debug(f"Compression failed: {e}")
            return content
    
    def _get_output_path(self, url: str, filename: str) -> Path:
        """Get organized output path based on settings"""
        output_dir = self.output_dir
        
        if self.organize_by_domain:
            domain = urlparse(url).netloc
            output_dir = output_dir / domain
        
        if self.organize_by_date:
            date = datetime.now().strftime('%Y-%m-%d')
            output_dir = output_dir / date
        
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir / filename
    
    def _download_with_retry(self, url: str, max_retries: Optional[int] = None) -> Optional[bytes]:
        """Download with exponential backoff retry"""
        if max_retries is None:
            max_retries = self.max_retries
        
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                headers = {'User-Agent': self._get_user_agent()}
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    headers=headers,
                    stream=True
                )
                response.raise_for_status()
                return response.content
                
            except requests.Timeout as e:
                last_exception = e
                self.stats['errors_timeout'] += 1
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Timeout, retry {attempt+1}/{max_retries} after {wait}s: {url}")
                    time.sleep(wait)
                    
            except requests.ConnectionError as e:
                last_exception = e
                self.stats['errors_connection'] += 1
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning(f"Connection error, retry {attempt+1}/{max_retries} after {wait}s: {url}")
                    time.sleep(wait)
                    
            except requests.HTTPError as e:
                last_exception = e
                if e.response.status_code == 404:
                    self.stats['errors_404'] += 1
                    logger.debug(f"404 Not Found: {url}")
                elif e.response.status_code == 403:
                    self.stats['errors_403'] += 1
                    logger.debug(f"403 Forbidden: {url}")
                break  # Don't retry HTTP errors
                
            except Exception as e:
                last_exception = e
                break
        
        logger.error(f"Failed after {max_retries} attempts: {url} - {last_exception}")
        return None
    
    def _download_image(self, url: str, base_url: str) -> bool:
        """Download a single image with full feature support"""
        try:
            full_url = urljoin(base_url, url)
            
            # Check robots.txt
            if not self._can_fetch(full_url):
                self.stats['errors_robots'] += 1
                logger.debug(f"Blocked by robots.txt: {full_url}")
                return False
            
            # Skip if already visited
            normalized = self._normalize_url(full_url)
            if normalized in self.visited_urls:
                return False
            self.visited_urls.add(normalized)
            
            # Rate limiting
            self._rate_limit_wait()
            
            # Download with retry
            content = self._download_with_retry(full_url)
            if not content:
                self.failed_urls.append({
                    'url': full_url,
                    'reason': 'download_failed',
                    'timestamp': datetime.now().isoformat()
                })
                return False
            
            # Check size
            if len(content) > self.max_size_mb * 1024 * 1024:
                logger.warning(f"Image too large: {full_url} ({len(content) / 1024 / 1024:.1f}MB)")
                self.stats['filtered_size'] += 1
                return False
            
            # Check if duplicate
            should_download, reason = self._should_download(content)
            if not should_download:
                self.stats['duplicates_skipped'] += 1
                logger.debug(f"Skipping duplicate ({reason}): {full_url}")
                return False
            
            # Compress if enabled
            if self.compress:
                content = self._compress_image(content)
            
            # Determine filename
            parsed = urlparse(full_url)
            filename = Path(parsed.path).name or 'image'
            filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
            
            if not filename or filename == '_':
                ext = mimetypes.guess_extension(
                    mimetypes.guess_type(full_url)[0] or 'image/jpeg'
                ) or '.jpg'
                filename = f"image_{len(self.downloaded_hashes)}{ext}"
            
            # Get organized output path
            output_path = self._get_output_path(full_url, filename)
            
            # Handle duplicate filenames
            counter = 1
            while output_path.exists():
                stem = output_path.stem
                suffix = output_path.suffix
                output_path = output_path.parent / f"{stem}_{counter}{suffix}"
                counter += 1
            
            # Save image
            output_path.write_bytes(content)
            self.downloaded_files.append(str(output_path))
            
            # Generate thumbnail if enabled
            if self.generate_thumbnails:
                thumb_content = self._create_thumbnail(content)
                if thumb_content:
                    thumb_path = output_path.parent / f"{output_path.stem}_thumb{output_path.suffix}"
                    thumb_path.write_bytes(thumb_content)
            
            # Save metadata if enabled
            if self.save_metadata:
                image_info = self._extract_image_info(content)
                if image_info:
                    image_info['url'] = full_url
                    image_info['downloaded_at'] = datetime.now().isoformat()
                    metadata_path = output_path.with_suffix('.json')
                    metadata_path.write_text(json.dumps(image_info, indent=2))
            
            self.stats['images_downloaded'] += 1
            logger.info(f"Downloaded: {output_path.name} ({len(content) / 1024:.1f}KB)")
            
            # Progress callback
            if self.progress_callback:
                self.progress_callback({
                    'url': full_url,
                    'status': 'success',
                    'filename': output_path.name,
                    'size': len(content),
                    'downloaded': self.stats['images_downloaded'],
                    'total': self.stats['images_found']
                })
            
            return True
            
        except Exception as e:
            self.stats['errors_total'] += 1
            logger.error(f"Error downloading {url}: {e}")
            self.failed_urls.append({
                'url': full_url,
                'reason': str(e),
                'timestamp': datetime.now().isoformat()
            })
            
            if self.progress_callback:
                self.progress_callback({
                    'url': full_url,
                    'status': 'error',
                    'error': str(e)
                })
            
            return False
    
    def _extract_images_from_html(self, soup: BeautifulSoup, base_url: str) -> Set[str]:
        """Extract image URLs from HTML with smart content filtering"""
        images = set()
        
        # Content-only mode
        if self.content_only:
            content_containers = (
                soup.find_all('article') or
                soup.find_all(attrs={'class': re.compile(r'(content|post-body|article|entry-content|main-content)', re.I)}) or
                soup.find_all('main') or
                [soup]
            )
            search_scope = content_containers
        else:
            search_scope = [soup]
        
        # Extract from scope
        for container in search_scope:
            # <img> tags
            for img in container.find_all('img'):
                src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                if src:
                    is_unwanted, reason = self._is_unwanted_image(img, src)
                    if is_unwanted:
                        self.stats['filtered_unwanted'] += 1
                        logger.debug(f"Filtered ({reason}): {src}")
                        continue
                    images.add(src)
            
            # <source> tags
            for source in container.find_all('source'):
                srcset = source.get('srcset')
                if srcset:
                    for item in srcset.split(','):
                        url = item.strip().split()[0]
                        images.add(url)
            
            # CSS backgrounds
            for tag in container.find_all(style=True):
                style = tag['style']
                urls = INLINE_STYLE_PATTERN.findall(style)
                images.update(urls)
        
        return images
    
    def _extract_images_from_css(self, css_text: str) -> Set[str]:
        """Extract image URLs from CSS"""
        return set(CSS_URL_PATTERN.findall(css_text))
    
    def _crawl_page(self, url: str) -> Set[str]:
        """Crawl a single page and extract image URLs"""
        try:
            self._rate_limit_wait()
            
            headers = {'User-Agent': self._get_user_agent()}
            response = self.session.get(url, timeout=self.timeout, headers=headers)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            images = self._extract_images_from_html(soup, url)
            
            if self.parse_css:
                for style_tag in soup.find_all('style'):
                    css_images = self._extract_images_from_css(style_tag.string or '')
                    images.update(css_images)
                
                for link in soup.find_all('link', rel='stylesheet'):
                    css_url = link.get('href')
                    if css_url:
                        try:
                            css_url = urljoin(url, css_url)
                            css_response = self.session.get(css_url, timeout=self.timeout, headers=headers)
                            css_images = self._extract_images_from_css(css_response.text)
                            images.update(css_images)
                        except Exception as e:
                            logger.debug(f"Failed to fetch CSS: {e}")
            
            self.stats['pages_crawled'] += 1
            return images
            
        except Exception as e:
            logger.error(f"Failed to crawl {url}: {e}")
            return set()
    
    def _save_report(self):
        """Save download report to JSON"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'stats': self.stats,
            'downloaded_files': self.downloaded_files,
            'failed_urls': self.failed_urls,
            'settings': {
                'content_only': self.content_only,
                'min_size': f"{self.min_width}x{self.min_height}",
                'respect_robots': self.respect_robots,
                'compress': self.compress,
            }
        }
        
        report_path = self.output_dir / 'download_report.json'
        report_path.write_text(json.dumps(report, indent=2))
        logger.info(f"Report saved: {report_path}")
    
    def download_from_url(self, start_url: str, depth: int = 0, max_pages: int = 50):
        """Download images from URL with optional crawling"""
        logger.info(f"Starting download from: {start_url}")
        logger.info(f"Content filtering: {'ENABLED' if self.content_only else 'DISABLED'}")
        logger.info(f"Min size: {self.min_width}x{self.min_height}")
        logger.info(f"Robots.txt: {'RESPECT' if self.respect_robots else 'IGNORE'}")
        logger.info(f"Crawl depth: {depth}, Max pages: {max_pages}")
        
        queue = [(start_url, 0)]
        visited_pages = set()
        
        while queue and len(visited_pages) < max_pages:
            current_url, current_depth = queue.pop(0)
            
            if current_url in visited_pages:
                continue
            visited_pages.add(current_url)
            
            logger.info(f"Crawling: {current_url} (depth: {current_depth})")
            
            images = self._crawl_page(current_url)
            self.stats['images_found'] += len(images)
            
            logger.info(f"Found {len(images)} images (after filtering)")
            
            for img_url in images:
                if self._is_valid_image_url(img_url):
                    self._download_image(img_url, current_url)
            
            if current_depth < depth:
                try:
                    headers = {'User-Agent': self._get_user_agent()}
                    response = self.session.get(current_url, timeout=self.timeout, headers=headers)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    current_domain = urlparse(current_url).netloc
                    for link in soup.find_all('a', href=True):
                        next_url = urljoin(current_url, link['href'])
                        next_domain = urlparse(next_url).netloc
                        
                        if next_domain == current_domain:
                            if next_url not in visited_pages:
                                queue.append((next_url, current_depth + 1))
                
                except Exception as e:
                    logger.debug(f"Failed to extract links: {e}")
        
        # Save report
        self._save_report()
        
        # Print statistics
        logger.info("\n" + "=" * 60)
        logger.info("DOWNLOAD SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Pages crawled:       {self.stats['pages_crawled']}")
        logger.info(f"Images found:        {self.stats['images_found']}")
        logger.info(f"Images downloaded:   {self.stats['images_downloaded']}")
        logger.info(f"Duplicates skipped:  {self.stats['duplicates_skipped']}")
        logger.info(f"Filtered (unwanted): {self.stats['filtered_unwanted']}")
        logger.info(f"Filtered (size):     {self.stats['filtered_size']}")
        logger.info(f"Blocked (robots):    {self.stats['errors_robots']}")
        logger.info(f"Errors - Total:      {self.stats['errors_total']}")
        logger.info(f"       - Timeout:    {self.stats['errors_timeout']}")
        logger.info(f"       - Connection: {self.stats['errors_connection']}")
        logger.info(f"       - 404:        {self.stats['errors_404']}")
        logger.info(f"       - 403:        {self.stats['errors_403']}")
        logger.info("=" * 60)
    
    def download_from_urls(self, urls: List[str], depth: int = 0, max_pages: int = 50):
        """Download from multiple URLs (batch mode)"""
        logger.info(f"Batch mode: Processing {len(urls)} URLs")
        
        for i, url in enumerate(urls, 1):
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing URL {i}/{len(urls)}: {url}")
            logger.info(f"{'='*60}\n")
            self.download_from_url(url, depth, max_pages)


def main():
    parser = argparse.ArgumentParser(
        description='Advanced image downloader with smart content filtering',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic download with content filtering:
  %(prog)s https://example.com/article --compress

  # Strict filtering with small minimum size:
  %(prog)s URL --min-width 300 --min-height 250

  # Organized output with metadata:
  %(prog)s URL --organize-by-domain --organize-by-date --save-metadata

  # Batch mode from file:
  %(prog)s --url-file urls.txt

  # Ignore robots.txt (use responsibly):
  %(prog)s URL --ignore-robots
        """
    )
    
    # Input
    parser.add_argument('url', nargs='?', help='Starting URL')
    parser.add_argument('--url-file', help='File with URLs (one per line)')
    
    # Output
    parser.add_argument('--out', default='./images', help='Output directory')
    parser.add_argument('--organize-by-domain', action='store_true', help='Organize files by domain')
    parser.add_argument('--organize-by-date', action='store_true', help='Organize files by date')
    
    # Crawling
    parser.add_argument('--depth', type=int, default=0, help='Crawl depth (0 = single page)')
    parser.add_argument('--max-pages', type=int, default=50, help='Maximum pages to crawl')
    
    # Filtering
    parser.add_argument('--min-width', type=int, default=200, help='Minimum image width')
    parser.add_argument('--min-height', type=int, default=150, help='Minimum image height')
    parser.add_argument('--no-content-filter', action='store_true', help='Disable content-only filtering')
    
    # Network
    parser.add_argument('--rate', type=float, default=2.0, help='Request rate limit (req/sec)')
    parser.add_argument('--timeout', type=int, default=20, help='Request timeout (seconds)')
    parser.add_argument('--max-retries', type=int, default=3, help='Max retry attempts')
    parser.add_argument('--ignore-robots', action='store_true', help='Ignore robots.txt')
    
    # Processing
    parser.add_argument('--parse-css', action='store_true', help='Parse CSS for background images')
    parser.add_argument('--compress', action='store_true', help='Compress images (JPEG, quality=85)')
    parser.add_argument('--perceptual-hash', action='store_true', help='Use perceptual hashing')
    parser.add_argument('--max-size', type=int, default=50, help='Max image size in MB')
    
    # Features
    parser.add_argument('--generate-thumbnails', action='store_true', help='Generate thumbnails (200x200)')
    parser.add_argument('--save-metadata', action='store_true', help='Save image metadata as JSON')
    
    # Misc
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    
    # Validate input
    if not args.url and not args.url_file:
        parser.error("Either url or --url-file must be provided")
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.perceptual_hash and not IMAGEHASH_AVAILABLE:
        logger.warning("imagehash not available, perceptual hashing disabled")
        args.perceptual_hash = False
    
    # Create downloader
    downloader = ImageDownloader(
        output_dir=args.out,
        rate_limit=args.rate,
        timeout=args.timeout,
        parse_css=args.parse_css,
        compress=args.compress,
        perceptual_hash=args.perceptual_hash,
        max_size_mb=args.max_size,
        min_width=args.min_width,
        min_height=args.min_height,
        content_only=not args.no_content_filter,
        respect_robots=not args.ignore_robots,
        organize_by_domain=args.organize_by_domain,
        organize_by_date=args.organize_by_date,
        generate_thumbnails=args.generate_thumbnails,
        save_metadata=args.save_metadata,
        max_retries=args.max_retries
    )
    
    # Download
    if args.url_file:
        urls = Path(args.url_file).read_text().strip().splitlines()
        urls = [u.strip() for u in urls if u.strip() and not u.startswith('#')]
        downloader.download_from_urls(urls, args.depth, args.max_pages)
    else:
        downloader.download_from_url(args.url, args.depth, args.max_pages)


if __name__ == '__main__':
    main()



