#!/usr/bin/env python3
"""
grab_images.py - Advanced Image Downloader (Windows-Compatible)
Mac version adapted for Windows EXE with full feature preservation
"""

import argparse
import csv
import hashlib
import imghdr
import json
import mimetypes
import os
import re
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from collections import deque
import logging

import requests
from bs4 import BeautifulSoup
from PIL import Image

# Optional: imagehash
try:
    import imagehash
    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False
    logging.warning("imagehash not available - perceptual hashing disabled")

# Logging setup with UTF-8 encoding for Windows
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('image_downloader.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


class BloomFilter:
    """Memory-efficient URL tracking with Bloom Filter"""
    def __init__(self, size=10000000, hash_count=3):
        self.size = size
        self.hash_count = hash_count
        self.bit_array = [False] * size

    def add(self, item):
        for i in range(self.hash_count):
            index = hash(item + str(i)) % self.size
            self.bit_array[index] = True

    def contains(self, item):
        return all(self.bit_array[hash(item + str(i)) % self.size]
                   for i in range(self.hash_count))


class ImageDownloader:
    def __init__(self, base_url, output_dir, depth=0, max_pages=50,
                 rate_limit=2.0, workers=4, use_bloom=False,
                 compress=False, quality=85, perceptual_hash=False,
                 checkpoint_file='checkpoint.json', parse_css=False,
                 auth_user=None, auth_pass=None, cookies=None,
                 render_js=False, ignore_robots=False):
        self.base_url = base_url
        
        # Windows-safe path handling
        self.output_dir = Path(output_dir).resolve().absolute()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.depth = depth
        self.max_pages = max_pages
        self.rate_limit = rate_limit
        self.workers = workers
        self.compress = compress
        self.quality = quality
        self.perceptual_hash = perceptual_hash and IMAGEHASH_AVAILABLE
        self.checkpoint_file = checkpoint_file
        self.parse_css = parse_css
        self.render_js = render_js
        self.ignore_robots = ignore_robots

        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 ImageDownloader/2.0'})

        # Authentication
        if auth_user and auth_pass:
            self.session.auth = (auth_user, auth_pass)
        if cookies:
            self.session.cookies.update(cookies)

        # URL and hash tracking
        if use_bloom:
            self.visited_urls = BloomFilter()
            self.visited_urls_set = set()
        else:
            self.visited_urls = set()
            self.visited_urls_set = self.visited_urls

        self.use_bloom = use_bloom
        self.downloaded_hashes = set()
        self.perceptual_hashes = set() if self.perceptual_hash else None
        self.csv_log = []
        self.robots_cache = {}
        self.robots_ttl = {}
        self.last_request_time = {}

        # Load checkpoint
        self.load_checkpoint()

        # Selenium (optional - disable for EXE by default)
        self.driver = None
        if render_js:
            logger.warning("Selenium/JS rendering not supported in EXE mode")
            self.render_js = False

        logger.info(f"Output directory: {self.output_dir}")
        logger.info(f"Perceptual hash: {'ENABLED' if self.perceptual_hash else 'DISABLED'}")

    def load_checkpoint(self):
        """Resume from previous run"""
        if os.path.exists(self.checkpoint_file):
            try:
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.visited_urls_set.update(data.get('visited_urls', []))
                    if self.use_bloom:
                        for url in data.get('visited_urls', []):
                            self.visited_urls.add(url)
                    self.downloaded_hashes.update(data.get('downloaded_hashes', []))
                    if self.perceptual_hash:
                        self.perceptual_hashes.update(data.get('perceptual_hashes', []))
                    logger.info(
                        f"Checkpoint loaded: {len(self.visited_urls_set)} URLs, "
                        f"{len(self.downloaded_hashes)} images"
                    )
            except Exception as e:
                logger.error(f"Checkpoint load error: {e}")

    def save_checkpoint(self):
        """Save progress"""
        try:
            data = {
                'visited_urls': list(self.visited_urls_set),
                'downloaded_hashes': list(self.downloaded_hashes),
                'perceptual_hashes': list(self.perceptual_hashes) if self.perceptual_hash else []
            }
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Checkpoint save error: {e}")

    def normalize_url(self, url):
        """Normalize URL"""
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))

    def can_fetch(self, url):
        """robots.txt check with TTL"""
        if self.ignore_robots:
            return True

        parsed = urlparse(url)
        robot_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        # TTL check (1 hour)
        if robot_url in self.robots_ttl:
            if time.time() - self.robots_ttl[robot_url] > 3600:
                if robot_url in self.robots_cache:
                    del self.robots_cache[robot_url]
                del self.robots_ttl[robot_url]

        if robot_url not in self.robots_cache:
            rp = RobotFileParser()
            rp.set_url(robot_url)
            try:
                rp.read()
                self.robots_cache[robot_url] = rp
                self.robots_ttl[robot_url] = time.time()
            except:
                self.robots_cache[robot_url] = None
                self.robots_ttl[robot_url] = time.time()

        rp = self.robots_cache[robot_url]
        if rp is None:
            return True
        return rp.can_fetch("*", url)

    def rate_limit_wait(self, domain):
        """Domain-based rate limiting"""
        now = time.time()
        if domain in self.last_request_time:
            elapsed = now - self.last_request_time[domain]
            wait_time = (1.0 / self.rate_limit) - elapsed
            if wait_time > 0:
                time.sleep(wait_time)
        self.last_request_time[domain] = time.time()

    def sanitize_filename(self, url, content_type=None):
        """
        Windows-safe filename sanitization
        Handles Turkish characters and invalid Windows chars
        """
        parsed = urlparse(url)
        path = parsed.path.split('/')[-1] or 'index'
        name, ext = os.path.splitext(path)

        if not ext and content_type:
            ext = mimetypes.guess_extension(content_type) or '.jpg'
        elif not ext:
            ext = '.jpg'

        # Remove invalid Windows characters: < > : " / \ | ? *
        invalid_chars = r'<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')

        # Replace Turkish characters for Windows compatibility
        turkish_map = {
            'ç': 'c', 'Ç': 'C',
            'ğ': 'g', 'Ğ': 'G',
            'ı': 'i', 'İ': 'I',
            'ö': 'o', 'Ö': 'O',
            'ş': 's', 'Ş': 'S',
            'ü': 'u', 'Ü': 'U',
        }
        for tr, en in turkish_map.items():
            name = name.replace(tr, en)

        # Remove non-alphanumeric (keep - _ .)
        name = re.sub(r'[^\w\-.]', '_', name)
        
        # Remove consecutive underscores
        name = re.sub(r'_+', '_', name)
        
        # Trim length (Windows MAX_PATH issues)
        if len(name) > 100:
            name = name[:100]
        
        # Remove leading/trailing dots and spaces
        name = name.strip('. ')
        
        # Ensure not empty
        if not name or name == '_':
            name = f"image_{int(time.time())}"

        return f"{name}{ext}"

    def get_output_path(self, page_url):
        """Create folder structure based on page URL"""
        parsed = urlparse(page_url)
        domain = parsed.netloc.replace(':', '_')
        
        # Sanitize domain for Windows
        domain = re.sub(r'[<>:"/\\|?*]', '_', domain)
        
        path_parts = [p for p in parsed.path.split('/') if p]
        
        # Sanitize path parts
        safe_parts = []
        for part in path_parts[:3]:
            part = re.sub(r'[<>:"/\\|?*]', '_', part)
            part = part[:50]  # Limit length
            safe_parts.append(part)
        
        if safe_parts:
            subdir = self.output_dir / domain / '_'.join(safe_parts)
        else:
            subdir = self.output_dir / domain
        
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir

    def verify_image(self, filepath):
        """Verify file is actually an image"""
        try:
            img_type = imghdr.what(filepath)
            if img_type is None:
                Image.open(filepath).verify()
                return True
            return True
        except:
            return False

    def get_perceptual_hash(self, filepath):
        """Perceptual hash for visual similarity"""
        if not IMAGEHASH_AVAILABLE:
            return None
        try:
            img = Image.open(filepath)
            return str(imagehash.average_hash(img))
        except:
            return None

    def compress_image(self, filepath):
        """Compress image"""
        try:
            img = Image.open(filepath)
            if img.mode in ('RGBA', 'LA', 'P'):
                img.save(filepath, 'PNG', optimize=True)
            else:
                if filepath.suffix.lower() not in ['.jpg', '.jpeg']:
                    new_path = filepath.with_suffix('.jpg')
                    img.convert('RGB').save(new_path, 'JPEG', quality=self.quality, optimize=True)
                    os.remove(filepath)
                    return new_path
                else:
                    img.convert('RGB').save(filepath, 'JPEG', quality=self.quality, optimize=True)
            return filepath
        except Exception as e:
            logger.warning(f"Compression error {filepath}: {e}")
            return filepath

    def _should_skip_by_extension(self, img_url: str):
        """Skip by extension filter"""
        try:
            path = urlparse(img_url).path.lower()
            if re.search(r'(?i)(?:/thumbs?/|[-_](?:thumb|thumbnail))', path):
                return True, "skipped_thumbnail_url_pattern"
            if path.endswith(".svg") or path.endswith(".svgz"):
                return True, "skipped_svg_extension"
        except:
            pass
        return False, ""

    def _should_skip_square_thumbnail_filename(self, img_url: str):
        """Skip square thumbnails like 400x400.png"""
        try:
            path = (urlparse(img_url).path or "").lower()
            filename = os.path.basename(path)
            m = re.search(r'-(\d{2,4})x(\d{2,4})\.(png|jpg|jpeg|webp)$', filename)
            if not m:
                return False, ""
            w = int(m.group(1))
            h = int(m.group(2))
            if w == h and w <= 512:
                return True, "skipped_square_thumbnail_filename"
        except:
            pass
        return False, ""

    def _should_skip_by_url_pattern(self, img_url: str):
        """Skip UI assets by URL pattern"""
        try:
            path = urlparse(img_url).path.lower()
            if re.search(r'/(assets/)?icons?/', path):
                return True, "skipped_ui_asset_icon_path"
            if re.search(r'/(assets/)?logos?/', path):
                return True, "skipped_ui_asset_logo_path"
            if "favicon" in path:
                return True, "skipped_ui_asset_favicon"
            if "sprite" in path:
                return True, "skipped_ui_asset_sprite"
            filename = os.path.basename(path)
            if re.search(r'(?:^|[\W_])(icon|logo|favicon|sprite|badge|appicon|app-icon)(?:[\W_]|$)', filename):
                return True, "skipped_ui_asset_keyword_filename"
        except:
            pass
        return False, ""

    def _should_skip_by_thumb_url(self, img_url: str):
        """Skip thumbnail URLs"""
        try:
            path = (urlparse(img_url).path or "").lower()
            filename = os.path.basename(path)
            if re.search(r'(?:^|[._-])(thumb|thumbnail)(?:[._-]|$)', filename):
                return True, "skipped_thumb_filename"
            if "/thumb/" in path or "/thumbs/" in path or "/thumbnail" in path:
                return True, "skipped_thumb_path"
        except:
            pass
        return False, ""

    def download_image(self, img_url, page_url, retries=3):
        """Download image with all features"""
        MIN_BYTES = 10 * 1024  # 10KB minimum

        # Early filters
        skip, reason = self._should_skip_by_extension(img_url)
        if skip:
            logger.info(f"⊘ Extension filter: {img_url}")
            self.csv_log.append([page_url, img_url, '', reason])
            return None

        skip, reason = self._should_skip_square_thumbnail_filename(img_url)
        if skip:
            logger.info(f"⊘ Square thumbnail filter: {img_url}")
            self.csv_log.append([page_url, img_url, '', reason])
            return None

        skip, reason = self._should_skip_by_url_pattern(img_url)
        if skip:
            logger.info(f"⊘ URL pattern filter: {img_url}")
            self.csv_log.append([page_url, img_url, '', reason])
            return None

        skip, reason = self._should_skip_by_thumb_url(img_url)
        if skip:
            logger.info(f"⊘ Thumb filter: {img_url}")
            self.csv_log.append([page_url, img_url, '', reason])
            return None

        normalized = self.normalize_url(img_url)
        img_hash = hashlib.md5(normalized.encode()).hexdigest()

        if img_hash in self.downloaded_hashes:
            return None

        if not self.can_fetch(img_url):
            logger.info(f"⊘ robots.txt blocked: {img_url}")
            self.csv_log.append([page_url, img_url, '', 'robots_blocked'])
            return None

        domain = urlparse(img_url).netloc

        for attempt in range(retries):
            try:
                self.rate_limit_wait(domain)
                resp = self.session.get(img_url, timeout=15, stream=True)
                resp.raise_for_status()

                content_type = (resp.headers.get('Content-Type', '').split(';')[0] or '').strip().lower()
                if content_type == "image/svg+xml":
                    logger.info(f"⊘ SVG content-type: {img_url}")
                    self.csv_log.append([page_url, img_url, '', 'skipped_svg_content_type'])
                    resp.close()
                    return None

                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit() and int(cl) < MIN_BYTES:
                    logger.info(f"⊘ Small file (Content-Length): {img_url} ({cl} bytes)")
                    self.csv_log.append([page_url, img_url, '', 'skipped_small_content_length'])
                    resp.close()
                    return None

                filename = self.sanitize_filename(img_url, content_type)
                output_path = self.get_output_path(page_url)
                filepath = output_path / filename

                counter = 1
                while filepath.exists():
                    name, ext = os.path.splitext(filename)
                    filepath = output_path / f"{name}_{counter}{ext}"
                    counter += 1

                # Download
                with open(filepath, 'wb') as f:
                    for chunk in resp.iter_content(8192):
                        if chunk:
                            f.write(chunk)

                # Size check
                try:
                    size = os.path.getsize(filepath)
                except OSError:
                    size = 0
                
                if size < MIN_BYTES:
                    os.remove(filepath)
                    logger.info(f"⊘ Small file (actual): {img_url} ({size} bytes)")
                    self.csv_log.append([page_url, img_url, '', 'skipped_small_actual'])
                    return None

                # Verify
                if not self.verify_image(filepath):
                    os.remove(filepath)
                    logger.warning(f"✗ Invalid image: {img_url}")
                    self.csv_log.append([page_url, img_url, '', 'invalid_image'])
                    return None

                # Perceptual hash
                if self.perceptual_hash:
                    phash = self.get_perceptual_hash(filepath)
                    if phash and phash in self.perceptual_hashes:
                        os.remove(filepath)
                        logger.info(f"⊘ Perceptual duplicate: {img_url}")
                        self.csv_log.append([page_url, img_url, '', 'perceptual_duplicate'])
                        return None
                    if phash:
                        self.perceptual_hashes.add(phash)

                # Compress
                if self.compress:
                    filepath = self.compress_image(filepath)

                self.downloaded_hashes.add(img_hash)
                self.csv_log.append([page_url, img_url, str(filepath), 'success'])
                logger.info(f"✓ {filepath.name}")
                return str(filepath)

            except Exception as e:
                if attempt == retries - 1:
                    logger.error(f"✗ Failed {img_url}: {e}")
                    self.csv_log.append([page_url, img_url, '', f'error_{type(e).__name__}'])
                    return None
                time.sleep(2 ** attempt)
        
        return None

    # Content-area scoping patterns
    NOISE_ANCESTOR_RE = re.compile(
        r"""(?ix)
        (?:
           \brelated\b|\brecommend\b|\brecommended\b|\bmore\b|\bother\b|\bsimilar\b|\btrending\b|
           \bnews-item\b|\bnews-box\b|\bnews-row\b|\bmanset\b|\bheadline\b|
           \bcard\b|\bgrid\b|\bfeed\b|\blist\b|\blisting\b|\bteaser\b|\bthumb\b|\bthumbnail\b|
           \bwidget\b|\bsidebar\b|\bfooter\b|\bpost-list\b|\bentry-thumbnail\b|\blatest-news-wrapper\b|\bsimulated-link\b|
           \bad\b|\bads\b|\badvert\b|\bbanner\b|\bpromo\b|\bsponsor\b|\boutbrain\b|\btaboola\b|
           \bilgili\b|\bbenzer\b|\bdiger\b|
           my-header|global-header|service-sub-menu|service-sub-menu-wrap|menu-logo|
           navbar|nav\b|menu\b|topbar|site-header|
           lig-listesi|service-.*header
        )
        """
    )

    HREF_ARTICLE_RE = re.compile(r"(?i)/(haber|news|article|post)/|\b\d{4}/\d{2}/\d{2}/")

    CONTENT_SCOPE_SELECTORS = [
        "article",
        "main",
        '[role="main"]',
        ".entry-content",
        ".post-content",
        ".article-body",
        ".content",
        ".story-content",
        ".news-content",
        ".detail-content",
    ]

    def _select_content_root(self, soup):
        """Select main content container"""
        try:
            el = soup.select_one("#page-article")
            if el:
                return el
        except:
            pass

        try:
            candidates = soup.select(".col-sm-8")
            for el in candidates:
                if el.find_parent(["header", "nav", "footer", "aside"]) is None:
                    return el
        except:
            pass

        try:
            candidates = []
            for sel in self.CONTENT_SCOPE_SELECTORS:
                try:
                    found = soup.select(sel)
                except:
                    found = []
                for el in (found or []):
                    try:
                        txt = el.get_text(" ", strip=True) or ""
                        score = len(txt)
                        if el.find("img"):
                            score += 500
                        candidates.append((score, el))
                    except:
                        continue
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                return candidates[0][1]
        except:
            pass
        
        return soup

    def _prune_noise_blocks(self, root):
        """Remove sidebar/related/nav blocks from DOM"""
        if root is None:
            return

        selectors = [
            "aside", "#sidebar", ".sidebar", "[class*='sidebar']",
            ".post-list", ".entry-thumbnail", ".latest-news-wrapper",
            ".simulated-link", ".compact",
            ".related", ".recommend", ".recommended", ".more", ".other", ".similar", ".trending",
            ".widget", ".footer", "footer",
        ]

        try:
            for sel in selectors:
                for node in root.select(sel):
                    try:
                        node.decompose()
                    except:
                        pass
        except:
            pass

    def _has_noise_ancestor(self, tag, max_hops: int = 8) -> bool:
        """Check if tag has noise ancestor (cards/related/widgets/ads)"""
        cur = tag
        hops = 0
        while cur is not None and hops < max_hops:
            try:
                name = getattr(cur, "name", "") or ""
                if name in ("header", "nav"):
                    return True

                cls = " ".join(cur.get("class", []) or [])
                _id = cur.get("id", "") or ""
                dsn = cur.get("data-section-name", "") or ""

                blob = f"{cls} {_id} {dsn}".strip()
                if blob and self.NOISE_ANCESTOR_RE.search(blob):
                    return True
            except:
                pass
            cur = cur.parent
            hops += 1
        return False

    def _is_small_from_attrs(self, tag, min_side: int = 200, min_area: int = 120_000) -> bool:
        """Check if image is thumbnail-sized from attributes"""
        try:
            w = tag.get("width")
            h = tag.get("height")
            if w is None or h is None:
                return False
            w = int(str(w).strip())
            h = int(str(h).strip())
            if w <= 0 or h <= 0:
                return False
            if min(w, h) < min_side:
                return True
            if (w * h) < min_area:
                return True
            return False
        except:
            return False

    def _href_looks_like_article(self, href: str) -> bool:
        """Check if href looks like article link"""
        try:
            return bool(href and self.HREF_ARTICLE_RE.search(href))
        except:
            return False

    def _should_skip_linked_media_tag(self, tag) -> bool:
        """Conditional skip for media tags inside <a>"""
        a = tag.find_parent("a")
        if a is None:
            return False

        if self._has_noise_ancestor(tag):
            return True

        href = a.get("href", "") or ""
        small = self._is_small_from_attrs(tag)

        if small:
            return True

        if self._href_looks_like_article(href) and small:
            return True

        return False

    def extract_css_images(self, css_url, page_url):
        """Extract background-images from external CSS"""
        images = set()
        try:
            self.rate_limit_wait(urlparse(css_url).netloc)
            resp = self.session.get(css_url, timeout=10)
            resp.raise_for_status()

            urls = re.findall(r'url\(["\']?([^"\')]+)["\']?\)', resp.text)
            for url in urls:
                full_url = urljoin(css_url, url)
                images.add(full_url)

            logger.info(f"CSS: {len(urls)} images found: {css_url}")
        except Exception as e:
            logger.warning(f"CSS parse error {css_url}: {e}")

        return images

    def extract_images(self, soup, page_url, html_content=None):
        """Extract all images from page with smart filtering"""
        images = set()

        # Scope to main content
        root = self._select_content_root(soup)

        # Prune noise blocks
        self._prune_noise_blocks(root)

        # <img src> and srcset
        for img in root.find_all('img'):
            if self._should_skip_linked_media_tag(img):
                continue
            
            if img.get('src'):
                full = urljoin(page_url, img['src'])
                if not self._should_skip_by_thumb_url(full)[0]:
                    images.add(full)
            
            if img.get('srcset'):
                for src in img['srcset'].split(','):
                    url = src.strip().split()[0]
                    full = urljoin(page_url, url)
                    if not self._should_skip_by_thumb_url(full)[0]:
                        images.add(full)
            
            if img.get('data-src'):
                full = urljoin(page_url, img['data-src'])
                if not self._should_skip_by_thumb_url(full)[0]:
                    images.add(full)

        # <picture><source srcset>
        for source in root.find_all('source'):
            if self._should_skip_linked_media_tag(source):
                continue
            
            if source.get('srcset'):
                for src in source['srcset'].split(','):
                    url = src.strip().split()[0]
                    full = urljoin(page_url, url)
                    if not self._should_skip_by_thumb_url(full)[0]:
                        images.add(full)

        # <meta property="og:image">
        for meta in soup.find_all('meta', property='og:image'):
            if meta.get('content'):
                images.add(urljoin(page_url, meta['content']))

        # <meta name="twitter:image">
        for meta in soup.find_all('meta', attrs={'name': 'twitter:image'}):
            if meta.get('content'):
                images.add(urljoin(page_url, meta['content']))

        # CSS background-image (inline style)
        for tag in root.find_all(style=True):
            if self._should_skip_linked_media_tag(tag):
                continue
            
            style = tag['style']
            urls = re.findall(r'url\(["\']?([^"\')]+)["\']?\)', style)
            for url in urls:
                full = urljoin(page_url, url)
                if not self._should_skip_by_thumb_url(full)[0]:
                    images.add(full)

        # External CSS files
        if self.parse_css:
            for link in soup.find_all('link', rel='stylesheet'):
                if link.get('href'):
                    css_url = urljoin(page_url, link['href'])
                    css_images = self.extract_css_images(css_url, page_url)
                    images.update(css_images)

        return images

    def extract_links(self, soup, page_url):
        """Extract same-domain links"""
        links = set()
        base_domain = urlparse(self.base_url).netloc

        for a in soup.find_all('a', href=True):
            full_url = urljoin(page_url, a['href'])
            if urlparse(full_url).netloc == base_domain:
                links.add(self.normalize_url(full_url))

        return links

    def fetch_page(self, url):
        """Fetch page content"""
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text

    def process_page(self, url):
        """Process single page"""
        try:
            domain = urlparse(url).netloc
            self.rate_limit_wait(domain)

            html = self.fetch_page(url)
            soup = BeautifulSoup(html, 'html.parser')

            images = self.extract_images(soup, url, html)
            logger.info(f"{len(images)} images found: {url}")

            return images, self.extract_links(soup, url)
        except Exception as e:
            logger.error(f"✗ Page processing error {url}: {e}")
            return set(), set()

    def crawl(self):
        """Main crawl loop with parallel download"""
        queue = deque([(self.base_url, 0)])
        pages_processed = 0

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            while queue and pages_processed < self.max_pages:
                url, current_depth = queue.popleft()
                normalized = self.normalize_url(url)

                # Visit check
                if self.use_bloom:
                    if self.visited_urls.contains(normalized):
                        continue
                    self.visited_urls.add(normalized)
                else:
                    if normalized in self.visited_urls:
                        continue
                    self.visited_urls.add(normalized)

                self.visited_urls_set.add(normalized)

                # robots.txt check
                if not self.can_fetch(url):
                    logger.info(f"⊘ Page blocked by robots.txt: {url}")
                    continue

                pages_processed += 1
                logger.info(f"\n[{pages_processed}/{self.max_pages}] Processing: {url}")

                # Process page
                images, links = self.process_page(url)

                # Parallel download
                futures = []
                for img_url in images:
                    future = executor.submit(self.download_image, img_url, url)
                    futures.append(future)

                # Wait for downloads
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.error(f"Download error: {e}")

                # Depth check and new links
                if current_depth < self.depth:
                    for link in links:
                        if self.use_bloom:
                            if not self.visited_urls.contains(link):
                                queue.append((link, current_depth + 1))
                        else:
                            if link not in self.visited_urls:
                                queue.append((link, current_depth + 1))

                # Periodic checkpoint
                if pages_processed % 10 == 0:
                    self.save_checkpoint()

        # Final checkpoint
        self.save_checkpoint()

        # Save CSV log
        csv_path = self.output_dir / 'download_log.csv'
        try:
            with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['source_page', 'image_url', 'local_path', 'status'])
                writer.writerows(self.csv_log)
        except Exception as e:
            logger.error(f"CSV save error: {e}")

        logger.info(f"\n✓ Complete!")
        logger.info(f"✓ Log file: {csv_path}")
        logger.info(f"✓ {len(self.downloaded_hashes)} unique images downloaded")
        logger.info(f"✓ {pages_processed} pages processed")


