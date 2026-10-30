# Now Playing RPC

Show what you're listening to on Discord, from **any** music app or site: YouTube, YouTube Music, SoundCloud, Apple Music, Deezer, TIDAL, Amazon Music, Bandcamp, the Juice WRLD Vault, VLC, foobar2000, and anything else that shows up in Windows' media controls.

Spotify is left out on purpose: Discord already shows it with its own card (connect it under Settings → Connections).

- 🎵 **"Listening to YouTube Music"** style card with the song, artist and **album art**
- ⏱️ **Progress bar** (elapsed / total) when the player reports the song length
- 🔗 **Open song** button (YouTube video, or a search on the platform)
- 🏷️ **Platform icon** in the corner
- ⏸️ Shows **Paused** while paused (hides after 10 minutes)
- 🪶 Runs in the **system tray**, starts with Windows, no window, no account, no setup

<!-- screenshot: assets/screenshot.png -->

## Download

Grab **`NowPlayingRPC.exe`** from the [latest release](https://github.com/revivechris/np-images/releases/latest) and run it. That's it.

- It sits in the tray (the blue music note). Right-click it for: what's playing now, **Share on Discord** on/off, **Start with Windows** on/off, settings folder, quit.
- The first run turns on "Start with Windows". Untick it in the tray menu if you don't want that.
- Discord has to be running (the desktop app, not the browser), and **Settings → Activity Privacy → Share your detected activities with others** must be on.

Windows SmartScreen may warn about an unknown app the first time (the exe isn't code-signed): **More info → Run anyway**.

## How it works

Windows keeps a list of what's playing (the media panel above the volume flyout). Every app and browser tab that plays media reports there, so the app reads that list once a second and turns the playing song into a Discord card:

| What | Where it comes from |
|---|---|
| Song, artist, play position | Windows media controls |
| Which platform (for browsers) | the browser tab's title, e.g. `... - YouTube Music` |
| Album art | iTunes Search, then Deezer, then the YouTube video thumbnail (YouTube / YouTube Music) |
| SoundCloud art | the track's own artwork |
| Juice WRLD Vault art | the Vault's own cover for the playing song |
| Open song link | the YouTube video / SoundCloud track, or a search on that platform |

Art is only ever a public link; nothing is uploaded anywhere and no account or key is needed.

**Browsers:** Chrome, Edge, Firefox, Opera / Opera GX, Brave, Vivaldi, Arc, Helium, LibreWolf, Zen and other Chromium/Firefox browsers. In Firefox, media keys support must be on (default; `media.hardwaremediakeys.enabled` in `about:config`).

## Settings

Tray menu → **Open settings folder** → `config.json`:

```json
{
  "client_id": "…",
  "show_paused": true,
  "paused_timeout_min": 10,
  "ignore_apps": [],
  "buttons": true
}
```

| Key | What it does |
|---|---|
| `client_id` | Discord application to show the card as. Leave it, or use your own from [discord.com/developers](https://discord.com/developers/applications). |
| `show_paused` | `false` hides the card as soon as you pause. |
| `paused_timeout_min` | Minutes a paused card stays before it's hidden. |
| `ignore_apps` | Skip apps or platforms, e.g. `["vlc", "YouTube"]` (matched against the platform name and the app id). |
| `buttons` | `false` removes the "Open song" button. |

Restart the app after editing. A log is kept next to it in `log.txt`.

## Build from source

Needs Windows 10/11 and Python 3.10+.

```bat
pip install -r requirements.txt
python nowplaying_rpc.py          :: run without building
build.bat                         :: builds dist\NowPlayingRPC.exe
```

`tools/make_icons.py` regenerates the tray/app icon and normalizes the platform icons in `assets/icons/` (the card loads those icons from this repo).

## Notes

- Discord accepts about 5 card updates per 20 seconds, so a burst of skips settles within a few seconds.
- Song detection is only as good as what the player reports. Some sites send the uploader as the artist; the app strips common noise (`VEVO`, `- Topic`, `(Official Video)` …) before looking up art.
- Not affiliated with Discord or any of the platforms; their names and icons belong to their owners.

## Repo layout

| Path | What |
|---|---|
| `nowplaying_rpc.py` | the whole app |
| `assets/icons/` | platform icons the Discord card loads (keep this repo public) |
| `assets/app.ico` | tray / exe icon |
| `tools/make_icons.py` | regenerates the icons |
| `np/` | unrelated: images used by a separate project |

## License

MIT, see [LICENSE](LICENSE).
