"""Now Playing RPC: shows what you're listening to as a Discord "Listening to" card.

Reads Windows' media controls (the panel the volume flyout shows), so it works with
anything that reports there: YouTube / YouTube Music / SoundCloud / Deezer / Apple
Music / TIDAL in any browser or desktop app, VLC, foobar2000, the Juice WRLD Vault, ...
Spotify is skipped: Discord shows it with its own card. Album art comes from public
lookups (iTunes, Deezer, SoundCloud, YouTube thumbnails), so nothing is uploaded anywhere.
"""
import asyncio
import ctypes
import ctypes.wintypes as wt
import difflib
import html
import json
import os
import re
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import winreg

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
)

APP_NAME = 'Now Playing RPC'
VERSION = '1.0.0'
DEFAULT_CLIENT_ID = '1565535184949084321'  # the "Now Playing" Discord app; override in config.json
ICON_BASE = 'https://raw.githubusercontent.com/revivechris/np-images/main/assets/icons/'
UA = {'User-Agent': f'NowPlayingRPC/{VERSION} (+https://github.com/revivechris/np-images)'}
CONFIG_DIR = os.path.join(os.environ.get('APPDATA', '.'), 'NowPlayingRPC')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
LOG_PATH = os.path.join(CONFIG_DIR, 'log.txt')
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def log(msg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512_000:
            os.replace(LOG_PATH, LOG_PATH + '.old')
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + msg + '\n')
    except OSError:
        pass

DEFAULT_CONFIG = {
    'client_id': DEFAULT_CLIENT_ID,
    'show_paused': True,           # keep the card with a "Paused" label instead of hiding it
    'paused_timeout_min': 10,      # hide a paused card after this long
    'ignore_apps': [],             # substrings of app ids / platform names to skip, e.g. ["vlc"]
    'buttons': True,               # "Open song" button
}

# ------------------------------------------------------------------ config

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)


# ------------------------------------------------------------------ platforms

# (key, display name, matcher on the media session's app id)
APP_PLATFORMS = [
    ('spotify', 'Spotify', 'spotify'),
    ('youtube_music', 'YouTube Music', 'youtube-music'),
    ('youtube_music', 'YouTube Music', 'youtubemusic'),
    ('apple_music', 'Apple Music', 'applemusic'),
    ('apple_music', 'Apple Music', 'itunes'),
    ('tidal', 'TIDAL', 'tidal'),
    ('deezer', 'Deezer', 'deezer'),
    ('amazon_music', 'Amazon Music', 'amazon music'),
    ('amazon_music', 'Amazon Music', 'amazonmusic'),
    ('soundcloud', 'SoundCloud', 'soundcloud'),
    ('juicewrld_vault', 'Juice WRLD Vault', 'juicewrldvault'),
    ('vlc', 'VLC', 'vlc'),
    ('foobar', 'foobar2000', 'foobar'),
    ('musicbee', 'MusicBee', 'musicbee'),
    ('media_player', 'Media Player', 'zunemusic'),
]

BROWSER_EXES = {'chrome.exe', 'msedge.exe', 'firefox.exe', 'opera.exe', 'brave.exe', 'vivaldi.exe',
                'librewolf.exe', 'waterfox.exe', 'floorp.exe', 'zen.exe', 'arc.exe', 'thorium.exe'}
BROWSER_AUMIDS = ('chrome', 'msedge', 'firefox', '308046b0af4a39cb', 'opera', 'brave', 'vivaldi',
                  'helium', 'librewolf', 'waterfox', 'floorp', 'zen', 'arc', 'thorium')

# (key, display name, regex on a browser tab title)
SITE_TITLES = [
    ('youtube_music', 'YouTube Music', r'YouTube Music'),
    ('youtube', 'YouTube', r' - YouTube\b'),
    ('soundcloud', 'SoundCloud', r'SoundCloud'),
    ('spotify', 'Spotify', r'Spotify'),
    ('deezer', 'Deezer', r'Deezer'),
    ('apple_music', 'Apple Music', r'Apple Music'),
    ('tidal', 'TIDAL', r'TIDAL'),
    ('bandcamp', 'Bandcamp', r'Bandcamp|bandcamp'),
    ('amazon_music', 'Amazon Music', r'Amazon Music'),
    ('twitch', 'Twitch', r'Twitch'),
]

