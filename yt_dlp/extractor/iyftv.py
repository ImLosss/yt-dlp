import re
import time

from .common import InfoExtractor
from ..utils import ExtractorError, float_or_none, url_or_none

# Playlists shorter than this are treated as ad/teaser reels rather than the
# actual episode (the site serves a ~33s ad layer before the main video).
_MIN_MAIN_DURATION = 120


class IyfTvIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?iyf\.tv/play/(?P<id>[a-zA-Z0-9]+)'
    IE_NAME = 'iyf.tv'
    IE_DESC = 'IYF.tv (爱壹帆)'

    _TESTS = [{
        'url': 'https://www.iyf.tv/play/q9eWLpBrRb9?id=VKoUCIIPRy1',
        'info_dict': {
            'id': 'q9eWLpBrRb9',
            'ext': 'mp4',
            'title': str,
            'duration': float,
        },
        'params': {'skip_download': True},
        'skip': 'Requires a Chromium-based browser for Playwright',
    }]

    def _parse_m3u8_info(self, m3u8_url):
        """Total duration of an HLS media playlist, or None if unreadable.

        These CDN playlists carry no EXT-X-STREAM-INF, so resolution/codec are
        filled in later by _enrich_hls_format_from_ffprobe.
        """
        playlist = self._download_webpage(
            m3u8_url, None, note='Checking playlist', fatal=False,
            headers={'Referer': 'https://www.iyf.tv/'})

        info = {'duration': None}

        if not playlist:
            return info

        # Extract duration from fragments
        if '#EXTINF' in playlist:
            durations = [float(seg) for seg in re.findall(r'#EXTINF:([\d.]+)', playlist)]
            if durations:
                info['duration'] = sum(durations)

        return info

    def _launch_browser(self, playwright):
        args = ['--no-sandbox', '--disable-blink-features=AutomationControlled']
        last_err = None
        for kwargs in ({'channel': 'chrome'}, {'channel': 'msedge'}, {'channel': 'chromium'}, {}):
            try:
                return playwright.chromium.launch(headless=True, args=args, **kwargs)
            except Exception as err:
                last_err = err
        self.report_warning(f'Could not launch a Chromium-based browser: {last_err}')
        return None

    def _capture_via_browser(self, url, video_id):
        """
        Load the page in a real browser and capture the HLS media playlist that
        carries the signed CDN URL (us/vv/pub parameters).

        Returns a dict with the playlist url and any metadata harvested from the
        player, or None when the browser path is unavailable.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.report_warning(
                'Playwright is not installed, so only the ad layer is reachable. '
                'Run "pip install playwright" for full-video extraction')
            return None

        captured = []
        info = {}

        with sync_playwright() as p:
            browser = self._launch_browser(p)
            if not browser:
                return None
            try:
                context = browser.new_context(
                    user_agent=('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'),
                    locale='zh-CN',
                    viewport={'width': 1366, 'height': 768},
                )
                page = context.new_page()
                page.on('request', lambda req: captured.append(req.url) if '.m3u8' in req.url else None)

                self.to_screen(f'Loading {url} in browser')
                page.goto(url, wait_until='domcontentloaded', timeout=60_000)

                # Start playback; the site usually autoplays, but click as a fallback.
                try:
                    page.wait_for_selector('video', timeout=15_000)
                    page.evaluate('''() => {
                        const v = document.querySelector('video');
                        if (!v) return;
                        v.muted = true;
                        const r = v.play();
                        if (r && r.catch) r.catch(() => {});
                    }''')
                except Exception:
                    pass

                best = None
                seen = set()
                deadline = time.time() + 45
                while time.time() < deadline:
                    page.wait_for_timeout(500)
                    for candidate in list(captured):
                        if candidate in seen:
                            continue
                        seen.add(candidate)
                        playlist_info = self._parse_m3u8_info(candidate)
                        duration = playlist_info.get('duration')
                        if not duration:
                            continue
                        self.to_screen(f'Playlist {duration:.1f}s: {candidate[:80]}...')
                        if duration >= _MIN_MAIN_DURATION:
                            best = (candidate, duration)
                            break
                        if best is None:
                            best = (candidate, duration)
                    if best and best[1] >= _MIN_MAIN_DURATION:
                        break

                if best:
                    info['m3u8_url'], info['duration'] = best
                    # Extract additional metadata from playlist if available
                    playlist_info = self._parse_m3u8_info(best[0])
                    if playlist_info.get('duration'):
                        info['duration'] = playlist_info['duration']

                info.update(self._harvest_metadata(page))
            except Exception as err:
                self.report_warning(f'Browser capture failed: {err}')
            finally:
                browser.close()

        return info or None

    def _harvest_metadata(self, page):
        try:
            raw = page.evaluate('''() => {
                const pick = (sel, attr) => {
                    const el = document.querySelector(sel);
                    return el ? el.getAttribute(attr) : null;
                };
                return {
                    title: document.title,
                    og_title: pick('meta[property="og:title"]', 'content'),
                    og_desc: pick('meta[property="og:description"]', 'content'),
                    og_image: pick('meta[property="og:image"]', 'content'),
                    duration: document.querySelector('video')?.duration || null,
                };
            }''')
        except Exception:
            return {}
        return raw or {}

    @staticmethod
    def _clean_title(title):
        if not title:
            return None
        # e.g. "灵境行者-14-免费在线观看-爱壹帆"
        title = re.sub(r'[-_|]\s*(?:免费在线观看|在线观看)\s*[-_|].*$', '', title)
        title = re.sub(r'[-_|]\s*(?:爱壹帆|IYF\.?tv|iyf\.tv)\s*$', '', title, flags=re.I)
        return title.strip() or None

    def _enrich_hls_format_from_ffprobe(self, fmt, m3u8_url):
        """
        For media playlists without EXT-X-STREAM-INF, use ffprobe to populate
        width/height/codec metadata so -F output shows resolution instead of 'unknown'.

        ffprobe can read an HLS playlist over HTTP directly, which is the only way
        to report stream details before yt-dlp downloads and re-probes the file.
        """
        try:
            from ..postprocessor.ffmpeg import FFmpegPostProcessor
            pp = FFmpegPostProcessor(self._downloader)
            if not pp.probe_available:
                return fmt
        except Exception:
            return fmt

        try:
            import json
            import subprocess
            proc = subprocess.run(
                [pp.probe_executable, '-v', 'error', '-show_format', '-show_streams',
                 '-print_format', 'json', '-headers', 'Referer: https://www.iyf.tv/\r\n',
                 m3u8_url],
                capture_output=True, text=True, timeout=45)
            if proc.returncode != 0 or not proc.stdout.strip():
                self.write_debug(f'ffprobe on HLS failed (rc={proc.returncode})')
                return fmt
            probe_data = json.loads(proc.stdout)
        except Exception as err:
            self.write_debug(f'ffprobe on HLS errored: {err}')
            return fmt

        for stream in probe_data.get('streams', []):
            codec_type = stream.get('codec_type')
            if codec_type == 'video':
                fmt['width'] = stream.get('width') or fmt.get('width')
                fmt['height'] = stream.get('height') or fmt.get('height')
                fmt['vcodec'] = stream.get('codec_name') or fmt.get('vcodec')
                rate = stream.get('avg_frame_rate') or stream.get('r_frame_rate')
                if rate and rate not in ('0/0', 'N/A'):
                    try:
                        num, denom = map(int, rate.split('/'))
                        if denom > 0:
                            fmt['fps'] = num / denom
                    except (ValueError, TypeError):
                        pass
            elif codec_type == 'audio':
                fmt['acodec'] = stream.get('codec_name') or fmt.get('acodec')
                if stream.get('sample_rate'):
                    fmt['asr'] = int(stream['sample_rate'])
                if stream.get('channels'):
                    fmt['audio_channels'] = int(stream['channels'])

        # Duration from the container/format (more reliable than summing EXTINF)
        duration = None
        fmt_duration = (probe_data.get('format') or {}).get('duration')
        if fmt_duration:
            try:
                duration = float(fmt_duration)
            except (ValueError, TypeError):
                pass

        return fmt, duration

    def _real_extract(self, url):
        video_id = self._match_id(url)
        query_id = self._search_regex(r'[?&]id=([a-zA-Z0-9]+)', url, 'media key', default=None)

        webpage = self._download_webpage(
            url, video_id, note='Downloading webpage', fatal=False) or ''

        title = self._og_search_title(webpage, default=None)
        description = self._og_search_description(webpage, default=None)
        thumbnail = self._og_search_thumbnail(webpage, default=None)
        duration = None
        formats = []
        m3u8_url = None

        browser_info = self._capture_via_browser(url, video_id) or {}

        if browser_info.get('m3u8_url'):
            m3u8_url = browser_info['m3u8_url']
            formats = self._extract_m3u8_formats(
                m3u8_url, video_id, 'mp4',
                entry_protocol='m3u8_native', m3u8_id='hls', fatal=False,
                headers={'Referer': 'https://www.iyf.tv/'})
            duration = browser_info.get('duration')
            # Enrich format with ffprobe metadata for display in -F
            if formats:
                result = self._enrich_hls_format_from_ffprobe(formats[0], m3u8_url)
                if isinstance(result, tuple):
                    formats[0], probe_duration = result
                    if probe_duration and not duration:
                        duration = probe_duration
                else:
                    formats[0] = result

        title = (self._clean_title(browser_info.get('og_title'))
                 or self._clean_title(browser_info.get('title'))
                 or self._clean_title(title)
                 or title)
        description = browser_info.get('og_desc') or description
        thumbnail = url_or_none(browser_info.get('og_image')) or thumbnail
        if duration is None:
            duration = float_or_none(browser_info.get('duration'))

        if not formats and query_id:
            # Ad layer only: the MasterPlayList endpoint maps the visible media
            # key to the pre-roll reel, not the episode itself.
            self.to_screen('Browser capture unavailable; falling back to ad-layer playlist')
            m3u8_url = f'https://upload.iyf.tv/api/video/MasterPlayList?id={query_id}'
            formats = self._extract_m3u8_formats(
                m3u8_url, video_id, 'mp4',
                entry_protocol='m3u8_native', m3u8_id='hls-ad', fatal=False,
                headers={'Referer': 'https://www.iyf.tv/'})

        if not formats:
            raise ExtractorError(
                'No video formats found. The site loads its player through a signed '
                'Angular frontend, so installing Playwright is required: '
                'pip install playwright', expected=True)

        return {
            'id': video_id,
            'title': title,
            'description': description,
            'thumbnail': thumbnail,
            'duration': duration,
            'formats': formats,
        }
