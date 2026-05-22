#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║             CSV PLAYLIST MUSIC DOWNLOADER                ║
║    Metadata from CSV · Audio from YT/YTMusic/SC          ║
╚══════════════════════════════════════════════════════════╝

Termux setup (run once):
    pkg update && pkg upgrade
    pkg install ffmpeg python
    pip install yt-dlp mutagen requests lyricsgenius colorama
    termux-setup-storage   ← to access /storage/emulated/0

Compatible with Exportify CSV exports.
Genius API (optional, lyrics): https://genius.com/api-clients
"""

import os, sys, json, time, shutil, threading, subprocess, csv
from pathlib import Path

# ─── Dependency check ────────────────────────────────────────────────────────

REQUIRED_PACKAGES = {
    "yt_dlp":       "yt-dlp",
    "mutagen":      "mutagen",
    "requests":     "requests",
    "lyricsgenius": "lyricsgenius",
    "colorama":     "colorama",
}

def check_and_install_deps():
    missing = []
    for module, package in REQUIRED_PACKAGES.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print(f"\n[!] Missing packages: {', '.join(missing)}")
        ans = input("    Install them now? [Y/n]: ").strip().lower()
        if ans in ("", "y", "yes"):
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
            print("    Packages installed. Restarting...\n")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            print("    Cannot continue without required packages.")
            sys.exit(1)

check_and_install_deps()

# ─── Imports ─────────────────────────────────────────────────────────────────

import yt_dlp
from mutagen.id3 import (ID3, TIT2, TPE1, TPE2, TALB, TDRC,
                          TRCK, TPOS, TCON, USLT, APIC, error as ID3Error)
from mutagen.flac import FLAC, Picture
import requests
import colorama
from colorama import Fore, Style

colorama.init(autoreset=True)

# ─── ffmpeg: Termux puts it in PATH, just verify ─────────────────────────────

FFMPEG_EXE = shutil.which("ffmpeg")

# ─── Source constants ─────────────────────────────────────────────────────────

SOURCE_YTM = "ytmusic"
SOURCE_YT  = "youtube"
SOURCE_SC  = "soundcloud"

SOURCE_NAMES = {
    SOURCE_YTM: "YouTube Music",
    SOURCE_YT:  "YouTube",
    SOURCE_SC:  "SoundCloud",
}

# ─── Config ──────────────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".csv_music_dl_config.json"

def _default_download_dir():
    shared = Path("/storage/emulated/0/Music/Downloads")
    if Path("/storage/emulated/0").exists():
        return str(shared)
    return str(Path.home() / "Music" / "Downloads")

DEFAULT_CONFIG = {
    "genius_token":           "",
    "source_priority":        [SOURCE_YTM, SOURCE_YT, SOURCE_SC],
    "download_dir":           _default_download_dir(),
    "audio_format":           "mp3",
    "audio_quality":          "320",
    "filename_template":      "{artist} - {title}",
    "add_track_numbers":      True,
    "create_playlist_folder": True,
    "skip_existing":          True,
    "max_retries":            3,
    "concurrent_downloads":   1,
    "embed_lyrics":           True,
    "save_lrc_file":          False,
    "embed_artwork":          True,
    "duration_tolerance_s":   10,
}

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── UI helpers ───────────────────────────────────────────────────────────────

def sanitize(name):
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()

def print_banner():
    os.system("clear")
    print(f"""
{Fore.GREEN}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   {Fore.WHITE}█▀ █▀█ █▀█ ▀█▀ █   █▀▄ █░░{Fore.GREEN}                              ║
║   {Fore.WHITE}▄█ █▀▀ █▄█ ░█░ █   █▄▀ █▄▄{Fore.GREEN}                              ║
║                                                              ║
║   {Fore.CYAN}Exportify CSV → YT Music / YouTube / SoundCloud{Fore.GREEN}        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{Style.RESET_ALL}
""")

def c(text, color=Fore.WHITE, bold=False):
    return f"{Style.BRIGHT if bold else ''}{color}{text}{Style.RESET_ALL}"

def source_label(src):
    colors = {SOURCE_YTM: Fore.RED, SOURCE_YT: Fore.RED, SOURCE_SC: Fore.YELLOW}
    return f"{colors.get(src, Fore.WHITE)}{SOURCE_NAMES.get(src, src)}{Style.RESET_ALL}"

def prompt(text, default=None):
    hint = f" [{default}]" if default is not None else ""
    val = input(f"  {Fore.CYAN}→{Style.RESET_ALL} {text}{hint}: ").strip()
    return val if val else default

def ok(msg):       print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {msg}")
def warn(msg):     print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL} {msg}")
def err(msg):      print(f"  {Fore.RED}✗{Style.RESET_ALL} {msg}")
def info(msg):     print(f"  {Fore.CYAN}·{Style.RESET_ALL} {msg}")
def press_enter(): input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")

# ─── CSV Parser (Exportify optimized) ────────────────────────────────────────

def parse_csv(file_path):
    """Parse an Exportify (or generic) CSV into a list of track dicts."""
    playlist_name = file_path.stem
    tracks = []

    with open(file_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            reader.fieldnames = [h.lower().strip() for h in reader.fieldnames]

        for idx, row in enumerate(reader, 1):
            title = (row.get("track name") or row.get("title") or
                     row.get("track") or row.get("name") or "").strip()
            artists_all = (row.get("artist name(s)") or row.get("artist name") or
                           row.get("artist") or "").strip()
            if not title or not artists_all:
                continue

            album       = (row.get("album name") or row.get("album") or "Unknown Album").strip()
            album_artist = (row.get("album artist name(s)") or row.get("album artist") or
                            row.get("album_artist") or artists_all).strip()

            release_date = (row.get("album release date") or row.get("year") or
                            row.get("date") or "").strip()
            year = release_date[:4] if release_date else ""

            track_num = row.get("track number") or row.get("track_number") or idx
            disc_num  = row.get("disc number")  or row.get("disc_number")  or "1"

            genres_raw = (row.get("artist genres") or row.get("genre") or
                          row.get("genres") or "").strip()
            genres = [g.strip() for g in genres_raw.split(",")] if genres_raw else []

            artwork_url = (row.get("album image url") or row.get("artwork_url") or
                           row.get("artwork") or "").strip()

            duration_raw = (row.get("track duration (ms)") or row.get("duration_ms") or
                            row.get("duration") or "0")
            try:
                duration_ms = int(duration_raw)
                if 0 < duration_ms < 1000:   # raw seconds fallback
                    duration_ms *= 1000
            except (ValueError, TypeError):
                duration_ms = 0

            primary_artist = artists_all.split(",")[0].strip()

            tracks.append({
                "title":        title,
                "artist":       primary_artist,
                "artists":      artists_all,
                "album":        album,
                "album_artist": album_artist,
                "year":         year,
                "track_number": int(track_num) if str(track_num).isdigit() else idx,
                "disc_number":  int(disc_num)  if str(disc_num).isdigit()  else 1,
                "genres":       genres,
                "duration_ms":  duration_ms,
                "artwork_url":  artwork_url if artwork_url.startswith("http") else None,
                "isrc":         row.get("isrc", "").strip(),
            })

    return playlist_name, tracks

# ─── Search queries per source ────────────────────────────────────────────────

SOURCE_QUERY = {
    SOURCE_YTM: lambda t: f"ytmsearch1:{t['artist']} {t['title']}",
    SOURCE_YT:  lambda t: f"ytsearch1:{t['artist']} - {t['title']} audio",
    SOURCE_SC:  lambda t: f"scsearch1:{t['artist']} {t['title']}",
}

# ─── Lyrics ───────────────────────────────────────────────────────────────────

_genius_client = None

def get_lyrics(title, artist, cfg):
    global _genius_client
    if not (cfg.get("embed_lyrics") or cfg.get("save_lrc_file")):
        return ""
    if not cfg.get("genius_token"):
        return ""
    try:
        if _genius_client is None:
            import lyricsgenius
            _genius_client = lyricsgenius.Genius(
                cfg["genius_token"], verbose=False,
                remove_section_headers=True, skip_non_songs=True,
            )
        song = _genius_client.search_song(title, artist, get_full_info=False)
        return song.lyrics if song else ""
    except Exception as e:
        warn(f"Lyrics: {e}")
        return ""

# ─── Artwork ─────────────────────────────────────────────────────────────────

def fetch_artwork(url):
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception:
        return None

# ─── Metadata embedding ───────────────────────────────────────────────────────

def embed_mp3(path, track, lyrics, artwork):
    try:
        try:    tags = ID3(path)
        except: tags = ID3()
        tags.delall("TIT2"); tags.add(TIT2(encoding=3, text=track["title"]))
        tags.delall("TPE1"); tags.add(TPE1(encoding=3, text=track["artists"]))
        tags.delall("TPE2"); tags.add(TPE2(encoding=3, text=track["album_artist"]))
        tags.delall("TALB"); tags.add(TALB(encoding=3, text=track["album"]))
        if track["year"]:
            tags.delall("TDRC"); tags.add(TDRC(encoding=3, text=track["year"]))
        tags.delall("TRCK"); tags.add(TRCK(encoding=3, text=str(track["track_number"])))
        tags.delall("TPOS"); tags.add(TPOS(encoding=3, text=str(track["disc_number"])))
        if track.get("genres"):
            tags.delall("TCON"); tags.add(TCON(encoding=3, text=", ".join(track["genres"])))
        if lyrics:
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        if artwork:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=artwork))
        tags.save(path, v2_version=3)
    except Exception as e:
        warn(f"MP3 tag error: {e}")

def embed_flac(path, track, lyrics, artwork):
    try:
        audio = FLAC(path)
        audio["title"]       = track["title"]
        audio["artist"]      = track["artists"]
        audio["albumartist"] = track["album_artist"]
        audio["album"]       = track["album"]
        audio["date"]        = track["year"]
        audio["tracknumber"] = str(track["track_number"])
        audio["discnumber"]  = str(track["disc_number"])
        if track.get("genres"): audio["genre"] = ", ".join(track["genres"])
        if lyrics: audio["lyrics"] = lyrics
        if artwork:
            pic = Picture()
            pic.type = 3; pic.mime = "image/jpeg"; pic.desc = "Cover"; pic.data = artwork
            audio.clear_pictures(); audio.add_picture(pic)
        audio.save()
    except Exception as e:
        warn(f"FLAC tag error: {e}")

def embed_m4a(path, track, lyrics, artwork):
    try:
        from mutagen.mp4 import MP4, MP4Cover
        audio = MP4(path)
        audio["\xa9nam"] = [track["title"]]
        audio["\xa9ART"] = [track["artists"]]
        audio["aART"]    = [track["album_artist"]]
        audio["\xa9alb"] = [track["album"]]
        if track["year"]: audio["\xa9day"] = [track["year"]]
        audio["trkn"] = [(track["track_number"], 0)]
        if lyrics: audio["\xa9lyr"] = [lyrics]
        if artwork: audio["covr"] = [MP4Cover(artwork, MP4Cover.FORMAT_JPEG)]
        audio.save()
    except Exception as e:
        warn(f"M4A tag error: {e}")

def embed_metadata(path, track, lyrics, artwork, fmt):
    if   fmt == "mp3":  embed_mp3(path, track, lyrics, artwork)
    elif fmt == "flac": embed_flac(path, track, lyrics, artwork)
    else:               embed_m4a(path, track, lyrics, artwork)

# ─── Core downloader ──────────────────────────────────────────────────────────

FORMAT_CODEC = {"mp3": "mp3", "flac": "flac", "m4a": "m4a", "opus": "opus"}

def _duration_ok(info_dict, expected_ms, tol_s):
    if not expected_ms or tol_s <= 0:
        return True
    got = info_dict.get("duration") or 0
    return abs(got - expected_ms / 1000) <= tol_s

def _try_source(source, track, out_dir, filename, cfg):
    """Attempt download from one source. Returns final Path or None."""
    fmt      = cfg["audio_format"]
    quality  = cfg["audio_quality"]
    tol      = cfg.get("duration_tolerance_s", 10)
    query    = SOURCE_QUERY[source](track)
    template = str(out_dir / f"{filename}.%(ext)s")

    opts = {
        "format":         "bestaudio/best",
        "outtmpl":        template,
        "quiet":          True,
        "no_warnings":    True,
        "noplaylist":     True,
        "postprocessors": [{
            "key":            "FFmpegExtractAudio",
            "preferredcodec": FORMAT_CODEC.get(fmt, "mp3"),
            **({"preferredquality": quality} if fmt == "mp3" else {}),
        }],
        # ffmpeg is in PATH via Termux — no ffmpeg_location needed
        "match_filter": lambda i, **kw: (
            None if _duration_ok(i, track["duration_ms"], tol)
            else f"duration mismatch ({i.get('duration')}s vs ~{track['duration_ms']//1000}s)"
        ),
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([query])

        final = out_dir / f"{filename}.{fmt}"
        if not final.exists():
            candidates = sorted(
                [p for p in out_dir.glob(f"{filename}.*") if p.suffix != f".{fmt}"],
                key=lambda p: p.stat().st_mtime, reverse=True
            )
            if candidates:
                candidates[0].rename(final)
            else:
                return None
        return final

    except Exception as e:
        warn(f"    {source_label(source)}: {e}")
        return None

def download_track(track, out_dir, cfg, index=None, total=None):
    fmt  = cfg["audio_format"]
    name = cfg["filename_template"].format(
        title=track["title"],   artist=track["artist"],
        artists=track["artists"], album=track["album"],
        year=track["year"],     track=str(track["track_number"]).zfill(2),
    )
    if cfg["add_track_numbers"] and index:
        name = f"{str(index).zfill(2)}. {name}"
    filename   = sanitize(name)
    final_path = out_dir / f"{filename}.{fmt}"

    idx_str = f"{Fore.WHITE}[{index}/{total}]{Style.RESET_ALL} " if index and total else ""
    label   = (f"{idx_str}{Fore.WHITE}{track['artist']}{Style.RESET_ALL}"
               f" — {Fore.CYAN}{track['title']}{Style.RESET_ALL}")

    if cfg["skip_existing"] and final_path.exists():
        info(f"{label}  {Fore.YELLOW}(skip){Style.RESET_ALL}")
        return True

    print(f"\n  {Fore.MAGENTA}↓{Style.RESET_ALL} {label}")

    sources = cfg.get("source_priority", [SOURCE_YTM, SOURCE_YT, SOURCE_SC])
    result  = None
    for src in sources:
        info(f"Trying {source_label(src)}…")
        result = _try_source(src, track, out_dir, filename, cfg)
        if result:
            ok(f"Downloaded from {source_label(src)}")
            break

    if not result:
        err(f"All sources failed: {track['artist']} — {track['title']}")
        return False

    # Lyrics
    lyrics = ""
    if cfg.get("embed_lyrics") or cfg.get("save_lrc_file"):
        lyrics = get_lyrics(track["title"], track["artist"], cfg)
        if lyrics:
            ok("Lyrics found")
            if cfg.get("save_lrc_file"):
                (out_dir / f"{filename}.lrc").write_text(lyrics, encoding="utf-8")

    # Artwork
    artwork = None
    if cfg.get("embed_artwork") and track.get("artwork_url"):
        artwork = fetch_artwork(track["artwork_url"])

    embed_metadata(str(result), track,
                   lyrics if cfg.get("embed_lyrics") else "",
                   artwork, fmt)
    ok(f"Saved → {result.name}")
    return True

# ─── Download session ─────────────────────────────────────────────────────────

class Session:
    def __init__(self, cfg):
        self.cfg       = cfg
        self.succeeded = 0
        self.failed    = 0
        self._lock     = threading.Lock()

    def _worker(self, args):
        result = download_track(*args)
        with self._lock:
            if result: self.succeeded += 1
            else:      self.failed    += 1

    def run(self, tracks, out_dir):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        workers = max(1, self.cfg["concurrent_downloads"])
        jobs    = [(t, out_dir, self.cfg, i + 1, len(tracks)) for i, t in enumerate(tracks)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for _ in as_completed(pool.submit(self._worker, j) for j in jobs):
                pass

    def summary(self):
        print(f"""
  ┌──────────────────────────────┐
  │  {c('Download Complete', Fore.GREEN, bold=True):<28}│
  │  Downloaded: {c(str(self.succeeded), Fore.GREEN):<21}│
  │  Failed:     {c(str(self.failed),    Fore.RED):<21}│
  └──────────────────────────────┘""")

# ─── Menu: Download ───────────────────────────────────────────────────────────

def menu_download(cfg):
    print_banner()
    print(c("  ── Download from CSV ───────────────────────────", Fore.GREEN))

    sp_list = cfg.get("source_priority", [SOURCE_YTM, SOURCE_YT, SOURCE_SC])
    order   = " → ".join(source_label(s) for s in sp_list)
    print(f"\n  Source order : {order}")
    print(f"  Format       : {c(cfg['audio_format'].upper(), Fore.CYAN)}  "
          f"Quality: {c(cfg['audio_quality']+'kbps', Fore.CYAN)}")
    print(f"  Output       : {cfg['download_dir']}\n")
    print(f"  Tip: Export your Spotify playlist with Exportify")
    print(f"       https://exportify.net\n")

    path_input = prompt("Path to CSV file (blank to cancel)", "").strip().strip("'\"")
    if not path_input:
        return

    csv_path = Path(path_input)
    if not csv_path.exists() or not csv_path.is_file():
        err("File not found."); press_enter(); return

    info("Parsing CSV…")
    try:
        name, tracks = parse_csv(csv_path)
    except Exception as e:
        err(f"Failed to parse CSV: {e}"); press_enter(); return

    if not tracks:
        warn("No tracks found — check that the CSV has Title and Artist columns.")
        press_enter(); return

    ok(f"Playlist: {c(name, Fore.CYAN, bold=True)}")
    info(f"Tracks found: {len(tracks)}")

    base_dir = Path(cfg["download_dir"])
    out_dir  = (base_dir / sanitize(name)) if cfg["create_playlist_folder"] else base_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    info(f"Output: {out_dir}\n")

    ans = prompt("Start download?", "Y").strip().upper()
    if ans not in ("Y", "YES", ""):
        return

    session = Session(cfg)
    t0      = time.time()
    session.run(tracks, out_dir)
    session.summary()
    info(f"Time elapsed: {time.time() - t0:.1f}s")
    press_enter()

# ─── Menu: Source priority ────────────────────────────────────────────────────

KEY_MAP = {
    "ytmusic": SOURCE_YTM, "ytm": SOURCE_YTM,
    "youtube": SOURCE_YT,  "yt":  SOURCE_YT,
    "soundcloud": SOURCE_SC, "sc": SOURCE_SC,
}

def menu_source_priority(cfg):
    while True:
        print_banner()
        print(c("  ── Source Priority ─────────────────────────────", Fore.CYAN))
        print("\n  Tracks are searched in this order — first hit wins:\n")

        current = cfg.get("source_priority", [SOURCE_YTM, SOURCE_YT, SOURCE_SC])
        for i, src in enumerate(current, 1):
            print(f"    {c(str(i), Fore.YELLOW, bold=True)}.  {Fore.GREEN}ON {Style.RESET_ALL}  {SOURCE_NAMES[src]}")

        off = [s for s in [SOURCE_YTM, SOURCE_YT, SOURCE_SC] if s not in current]
        if off:
            print()
            for src in off:
                print(f"         {Fore.RED}OFF{Style.RESET_ALL}  {SOURCE_NAMES[src]}")

        print(f"""
  Reorder — type sources space-separated:
    {Fore.CYAN}ytmusic youtube soundcloud{Style.RESET_ALL}

  Toggle on/off:
    {Fore.CYAN}toggle soundcloud{Style.RESET_ALL}

  Shortcuts: ytm · yt · sc
  {c('[B]', Fore.GREEN)} Back