# Discord already shows Spotify with its own built-in card.
SKIP_PLATFORMS = {'spotify'}

# Sources whose "artist" is often just the uploader and whose titles carry
# "Artist - Song (Official Video)": the card shows the cleaned-up song instead.
VIDEO_SITES = {'youtube', 'youtube_music', 'soundcloud', 'twitch', 'music'}

PLATFORM_ICON = {
    'youtube_music', 'youtube', 'soundcloud', 'apple_music', 'tidal', 'deezer',
    'amazon_music', 'bandcamp', 'juicewrld_vault', 'twitch', 'music',
}


def window_titles():
    """(exe name, title) of every visible top-level window."""
    user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    out = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ''
        h = k32.OpenProcess(0x1000, False, pid.value)
        if h:
            pbuf = ctypes.create_unicode_buffer(520)
            size = wt.DWORD(520)
            if k32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                exe = os.path.basename(pbuf.value).lower()
            k32.CloseHandle(h)
        out.append((exe, buf.value))
        return True

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(proto(cb), 0)
    return out


BROWSER_NAMES = {'308046b0af4a39cb': 'Firefox', 'msedge': 'Edge', 'chrome': 'Chrome'}


def browser_name(aumid):
    """"Helium.4RKE..." -> "Helium", Firefox's id -> "Firefox"."""
    a = (aumid or '').lower()
    for k, v in BROWSER_NAMES.items():
        if k in a:
            return v
    first = re.split(r'[.!_\s]', aumid or '')[0]
    return first[:1].upper() + first[1:] if first else 'Browser'


def detect_platform(aumid, title, artist=''):
    """Which service is playing: (key, display name)."""
    a = (aumid or '').lower()
    for key, name, pat in APP_PLATFORMS:
        if pat in a:
            return key, name
    if any(b in a for b in BROWSER_AUMIDS):
        # The media session doesn't say which site; the browser tab title does
        # (only the active tab's, per window). Prefer the tab naming the song.
        tabs = [t for exe, t in window_titles() if exe in BROWSER_EXES]
        want = (title or '').lower()[:40]
        song_tabs = [t for t in tabs if want and want in t.lower()]
        for pool in (song_tabs, tabs):
            for t in pool:
                for key, name, pat in SITE_TITLES:
                    if re.search(pat, t):
                        return key, name
        # SoundCloud retitles its tab to just "<song> by <artist>" while playing
        if artist and any(f' by {artist.lower()}' in t.lower() for t in song_tabs):
            return 'soundcloud', 'SoundCloud'
        return 'music', browser_name(aumid)
    return 'music', 'Music'


# ------------------------------------------------------------------ art lookup

def http_json(url, timeout=6):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def http_text(url, timeout=6, limit=1_500_000):
    with urllib.request.urlopen(urllib.request.Request(url, headers={**UA, 'Accept-Language': 'en'}), timeout=timeout) as r:
        return r.read(limit).decode('utf-8', 'replace')


NOISE = re.compile(r'\s*[\[(](?:official|lyric|lyrics|audio|video|music video|visualizer|hd|hq|4k|explicit|clean|'
                   r'remaster(?:ed)?(?: \d{4})?|prod\.?[^)\]]*|feat\.?[^)\]]*|ft\.?[^)\]]*|with [^)\]]*)[^)\]]*[\])]', re.I)


