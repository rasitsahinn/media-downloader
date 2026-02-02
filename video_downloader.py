#!/usr/bin/env python3
"""
Universal Video Downloader
Supports: Direct videos, Dailymotion, YouTube, Vimeo, custom embeds
Extraction method: Page scraping (no API dependency)
"""

import os
import sys
import re
import json
import subprocess
import logging
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import argparse

import requests
from bs4 import BeautifulSoup

# Optional: Selenium for JS-heavy pages
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class UniversalVideoDownloader:
    """
    Universal video downloader with multiple extraction strategies
    Designed for standalone EXE usage (no external API dependencies)
    """
    
    def __init__(self, output_dir='./videos', render_js=False, ignore_robots=False):
        # Windows-safe path handling
        self.output_dir = Path(output_dir).resolve().absolute()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        self.render_js = render_js and SELENIUM_AVAILABLE
        self.ignore_robots = ignore_robots
        self.driver = None
        
        # Check dependencies
        self.has_ffmpeg = self._check_ffmpeg()
        
        # Stats
        self.stats = {
            'videos_found': 0,
            'videos_downloaded': 0,
            'errors': 0
        }
        
        logger.info(f"Output directory: {self.output_dir}")
        logger.info(f"FFmpeg: {'Available' if self.has_ffmpeg else 'NOT available'}")
        logger.info(f"Selenium: {'Available' if SELENIUM_AVAILABLE else 'NOT available'}")
        
        if self.render_js:
            self._setup_selenium()
    
    def _check_ffmpeg(self):
        """Check if ffmpeg is available"""
        try:
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
                ffmpeg_path = exe_dir / 'ffmpeg.exe'
                return ffmpeg_path.exists()
            
            subprocess.run(
                ['ffmpeg', '-version'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            return True
        except:
            return False
    
    def _get_ffmpeg_path(self):
        """Get ffmpeg executable path"""
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
            return str(exe_dir / 'ffmpeg.exe')
        return 'ffmpeg'
    
    def _setup_selenium(self):
        """Setup Selenium WebDriver"""
        try:
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
                chromedriver_path = exe_dir / 'chromedriver.exe'
                if not chromedriver_path.exists():
                    logger.warning("chromedriver.exe not found, JS rendering disabled")
                    self.render_js = False
                    return
            
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            
            if getattr(sys, 'frozen', False):
                service = webdriver.ChromeService(executable_path=str(chromedriver_path))
                self.driver = webdriver.Chrome(service=service, options=options)
            else:
                self.driver = webdriver.Chrome(options=options)
            
            logger.info("Selenium initialized successfully")
        except Exception as e:
            logger.warning(f"Selenium setup failed: {e}")
            self.render_js = False
    
    def sanitize_filename(self, filename):
        """Windows-safe filename sanitization"""
        # Remove invalid Windows characters
        invalid_chars = r'<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        
        # Turkish character replacement
        turkish_map = {
            'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G',
            'ı': 'i', 'İ': 'I', 'ö': 'o', 'Ö': 'O',
            'ş': 's', 'Ş': 'S', 'ü': 'u', 'Ü': 'U',
        }
        for tr, en in turkish_map.items():
            filename = filename.replace(tr, en)
        
        # Remove non-alphanumeric (keep - _ .)
        filename = re.sub(r'[^\w\-.]', '_', filename)
        filename = re.sub(r'_+', '_', filename)
        
        # Length limit
        if len(filename) > 150:
            name, ext = os.path.splitext(filename)
            filename = name[:150] + ext
        
        filename = filename.strip('. ')
        
        if not filename or filename == '_':
            filename = f"video_{int(time.time())}"
        
        return filename
    
    def extract_json_from_script(self, script_text, pattern):
        """Extract JSON from script tag"""
        try:
            match = re.search(pattern, script_text, re.DOTALL)
            if match:
                json_str = match.group(1)
                return json.loads(json_str)
        except:
            pass
        return None
    
    def extract_dailymotion_videos(self, soup, page_url):
        """
        Extract Dailymotion videos from page
        Strategy: Fetch embed page and scrape video URLs
        """
        videos = []
        
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if 'dailymotion.com' not in src:
                continue
            
            # Extract video ID
            match = re.search(r'video[=/]([a-zA-Z0-9]+)', src)
            if not match:
                continue
            
            video_id = match.group(1)
            logger.info(f"Found Dailymotion video: {video_id}")
            
            try:
                # Fetch embed page
                embed_url = f"https://www.dailymotion.com/embed/video/{video_id}"
                resp = self.session.get(embed_url, timeout=10)
                resp.raise_for_status()
                
                # Strategy 1: Look for __PLAYER_CONFIG__
                config = self.extract_json_from_script(
                    resp.text,
                    r'var __PLAYER_CONFIG__ = ({.+?});'
                )
                
                if config:
                    metadata = config.get('metadata', {})
                    qualities = metadata.get('qualities', {})
                    
                    # Get best quality
                    for quality in ['1080', '720', '480', '380', '240']:
                        if quality in qualities and qualities[quality]:
                            video_url = qualities[quality][0].get('url')
                            if video_url:
                                videos.append({
                                    'type': 'dailymotion',
                                    'url': video_url,
                                    'id': video_id,
                                    'title': metadata.get('title', video_id),
                                    'quality': quality,
                                    'format': 'm3u8' if '.m3u8' in video_url else 'mp4'
                                })
                                break
                    
                    if videos and videos[-1]['id'] == video_id:
                        continue
                
                # Strategy 2: Regex search for video URLs
                # Look for m3u8
                m3u8_patterns = [
                    r'"(https://[^"]*\.m3u8[^"]*)"',
                    r"'(https://[^']*\.m3u8[^']*)'",
                    r'url":"(https://[^"]*\.m3u8[^"]*)"',
                ]
                
                for pattern in m3u8_patterns:
                    matches = re.findall(pattern, resp.text)
                    if matches:
                        video_url = matches[0].replace('\\/', '/')
                        videos.append({
                            'type': 'dailymotion',
                            'url': video_url,
                            'id': video_id,
                            'title': f"dailymotion_{video_id}",
                            'quality': 'auto',
                            'format': 'm3u8'
                        })
                        break
                
                # Look for mp4
                if not any(v['id'] == video_id for v in videos):
                    mp4_patterns = [
                        r'"(https://[^"]*\.mp4[^"]*)"',
                        r"'(https://[^']*\.mp4[^']*)'",
                    ]
                    
                    for pattern in mp4_patterns:
                        matches = re.findall(pattern, resp.text)
                        if matches:
                            video_url = matches[0].replace('\\/', '/')
                            videos.append({
                                'type': 'dailymotion',
                                'url': video_url,
                                'id': video_id,
                                'title': f"dailymotion_{video_id}",
                                'quality': 'auto',
                                'format': 'mp4'
                            })
                            break
                
                if not any(v['id'] == video_id for v in videos):
                    logger.warning(f"Could not extract video URL for {video_id}")
                
            except Exception as e:
                logger.error(f"Error extracting Dailymotion {video_id}: {e}")
        
        return videos
    
    def extract_youtube_videos(self, soup, page_url):
        """Extract YouTube video embeds (info only - needs yt-dlp to download)"""
        videos = []
        
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if 'youtube.com' not in src and 'youtu.be' not in src:
                continue
            
            # Extract video ID
            match = re.search(r'(?:embed/|v=|youtu\.be/)([a-zA-Z0-9_-]{11})', src)
            if match:
                video_id = match.group(1)
                videos.append({
                    'type': 'youtube',
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'id': video_id,
                    'title': f"youtube_{video_id}",
                    'format': 'requires_ytdlp'
                })
                logger.info(f"Found YouTube video: {video_id} (requires yt-dlp)")
        
        return videos
    
    def extract_vimeo_videos(self, soup, page_url):
        """Extract Vimeo video embeds (info only - needs yt-dlp to download)"""
        videos = []
        
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if 'vimeo.com' not in src:
                continue
            
            match = re.search(r'vimeo\.com/video/(\d+)', src)
            if match:
                video_id = match.group(1)
                videos.append({
                    'type': 'vimeo',
                    'url': f"https://vimeo.com/{video_id}",
                    'id': video_id,
                    'title': f"vimeo_{video_id}",
                    'format': 'requires_ytdlp'
                })
                logger.info(f"Found Vimeo video: {video_id} (requires yt-dlp)")
        
        return videos
    
    def extract_direct_videos(self, soup, page_url):
        """Extract direct <video> tags"""
        videos = []
        
        for video_tag in soup.find_all('video'):
            # Get src from video tag
            src = video_tag.get('src')
            if src:
                full_url = urljoin(page_url, src)
                videos.append({
                    'type': 'direct',
                    'url': full_url,
                    'title': Path(urlparse(full_url).path).name or 'video',
                    'format': self._detect_format(full_url)
                })
            
            # Get src from <source> tags
            for source in video_tag.find_all('source'):
                src = source.get('src')
                if src:
                    full_url = urljoin(page_url, src)
                    videos.append({
                        'type': 'direct',
                        'url': full_url,
                        'title': Path(urlparse(full_url).path).name or 'video',
                        'format': self._detect_format(full_url)
                    })
        
        return videos
    
    def extract_generic_embeds(self, soup, page_url):
        """
        Extract other video embeds (Twitter, Instagram, etc.)
        Generic pattern matching
        """
        videos = []
        
        # Look for common video URL patterns in any iframe
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if not src:
                continue
            
            # Twitter
            if 'twitter.com' in src or 'x.com' in src:
                videos.append({
                    'type': 'twitter',
                    'url': src,
                    'title': 'twitter_video',
                    'format': 'requires_ytdlp'
                })
            
            # Instagram
            elif 'instagram.com' in src:
                videos.append({
                    'type': 'instagram',
                    'url': src,
                    'title': 'instagram_video',
                    'format': 'requires_ytdlp'
                })
            
            # Generic video player detection
            elif any(keyword in src.lower() for keyword in ['player', 'embed', 'video']):
                # Try to extract video URLs from embed page
                try:
                    embed_resp = self.session.get(src, timeout=5)
                    
                    # Look for .mp4 or .m3u8 URLs
                    video_urls = re.findall(
                        r'(https?://[^\s"\'>]+\.(?:mp4|m3u8)[^\s"\'>]*)',
                        embed_resp.text
                    )
                    
                    for video_url in video_urls:
                        video_url = video_url.replace('\\/', '/')
                        videos.append({
                            'type': 'generic',
                            'url': video_url,
                            'title': 'generic_video',
                            'format': self._detect_format(video_url)
                        })
                except:
                    pass
        
        return videos
    
    def _detect_format(self, url):
        """Detect video format from URL"""
        url_lower = url.lower()
        if '.m3u8' in url_lower:
            return 'm3u8'
        elif '.mpd' in url_lower:
            return 'dash'
        elif '.mp4' in url_lower:
            return 'mp4'
        elif '.webm' in url_lower:
            return 'webm'
        else:
            return 'unknown'
    
    def extract_all_videos(self, page_url):
        """
        Extract all videos from page using multiple strategies
        """
        try:
            logger.info(f"Fetching page: {page_url}")
            
            # Fetch page (with or without JS rendering)
            if self.render_js and self.driver:
                self.driver.get(page_url)
                time.sleep(3)  # Wait for JS
                html = self.driver.page_source
            else:
                resp = self.session.get(page_url, timeout=15)
                resp.raise_for_status()
                html = resp.text
            
            soup = BeautifulSoup(html, 'html.parser')
            
            all_videos = []
            
            # Extract from different sources
            logger.info("Extracting videos...")
            all_videos.extend(self.extract_dailymotion_videos(soup, page_url))
            all_videos.extend(self.extract_youtube_videos(soup, page_url))
            all_videos.extend(self.extract_vimeo_videos(soup, page_url))
            all_videos.extend(self.extract_direct_videos(soup, page_url))
            all_videos.extend(self.extract_generic_embeds(soup, page_url))
            
            # Remove duplicates (by URL)
            seen_urls = set()
            unique_videos = []
            for video in all_videos:
                if video['url'] not in seen_urls:
                    seen_urls.add(video['url'])
                    unique_videos.append(video)
            
            self.stats['videos_found'] = len(unique_videos)
            
            return unique_videos
            
        except Exception as e:
            logger.error(f"Error extracting videos: {e}")
            return []
    
    def download_hls_with_ffmpeg(self, m3u8_url, output_file):
        """Download HLS stream (.m3u8) with ffmpeg"""
        if not self.has_ffmpeg:
            logger.error("ffmpeg not available - cannot download HLS stream")
            return False
        
        try:
            ffmpeg_path = self._get_ffmpeg_path()
            
            cmd = [
                ffmpeg_path,
                '-i', m3u8_url,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                '-y',
                str(output_file)
            ]
            
            logger.info(f"Downloading HLS stream: {output_file.name}")
            
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            if result.returncode == 0:
                logger.info(f"✓ Downloaded: {output_file.name}")
                return True
            else:
                logger.error(f"ffmpeg error: {result.stderr[:300]}")
                return False
                
        except Exception as e:
            logger.error(f"ffmpeg execution failed: {e}")
            return False
    
    def download_direct_video(self, video_url, output_file):
        """Download direct video URL (MP4, WebM, etc.)"""
        try:
            logger.info(f"Downloading: {output_file.name}")
            
            resp = self.session.get(video_url, stream=True, timeout=30)
            resp.raise_for_status()
            
            total_size = int(resp.headers.get('content-length', 0))
            downloaded = 0
            
            with open(output_file, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            print(f"\r  Progress: {percent:.1f}%", end='', flush=True)
            
            print()  # New line
            logger.info(f"✓ Downloaded: {output_file.name}")
            return True
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            return False
    
    def download_video(self, video_info, index=1):
        """Download single video"""
        
        video_type = video_info['type']
        video_url = video_info['url']
        video_title = video_info.get('title', f'video_{index}')
        video_format = video_info.get('format', 'unknown')
        
        logger.info(f"\n{'='*60}")
        logger.info(f"[Video {index}] Type: {video_type}, Format: {video_format}")
        logger.info(f"{'='*60}")
        
        # Check if requires yt-dlp
        if video_format == 'requires_ytdlp':
            logger.warning(f"⚠ {video_type.capitalize()} videos require yt-dlp")
            logger.warning("  Install: pip install yt-dlp")
            logger.warning("  Or: https://github.com/yt-dlp/yt-dlp/releases")
            self.stats['errors'] += 1
            return False
        
        # Sanitize filename
        safe_title = self.sanitize_filename(video_title)
        
        # Determine output filename
        if video_format == 'm3u8':
            filename = f"{safe_title}.mp4"
        elif video_format in ['mp4', 'webm']:
            ext = os.path.splitext(safe_title)[1] or f'.{video_format}'
            filename = safe_title if ext else f"{safe_title}.{video_format}"
        else:
            filename = f"{safe_title}.mp4"
        
        output_file = self.output_dir / filename
        
        # Handle duplicate filenames
        counter = 1
        while output_file.exists():
            name, ext = os.path.splitext(filename)
            output_file = self.output_dir / f"{name}_{counter}{ext}"
            counter += 1
        
        # Download based on format
        if video_format == 'm3u8':
            success = self.download_hls_with_ffmpeg(video_url, output_file)
        elif video_format in ['mp4', 'webm']:
            success = self.download_direct_video(video_url, output_file)
        else:
            logger.warning(f"Unknown format: {video_format}, trying direct download...")
            success = self.download_direct_video(video_url, output_file)
        
        if success:
            self.stats['videos_downloaded'] += 1
        else:
            self.stats['errors'] += 1
        
        return success
    
    def download_from_page(self, page_url):
        """Main entry point: download all videos from page"""
        try:
            # Extract videos
            videos = self.extract_all_videos(page_url)
            
            if not videos:
                logger.warning("\n⚠ No videos found on page")
                logger.warning("  Supported: Dailymotion, YouTube*, Vimeo*, Direct MP4/M3U8")
                logger.warning("  *YouTube/Vimeo require yt-dlp (not included)")
                return
            
            logger.info(f"\n✓ Found {len(videos)} video(s)")
            
            # Download each video
            for i, video_info in enumerate(videos, 1):
                self.download_video(video_info, index=i)
            
            # Print summary
            logger.info(f"\n{'='*60}")
            logger.info("DOWNLOAD SUMMARY")
            logger.info(f"{'='*60}")
            logger.info(f"Videos found:      {self.stats['videos_found']}")
            logger.info(f"Videos downloaded: {self.stats['videos_downloaded']}")
            logger.info(f"Errors:            {self.stats['errors']}")
            logger.info(f"Output directory:  {self.output_dir}")
            logger.info(f"{'='*60}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
            self.stats['errors'] += 1
        finally:
            if self.driver:
                self.driver.quit()


def main():
    parser = argparse.ArgumentParser(
        description='Universal Video Downloader (Standalone EXE)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download from page:
  %(prog)s https://example.com/article
  
  # With JavaScript rendering:
  %(prog)s https://example.com/article --render-js
  
  # Specify output directory:
  %(prog)s https://example.com/article --out ./my_videos

Supported:
  ✓ Dailymotion (built-in)
  ✓ Direct MP4/WebM/M3U8
  ✗ YouTube/Vimeo (requires yt-dlp)
        """
    )
    
    parser.add_argument('url', help='Page URL containing videos')
    parser.add_argument('--out', default='./videos', help='Output directory (default: ./videos)')
    parser.add_argument('--render-js', action='store_true', help='Render JavaScript (requires chromedriver)')
    parser.add_argument('--ignore-robots', action='store_true', help='Ignore robots.txt')
    
    args = parser.parse_args()
    
    downloader = UniversalVideoDownloader(
        output_dir=args.out,
        render_js=args.render_js,
        ignore_robots=args.ignore_robots
    )
    
    downloader.download_from_page(args.url)


if __name__ == '__main__':
    main()
