"""Commands: doctor, init."""

import importlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from .config import get_config


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
        print("       Run scripts/install_ffmpeg.ps1 (Windows) or scripts/install_ffmpeg.sh")
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
    token = get_config().tmdb_token or os.environ.get('TMDB_TOKEN', '').strip()
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

    # Prompt for library_root
    while True:
        raw = input("Path to your Plex Media Library folder: ").strip()
        if not raw:
            print("  library_root is required.")
            continue
        library_root = Path(raw).expanduser().resolve()
        if library_root.is_dir():
            break
        print(f"  Directory not found: {library_root}")
        print("  Please enter a valid path.")

    # Prompt for TMDB token
    print()
    print("TMDB Read Access Token (optional — required for tmdb-* commands).")
    print("Leave blank to skip; add it later via the config file or TMDB_TOKEN env var.")
    print("Get one free at: https://www.themoviedb.org/settings/api")
    tmdb_token = input("TMDB Read Access Token: ").strip()

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
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)
        f.write('\n')

    print()
    print(f"Config written to: {config_path}")
    print()
    print("Next steps:")
    print("  1. Run: python media_agent.py doctor   (verify everything is working)")
    print("  2. Run: python media_agent.py status   (see your library stats)")
    print()
    print("To customise section paths, indexes location, or other options,")
    print(f"edit {config_path}")