def main():
    parser = argparse.ArgumentParser(
        description='Advanced image downloader (Windows-compatible)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('url', help='Target URL')
    parser.add_argument('--out', default='./downloads', help='Output folder (default: ./downloads)')
    parser.add_argument('--depth', type=int, default=0, help='Crawl depth (default: 0)')
    parser.add_argument('--max-pages', type=int, default=50, help='Maximum pages (default: 50)')
    parser.add_argument('--rate', type=float, default=2.0, help='Request rate (req/sec, default: 2.0)')
    parser.add_argument('--workers', type=int, default=4, help='Parallel threads (default: 4)')
    parser.add_argument('--use-bloom', action='store_true', help='Use Bloom Filter (memory efficient)')
    parser.add_argument('--compress', action='store_true', help='Compress images')
    parser.add_argument('--quality', type=int, default=85, help='JPEG quality (default: 85)')
    parser.add_argument('--perceptual-hash', action='store_true', help='Perceptual similarity check')
    parser.add_argument('--checkpoint', default='checkpoint.json', help='Checkpoint file (default: checkpoint.json)')
    parser.add_argument('--parse-css', action='store_true', help='Parse external CSS')
    parser.add_argument('--ignore-robots', action='store_true', help='Ignore robots.txt (caution)')

    args = parser.parse_args()

    if not IMAGEHASH_AVAILABLE and args.perceptual_hash:
        logger.warning("imagehash not available - perceptual hashing disabled")
        args.perceptual_hash = False

    downloader = ImageDownloader(
        args.url,
        args.out,
        depth=args.depth,
        max_pages=args.max_pages,
        rate_limit=args.rate,
        workers=args.workers,
        use_bloom=args.use_bloom,
        compress=args.compress,
        quality=args.quality,
        perceptual_hash=args.perceptual_hash,
        checkpoint_file=args.checkpoint,
        parse_css=args.parse_css,
        ignore_robots=args.ignore_robots
    )

    try:
        downloader.crawl()
    except KeyboardInterrupt:
        logger.info("\n\n⊘ Stopped by user")
        downloader.save_checkpoint()
        logger.info("✓ Checkpoint saved. Run same command to resume.")


if __name__ == '__main__':
    main()
