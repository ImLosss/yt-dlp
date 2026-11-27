import random
import re
import string
import time
import urllib.parse

from .common import InfoExtractor
from ..utils import (
    ExtractorError,
    clean_html,
    get_element_by_class,
    int_or_none,
    js_to_json,
    str_or_none,
    strip_jsonp,
)


class YoukuIE(InfoExtractor):
    IE_NAME = 'youku'
    IE_DESC = '优酷'
    _VALID_URL = r'''(?x)
        (?:
            https?://(
                (?:v|play(?:er)?)\.(?:youku|tudou)\.com/(?:v_show/id_|player\.php/sid/)|
                (?:www\.)?youku\.tv/(?:v/)?v_show/id_|
                video\.tudou\.com/v/)|
            youku:)
        (?P<id>[A-Za-z0-9_-]+)(?:={0,2})(?:\.html|/v\.swf|)
    '''

    _TESTS = [{
        'url': 'http://player.youku.com/player.php/sid/XNDgyMDQ2NTQw/v.swf',
        'only_matching': True,
    }, {
        'url': 'http://v.youku.com/v_show/id_XNjA1NzA2Njgw.html',
        'note': 'Video protected with password',
        'info_dict': {
            'id': 'XNjA1NzA2Njgw',
            'ext': 'mp4',
            'title': '邢義田复旦讲座之想象中的胡人—从"左衽孔子"说起',
            'duration': 7264.5,
            'thumbnail': r're:^https?://.*',
            'uploader': 'FoxJin1006',
            'uploader_id': '322014285',
            'uploader_url': 'http://i.youku.com/u/UMTI4ODA1NzE0MA==',
            'tags': list,
        },
        'params': {
            'videopassword': '100600',
        },
        'skip': '404',
    }, {
        # /play/get.json contains streams with "channel_type":"tail"
        'url': 'http://v.youku.com/v_show/id_XOTUxMzg4NDMy.html',
        'info_dict': {
            'id': 'XOTUxMzg4NDMy',
            'ext': 'mp4',
            'title': '我的世界☆明月庄主☆车震猎杀☆杀人艺术Minecraft',
            'duration': 702.08,
            'thumbnail': r're:^https?://.*',
            'uploader': '明月庄主moon',
            'uploader_id': '38465621',
            'uploader_url': 'https://www.youku.com/profile/index/?uid=UMTUzODYyNDg0',
            'tags': list,
        },
    }, {
        'url': 'https://v.youku.com/v_show/id_XNTA2NTA0MjA1Mg==.html',
        'info_dict': {
            'id': 'XNTA2NTA0MjA1Mg',
            'ext': 'mp4',
            'title': 'Minecraft我的世界：建造超大巨型航空飞机，菜鸟vs高手vs黑客',
            'duration': 542.13,
            'thumbnail': r're:^https?://.*',
            'uploader': '波哥游戏解说',
            'uploader_id': '156688084',
            'uploader_url': 'https://www.youku.com/profile/index/?uid=UNjI2NzUyMzM2',
            'tags': list,
        },
    }, {
        'url': 'https://v.youku.com/v_show/id_XNTE1MzczOTg4MA==.html',
        'info_dict': {
            'id': 'XNTE1MzczOTg4MA',
            'ext': 'mp4',
            'title': '国产超A特工片',
            'duration': 362.97,
            'thumbnail': r're:^https?://.*',
            'uploader': '陈晓娟说历史',
            'uploader_id': '1640913339',
            'uploader_url': 'https://www.youku.com/profile/index/?uid=UNjU2MzY1MzM1Ng==',
            'tags': list,
        },
    }, {
        'url': 'https://play.tudou.com/v_show/id_XNjAxNjI2OTU3Ng==.html?',
        'info_dict': {
            'id': 'XNjAxNjI2OTU3Ng',
            'ext': 'mp4',
            'title': '阿斯塔意识到哈里杀了人，自己被骗了',
            'thumbnail': 'https://m.ykimg.com/0541010164F732752794D4D7B70331D1',
            'uploader_id': '88758207',
            'tags': [],
            'uploader_url': 'https://www.youku.com/profile/index/?uid=UMzU1MDMyODI4',
            'uploader': '英美剧场',
            'duration': 72.91,
        },
    }, {
        'url': 'https://www.youku.tv/v/v_show/id_XNjU5NDI1NTMwMA==.html?s=bbea76cc3fa74f789d29&spm=a2hje.13141534.1_2.d_zj2_19&scm=20140719.apircmd.242627.video_XNjU5NDI1NTMwMA==',
        'only_matching': True,
    }]

    @staticmethod
    def get_ysuid():
        return '{}{}'.format(int(time.time()), ''.join(
            random.choices(string.ascii_letters, k=3)))

    def get_format_name(self, fm):
        _dict = {
            '3gp': 'h6',
            '3gphd': 'h5',
            'flv': 'h4',
            'flvhd': 'h4',
            'mp4': 'h3',
            'mp4hd': 'h3',
            'mp4hd2': 'h4',
            'mp4hd3': 'h4',
            'hd2': 'h2',
            'hd3': 'h1',
        }
        return _dict.get(fm)

    _LANGUAGE_CODES = {
        'default': 'zh-Hans',
        'chs': 'zh-Hans',
        'cht': 'zh-Hant',
        'kr': 'ko',
        'po': 'pt',
        'jp': 'ja',
        'vn': 'vi',
    }

    def _extract_subtitles(self, data):
        subtitles = {}
        for sub in data.get('subtitle') or []:
            sub_url = sub.get('url')
            if not sub_url:
                continue
            lang = sub.get('subtitle_lang') or (sub.get('subtitle_info_code') or ['unknown'])[0]
            lang = self._LANGUAGE_CODES.get(lang, lang)
            ext = sub_url.rsplit('.', 1)[-1].split('?')[0].lower()
            if ext not in ('ass', 'srt', 'vtt'):
                ext = 'vtt'
            subtitles.setdefault(lang, []).append({
                'url': sub_url,
                'ext': ext,
                'name': (sub.get('subtitle_info') or [None])[0],
            })
        return subtitles

    def _real_extract(self, url):
        video_id = self._match_id(url)
        is_youku_tv = urllib.parse.urlparse(url).netloc.endswith('youku.tv')

        if is_youku_tv:
            site = 'youku.tv'
            ccode = '0597'
        else:
            site = 'youku.com'
            ccode = '0564'

        self._set_cookie(site, '__ysuid', self.get_ysuid())
        self._set_cookie(site, 'xreferrer', 'http://www.youku.com')

        # Reuse the cna from the browser (if cookies were provided), since it
        # belongs to the same session as the login/VIP cookies.
        cna = self._get_cookies(f'https://www.{site}/').get('cna')
        cna = cna.value if cna else None
        if not cna:
            _, urlh = self._download_webpage_handle(
                'https://log.mmstat.com/eg.js', video_id, 'Retrieving cna info')
            # The etag header is '"foobar"'; let's remove the double quotes
            cna = urlh.headers['etag'][1:-1]
            self._set_cookie(site, 'cna', cna)

        # request basic data
        basic_data_params = {
            'vid': video_id,
            'ccode': ccode,
            'client_ip': '192.168.1.1',
            'utid': cna,
            'client_ts': time.time() / 1000,
        }

        if is_youku_tv:
            # Required to get the modern streams and the subtitle list
            basic_data_params.update({
                'media_type': 'standard,subtitle',
                'play_ability': 16782592,
                'master_m3u8': 1,
                'drm_type': 3,
                'key_index': 'web01',
                'app_ver': '9.8.11',
                'enc_ver': 2,
            })
            # The `s` query parameter (also exposed as `showId` on the page) is
            # the *show* id, not the episode id.  Do not send it as `showid`
            # together with `vid`: when both are present Youku ignores `vid`
            # and returns the show's current episode instead, which is how the
            # extractor used to return the wrong episode.  Only use it as a
            # fallback for the misleading "please log in" error (-3007).
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            show_id = (query.get('s') or query.get('showid') or [None])[0]
            if not (show_id and re.fullmatch(r'[0-9a-fA-F]{16,32}', show_id)):
                show_id = None

        video_password = self.get_param('videopassword')
        if video_password:
            basic_data_params['password'] = video_password

        headers = {
            'Referer': url,
        }
        headers.update(self.geo_verification_headers())
        api_headers = headers
        if is_youku_tv:
            # The login/VIP cookies live on youku.tv, but the API host is
            # ups.youku.com, so the cookie jar would not send them there.
            tv_cookies = self._get_cookies('https://www.youku.tv/')
            if tv_cookies:
                api_headers = {
                    **headers,
                    'Cookie': '; '.join(f'{name}={morsel.value}' for name, morsel in tv_cookies.items()),
                }

        def download_data(params):
            return self._download_json(
                'https://ups.youku.com/ups/get.json', video_id,
                'Downloading JSON metadata',
                query=params, headers=api_headers)['data']

        data = download_data(basic_data_params)
        error = data.get('error')

        def is_requested_video(data):
            encodeid = (data.get('video') or {}).get('encodeid') or ''
            return encodeid.rstrip('=') == video_id.rstrip('=')

        if is_youku_tv and error and error.get('code') == -3007:
            # Fallback for URLs whose `s` parameter is missing or is not the
            # show id: the video page contains the show id needed to access
            # show/season videos.
            webpage = self._download_webpage(url, video_id, 'Downloading video page', fatal=False) or ''
            page_show_id = self._search_regex(
                r'"showId"\s*:\s*"([0-9a-zA-Z_-]+)"', webpage, 'show id', default=None)
            show_id = page_show_id or show_id
            if show_id:
                basic_data_params['showid'] = show_id
                data = download_data(basic_data_params)
                error = data.get('error')
                if not error and not is_requested_video(data):
                    # `showid` made Youku return a different episode of the
                    # show; never silently accept it.
                    basic_data_params.pop('showid', None)
                    data = download_data(basic_data_params)
                    error = data.get('error')

        if not error and is_youku_tv and not is_requested_video(data):
            raise ExtractorError(
                'Youku returned the wrong video (%s) instead of the requested one (%s)'
                % ((data.get('video') or {}).get('encodeid'), video_id))

        if error:
            error_note = error.get('note')
            if error_note is not None and '因版权原因无法观看此视频' in error_note:
                raise ExtractorError(
                    'Youku said: Sorry, this video is available in China only', expected=True)
            elif error_note and '该视频被设为私密' in error_note:
                raise ExtractorError(
                    'Youku said: Sorry, this video is private', expected=True)
            elif error.get('code') == -6004:
                raise ExtractorError(
                    'Youku said: This account has been restricted, please log in again or use a different account',
                    expected=True)
            else:
                msg = 'Youku server reported error %i' % error.get('code')
                if error_note is not None:
                    msg += ': ' + clean_html(error_note)
                raise ExtractorError(msg)

        # get video title
        video_data = data['video']
        title = video_data['title']

        def extract_stream_formats(stream):
            stream_type = stream.get('stream_type')
            # Youku serves its higher resolutions (e.g. 1080p on youku.tv) as
            # CENC/CBCS encrypted HLS.  The API labels such streams explicitly;
            # mark them so they are not offered as normal downloadable formats
            # (the media is unplayable without a DRM license).  When the video
            # is encrypted, its audio rendition is encrypted as well.
            is_drm = (stream.get('drm_type') or '').lower() in (
                'cenc', 'cbcs', 'widevine', 'playready', 'fairplay')
            try:
                parsed_formats, _ = self._extract_m3u8_formats_and_subtitles(
                    stream['m3u8_url'], video_id, ext='mp4',
                    entry_protocol='m3u8_native', m3u8_id=stream_type, headers=headers)
            except ExtractorError:
                # Fall back to the raw (master) playlist URL
                return [{
                    'url': stream['m3u8_url'],
                    'format_id': self.get_format_name(stream_type) or stream_type,
                    'ext': 'mp4',
                    'protocol': 'm3u8_native',
                    'filesize': int_or_none(stream.get('size')),
                    'width': int_or_none(stream.get('width')),
                    'height': int_or_none(stream.get('height')),
                    'has_drm': is_drm,
                    'http_headers': headers,
                }]
            for f in parsed_formats:
                f['http_headers'] = headers
                if f.get('vcodec') == 'none':
                    # Separate audio rendition of the master playlist
                    f['format_id'] = f'{stream_type}-audio'
                    f['has_drm'] = is_drm
                    continue
                f['format_id'] = self.get_format_name(stream_type) or stream_type
                f['filesize'] = int_or_none(stream.get('size'))
                if stream.get('width'):
                    f['width'] = int_or_none(stream.get('width'))
                if stream.get('height'):
                    f['height'] = int_or_none(stream.get('height'))
                if is_drm:
                    f['has_drm'] = True
            return parsed_formats

        formats = []
        inline_m3u8_streams = []
        for stream in data.get('stream') or []:
            if stream.get('channel_type') == 'tail':
                continue
            m3u8_url = stream.get('m3u8_url')
            if not m3u8_url:
                continue
            if m3u8_url.startswith('#EXTM3U'):
                inline_m3u8_streams.append(stream)
                continue
            formats.extend(extract_stream_formats(stream))

        # Some streams (e.g. "auto") embed a master m3u8 playlist in m3u8_url
        if not formats:
            for stream in inline_m3u8_streams:
                parsed_formats, _ = self._parse_m3u8_formats_and_subtitles(
                    stream['m3u8_url'], video_id=video_id, ext='mp4',
                    entry_protocol='m3u8_native', m3u8_id='hls', headers=headers)
                formats.extend(parsed_formats)

        # 1080p and above are only offered when the client advertises DRM
        # support.  Depending on the video, the API may return them either as
        # clear ("default") streams or as CENC/CBCS encrypted streams.  Ask for
        # them even without a recognised VIP session: some videos (and some
        # API backends) serve a clear 1080p stream to anonymous clients.
        if is_youku_tv:
            max_height = max((f.get('height') or 0) for f in formats) or 0
            if max_height < 1080:
                hd_data = (self._download_json(
                    'https://ups.youku.com/ups/get.json', video_id,
                    'Downloading high quality metadata',
                    query={**basic_data_params, 'drm_type': 163},
                    headers=api_headers, fatal=False) or {}).get('data') or {}
                if not hd_data.get('error'):
                    for stream in hd_data.get('stream') or []:
                        height = int_or_none(stream.get('height')) or 0
                        m3u8_url = stream.get('m3u8_url')
                        if height <= max_height or not m3u8_url or m3u8_url.startswith('#EXTM3U'):
                            continue
                        formats.extend(extract_stream_formats(stream))

        # Some episodes have multiple audio languages.  Youku often marks the
        # original Mandarin track as an alternate rendition (DEFAULT=NO /
        # AUTOSELECT=NO), which makes yt-dlp prefer a dub (e.g. Vietnamese) for
        # `bestaudio`.  Explicitly prefer Mandarin (guoyu), then its
        # higher-quality clear rendition (cmfa1hd).
        if is_youku_tv:
            mandarin_languages = {'guoyu', 'zh', 'zh-hans', 'zh-hant', 'cmn', 'mandarin'}
            audio_quality = {
                'cmfv4hd3': 3,
                'cmfv4hd2': 2,
                'cmfv4hd': 1,
                'cmfv4sd': 0,
            }
            for f in formats:
                if f.get('vcodec') != 'none':
                    continue
                language = (f.get('language') or '').lower()
                if language in mandarin_languages:
                    f['language_preference'] = 10
                    if not f.get('has_drm'):
                        stream_type = urllib.parse.parse_qs(
                            urllib.parse.urlparse(f.get('url') or '').query).get('type', [None])[0]
                        if stream_type in audio_quality:
                            f['quality'] = audio_quality[stream_type]
                elif language == 'default':
                    # `default` is normally the original audio, but keep it below
                    # an explicitly tagged Mandarin track.
                    f['language_preference'] = 5

        return {
            'id': video_id,
            'title': title,
            'formats': formats,
            'subtitles': self._extract_subtitles(data),
            'duration': video_data.get('seconds'),
            'thumbnail': video_data.get('logo'),
            'uploader': video_data.get('username'),
            'uploader_id': str_or_none(video_data.get('userid')),
            'uploader_url': data.get('uploader', {}).get('homepage'),
            'tags': video_data.get('tags'),
        }


