import hashlib
import json
import re
import time
import urllib.parse

from .common import InfoExtractor
from ..utils import ExtractorError, float_or_none, url_or_none

# Playlists shorter than this are treated as ad/teaser reels rather than the
# actual episode (the site serves a ~33s ad layer before the main video).
_MIN_MAIN_DURATION = 120


class IyfTvIE(InfoExtractor):
    _VALID_URL = r'https?://(?:www\.)?(?:iyf\.tv|kubb\.tv)/play/(?P<id>[a-zA-Z0-9]+)'
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
    }]

    @staticmethod
    def _base_domain(url):
        host = urllib.parse.urlparse(url).netloc.lower()
        return re.sub(r'^(?:www|m)\.', '', host)

    @staticmethod
    def _iyf_query(url):
        if '?' not in url:
            return ''
        query = url.split('?', 1)[1].split('#', 1)[0]
        parts = []
        for item in query.split('&'):
            if not item:
                continue
            key, sep, value = item.partition('=')
            parts.append(f'{key}={urllib.parse.unquote_plus(value)}' if sep else key)
        return '&'.join(parts)

    def _sign_iyf_url(self, url, public_key, private_key):
        if not (public_key and private_key):
            return url
        if 'pub=' in url or 'vv=' in url:
            return url
        query = self._iyf_query(url).lower()
        digest = hashlib.md5(f'{public_key}&{query}&{private_key}'.encode()).hexdigest()
        separator = '&' if '?' in url else '?'
        return f'{url}{separator}vv={digest}&pub={public_key}'

    def _get_api_context(self, webpage):
        ctx = {'public_key': None, 'private_key': None, 'region': 'GL'}
        mobj = re.search(r'var\s+injectJson\s*=\s*(\{.+?\});', webpage, re.S)
        if not mobj:
            return ctx
        try:
            data = json.loads(mobj.group(1))
        except (TypeError, ValueError):
            return ctx

        configs = data.get('config')
        if isinstance(configs, list):
            for config in configs:
                pconfig = config.get('pConfig') if isinstance(config, dict) else None
                if not (isinstance(pconfig, dict) and pconfig.get('publicKey')):
                    continue
                ctx['public_key'] = pconfig.get('publicKey')
                keys = pconfig.get('privateKey')
                if isinstance(keys, list) and keys:
                    ctx['private_key'] = keys[0]
                elif isinstance(keys, str):
                    ctx['private_key'] = keys
                break

        regions = data.get('switch-region')
        if isinstance(regions, list) and regions and isinstance(regions[0], dict):
            ctx['region'] = (regions[0].get('regionCode')
                             or regions[0].get('ipCountry')
                             or ctx['region'])
        return ctx

    def _api_get(self, domain, path, params, ctx, video_id, note):
        query = '&'.join(
            f'{key}={value}' for key, value in params.items()
            if value is not None and value != '')
        api_url = f'https://m10.{domain}/v3/{path}?{query}&v=1'
        api_url = self._sign_iyf_url(
            api_url, ctx['public_key'], ctx['private_key'])
        data = self._download_json(
            api_url, video_id, note=note, fatal=False,
            headers={
                'Referer': f'https://{domain}/',
                'Origin': f'https://{domain}',
            })
        if not isinstance(data, dict):
            return None
        payload = data.get('data')
        if not isinstance(payload, dict):
            return None
        if payload.get('code') not in (0, None):
            self.report_warning(
                f"IYF API {path} returned {payload.get('code')}: "
                f"{payload.get('msg') or 'unknown error'}")
            return None
        info = payload.get('info')
        if isinstance(info, list):
            return info[0] if info else None
        return info

    def _extract_api_formats(self, info, video_id, ctx, domain):
        formats = []
        seen = set()
        m3u8_url = None
        headers = {'Referer': f'https://{domain}/'}

        def add_source(source, is_hls, label=None, need_sign=False):
            nonlocal m3u8_url
            if not isinstance(source, str) or not source:
                return
            source = url_or_none(source)
            if not source or source in seen:
                return
            seen.add(source)
            if need_sign:
                source = self._sign_iyf_url(
                    source, ctx['public_key'], ctx['private_key'])
            if is_hls or '.m3u8' in source:
                extracted = self._extract_m3u8_formats(
                    source, video_id, 'mp4', entry_protocol='m3u8_native',
                    m3u8_id='hls', fatal=False, headers=headers)
                if extracted:
                    if label:
                        for fmt in extracted:
                            fmt.setdefault('format_note', label)
                    formats.extend(extracted)
                    if m3u8_url is None:
                        m3u8_url = source
            else:
                formats.append({
                    'url': source,
                    'ext': 'mp4',
                    'format_id': 'http',
                    'format_note': label,
                    'http_headers': headers,
                })

        clarity = info.get('clarity')
        playing_media = info.get('playingMedia')
        playing_key = (playing_media.get('key')
                       if isinstance(playing_media, dict) else None)
        entries = [entry for entry in clarity if isinstance(entry, dict)] \
            if isinstance(clarity, list) else []
        entries.sort(key=lambda entry: 0 if (
            playing_key and entry.get('key') == playing_key) else 1)

        for entry in entries:
            label = entry.get('description') or entry.get('title')
            path = entry.get('path')
            if isinstance(path, str):
                add_source(path, '.m3u8' in path, label)
            elif isinstance(path, dict):
                source = path.get('result') or path.get('backup')
                is_hls = (bool(path.get('isMasterPlaylist'))
                          or '.m3u8' in (source or ''))
                add_source(source, is_hls, label, bool(path.get('needSign')))

        if not formats:
            for entry in info.get('flvPathList') or []:
                if not isinstance(entry, dict) or entry.get('link'):
                    continue
                source = entry.get('result') or entry.get('backup')
                is_hls = bool(entry.get('isHls')) or '.m3u8' in (source or '')
                add_source(source, is_hls, entry.get('title'),
                           bool(entry.get('needSign')))

        return formats, m3u8_url

    def _mobile_api_get(self, domain, path, params, video_id, note):
        common = {
            'System': 'h5',
            'AppVersion': '1.0',
            'SystemVersion': 'h5',
            'version': 'H3',
            'v': 1,
            'i18n': 0,
        }
        query = '&'.join(
            f'{key}={value}' for key, value in {**params, **common}.items()
            if value is not None)
        api_url = f'https://api.{domain}/api/{path}?{query}'
        data = self._download_json(
            api_url, video_id, note=note, fatal=False,
            headers={
                'Referer': f'https://m.{domain}/',
                'Origin': f'https://m.{domain}',
            })
        if not isinstance(data, dict):
            return None
        if data.get('ret') not in (200, None):
            self.report_warning(
                f"IYF mobile API {path} returned {data.get('ret')}: "
                f"{data.get('msg') or 'unknown error'}")
            return None
        payload = data.get('data')
        return payload if isinstance(payload, dict) else None

    def _extract_mobile_formats(self, play_data, video_id, domain):
        formats = []
        sources = []
        seen = set()
        duration = None

        entries = play_data.get('list')
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                source = url_or_none(entry.get('mediaUrl'))
                if not source or source in seen:
                    continue
                seen.add(source)
                extra = entry.get('extra')
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except (TypeError, ValueError):
                        extra = {}
                if not isinstance(extra, dict):
                    extra = {}
                width = height = None
                dimension = re.match(r'(\d+):(\d+)', str(extra.get('v') or ''))
                if dimension:
                    width, height = int(dimension.group(1)), int(dimension.group(2))
                if duration is None:
                    duration = float_or_none(extra.get('t'))
                sources.append((
                    source,
                    entry.get('resolutionDes') or entry.get('resolution'),
                    width, height))

        m3u8_url = None
        headers = {'Referer': f'https://{domain}/'}
        for source, label, width, height in sources:
            extracted = self._extract_m3u8_formats(
                source, video_id, 'mp4', entry_protocol='m3u8_native',
                m3u8_id='hls', fatal=False, headers=headers)
            if not extracted:
                continue
            for fmt in extracted:
                if label:
                    fmt.setdefault('format_note', label)
                if width:
                    fmt['width'] = width
                if height:
                    fmt['height'] = height
            formats.extend(extracted)
            if m3u8_url is None:
                m3u8_url = source

        if len(sources) > 1:
            for fmt in formats:
                if fmt.get('height'):
                    fmt['format_id'] = f"hls-{fmt['height']}p"

        return formats, m3u8_url, duration

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
                'Playwright is not installed, so browser-based extraction is '
                'unavailable. Run "pip install playwright" to enable it')
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
        duration = float_or_none(
            (probe_data.get('format') or {}).get('duration'))

        return fmt, duration

    def _real_extract(self, url):
        video_id = self._match_id(url)
        episode_key = self._search_regex(
            r'[?&]id=([a-zA-Z0-9]+)', url, 'episode key', default=None)

        webpage = self._download_webpage(
            url, video_id, note='Downloading webpage', fatal=False) or ''

        domain = self._base_domain(url)
        ctx = self._get_api_context(webpage)
        detail_info = {}
        play_info = None
        play_data = None
        duration = None

        details = self._mobile_api_get(domain, 'video/videodetails', {
            'id': episode_key or video_id,
            'mediaKey': video_id,
            'episodeKey': episode_key or '',
        }, video_id, 'Downloading video details') or {}
        if isinstance(details.get('detailInfo'), dict):
            detail_info = details['detailInfo']

        episodes = detail_info.get('episodes')
        if not isinstance(episodes, list):
            episodes = []
        if not episode_key:
            episode_key = (episodes[-1].get('episodeKey') if episodes
                           else detail_info.get('episodeKey'))

        selected = next((episode for episode in episodes
                         if isinstance(episode, dict)
                         and episode.get('episodeKey') == episode_key), None)
        if selected is None:
            selected = detail_info
        episode_id = selected.get('episodeId') or detail_info.get('episodeId')
        video_type = selected.get('videoType') or detail_info.get('videoType') or 1

        if episode_id:
            play_data = self._mobile_api_get(domain, 'video/getplaydata', {
                'mediaKey': video_id,
                'videoId': episode_id,
                'videoType': video_type,
                'liveLine': '',
            }, video_id, 'Downloading video info')

        formats = []
        m3u8_url = None
        if isinstance(play_data, dict):
            formats, m3u8_url, duration = self._extract_mobile_formats(
                play_data, video_id, domain)

        if not formats and ctx['public_key'] and ctx['private_key'] and episode_key:
            region = str(ctx['region'])
            play_info = self._api_get(domain, 'video/play', {
                'cinema': 1,
                'id': episode_key,
                'a': 0,
                'lang': 'cns',
                'usersign': 1,
                'region': region if region.endswith('.') else f'{region}.',
                'device': 1,
                'isMasterSupport': 1,
                'sharpness': 2,
                'line': 0,
            }, ctx, video_id, 'Downloading video info')
            if isinstance(play_info, dict):
                formats, m3u8_url = self._extract_api_formats(
                    play_info, video_id, ctx, domain)

        if not formats:
            browser_info = self._capture_via_browser(url, video_id) or {}
            if browser_info.get('m3u8_url'):
                m3u8_url = browser_info['m3u8_url']
                formats = self._extract_m3u8_formats(
                    m3u8_url, video_id, 'mp4',
                    entry_protocol='m3u8_native', m3u8_id='hls', fatal=False,
                    headers={'Referer': f'https://{domain}/'})
                duration = browser_info.get('duration')

            if browser_info:
                detail_info = {**detail_info}
                for info_key, browser_key in (
                        ('title', ('og_title', 'title')),
                        ('introduce', ('og_desc',)),
                        ('coverImgUrl', ('og_image',))):
                    value = next((browser_info.get(key)
                                  for key in browser_key
                                  if browser_info.get(key)), None)
                    if value:
                        detail_info[info_key] = (
                            self._clean_title(value) if info_key == 'title' else value)

        if formats and m3u8_url:
            result = self._enrich_hls_format_from_ffprobe(formats[0], m3u8_url)
            if isinstance(result, tuple):
                formats[0], probe_duration = result
                if probe_duration and not duration:
                    duration = probe_duration
            else:
                formats[0] = result

        if not formats:
            raise ExtractorError(
                'No video formats found. IYF.tv returned its ad layer only '
                '(访问过量 / "检测到您的流量异常" rate-limit challenge) or the '
                'player API failed. Try again later or use a different '
                'network/IP.', expected=True)

        title = self._clean_title(detail_info.get('title'))
        episode_title = selected.get('episodeTitle') or detail_info.get('episodeTitle')
        if title and episode_title and str(episode_title) not in title:
            title = f'{title}-{episode_title}'
        if not title:
            title = self._clean_title(self._og_search_title(webpage, default=None))
        if duration is None and isinstance(play_info, dict):
            duration = float_or_none(play_info.get('duration'))

        return {
            'id': video_id,
            'title': title,
            'description': detail_info.get('introduce') or self._og_search_description(webpage, default=None),
            'thumbnail': url_or_none(detail_info.get('coverImgUrl')) or self._og_search_thumbnail(webpage, default=None),
            'duration': duration,
            'formats': formats,
        }
