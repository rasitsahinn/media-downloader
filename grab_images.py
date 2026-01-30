#!/usr/bin/env python3
"""
Image downloader with advanced features
Supports crawling, CSS parsing, compression, and perceptual hashing
"""

import os
import sys
import argparse
import logging
import time
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from typing import Set, Optional, Tuple
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

# Regex patterns - FLAGS AT THE START!
CSS_URL_PATTERN = re.compile(
    r'(?i)url\s*\(\s*["\']?([^"\')]+)["\']?\s*\)',
    re.IGNORECASE | re.MULTILINE
)

INLINE_STYLE_PATTERN = re.compile(
    r'(?i)url\s*\(\s*["\']?([^"\')]+)["\']?\s*\)',
    re.IGNORECASE
)


class ImageDownloader:
    """Advanced image downloader with crawling and deduplication"""
    
    def __init__(
        self,
        output_dir: str = "./images",
        rate_limit: float = 2.0,
        timeout: int = 20,
        parse_css: bool = False,
        compress: bool = False,
        perceptual_hash: bool = False,
        max_size_mb: int = 50
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.rate_limit = rate_limit
        self.timeout = timeout
        self.parse_css = parse_css
        self.compress = compress
        self.perceptual_hash = perceptual_hash
        self.max_size_mb = max_size_mb
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        self.visited_urls: Set[str] = set()
        self.downloaded_hashes: Set[str] = set()
        self.downloaded_phashes: Set[str] = set()
        self.last_request_time = 0
        
        self.stats = {
            'pages_crawled': 0,
            'images_found': 0,
            'images_downloaded': 0,
            'duplicates_skipped': 0,
            'errors': 0
        }
    
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
        
        # Remove query strings for extension check
        path = urlparse(url).path.lower()
        
        # Check common image extensions
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.ico'}
        if any(path.endswith(ext) for ext in image_extensions):
            return True
        
        # Check if path looks like image (common patterns)
        if any(x in path for x in ['/image', '/img', '/photo', '/picture', '/thumb']):
            return True
        
        return False
    
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
        # Check content hash
        content_hash = self._get_image_hash(content)
        if content_hash in self.downloaded_hashes:
            return False, "duplicate_hash"
        
        # Check perceptual hash if enabled
        if self.perceptual_hash and IMAGEHASH_AVAILABLE:
            phash = self._get_perceptual_hash(content)
            if phash and phash in self.downloaded_phashes:
                return False, "duplicate_phash"
            if phash:
                self.downloaded_phashes.add(phash)
        
        self.downloaded_hashes.add(content_hash)
        return True, ""
    
    def _compress_image(self, content: bytes) -> bytes:
        """Compress image to reduce file size"""
        try:
            img = Image.open(io.BytesIO(content))
            
            # Convert RGBA to RGB if necessary
            if img.mode == 'RGBA':
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            elif img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            
            # Compress
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=85, optimize=True)
            return output.getvalue()
        except Exception as e:
            logger.debug(f"Compression failed: {e}")
            return content
    
    def _download_image(self, url: str, base_url: str) -> bool:
        """Download a single image"""
        try:
            # Resolve relative URLs
            full_url = urljoin(base_url, url)
            
            # Skip if already visited
            normalized = self._normalize_url(full_url)
            if normalized in self.visited_urls:
                return False
            self.visited_urls.add(normalized)
            
            # Rate limiting
            self._rate_limit_wait()
            
            # Download
            response = self.session.get(
                full_url,
                timeout=self.timeout,
                stream=True
            )
            response.raise_for_status()
            
            # Check size
            content_length = response.headers.get('content-length')
            if content_length and int(content_length) > self.max_size_mb * 1024 * 1024:
                logger.warning(f"Image too large: {full_url} ({int(content_length) / 1024 / 1024:.1f}MB)")
                return False
            
            # Get content
            content = response.content
            
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
            
            # Ensure valid filename
            filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
            if not filename or filename == '_':
                ext = mimetypes.guess_extension(response.headers.get('content-type', '')) or '.jpg'
                filename = f"image_{len(self.downloaded_hashes)}{ext}"
            
            # Save
            output_path = self.output_dir / filename
            
            # Handle duplicate filenames
            counter = 1
            while output_path.exists():
                stem = output_path.stem
                suffix = output_path.suffix
                output_path = self.output_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            
            output_path.write_bytes(content)
            
            self.stats['images_downloaded'] += 1
            logger.info(f"Downloaded: {output_path.name} ({len(content) / 1024:.1f}KB)")
            return True
            
        except requests.RequestException as e:
            self.stats['errors'] += 1
            logger.warning(f"Failed to download {url}: {e}")
            return False
        except Exception as e:
            self.stats['errors'] += 1
            logger.error(f"Error downloading {url}: {e}")
            return False
    
    def _extract_images_from_html(self, soup: BeautifulSoup, base_url: str) -> Set[str]:
        """Extract image URLs from HTML"""
        images = set()
        
        # <img> tags
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if src:
                images.add(src)
        
        # <source> tags (for <picture> elements)
        for source in soup.find_all('source'):
            srcset = source.get('srcset')
            if srcset:
                # Parse srcset (can contain multiple URLs)
                for item in srcset.split(','):
                    url = item.strip().split()[0]
                    images.add(url)
        
        # CSS background images in style attributes
        for tag in soup.find_all(style=True):
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
            
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract images from HTML
            images = self._extract_images_from_html(soup, url)
            
            # Extract images from CSS if enabled
            if self.parse_css:
                # Inline styles
                for style_tag in soup.find_all('style'):
                    css_images = self._extract_images_from_css(style_tag.string or '')
                    images.update(css_images)
                
                # External CSS files
                for link in soup.find_all('link', rel='stylesheet'):
                    css_url = link.get('href')
                    if css_url:
                        try:
                            css_url = urljoin(url, css_url)
                            css_response = self.session.get(css_url, timeout=self.timeout)
                            css_images = self._extract_images_from_css(css_response.text)
                            images.update(css_images)
                        except Exception as e:
                            logger.debug(f"Failed to fetch CSS: {e}")
            
            self.stats['pages_crawled'] += 1
            return images
            
        except Exception as e:
            logger.error(f"Failed to crawl {url}: {e}")
            return set()
    
    def download_from_url(
        self,
        start_url: str,
        depth: int = 0,
        max_pages: int = 50
    ):
        """Download images from URL with optional crawling"""
        logger.info(f"Starting download from: {start_url}")
        logger.info(f"Crawl depth: {depth}, Max pages: {max_pages}")
        
        # Queue for BFS crawling
        queue = [(start_url, 0)]
        visited_pages = set()
        
        while queue and len(visited_pages) < max_pages:
            current_url, current_depth = queue.pop(0)
            
            # Skip if already visited
            if current_url in visited_pages:
                continue
            visited_pages.add(current_url)
            
            logger.info(f"Crawling: {current_url} (depth: {current_depth})")
            
            # Extract images from page
            images = self._crawl_page(current_url)
            self.stats['images_found'] += len(images)
            
            logger.info(f"Found {len(images)} images on page")
            
            # Download images
            for img_url in images:
                if self._is_valid_image_url(img_url):
                    self._download_image(img_url, current_url)
            
            # If we should crawl deeper, extract links
            if current_depth < depth:
                try:
                    response = self.session.get(current_url, timeout=self.timeout)
                    soup = BeautifulSoup(response.text, 'html.parser')
                    
                    # Find all links on same domain
                    current_domain = urlparse(current_url).netloc
                    for link in soup.find_all('a', href=True):
                        next_url = urljoin(current_url, link['href'])
                        next_domain = urlparse(next_url).netloc
                        
                        # Only crawl same domain
                        if next_domain == current_domain:
                            if next_url not in visited_pages:
                                queue.append((next_url, current_depth + 1))
                
                except Exception as e:
                    logger.debug(f"Failed to extract links: {e}")
        
        # Print statistics
        logger.info("\n" + "=" * 60)
        logger.info("DOWNLOAD SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Pages crawled:      {self.stats['pages_crawled']}")
        logger.info(f"Images found:       {self.stats['images_found']}")
        logger.info(f"Images downloaded:  {self.stats['images_downloaded']}")
        logger.info(f"Duplicates skipped: {self.stats['duplicates_skipped']}")
        logger.info(f"Errors:             {self.stats['errors']}")
        logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description='Download images from web pages with advanced features'
    )
    parser.add_argument('url', help='Starting URL')
    parser.add_argument('--out', default='./images', help='Output directory')
    parser.add_argument('--depth', type=int, default=0, help='Crawl depth (0 = single page)')
    parser.add_argument('--max-pages', type=int, default=50, help='Maximum pages to crawl')
    parser.add_argument('--rate', type=float, default=2.0, help='Request rate limit (req/sec)')
    parser.add_argument('--timeout', type=int, default=20, help='Request timeout (seconds)')
    parser.add_argument('--parse-css', action='store_true', help='Parse CSS for background images')
    parser.add_argument('--compress', action='store_true', help='Compress images (JPEG, quality=85)')
    parser.add_argument('--perceptual-hash', action='store_true', help='Use perceptual hashing for deduplication')
    parser.add_argument('--max-size', type=int, default=50, help='Max image size in MB')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    if args.perceptual_hash and not IMAGEHASH_AVAILABLE:
        logger.warning("imagehash not available, perceptual hashing disabled")
        args.perceptual_hash = False
    
    downloader = ImageDownloader(
        output_dir=args.out,
        rate_limit=args.rate,
        timeout=args.timeout,
        parse_css=args.parse_css,
        compress=args.compress,
        perceptual_hash=args.perceptual_hash,
        max_size_mb=args.max_size
    )
    
    downloader.download_from_url(
        start_url=args.url,
        depth=args.depth,
        max_pages=args.max_pages
    )


if __name__ == '__main__':
    main()