class YoukuShowIE(InfoExtractor):
    _VALID_URL = r'https?://list\.youku\.com/show/id_(?P<id>[0-9a-z]+)\.html'
    IE_NAME = 'youku:show'

    _TESTS = [{
        'url': 'http://list.youku.com/show/id_zc7c670be07ff11e48b3f.html',
        'info_dict': {
            'id': 'zc7c670be07ff11e48b3f',
            'title': '花千骨 DVD版',
            'description': 'md5:a1ae6f5618571bbeb5c9821f9c81b558',
        },
        'playlist_count': 50,
    }, {
        # Episode number not starting from 1
        'url': 'http://list.youku.com/show/id_zefbfbd70efbfbd780bef.html',
        'info_dict': {
            'id': 'zefbfbd70efbfbd780bef',
            'title': '超级飞侠3',
            'description': 'md5:275715156abebe5ccc2a1992e9d56b98',
        },
        'playlist_count': 24,
    }, {
        # Ongoing playlist. The initial page is the last one
        'url': 'http://list.youku.com/show/id_za7c275ecd7b411e1a19e.html',
        'only_matching': True,
    }, {
        #  No data-id value.
        'url': 'http://list.youku.com/show/id_zefbfbd61237fefbfbdef.html',
        'only_matching': True,
    }, {
        #  Wrong number of reload_id.
        'url': 'http://list.youku.com/show/id_z20eb4acaf5c211e3b2ad.html',
        'only_matching': True,
    }]

    def _extract_entries(self, playlist_data_url, show_id, note, query):
        query['callback'] = 'cb'
        playlist_data = self._download_json(
            playlist_data_url, show_id, query=query, note=note,
            transform_source=lambda s: js_to_json(strip_jsonp(s))).get('html')
        if playlist_data is None:
            return [None, None]
        drama_list = (get_element_by_class('p-drama-grid', playlist_data)
                      or get_element_by_class('p-drama-half-row', playlist_data))
        if drama_list is None:
            raise ExtractorError('No episodes found')
        video_urls = re.findall(r'<a[^>]+href="([^"]+)"', drama_list)
        return playlist_data, [
            self.url_result(self._proto_relative_url(video_url, 'http:'), YoukuIE.ie_key())
            for video_url in video_urls]

    def _real_extract(self, url):
        show_id = self._match_id(url)
        webpage = self._download_webpage(url, show_id)

        entries = []
        page_config = self._parse_json(self._search_regex(
            r'var\s+PageConfig\s*=\s*({.+});', webpage, 'page config'),
            show_id, transform_source=js_to_json)
        first_page, initial_entries = self._extract_entries(
            'http://list.youku.com/show/module', show_id,
            note='Downloading initial playlist data page',
            query={
                'id': page_config['showid'],
                'tab': 'showInfo',
            })
        first_page_reload_id = self._html_search_regex(
            r'<div[^>]+id="(reload_\d+)', first_page, 'first page reload id')
        # The first reload_id has the same items as first_page
        reload_ids = re.findall('<li[^>]+data-id="([^"]+)">', first_page)
        entries.extend(initial_entries)
        for idx, reload_id in enumerate(reload_ids):
            if reload_id == first_page_reload_id:
                continue
            _, new_entries = self._extract_entries(
                'http://list.youku.com/show/episode', show_id,
                note=f'Downloading playlist data page {idx + 1}',
                query={
                    'id': page_config['showid'],
                    'stage': reload_id,
                })
            if new_entries is not None:
                entries.extend(new_entries)
        desc = self._html_search_meta('description', webpage, fatal=False)
        playlist_title = desc.split(',')[0] if desc else None
        detail_li = get_element_by_class('p-intro', webpage)
        playlist_description = get_element_by_class(
            'intro-more', detail_li) if detail_li else None

        return self.playlist_result(
            entries, show_id, playlist_title, playlist_description)