def clean_song(artist, title, video=False, lookup=True):
    """YouTube-style "Artist - Song (Official Video)" by "ArtistVEVO" -> ("Artist", "Song").
    video: the title comes from a video site, where "X - Y" means artist - song even
    when the uploader's channel is someone else. lookup: also drop trailing tags like
    "(Remix)" / "(unreleased)" so album searches match; the card keeps them."""
    artist = re.sub(r'\s*(VEVO|- Topic|Official)$', '', artist or '', flags=re.I).strip()
    title = NOISE.sub('', title or '').strip()
    m = re.match(r'^(.+?)\s+[-–—]\s+(.+)$', title)
    if m and (video or not artist or artist.lower() in m.group(1).lower() or m.group(1).lower() in artist.lower()
              or len(artist) < 2):
        artist, title = m.group(1).strip(), m.group(2).strip()
    if lookup:
        title = re.sub(r'\s*[\[(][^\])]*[\])]\s*$', '', title).strip() or title
    return artist, title


def similar(a, b):
    return difflib.SequenceMatcher(None, (a or '').lower(), (b or '').lower()).ratio()


def itunes_art(artist, title):
    q = urllib.parse.quote(f'{artist} {title}')
    for hit in http_json(f'https://itunes.apple.com/search?term={q}&entity=song&limit=8').get('results', []):
        if similar(hit.get('artistName'), artist) > 0.6 and similar(hit.get('trackName'), title) > 0.5:
            art = hit.get('artworkUrl100', '').replace('100x100bb', '600x600bb')
            return art, hit.get('collectionName', '')
    return '', ''


def deezer_art(artist, title):
    q = urllib.parse.quote(f'{artist} {title}')  # Deezer's artist:"" track:"" syntax finds nothing
    for hit in http_json(f'https://api.deezer.com/search?q={q}&limit=8').get('data', []):
        if similar(hit.get('artist', {}).get('name'), artist) > 0.6 and similar(hit.get('title'), title) > 0.5:
            return hit.get('album', {}).get('cover_xl', ''), hit.get('album', {}).get('title', '')
    return '', ''


def youtube_video(query):
    """First YouTube search result for the query: (video id, thumbnail url)."""
    page = http_text('https://www.youtube.com/results?search_query=' + urllib.parse.quote(query))
    m = re.search(r'"videoId":"([\w-]{11})"', page)
    if not m:
        return '', ''
    vid = m.group(1)
    return vid, f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'


def soundcloud_art(artist, title):
    """The track's own artwork: SoundCloud's search page lists track links, and its
    oEmbed gives each one's uploader + artwork. Only a match on the uploader counts."""
    page = http_text('https://soundcloud.com/search/sounds?q=' + urllib.parse.quote(f'{artist} {title}'))
    for path, _ in re.findall(r'<li><h2><a href="(/[^"/]+/[^"/]+)">([^<]+)</a>', page)[:6]:
        url = 'https://soundcloud.com' + path
        o = http_json('https://soundcloud.com/oembed?format=json&url=' + urllib.parse.quote(url))
        if similar(o.get('author_name'), artist) > 0.7 and o.get('thumbnail_url'):
            return o['thumbnail_url'], url
    return '', ''


def vault_art(title):
    """The Juice WRLD Vault's own cover for the playing song: its queue state in
    localStorage names the song id, and the public song page has the og:image."""
    import glob
    d = os.path.join(os.environ.get('APPDATA', ''), 'JuiceWRLDVault', 'Local Storage', 'leveldb')
    best = None
    for f in glob.glob(os.path.join(d, '*.log')) + glob.glob(os.path.join(d, '*.ldb')):
        try:
            with open(f, 'rb') as fh:
                t = fh.read().decode('utf-8', 'replace')
        except OSError:
            continue
        for m in re.finditer(r'vault-player-state.{0,8}?\{"activeId":"(\d+)","ids":\[([^\]]*)\].*?"savedAt":(\d+)', t, re.S):
            if best is None or int(m.group(3)) > best[2]:
                best = (m.group(1), re.findall(r'"(\d+)"', m.group(2)), int(m.group(3)))
    if not best:
        return '', ''
    active, ids, _ = best
    near = ids[ids.index(active):ids.index(active) + 2] if active in ids else [active]
    for sid in near:  # the saved id can lag one song behind
        page = http_text(f'https://juicewrldvault.com/song/{sid}', limit=400_000)
        img = re.search(r'<meta property="og:image" content="([^"]+)"', page)
        name = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        if img and name and title.lower() in html.unescape(name.group(1)).lower():
            return html.unescape(img.group(1)), f'https://juicewrldvault.com/song/{sid}'
    return '', ''


