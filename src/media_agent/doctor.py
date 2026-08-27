"""Commands: doctor, init."""

import getpass
import importlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .config import FFMPEG_HINT, get_config, resolve_tmdb_token


_TMDB_TOKEN_HINT = """\
  TMDB token: not set
    The tmdb-* commands require a free TMDB API Read Access Token.
    1. Create an account at https://www.themoviedb.org/signup
    2. Request an API key at https://www.themoviedb.org/settings/api
       (choose "Developer" tier — free, instant approval for personal use)
    3. Copy the "API Read Access Token" (the long v4 token, NOT the v3 key)
    4. Add it to media_agent_config.json as "tmdb_read_access_token"
       or set the environment variable: TMDB_TOKEN=<token>"""


def cmd_doctor(args):
    """Health check: ffprobe, mutagen, library paths, TMDB token."""
    ok = True

    print("── media-agent doctor ────────────────────────────────────────────────────────")

    # ffprobe
    if get_config().ffprobe:
        try:
            result = subprocess.run(
                [get_config().ffprobe, '-version'],
                capture_output=True, text=True, timeout=10,
            )
            version_line = result.stdout.splitlines()[0] if result.stdout else '(no output)'
            print(f"  [OK] ffprobe     : {version_line}")
        except Exception as e:
            print(f"  [!!] ffprobe     : found at {get_config().ffprobe} but failed to run: {e}")
            ok = False
    else:
        print("  [!!] ffprobe     : NOT FOUND")
        print(FFMPEG_HINT)
        ok = False

    # mutagen
    try:
        importlib.import_module('mutagen')
        print("  [OK] mutagen     : importable")
    except ImportError:
        print("  [!!] mutagen     : not installed — run: pip install mutagen")
        ok = False

    # requests
    try:
        importlib.import_module('requests')
        print("  [OK] requests    : importable")
    except ImportError:
        print("  [!!] requests    : not installed — run: pip install requests")
        ok = False

    # library root
    if get_config().library_root.is_dir():
        print(f"  [OK] library_root: {get_config().library_root}")
    else:
        print(f"  [!!] library_root: NOT FOUND: {get_config().library_root}")
        ok = False

    # sections — missing dirs are warnings, not hard failures (fresh installs may not have them yet)
    for key in ('movies', 'tv_shows', 'music'):
        path = get_config().sections.get(key)
        if path is None:
            print(f"  [--] section/{key:<8}: not configured")
        elif path.is_dir():
            print(f"  [OK] section/{key:<8}: {path}")
        else:
            print(f"  [!!] section/{key:<8}: NOT FOUND: {path}")

    # TMDB token
    token = resolve_tmdb_token()
    if not token:
        print("  [--] TMDB token  : not configured (tmdb-* commands will fail)")
        print()
        print(_TMDB_TOKEN_HINT)
    else:
        try:
            req = urllib.request.Request(
                'https://api.themoviedb.org/3/configuration',
                headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    print("  [OK] TMDB token  : valid")
                else:
                    print(f"  [!!] TMDB token  : unexpected status {resp.status}")
                    ok = False
        except urllib.error.HTTPError as e:
            if e.code == 401:
                print("  [!!] TMDB token  : invalid (401 Unauthorized) — check your token")
            else:
                print(f"  [??] TMDB token  : HTTP {e.code} — could not verify")
            ok = False
        except urllib.error.URLError as e:
            print(f"  [??] TMDB token  : network error — could not verify ({e.reason})")
        except Exception:
            print("  [??] TMDB token  : could not verify (unexpected error)")

    print()
    if ok:
        print("All checks passed.")
    else:
        print("One or more checks failed — see above.")
        raise SystemExit(1)


_SECTION_NAMES = ('Movies', 'TV Shows', 'Music')
_VIDEO_HINT_EXTS = ('.mkv', '.mp4', '.avi', '.m4v', '.mov')


def _restrict_config_permissions(path, *, platform_name=None):
    """Best-effort: restrict a config file that now holds a credential to
    owner read/write only.

    POSIX only, via chmod. Windows has no direct equivalent -- NTFS ACLs are
    a different model, and an icacls-based fix was judged not worth the
    complexity for this release; on Windows this is left to the user's
    account/disk protections instead.

    `platform_name` defaults to os.name and only exists so tests can
    exercise the POSIX branch without mutating the real os.name (which
    pathlib itself reads, so flipping it globally breaks unrelated code on
    whichever OS the tests actually run on).
    """
    if (platform_name if platform_name is not None else os.name) == 'nt':
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _confirm_library_root(root):
    """Sanity-check the folder the user gave, and say what looks wrong.

    Pointing at the wrong folder is the most common setup mistake, and it fails
    silently: every command runs fine and reports zero files. Catching it here,
    while the user is still thinking about paths, saves them from concluding the
    tool is broken.

    Returns True to accept the folder.
    """
    def ask(prompt):
        try:
            return input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 'n'

    try:
        children = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError as exc:
        print(f"  Cannot read that folder: {exc}")
        return False

    lowered = {c.lower() for c in children}
    found = [n for n in _SECTION_NAMES if n.lower() in lowered]
    if found:
        print(f"  Found: {', '.join(found)}")
        missing = [n for n in _SECTION_NAMES if n not in found]
        if missing:
            print(f"  Not found: {', '.join(missing)} — that is fine if you")
            print("  do not have those, or if your folders use different names.")
        return True

    # No section folders. Did they point at a section itself?
    try:
        has_video = any(p.suffix.lower() in _VIDEO_HINT_EXTS
                        for p in root.iterdir() if p.is_file())
    except OSError:
        has_video = False

    print()
    print("  No 'Movies', 'TV Shows' or 'Music' folder inside there.")
    if has_video or root.name.lower() in {n.lower() for n in _SECTION_NAMES}:
        print(f"  It looks like '{root.name}' might BE one of those folders.")
        print(f"  If so, the answer is probably its parent:")
        print(f"    {root.parent}")
    elif children:
        shown = ', '.join(children[:6]) + ('...' if len(children) > 6 else '')
        print(f"  That folder contains: {shown}")
    else:
        print("  That folder is empty.")
    print()
    print("  You can still use this folder — media-agent lets you name your")
    print("  section folders yourself in the config afterwards.")
    return ask("  Use it anyway? [y/N] ") == 'y'


def cmd_init(args):
    """Interactive first-run configuration bootstrap."""
    config_path = Path.home() / '.config' / 'media-agent' / 'config.json'

    print("── media-agent init ──────────────────────────────────────────────────────────")
    print("This will create a config file at:")
    print(f"  {config_path}")
    print()

    if config_path.exists():
        answer = input("Config file already exists. Overwrite? [y/N] ").strip().lower()
        if answer != 'y':
            print("Aborted. Existing config unchanged.")
            return

    print("This is the ONE folder that contains your Movies, TV Shows and Music")
    print("folders — not one of those folders itself.")
    print()
    print("  Example: if you have D:\\Media\\Movies and D:\\Media\\TV Shows,")
    print("           then the answer is  D:\\Media")
    print()

    # Prompt for library_root
    while True:
        try:
            raw = input("Path to your media library folder: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted. No config was written.")
            return
        raw = raw.strip('"').strip("'")        # people paste quoted paths
        if not raw:
            print("  A path is required.")
            continue
        library_root = Path(raw).expanduser().resolve()
        if not library_root.is_dir():
            print(f"  That folder does not exist: {library_root}")
            print("  Check for typos, and make sure any network drive is connected.")
            continue
        if _confirm_library_root(library_root):
            break

    # Prompt for TMDB token
    print()
    print("TMDB Read Access Token (optional — required for tmdb-* commands).")
    print("Leave blank to skip; add it later via the config file or TMDB_TOKEN env var.")
    print("Get one free at: https://www.themoviedb.org/settings/api")
    try:
        if sys.stdin.isatty():
            # Hide the token as it's typed so it doesn't echo to the screen
            # or end up in a terminal's scrollback/transcript.
            tmdb_token = getpass.getpass("TMDB Read Access Token (input hidden): ").strip()
        else:
            # No real terminal to hide input on (piped stdin, some CI/test
            # environments) -- getpass would only print a warning and echo
            # anyway, so go straight to a plain prompt.
            tmdb_token = input("TMDB Read Access Token: ").strip()
    except (EOFError, KeyboardInterrupt):
        tmdb_token = ''

    config = {
        "library_root": str(library_root),
        "sections":     {"movies": None, "tv_shows": None, "music": None},
        "indexes_dir":  None,
        "reports_dir":  None,
        "ffprobe_path": None,
    }
    if tmdb_token:
        config["tmdb_read_access_token"] = tmdb_token

    config_path.parent.mkdir(parents=True, exist_ok=True)

    if tmdb_token:
        # The file is about to hold a credential. A plain open() creates it
        # with the umask's default permissions (often group/other-readable)
        # and there is a window between that creation and a later chmod
        # where the token sits on disk at those looser permissions. Passing
        # the restrictive mode to os.open() applies it atomically at
        # creation instead -- POSIX guarantees mode & ~umask, so there is no
        # window a concurrent local reader could land in. This only closes
        # the gap for a brand-new file; an *existing* file being overwritten
        # keeps whatever permissions it already had, which is why the
        # chmod-after fallback below still runs regardless.
        fd = os.open(str(config_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
            f.write('\n')
        _restrict_config_permissions(config_path)
    else:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
            f.write('\n')

    print()
    print(f"Config written to: {config_path}")

    # Surface a missing ffprobe now, while the user is still setting up, rather
    # than as a surprise in the middle of their first scan.
    if not shutil.which('ffprobe') and not shutil.which('ffprobe.exe'):
        print()
        print("One thing still to do — ffprobe was not found. It is needed to read")
        print("video files, so 'rescan' and 'scan-tvshows' will not work without it.")
        print(FFMPEG_HINT)

    print()
    print("Next steps:")
    print("  1. media-agent doctor     check everything is set up correctly")
    print("  2. media-agent status     see what is in your library")
    print()
    print("If 'media-agent' is not recognised, use 'python -m media_agent' instead.")
    print()
    print("Before anything that renames files, always preview it first:")
    print("  media-agent normalize --dry-run     then read the preview file")
    print("  media-agent normalize --apply       only once it looks right")
    print()
    print(f"To change section folder names or other options, edit:")
    print(f"  {config_path}")