""")
        inp = input(f"  {Fore.CYAN}→{Style.RESET_ALL} Command: ").strip().lower()
        if inp in ("b", "back", ""):
            return cfg

        if inp.startswith("toggle "):
            key = inp.split(" ", 1)[1].strip()
            src = KEY_MAP.get(key)
            if not src:
                warn("Unknown source. Use: ytmusic, youtube, soundcloud (or ytm/yt/sc)"); continue
            if src in current:
                if len(current) == 1:
                    warn("Can't disable the only active source!"); continue
                current.remove(src)
                info(f"Disabled {SOURCE_NAMES[src]}")
            else:
                current.append(src)
                info(f"Enabled {SOURCE_NAMES[src]}")
            cfg["source_priority"] = current
        else:
            parts     = inp.split()
            new_order = [KEY_MAP[p] for p in parts if p in KEY_MAP]
            if not new_order:
                warn("Invalid. Example: ytmusic youtube soundcloud"); continue
            cfg["source_priority"] = new_order
            ok("Source order updated.")

# ─── Menu: Settings ───────────────────────────────────────────────────────────

def menu_settings(cfg):
    while True:
        print_banner()
        print(c("  ── Settings ────────────────────────────────────", Fore.CYAN))
        print()

        sp_display = " → ".join(SOURCE_NAMES.get(s, s) for s in cfg.get("source_priority", []))

        rows = [
            ("1", "Genius Token",           "genius_token",
             "●●●●●●●●" if cfg["genius_token"] else c("not set", Fore.YELLOW)),
            ("2", "Source Priority",        "_source_menu",   sp_display),
            ("3", "Download Directory",     "download_dir",   cfg["download_dir"]),
            ("4", "Audio Format",           "audio_format",   cfg["audio_format"]),
            ("5", "Audio Quality (kbps)",   "audio_quality",  cfg["audio_quality"]),
            ("6", "Filename Template",      "filename_template", cfg["filename_template"]),
            ("7", "Embed Lyrics",           "embed_lyrics",
             c("ON", Fore.GREEN) if cfg["embed_lyrics"] else c("OFF", Fore.RED)),
            ("8", "Save Lyrics as .lrc",    "save_lrc_file",
             c("ON", Fore.GREEN) if cfg["save_lrc_file"] else c("OFF", Fore.RED)),
            ("9", "Embed Artwork",          "embed_artwork",
             c("ON", Fore.GREEN) if cfg["embed_artwork"] else c("OFF", Fore.RED)),
            ("A", "Skip Existing Files",    "skip_existing",
             c("ON", Fore.GREEN) if cfg["skip_existing"] else c("OFF", Fore.RED)),
            ("B", "Concurrent Downloads",   "concurrent_downloads", str(cfg["concurrent_downloads"])),
            ("C", "Add Track Numbers",      "add_track_numbers",
             c("ON", Fore.GREEN) if cfg["add_track_numbers"] else c("OFF", Fore.RED)),
            ("D", "Create Folder per CSV",  "create_playlist_folder",
             c("ON", Fore.GREEN) if cfg["create_playlist_folder"] else c("OFF", Fore.RED)),
            ("E", "Duration Tolerance (s)", "duration_tolerance_s", str(cfg["duration_tolerance_s"])),
        ]

        for key, label, _, val in rows:
            print(f"  {c(f'[{key}]', Fore.YELLOW)}  {label:<28} {Fore.WHITE}{val}{Style.RESET_ALL}")

        print()
        print(f"  {c('[S]', Fore.GREEN)}  Save & Return     {c('[R]', Fore.MAGENTA)} Reset Defaults")
        print()
        choice = input(f"  {Fore.CYAN}→{Style.RESET_ALL} Choose: ").strip().upper()
        key_map = {r[0]: (r[2], r[1]) for r in rows}

        if choice == "S":
            save_config(cfg); ok("Settings saved!"); time.sleep(0.7); return cfg

        elif choice == "R":
            if prompt("Reset ALL settings to defaults?", "n").upper() in ("Y", "YES"):
                cfg = dict(DEFAULT_CONFIG); save_config(cfg)
                ok("Settings reset."); time.sleep(0.7)

        elif choice in key_map:
            field, label = key_map[choice]

            if field == "_source_menu":
                cfg = menu_source_priority(cfg); continue

            current = cfg[field]

            if isinstance(current, bool):
                cfg[field] = not current; continue

            if field == "audio_format":
                print(f"\n    Options: mp3  flac  m4a  opus")
                val = prompt("Format", current).lower()
                if val in ("mp3", "flac", "m4a", "opus"): cfg[field] = val
                else: warn("Invalid format.")

            elif field == "audio_quality":
                print(f"\n    Options: 128  192  256  320  (mp3 only)")
                val = prompt("Quality kbps", current)
                if val in ("128", "192", "256", "320"): cfg[field] = val
                else: warn("Invalid quality.")

            elif field == "concurrent_downloads":
                val = prompt("Concurrent downloads (1-5)", str(current))
                if val and val.isdigit() and 1 <= int(val) <= 5: cfg[field] = int(val)
                else: warn("Enter a number from 1 to 5.")

            elif field == "duration_tolerance_s":
                val = prompt("Tolerance in seconds (0 = disabled)", str(current))
                if val and val.isdigit(): cfg[field] = int(val)
                else: warn("Enter a number.")

            elif field == "genius_token":
                print(f"\n    {Fore.YELLOW}Get free token: https://genius.com/api-clients{Style.RESET_ALL}")
                val = prompt("Genius token", current or "")
                cfg[field] = val

            elif field == "download_dir":
                print(f"\n    Termux home:    {Path.home() / 'Music'}")
                print(f"    Android shared: /storage/emulated/0/Music")
                print(f"    (run termux-setup-storage first for shared storage)\n")
                val = prompt("Download directory", current)
                cfg[field] = val or current

            elif field == "filename_template":
                print(f"\n    Variables: {{title}} {{artist}} {{artists}} {{album}} {{year}} {{track}}")
                val = prompt("Template", current)
                cfg[field] = val or current

            else:
                val = prompt(label, str(current))
                cfg[field] = val or current

    return cfg

# ─── Menu: Dependency check ───────────────────────────────────────────────────

def menu_check_deps():
    print_banner()
    print(c("  ── Dependency Check ────────────────────────────", Fore.CYAN))
    print()

    if FFMPEG_EXE:
        ok(f"ffmpeg: {FFMPEG_EXE}")
    else:
        err("ffmpeg: NOT FOUND")
        print(f"    Fix: {Fore.CYAN}pkg install ffmpeg{Style.RESET_ALL}\n")

    if shutil.which("ffprobe"):
        ok(f"ffprobe: {shutil.which('ffprobe')}")
    else:
        warn("ffprobe not found (usually installed with ffmpeg)")

    print()
    for module, package in REQUIRED_PACKAGES.items():
        try:
            __import__(module)
            ok(f"{package}")
        except ImportError:
            err(f"{package} — MISSING  →  pip install {package}")

    print()
    shared = Path("/storage/emulated/0")
    if shared.exists():
        ok(f"Shared storage: accessible ({shared})")
    else:
        warn("Shared storage not accessible — run: termux-setup-storage")

    press_enter()

# ─── Menu: About ─────────────────────────────────────────────────────────────

def menu_about():
    print_banner()
    print(c("  ── About ───────────────────────────────────────", Fore.CYAN))
    ffmpeg_str = (c(FFMPEG_EXE, Fore.GREEN) if FFMPEG_EXE
                  else c("NOT FOUND — run: pkg install ffmpeg", Fore.RED))
    print(f"""
  {c('CSV Music Downloader', Fore.GREEN, bold=True)}  (Exportify Edition)
  Version 4.0 · Termux · Python {sys.version.split()[0]}

  {c('How it works:', Fore.YELLOW)}
    1. Read track list from an Exportify CSV
    2. Search each source in priority order:
         YouTube Music → YouTube → SoundCloud
    3. Duration from CSV used to reject wrong matches
    4. Download + convert audio via yt-dlp + ffmpeg
    5. Embed metadata, artwork, lyrics via mutagen

  {c('Exportify CSV columns used:', Fore.YELLOW)}
    Track Name · Artist Name(s) · Album Name
    Album Release Date · Album Image URL
    Track Duration (ms) · Track Number · ISRC

  {c('Get your Exportify CSV:', Fore.YELLOW)}
    https://exportify.net
    Sign in with Spotify → Export any playlist

  {c('Audio formats:', Fore.YELLOW)}
    MP3 (128–320 kbps) · FLAC · M4A · Opus

  {c('ffmpeg:', Fore.YELLOW)}
    {ffmpeg_str}

  {c('Termux setup:', Fore.YELLOW)}
    pkg install ffmpeg python
    pip install yt-dlp mutagen requests lyricsgenius colorama
    termux-setup-storage

  {c('Genius API (optional, for lyrics):', Fore.YELLOW)}
    https://genius.com/api-clients
