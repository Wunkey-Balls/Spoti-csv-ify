#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║             CSV PLAYLIST MUSIC DOWNLOADER                ║
║    Metadata from CSV · Audio from YT/YTMusic/SC          ║
╚══════════════════════════════════════════════════════════╝
pip install yt-dlp mutagen requests lyricsgenius colorama imageio-ffmpeg

Compatible natively with Exportify CSV structures.
Genius API (lyrics, free): https://genius.com/api-clients
"""

import os, sys, json, time, shutil, threading, subprocess, csv
from pathlib import Path

# ─── Dependency check ────────────────────────────────────────────────────────
REQUIRED_PACKAGES = {
    "yt_dlp":          "yt-dlp",
    "mutagen":         "mutagen",
    "requests":        "requests",
    "lyricsgenius":    "lyricsgenius",
    "colorama":        "colorama",
    "imageio_ffmpeg":  "imageio-ffmpeg",
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

# ─── ffmpeg: prefer system, fall back to imageio-ffmpeg (Pydroid/Android) ────
import imageio_ffmpeg as _iio_ffmpeg

def _find_ffmpeg() -> str | None:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        path = _iio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            os.environ["PATH"] = os.path.dirname(path) + os.pathsep + os.environ.get("PATH", "")
            return path
    except Exception:
        pass
    return None

FFMPEG_PATH = _find_ffmpeg()

# ─── Imports ──────────────────────────────────────────────────────────────────
import yt_dlp
from mutagen.id3 import (ID3, TIT2, TPE1, TPE2, TALB, TDRC, TRCK, TPOS,
                          TCON, USLT, APIC, error as ID3Error)
from mutagen.flac import FLAC, Picture
import requests
import colorama
from colorama import Fore, Style

colorama.init(autoreset=True)

# ─── Config ───────────────────────────────────────────────────────────────────
CONFIG_FILE = Path.home() / ".csv_music_dl_config.json"
SOURCES = ["ytmusic", "youtube", "soundcloud"]

DEFAULT_CONFIG = {
    # Genius API
    "genius_token":           "",
    # Download
    "download_dir":           str(Path.home() / "Music" / "CSVDownloads"),
    "audio_format":           "mp3",
    "audio_quality":          "320",
    "filename_template":      "{artist} - {title}",
    "add_track_numbers":      True,
    "create_playlist_folder": True,
    "skip_existing":          True,
    "max_retries":            3,
    "concurrent_downloads":   2,
    # Sources (ordered list — tried left to right until one works)
    "source_priority":        ["ytmusic", "youtube", "soundcloud"],
    # Metadata & Lyrics Settings
    "embed_lyrics":           True,
    "save_lrc_file":          False,
    "embed_artwork":          True,
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── Terminal helpers ─────────────────────────────────────────────────────────
def sanitize(name: str) -> str:
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name.strip()

def print_banner():
    os.system("cls" if os.name == "nt" else "clear")
    print(f"""{Fore.GREEN}╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   {Fore.WHITE}█▀ █▀█ █▀█ ▀█▀ █   █▀▄ █░░{Fore.GREEN}                              ║