class Art:
    """Cover + link for a song, looked up once per song in a background thread."""

    def __init__(self, on_ready):
        self.cache = {}
        self.on_ready = on_ready
        self.lock = threading.Lock()

    def get(self, song):
        k = (song['platform'], song['artist'], song['title'])
        with self.lock:
            if k in self.cache:
                return self.cache[k]
            self.cache[k] = None
        threading.Thread(target=self._lookup, args=(k, song), daemon=True).start()
        return None

    def _lookup(self, k, song):
        res = {'art': '', 'album': song.get('album') or '', 'link': ''}
        artist, title = clean_song(song['artist'], song['title'], song['platform'] in VIDEO_SITES)
        plat = song['platform']
        try:
            if plat == 'juicewrld_vault':
                for _ in range(10):
                    res['art'], res['link'] = vault_art(song['title'])
                    if res['art']:
                        break
                    time.sleep(3)
            if plat == 'soundcloud':
                try:
                    res['art'], res['link'] = soundcloud_art(song['artist'], song['title'])
                except Exception:
                    pass
            if not res['art']:
                for fn in (itunes_art, deezer_art):
                    try:
                        art, album = fn(artist, title)
                    except Exception:
                        continue
                    if art:
                        res['art'], res['album'] = art, res['album'] or album
                        break
            if plat in ('youtube', 'youtube_music', 'music') and (not res['art'] or not res['link']):
                vid, thumb = youtube_video(f'{song["artist"]} {song["title"]}')
                if vid:
                    host = 'music.youtube.com' if plat == 'youtube_music' else 'www.youtube.com'
                    res['link'] = res['link'] or f'https://{host}/watch?v={vid}'
                    res['art'] = res['art'] or thumb
        except Exception:
            pass
        if not res['link']:
            res['link'] = search_link(plat, artist, title)
        with self.lock:
            self.cache[k] = res
        self.on_ready()


def search_link(plat, artist, title):
    q = urllib.parse.quote(f'{artist} {title}'.strip())
    return {
        'soundcloud': f'https://soundcloud.com/search?q={q}',
        'deezer': f'https://www.deezer.com/search/{q}',
        'apple_music': f'https://music.apple.com/search?term={q}',
        'tidal': f'https://listen.tidal.com/search?q={q}',
        'youtube': f'https://www.youtube.com/results?search_query={q}',
    }.get(plat, f'https://music.youtube.com/search?q={q}')


# ------------------------------------------------------------------ Discord IPC

class Discord:
    """Minimal Discord RPC over the local named pipe (no extra libraries)."""

    def __init__(self, client_id):
        self.client_id = client_id
        self.pipe = None
        self.next_try = 0.0

    def connect(self):
        # Discord closed / not running: try again every 15 s, not every tick
        if time.time() < self.next_try:
            return False
        self.next_try = time.time() + 15
        for i in range(10):
            try:
                self.pipe = open(rf'\\.\pipe\discord-ipc-{i}', 'r+b', buffering=0)
                break
            except OSError:
                continue
        if not self.pipe:
            return False
        try:
            self._send(0, {'v': 1, 'client_id': self.client_id})
            op, reply = self._recv()
            if op == 2 or reply.get('evt') == 'ERROR':  # 2 = Discord closed it (e.g. bad client id)
                log(f'discord handshake failed: {reply}')
                self.close()
                return False
            return True
        except OSError:
            self.close()
            return False

    def _send(self, op, payload):
        data = json.dumps(payload).encode('utf-8')
        self.pipe.write(struct.pack('<II', op, len(data)) + data)

    def _recv(self):
        head = self.pipe.read(8)
        if len(head) < 8:
            raise OSError('discord closed the pipe')
        op, n = struct.unpack('<II', head)
        return op, json.loads(self.pipe.read(n) or b'{}')

    def set_activity(self, activity):
        if not self.pipe and not self.connect():
            return False
        try:
            self._send(1, {'cmd': 'SET_ACTIVITY', 'args': {'pid': os.getpid(), 'activity': activity},
                           'nonce': str(uuid.uuid4())})
            _, reply = self._recv()
            if reply.get('evt') == 'ERROR':
                log(f"discord refused the card: {reply.get('data')}")
                return False
            return True
        except OSError:
            self.close()
            return False

    def close(self):
        try:
            if self.pipe:
                self.pipe.close()
        except OSError:
            pass
        self.pipe = None