""")
    press_enter()

# ─── Main menu ────────────────────────────────────────────────────────────────

def main():
    cfg = load_config()

    while True:
        print_banner()

        # Warnings
        if not FFMPEG_EXE:
            print(f"  {Fore.RED}✗  ffmpeg not found — run: pkg install ffmpeg{Style.RESET_ALL}")
        if not cfg.get("genius_token"):
            print(f"  {Fore.YELLOW}·  No Genius token — lyrics disabled (Settings to add){Style.RESET_ALL}")

        sp_list = cfg.get("source_priority", [SOURCE_YTM, SOURCE_YT, SOURCE_SC])
        order   = " → ".join(source_label(s) for s in sp_list)
        print(f"\n  Sources : {order}")
        print(f"  Format  : {c(cfg['audio_format'].upper(), Fore.CYAN)}  "
              f"Quality: {c(cfg['audio_quality']+'kbps', Fore.CYAN)}\n")

        print(c("  ── Main Menu ───────────────────────────────────", Fore.GREEN))
        print(f"""
  {c('[1]', Fore.GREEN, bold=True)}  Download from CSV
  {c('[2]', Fore.CYAN)}  Source Priority
  {c('[3]', Fore.CYAN)}  Settings
  {c('[4]', Fore.CYAN)}  Check Dependencies
  {c('[5]', Fore.CYAN)}  About
  {c('[Q]', Fore.RED)}  Quit
""")
        choice = input(f"  {Fore.CYAN}→{Style.RESET_ALL} Choose: ").strip().upper()

        if   choice == "1": menu_download(cfg)
        elif choice == "2": cfg = menu_source_priority(cfg)
        elif choice == "3": cfg = menu_settings(cfg)
        elif choice == "4": menu_check_deps()
        elif choice == "5": menu_about()
        elif choice in ("Q", "QUIT", "EXIT", "0"):
            print(f"\n  {Fore.GREEN}Goodbye!{Style.RESET_ALL}\n")
            sys.exit(0)

if __name__ == "__main__":
    main()