║   {Fore.WHITE}▄█ █▀▀ █▄█ ░█░ █   █▄▀ █▄▄{Fore.GREEN}                              ║
║                                                              ║
║   {Fore.CYAN}Exportify CSV → YT Music / YouTube / SoundCloud{Fore.GREEN}        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝{Style.RESET_ALL}""")

def c(text, color=Fore.WHITE, bold=False):
    return f"{Style.BRIGHT if bold else ''}{color}{text}{Style.RESET_ALL}"

def prompt(text, default=None):
    hint = f" [{default}]" if default is not None else ""
    val = input(f"  {Fore.CYAN}→{Style.RESET_ALL} {text}{hint}: ").strip()
    return val if val else default

def ok(msg):   print(f"  {Fore.GREEN}✓{Style.RESET_ALL} {msg}")
def warn(msg): print(f"  {Fore.YELLOW}⚠{Style.RESET_ALL}  {msg}")
def err(msg):  print(f"  {Fore.RED}✗{Style.RESET_ALL} {msg}")
def info(msg): print(f"  {Fore.CYAN}·{Style.RESET_ALL} {msg}")
def press_enter(): input(f"\n  {Fore.YELLOW}Press Enter to continue...{Style.RESET_ALL}")

# ─── CSV Parser Engine (Exportify Optimized) ──────────────────────────────────
def parse_tracks_from_csv(file_path: Path) -> tuple[str, list[dict]]:
    playlist_name = file_path.stem
    tracks = []
    
    with open(file_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        # Standardize field names to simple lowercase to combat whitespace variations
        if reader.fieldnames:
            reader.fieldnames = [field.lower().strip() for field in reader.fieldnames]
        
        for idx, row in enumerate(reader, 1):
            # Resolve properties matching direct Exportify headers or generic configurations
            title = (row.get("track name") or row.get("title") or row.get("track") or row.get("name") or "").strip()
            artists_all = (row.get("artist name(s)") or row.get("artist name") or row.get("artist") or "").strip()
            
            if not title or not artists_all:
                continue  
                
            album = (row.get("album name") or row.get("album") or "Unknown Album").strip()
            album_artist = (row.get("album artist name(s)") or row.get("album artist") or row.get("album_artist") or artists_all).strip()
            
            # Extract year from Exportify 'Album Release Date' (e.g., "2023-11-15")
            release_date = (row.get("album release date") or row.get("year") or row.get("date", "")).strip()
            year = release_date[:4] if release_date else ""
            
            track_num = row.get("track number") or row.get("track_number") or row.get("track_num")
            disc_num = row.get("disc number") or row.get("disc_number") or "1"
            
            # Optional extra genre target toggle from Exportify settings panel
            genres = (row.get("artist genres") or row.get("genre") or row.get("genres", "")).strip()
            genres_list = [g.strip() for g in genres.split(",")] if genres else []
            
            artwork_url = (row.get("album image url") or row.get("artwork_url") or row.get("artwork") or "").strip()
            duration = row.get("track duration (ms)") or row.get("duration_ms") or row.get("duration") or "0"
            
            try:
                duration_ms = int(duration)
                if 0 < duration_ms < 1000:  # Fix if format fallback maps raw seconds
                    duration_ms *= 1000
            except ValueError:
                duration_ms = 0

            # Pull first primary artist for strict query searches to reduce target mismatch issues
            primary_artist = artists_all.split(",")[0].strip() if "," in artists_all else artists_all

            tracks.append({
                "title":        title,
                "artist":       primary_artist,
                "artists":      artists_all,
                "album":        album,
                "album_artist": album_artist,
                "year":         year,
                "track_number": int(track_num) if str(track_num).isdigit() else idx,
                "disc_number":  int(disc_num) if str(disc_num).isdigit() else 1,
                "genres":       genres_list,
                "duration_ms":  duration_ms,
                "artwork_url":  artwork_url if artwork_url.startswith("http") else None,
                "isrc":         row.get("isrc", "").strip(),
            })
            
    return playlist_name, tracks

# ─── Source search queries ────────────────────────────────────────────────────
def _ytmusic_query(track: dict) -> str:
    return f"ytmsearch1:{track['artist']} {track['title']}"

def _youtube_query(track: dict) -> str:
    return f"ytsearch3:{track['artist']} - {track['title']} audio"

def _soundcloud_query(track: dict) -> str:
    return f"scsearch3:{track['artist']} {track['title']}"

SOURCE_QUERY = {"ytmusic": _ytmusic_query, "youtube": _youtube_query, "soundcloud": _soundcloud_query}
SOURCE_LABEL = {
    "ytmusic":    f"{Fore.RED}YT Music{Style.RESET_ALL}",
    "youtube":    f"{Fore.RED}YouTube{Style.RESET_ALL}",
    "soundcloud": f"{Fore.YELLOW}SoundCloud{Style.RESET_ALL}",
}

# ─── Lyrics Engine ────────────────────────────────────────────────────────────
_genius_client = None

def fetch_lyrics(title: str, artist: str, cfg: dict) -> str:
    global _genius_client
    if not (cfg.get("embed_lyrics") or cfg.get("save_lrc_file")) or not cfg.get("genius_token"):
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
        warn(f"Lyrics Error: {e}")
        return ""

def fetch_artwork(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.content
    except Exception:
        return None

# ─── Metadata embedding ───────────────────────────────────────────────────────
def embed_mp3(path: str, track: dict, lyrics: str, art: bytes | None):
    try:
        try: tags = ID3(path)
        except: tags = ID3()
        tags.add(TIT2(encoding=3, text=track["title"]))
        tags.add(TPE1(encoding=3, text=track["artists"]))
        tags.add(TPE2(encoding=3, text=track["album_artist"]))
        tags.add(TALB(encoding=3, text=track["album"]))
        if track["year"]: tags.add(TDRC(encoding=3, text=track["year"]))
        tags.add(TRCK(encoding=3, text=str(track["track_number"])))
        tags.add(TPOS(encoding=3, text=str(track["disc_number"])))
        if track.get("genres"): tags.add(TCON(encoding=3, text=", ".join(track["genres"])))
        if lyrics: tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
        if art: tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art))
        tags.save(path, v2_version=3)
    except Exception as e:
        warn(f"MP3 tag error: {e}")

def embed_flac(path: str, track: dict, lyrics: str, art: bytes | None):
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
        if art:
            pic = Picture(); pic.type=3; pic.mime="image/jpeg"
            pic.desc="Cover"; pic.data=art
            audio.clear_pictures(); audio.add_picture(pic)
        audio.save()
    except Exception as e:
        warn(f"FLAC tag error: {e}")

def embed_mp4(path: str, track: dict, lyrics: str, art: bytes | None):
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
        if art: audio["covr"] = [MP4Cover(art, MP4Cover.FORMAT_JPEG)]
        audio.save()
    except Exception as e:
        warn(f"M4A tag error: {e}")

def embed_metadata(path: str, track: dict, lyrics: str, art: bytes | None, fmt: str):
    if fmt == "mp3":  embed_mp3(path, track, lyrics, art)
    elif fmt == "flac": embed_flac(path, track, lyrics, art)
    else:               embed_mp4(path, track, lyrics, art)

# ─── Download One Track ───────────────────────────────────────────────────────
FORMAT_CODEC = {"mp3": "mp3", "flac": "flac", "m4a": "m4a", "opus": "opus"}

def build_filename(track: dict, cfg: dict, idx: int | None) -> str:
    name = cfg["filename_template"].format(
        title=track["title"], artist=track["artist"],
        artists=track["artists"], album=track["album"],
        year=track["year"], track=str(track["track_number"]).zfill(2),
    )
    if cfg["add_track_numbers"] and idx is not None:
        name = f"{str(idx).zfill(2)}. {name}"
    return sanitize(name)

def _duration_ok(info_dict: dict, expected_ms: int, tolerance: float = 0.25) -> bool:
    if not expected_ms or expected_ms < 1000:
        return True
    got_s = info_dict.get("duration") or 0
    exp_s = expected_ms / 1000
    return abs(got_s - exp_s) / exp_s <= tolerance

def download_track(track: dict, out_dir: Path, cfg: dict,
                   idx: int | None = None, total: int | None = None) -> bool:
    fmt      = cfg["audio_format"]
    quality  = cfg["audio_quality"]
    retries  = cfg["max_retries"]
    filename = build_filename(track, cfg, idx)
    dest     = out_dir / f"{filename}.{fmt}"
    label    = (f"[{idx}/{total}] " if idx and total else "") + \
               f"{Fore.WHITE}{track['artist']}{Style.RESET_ALL} — {Fore.CYAN}{track['title']}{Style.RESET_ALL}"

    if cfg["skip_existing"] and dest.exists():
        info(f"{label}  {Fore.YELLOW}(skipped){Style.RESET_ALL}")
        return True

    print(f"\n  {Fore.MAGENTA}↓{Style.RESET_ALL} {label}")
    codec = FORMAT_CODEC.get(fmt, "mp3")
    postproc = [{"key": "FFmpegExtractAudio", "preferredcodec": codec,
                 **({"preferredquality": quality} if fmt == "mp3" else {})}]
    
    base_opts = {
        "format":         "bestaudio/best",
        "outtmpl":        str(out_dir / f"{filename}.%(ext)s"),
        "quiet":          True,
        "no_warnings":    True,
        "noplaylist":     True,
        "postprocessors": postproc,
    }
    if FFMPEG_PATH:
        base_opts["ffmpeg_location"] = os.path.dirname(FFMPEG_PATH)

    sources = cfg.get("source_priority", SOURCES)
    for source in sources:
        query_fn = SOURCE_QUERY.get(source)
        if not query_fn:
            continue
        query = query_fn(track)
        src_label = SOURCE_LABEL.get(source, source)
        
        for attempt in range(1, retries + 1):
            try:
                opts = dict(base_opts)
                if source in ("youtube", "soundcloud"):
                    opts["format"] = "bestaudio/best"
                    with yt_dlp.YoutubeDL({**opts, "quiet": True, "skip_download": True, "extract_flat": False}) as ydl:
                        info_result = ydl.extract_info(query, download=False)
                    entries = info_result.get("entries") or [info_result]
                    
                    best = None
                    for entry in entries:
                        if entry and _duration_ok(entry, track["duration_ms"]):
                            best = entry
                            break
                    if not best:
                        best = entries[0] if entries else None
                    if not best:
                        raise ValueError("No results discovered")
                        
                    dl_url = best.get("webpage_url") or best.get("url")
                    opts_dl = {**opts, "default_search": None}
                    with yt_dlp.YoutubeDL(opts_dl) as ydl:
                        ydl.download([dl_url])
                else:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([query])

                if not dest.exists():
                    candidates = list(out_dir.glob(f"{filename}.*"))
                    candidates = [f for f in candidates if f.suffix != f".{fmt}"]
                    if candidates:
                        candidates[0].rename(dest)
                    elif not dest.exists():
                        raise FileNotFoundError("Output asset missing after conversion pipeline")

                info(f"  Source: {src_label}")
                
                # Fetch lyrics
                lyrics = fetch_lyrics(track["title"], track["artist"], cfg)
                if lyrics:
                    ok("  Lyrics pulled successfully")
                    if cfg.get("save_lrc_file"):
                        (out_dir / f"{filename}.lrc").write_text(lyrics, encoding="utf-8")
                        ok("  → Created standalone .lrc file")

                # Track artwork processing
                art = fetch_artwork(track["artwork_url"]) if cfg["embed_artwork"] and track.get("artwork_url") else None
                
                # Verify conditional tracking options for internal embed
                metadata_lyrics = lyrics if cfg.get("embed_lyrics") else ""
                embed_metadata(str(dest), track, metadata_lyrics, art, fmt)
                
                ok(f"  → Completed writing tags: {dest.name}")
                return True
            except Exception as e:
                if attempt < retries:
                    warn(f"  [{source}] attempt {attempt} failed: {e} — retrying…")
                    time.sleep(1.5)
                else:
                    warn(f"  [{source}] given up: {e}")
                    break
                    
    err(f"  All targets failed matching data rows for: {track['artist']} — {track['title']}")
    return False

# ─── Session ──────────────────────────────────────────────────────────────────
class Session:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ok = self.fail = 0
        self._lock = threading.Lock()

    def _run(self, args):
        result = download_track(*args)
        with self._lock:
            if result: self.ok += 1
            else:       self.fail += 1

    def download_all(self, tracks, out_dir):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        jobs = [(t, out_dir, self.cfg, i+1, len(tracks)) for i, t in enumerate(tracks)]
        with ThreadPoolExecutor(max_workers=max(1, self.cfg["concurrent_downloads"])) as ex:
            for _ in as_completed(ex.submit(self._run, j) for j in jobs):
                pass

    def summary(self):
        print(f"""  ┌───────────────────────────────┐
  │  {c('Download Complete', Fore.GREEN, bold=True):<29}│
  │  Downloaded: {c(str(self.ok),   Fore.GREEN):<20}│
  │  Failed:     {c(str(self.fail), Fore.RED):<20}│
  └───────────────────────────────┘""")

# ─── Menus ────────────────────────────────────────────────────────────────────
def menu_download(cfg):
    print_banner()
    print(c("  ── Process Exportify CSV ───────────────────────", Fore.GREEN))
    priority = cfg.get("source_priority", SOURCES)
    print(f"\n  Active Search Cascade: {' → '.join(SOURCE_LABEL.get(s, s) for s in priority)}\n")
    
    path_input = prompt("Drag & Drop your Exportify CSV file here or type its path").strip()
    if not path_input:
        return
        
    csv_path = Path(path_input.strip("'\""))  # Strip quotes added via drag-and-drop actions cleanly
    if not csv_path.exists() or not csv_path.is_file():
        err("Target file destination does not exist."); press_enter(); return

    info("Parsing structured Exportify dataset rows…")
    try:
        name, tracks = parse_tracks_from_csv(csv_path)
    except Exception as e:
        err(f"Failed parsing file configurations: {e}"); press_enter(); return

    if not tracks:
        warn("No processable tracking fields populated (Missing Title/Artist columns)."); press_enter(); return

    ok(f"Parsed Target Title: {c(name, Fore.CYAN, bold=True)}")
    info(f"Total Tracks Discovered: {len(tracks)}")
    
    base = Path(cfg["download_dir"])
    out_dir = (base / sanitize(name)) if cfg["create_playlist_folder"] else base
    out_dir.mkdir(parents=True, exist_ok=True)
    info(f"Output Directory Path: {out_dir}\n")

    if prompt("Initiate track assembly sequence?", "Y").upper() not in ("Y", "YES", ""):
        return

    session = Session(cfg)
    t0 = time.time()
    session.download_all(tracks, out_dir)
    session.summary()
    info(f"Elapsed Runtime Process: {time.time()-t0:.1f}s")
    press_enter()

def menu_source_priority(cfg):
    while True:
        print_banner()
        print(c("  ── Source Priority ─────────────────────────────", Fore.CYAN))
        print(f"\n  Tracks are searched in this order; first hit wins.\n")
        current = cfg.get("source_priority", list(SOURCES))
        for i, s in enumerate(current, 1):
            print(f"  {c(f'[{i}]', Fore.YELLOW)}  {s.upper()}")
        print(f"\n  Enter new order as numbers (e.g. {c('2 1 3', Fore.GREEN)})")
        print(f"  Sources: 1=YT Music  2=YouTube  3=SoundCloud")
        print(f"  {c('[B]', Fore.CYAN)} Back\n")
        
        choice = input(f"  {Fore.CYAN}→{Style.RESET_ALL} New order: ").strip().upper()
        if choice in ("B", ""):
            return
        try:
            nums = [int(x) for x in choice.split()]
            all_sources = ["ytmusic", "youtube", "soundcloud"]
            new_order = [all_sources[n-1] for n in nums]
            for s in all_sources:
                if s not in new_order:
                    new_order.append(s)
            cfg["source_priority"] = new_order
            ok(f"Updated cascade routing values.")
            time.sleep(0.8)
            return
        except Exception:
            warn("Invalid entry array format.")

def menu_settings(cfg):
    while True:
        print_banner()
        print(c("  ── Settings ────────────────────────────────────", Fore.CYAN))
        priority_str = " → ".join(s.upper() for s in cfg.get("source_priority", SOURCES))
        print()
        rows = [
            ("1", "Genius API Token",       "genius_token",           "●●●●●●●●" if cfg["genius_token"] else c("not set", Fore.YELLOW)),
            ("2", "Download Directory",     "download_dir",           cfg["download_dir"]),
            ("3", "Audio Format",           "audio_format",           cfg["audio_format"]),
            ("4", "Audio Quality (kbps)",   "audio_quality",          cfg["audio_quality"]),
            ("5", "Filename Template",      "filename_template",      cfg["filename_template"]),
            ("6", "Embed Lyrics Metadata",  "embed_lyrics",           c("ON", Fore.GREEN) if cfg["embed_lyrics"] else c("OFF", Fore.RED)),
            ("7", "Save Lyrics as .lrc",    "save_lrc_file",          c("ON", Fore.GREEN) if cfg["save_lrc_file"] else c("OFF", Fore.RED)),
            ("8", "Embed Artwork",          "embed_artwork",          c("ON", Fore.GREEN) if cfg["embed_artwork"] else c("OFF", Fore.RED)),
            ("9", "Skip Existing Files",    "skip_existing",          c("ON", Fore.GREEN) if cfg["skip_existing"] else c("OFF", Fore.RED)),
            ("A", "Concurrent Downloads",   "concurrent_downloads",   str(cfg["concurrent_downloads"])),
            ("B", "Add Track Numbers",      "add_track_numbers",      c("ON", Fore.GREEN) if cfg["add_track_numbers"] else c("OFF", Fore.RED)),
            ("C", "Create Folder per CSV",  "create_playlist_folder", c("ON", Fore.GREEN) if cfg["create_playlist_folder"] else c("OFF", Fore.RED)),
        ]
        for key, label, _, val in rows:
            print(f"  {c(f'[{key}]', Fore.YELLOW)}  {label:<26} {Fore.WHITE}{val}{Style.RESET_ALL}")
            
        print(f"\n  {c('[P]', Fore.MAGENTA)}  Source Priority          {priority_str}")
        print(f"\n  {c('[S]', Fore.GREEN)}  Save & Return    {c('[R]', Fore.RED)} Reset Defaults\n")
        
        choice = input(f"  {Fore.CYAN}→{Style.RESET_ALL} Choose: ").strip().upper()
        key_map = {r[0]: (r[2], r[1]) for r in rows}
        
        if choice == "S":
            save_config(cfg); ok("Configurations verified and locked."); time.sleep(0.6); return cfg
        elif choice == "R":
            if prompt("Reset all configurations?", "n").upper() in ("Y","YES"):
                cfg = dict(DEFAULT_CONFIG); save_config(cfg); ok("Defaults restored."); time.sleep(0.6)
        elif choice == "P":
            menu_source_priority(cfg)
        elif choice in key_map:
            field, label = key_map[choice]
            current = cfg[field]
            if isinstance(current, bool):
                cfg[field] = not current; continue
            if field == "audio_format":
                print("\n    Options: mp3  flac  m4a  opus")
                v = prompt("Format", current).lower()
                if v in ("mp3","flac","m4a","opus"): cfg[field] = v
                else: warn("Unsupported specification option.")
            elif field == "audio_quality":
                print("\n    Options: 128  192  256  320")
                v = prompt("Quality", current)
                if v in ("128","192","256","320"): cfg[field] = v
                else: warn("Fallback quality option mismatch.")
            elif field == "concurrent_downloads":
                v = prompt("1-5", str(current))
                if v.isdigit() and 1 <= int(v) <= 5: cfg[field] = int(v)
                else: warn("Parallel worker range locked 1-5.")
            elif field == "genius_token":
                print(f"\n    {Fore.YELLOW}Genius Token Setup: https://genius.com/api-clients{Style.RESET_ALL}\n")
                cfg[field] = prompt(label, current or "") or current
            elif field == "filename_template":
                print("\n    Variables: {title} {artist} {artists} {album} {year} {track}")
                v = prompt("Template", current)
                cfg[field] = v or current
            else:
                v = prompt(label, str(current))
                cfg[field] = v or current
    return cfg

def menu_deps():
    print_banner()
    print(c("  ── Dependency Check ────────────────────────────", Fore.CYAN))
    print()
    if FFMPEG_PATH: ok(f"ffmpeg decoder location: {FFMPEG_PATH}")
    else: err("ffmpeg mapping: MISSING. Install system package or bundled imageio-ffmpeg wrapper.")
    print()
    for module, package in REQUIRED_PACKAGES.items():
        try:
            __import__(module)
            ok(f"Environment library validated: {package}")
        except ImportError:
            err(f"{package} — MISSING")
    press_enter()

def menu_about():
    print_banner()
    print(c("  ── About & Information ─────────────────────────", Fore.CYAN))
    print(f"""  {c('Exportify CSV Music Downloader', Fore.GREEN, bold=True)}
  
  {c('Operational Layout:', Fore.YELLOW)}
    1. Reads clean exported .csv structures produced natively via Exportify.
    2. Identifies specific target matching streams across selected endpoints:
       YouTube Music → YouTube Web Content → SoundCloud Links
    3. Calculates timing variations from 'Track Duration (ms)' to match tracking values accurately.
    4. Downloads target stream data via asynchronous backend loops.
    5. Injects file parameters, image arrays, and custom lyrics setups.

  {c('Exportify Structural Mapping Rules Summary:', Fore.YELLOW)}
    - Automatically references fields: 'Track Name', 'Artist Name(s)', 'Album Name', 
      'Album Release Date', 'Album Image URL', 'Track Duration (ms)'.
    - Gracefully uses the first entry if multi-artist strings are comma-separated to ensure high matching query rates on search engines.""")
    press_enter()

def main():
    cfg = load_config()
    while True:
        print_banner()
        if not FFMPEG_PATH:
            print(f"  {Fore.RED}✗  System Mapping Error: ffmpeg asset link absent. Ensure proper setup.{Style.RESET_ALL}\n")
        priority = cfg.get("source_priority", SOURCES)
        src_str = " → ".join(s.upper() for s in priority)
        print(c("  ── Main Menu Interface ─────────────────────────", Fore.GREEN))
        print(f"""  {c('[1]', Fore.GREEN, bold=True)}  Process Local Exportify CSV Spreadsheet
  {c('[2]', Fore.CYAN)}  Settings & File Toggles
  {c('[3]', Fore.CYAN)}  Verify Resource Dependencies
  {c('[4]', Fore.CYAN)}  System Manual & Metadata Info
  {c('[Q]', Fore.RED)}  Terminate Application Session

  Target Active Cascade Layout: {src_str}""")
        
        choice = input(f"  {Fore.CYAN}→{Style.RESET_ALL} Choose action: ").strip().upper()
        if   choice == "1": menu_download(cfg)
        elif choice == "2": cfg = menu_settings(cfg)
        elif choice == "3": menu_deps()
        elif choice == "4": menu_about()
        elif choice in ("Q","QUIT","EXIT","0"):
            print(f"\n  {Fore.GREEN}Session Disposed. Goodbye!{Style.RESET_ALL}\n"); sys.exit(0)

if __name__ == "__main__":
    main()