# ------------------------------------------------------------------ media watcher

class Watcher:
    def __init__(self):
        self.cfg = load_config()
        self.discord = Discord(self.cfg['client_id'])
        self.art = Art(self.wake)
        self.enabled = True
        self.status_text = 'Starting...'
        self.dirty = threading.Event()
        self.last_sent = None
        self.last_sent_at = 0.0
        self.paused_since = None
        self.on_status = lambda text: None

    def wake(self):
        self.dirty.set()

    async def current_song(self, mgr):
        """The session to show: a playing one first (the most recently started), else a paused one."""
        best = None
        for s in mgr.get_sessions():
            aumid = s.source_app_user_model_id or ''
            status = s.get_playback_info().playback_status
            if status not in (Status.PLAYING, Status.PAUSED):
                continue
            p = await s.try_get_media_properties_async()
            if not p or not p.title:
                continue
            key, name = detect_platform(aumid, p.title, p.artist)
            if key in SKIP_PLATFORMS:
                continue
            ignore = [x.lower() for x in self.cfg.get('ignore_apps', [])]
            if any(x in aumid.lower() or x in name.lower() for x in ignore):
                continue
            t = s.get_timeline_properties()
            song = {
                'aumid': aumid, 'platform': key, 'platform_name': name,
                'title': p.title, 'artist': p.artist or '', 'album': p.album_title or '',
                'playing': status == Status.PLAYING,
                'position': t.position.total_seconds() if t else 0,
                'duration': (t.end_time - t.start_time).total_seconds() if t else 0,
                'updated': t.last_updated_time.timestamp() if t and t.last_updated_time.year > 2000 else time.time(),
            }
            if best is None or (song['playing'] and not best['playing']):
                best = song
        return best

    def activity(self, song):
        res = self.art.get(song) or {}
        if song['platform'] in VIDEO_SITES:
            artist, title = clean_song(song['artist'], song['title'], True, lookup=False)
        else:
            artist, title = song['artist'], song['title']
        artist = artist or song['platform_name']
        icon = song['platform'] if song['platform'] in PLATFORM_ICON else 'music'
        icon_url = ICON_BASE + icon + '.png'
        a = {
            'type': 2,  # Listening
            'name': song['platform_name'],
            'details': title[:128],
            'state': (artist if song['playing'] else f'⏸ Paused · {artist}')[:128],
            'assets': {
                'large_image': res.get('art') or icon_url,
                'small_image': icon_url,
                'small_text': song['platform_name'],
            },
        }
        if res.get('album'):  # Discord shows this as the third line; leave it off rather than repeat the title
            a['assets']['large_text'] = res['album'][:128]
        if song['playing'] and song['duration'] > 1:
            pos = song['position'] + (time.time() - song['updated'])
            start = time.time() - min(pos, song['duration'])
            a['timestamps'] = {'start': int(start * 1000), 'end': int((start + song['duration']) * 1000)}
        if self.cfg.get('buttons', True) and res.get('link'):
            a['buttons'] = [{'label': 'Open song', 'url': res['link']}]
        return a

    def push(self, activity):
        # Discord takes about 5 updates per 20 s; space them and skip repeats
        # (progress bars ignore drift under 3 s).
        def comparable(x):
            if not x:
                return x
            y = dict(x)
            if 'timestamps' in y:
                y['timestamps'] = {k: v // 3000 for k, v in y['timestamps'].items()}
            return json.dumps(y, sort_keys=True)
        if comparable(activity) == comparable(self.last_sent):
            return
        wait = 4.0 - (time.time() - self.last_sent_at)
        if wait > 0:
            time.sleep(wait)
        ok = self.discord.set_activity(activity)
        if ok:
            self.last_sent, self.last_sent_at = activity, time.time()
            log('card: ' + (f"{activity['name']} | {activity['details']} | {activity['state']} | art={activity['assets']['large_image']}"
                            if activity else 'cleared'))

    async def run(self):
        log(f'{APP_NAME} {VERSION} started')
        while True:
            try:
                await self.loop()
            except Exception as e:  # keep going; a media app or Discord hiccup shouldn't kill it
                log(f'error: {e!r}')
                await asyncio.sleep(3)

    async def loop(self):
        mgr = await SessionManager.request_async()
        while True:
            self.dirty.clear()
            song = await self.current_song(mgr) if self.enabled else None
            if song and not song['playing']:
                self.paused_since = self.paused_since or time.time()
                timeout = self.cfg.get('paused_timeout_min', 10) * 60
                if not self.cfg.get('show_paused', True) or time.time() - self.paused_since > timeout:
                    song = None
            else:
                self.paused_since = None
            if song:
                self.push(self.activity(song))
                self.set_status(f"{'▶' if song['playing'] else '⏸'} {song['title']} — {song['artist']} ({song['platform_name']})")
            else:
                self.push(None)
                self.set_status('Paused sharing' if not self.enabled else 'Nothing playing')
            await asyncio.get_running_loop().run_in_executor(None, self.dirty.wait, 1.0)

    def set_status(self, text):
        if text != self.status_text:
            self.status_text = text
            self.on_status(text)


# ------------------------------------------------------------------ tray + autostart

def exe_command():
    if getattr(sys, 'frozen', False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, exe_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


def tray_image():
    from PIL import Image, ImageDraw
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=(88, 101, 242, 255))
    d.rectangle((36, 14, 41, 44), fill='white')                 # note stem
    d.polygon([(41, 14), (52, 20), (52, 26), (41, 21)], fill='white')  # flag
    d.ellipse((22, 36, 42, 52), fill='white')                   # note head
    return img


def single_instance():
    ctypes.windll.kernel32.CreateMutexW(None, False, 'NowPlayingRPC.single')
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def main():
    if not single_instance():
        return
    first_run = not os.path.exists(CONFIG_PATH)
    if first_run:
        save_config(load_config())
        try:
            set_autostart(True)
        except OSError:
            pass

    import pystray
    watcher = Watcher()
    threading.Thread(target=lambda: asyncio.run(watcher.run()), daemon=True).start()

    def toggle_sharing(icon, item):
        watcher.enabled = not watcher.enabled
        watcher.wake()

    def toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    def open_config(icon, item):
        os.startfile(CONFIG_DIR)

    def quit_app(icon, item):
        watcher.discord.close()
        icon.stop()

    icon = pystray.Icon('NowPlayingRPC', tray_image(), APP_NAME, menu=pystray.Menu(
        pystray.MenuItem(lambda item: watcher.status_text[:100], None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Share on Discord', toggle_sharing, checked=lambda item: watcher.enabled),
        pystray.MenuItem('Start with Windows', toggle_autostart, checked=lambda item: autostart_enabled()),
        pystray.MenuItem('Open settings folder', open_config),
        pystray.MenuItem(f'Quit (v{VERSION})', quit_app),
    ))

    def on_status(text):
        icon.title = f'{APP_NAME}\n{text}'[:127]
        icon.update_menu()
    watcher.on_status = on_status
    icon.run()


if __name__ == '__main__':
    main()
