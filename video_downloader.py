#!/usr/bin/env python3
import os
import sys
import re
import subprocess
import logging
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
import argparse

import requests
from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class VideoDownloader:
    def __init__(self, output_dir='./videos', render_js=False, ignore_robots=False):
        self.output_dir = Path(output_dir).resolve().absolute()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        self.render_js = render_js and SELENIUM_AVAILABLE
        self.ignore_robots = ignore_robots
        self.driver = None
        
        if self.render_js:
            self.setup_selenium()
        
        self.check_ffmpeg()
    
    def check_ffmpeg(self):
        """Check if ffmpeg is available"""
        try:
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
                self.ffmpeg_path = str(exe_dir / 'ffmpeg.exe')
            else:
                self.ffmpeg_path = 'ffmpeg'
            
            subprocess.run(
                [self.ffmpeg_path, '-version'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
            logger.info("FFmpeg: Available")
        except:
            logger.warning("FFmpeg: NOT available")
            self.ffmpeg_path = None
    
    def setup_selenium(self):
        """Setup Selenium for JavaScript rendering"""
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            
            if getattr(sys, 'frozen', False):
                exe_dir = Path(sys.executable).parent
                chromedriver_path = exe_dir / 'chromedriver.exe'
                if chromedriver_path.exists():
                    self.driver = webdriver.Chrome(
                        executable_path=str(chromedriver_path),
                        options=options
                    )
                else:
                    logger.warning("chromedriver.exe not found")
                    self.render_js = False
            else:
                self.driver = webdriver.Chrome(options=options)
            
            if self.driver:
                logger.info("Selenium: Available")
        except Exception as e:
            logger.warning(f"Selenium setup failed: {e}")
            self.render_js = False
    
    def extract_videos_from_page(self, url):
        """Extract video URLs from page"""
        try:
            logger.info(f"Fetching page: {url}")
            
            if self.render_js and self.driver:
                self.driver.get(url)
                time.sleep(3)
                html_content = self.driver.page_source
            else:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                html_content = response.text
            
            soup = BeautifulSoup(html_content, 'html.parser')
            video_urls = []
            
            # Direct <video> tags
            for video in soup.find_all('video'):
                src = video.get('src')
                if src:
                    video_urls.append(urljoin(url, src))
                
                for source in video.find_all('source'):
                    src = source.get('src')
                    if src:
                        video_urls.append(urljoin(url, src))
            
            # <iframe> embeds (Dailymotion only)
            for iframe in soup.find_all('iframe'):
                src = iframe.get('src')
                if src and 'dailymotion' in src:
                    video_urls.append(src)
            
            # data-src attributes
            for tag in soup.find_all(attrs={'data-src': True}):
                src = tag['data-src']
                if any(ext in src for ext in ['.mp4', '.m3u8', '.webm']):
                    video_urls.append(urljoin(url, src))
            
            logger.info(f"Found {len(video_urls)} video(s)")
            return video_urls
            
        except Exception as e:
            logger.error(f"Error extracting videos: {e}")
            return []
    
    def extract_dailymotion_video_url(self, embed_url):
        """
        Extract Dailymotion video URL from embed
        Returns: (video_url, video_id) or (None, None)
        """
        try:
            # Extract video ID
            match = re.search(r'video[=/]([a-zA-Z0-9]+)', embed_url)
            if not match:
                logger.error("Could not extract Dailymotion video ID")
                return None, None
            
            video_id = match.group(1)
            logger.info(f"Dailymotion video ID: {video_id}")
            
            # Fetch embed page
            embed_page = f"https://www.dailymotion.com/embed/video/{video_id}"
            response = self.session.get(embed_page, timeout=10)
            response.raise_for_status()
            
            # Look for m3u8 URL
            m3u8_match = re.search(r'"(https://[^"]+\.m3u8[^"]*)"', response.text)
            if m3u8_match:
                video_url = m3u8_match.group(1).replace('\\/', '/')
                logger.info(f"Found HLS stream: {video_url[:60]}...")
                return video_url, video_id
            
            # Look for mp4 URL
            mp4_match = re.search(r'"(https://[^"]+\.mp4[^"]*)"', response.text)
            if mp4_match:
                video_url = mp4_match.group(1).replace('\\/', '/')
                logger.info(f"Found MP4: {video_url[:60]}...")
                return video_url, video_id
            
            logger.warning(f"Could not find video URL for {video_id}")
            return None, None
            
        except Exception as e:
            logger.error(f"Dailymotion extraction error: {e}")
            return None, None
    
    def download_hls(self, video_url, output_path):
        """Download HLS stream using ffmpeg"""
        if not self.ffmpeg_path:
            logger.error("FFmpeg not available - cannot download HLS")
            return False
        
        try:
            cmd = [
                self.ffmpeg_path,
                '-i', video_url,
                '-c', 'copy',
                '-bsf:a', 'aac_adtstoasc',
                '-y',
                str(output_path)
            ]
            
            logger.info(f"Downloading HLS stream...")
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            if result.returncode == 0:
                logger.info(f"✓ Downloaded: {output_path.name}")
                return True
            else:
                logger.error("ffmpeg failed")
                return False
                
        except Exception as e:
            logger.error(f"HLS download error: {e}")
            return False
    
    def download_direct(self, video_url, output_path):
        """Download direct video URL"""
        try:
            logger.info(f"Downloading video...")
            
            response = self.session.get(video_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            print(f"\r  Progress: {percent:.1f}%", end='', flush=True)
            
            print()
            logger.info(f"✓ Downloaded: {output_path.name}")
            return True
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            return False
    
    def download_video(self, video_url, output_path):
        """Download a single video"""
        try:
            # Check if Dailymotion embed
            if 'dailymotion.com' in video_url and '/embed/' not in video_url:
                real_url, video_id = self.extract_dailymotion_video_url(video_url)
                if real_url:
                    video_url = real_url
                    output_path = output_path.parent / f"dailymotion_{video_id}.mp4"
                else:
                    logger.error("Could not extract Dailymotion video URL")
                    return False
            
            elif 'dailymotion.com/embed/' in video_url or 'geo.dailymotion.com' in video_url:
                real_url, video_id = self.extract_dailymotion_video_url(video_url)
                if real_url:
                    video_url = real_url
                    output_path = output_path.parent / f"dailymotion_{video_id}.mp4"
                else:
                    logger.error("Could not extract Dailymotion video URL")
                    return False
            
            # Download based on type
            if '.m3u8' in video_url:
                return self.download_hls(video_url, output_path)
            else:
                return self.download_direct(video_url, output_path)
                
        except Exception as e:
            logger.error(f"Download error: {e}")
            return False
    
    def download_from_page(self, page_url):
        """Main entry point"""
        try:
            video_urls = self.extract_videos_from_page(page_url)
            
            if not video_urls:
                logger.warning("⚠ No videos found on page")
                return
            
            success_count = 0
            for i, video_url in enumerate(video_urls, 1):
                logger.info(f"\n{'='*60}")
                logger.info(f"Video {i}/{len(video_urls)}")
                logger.info(f"{'='*60}")
                
                output_path = self.output_dir / f"video_{i}.mp4"
                
                if self.download_video(video_url, output_path):
                    success_count += 1
            
            logger.info(f"\n{'='*60}")
            logger.info(f"Downloaded {success_count}/{len(video_urls)} video(s)")
            logger.info(f"Output: {self.output_dir}")
            logger.info(f"{'='*60}")
            
        except Exception as e:
            logger.error(f"Error: {e}")
        finally:
            if self.driver:
                self.driver.quit()


def main():
    parser = argparse.ArgumentParser(description='Video Downloader (Dailymotion support)')
    parser.add_argument('url', help='Page URL')
    parser.add_argument('--out', default='./videos', help='Output directory')
    parser.add_argument('--render-js', action='store_true', help='Use Selenium')
    parser.add_argument('--ignore-robots', action='store_true', help='Ignore robots.txt')
    
    args = parser.parse_args()
    
    downloader = VideoDownloader(
        output_dir=args.out,
        render_js=args.render_js,
        ignore_robots=args.ignore_robots
    )
    
    downloader.download_from_page(args.url)


if __name__ == '__main__':
    main()
