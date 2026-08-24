"""ffprobe invocation and resolution classification."""

import json
import os
import subprocess
from datetime import datetime

from .config import get_config


def classify_resolution(width, height):
    if width is None or height is None:
        return 'unknown'
    if height >= 2160 or width >= 3840:
        return '4K'
    if height >= 1080 or width >= 1920:
        return '1080p'
    if height >= 720 or width >= 1280:
        return '720p'
    if height >= 480 or width >= 854:
        return '480p'
    if height >= 360 or width >= 640:
        return '360p'
    return 'SD'


def get_media_info(file_path):
    """Run ffprobe and return a dict of media properties, or {'error': ...}."""
    if not get_config().ffprobe:
        return {'error': 'ffprobe not found — set ffprobe_path in config or add ffprobe to PATH'}
    try:
        result = subprocess.run(
            [get_config().ffprobe, '-v', 'quiet', '-print_format', 'json',
             '-show_streams', '-show_format', file_path],
            capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=30,
        )
        if result.returncode != 0:
            return {'error': result.stderr.strip() or 'ffprobe failed'}
        data    = json.loads(result.stdout)
        fmt     = data.get('format', {})
        streams = data.get('streams', [])
        video   = next((s for s in streams if s.get('codec_type') == 'video'), {})
        audio   = next((s for s in streams if s.get('codec_type') == 'audio'), {})
        has_sub = any(s.get('codec_type') == 'subtitle' for s in streams)
        bitrate = int(fmt.get('bit_rate', 0)) // 1000
        mtime   = datetime.fromtimestamp(
            os.path.getmtime(file_path)).strftime('%Y-%m-%d %H:%M:%S')
        return {
            'name':          os.path.basename(file_path),
            'extension':     os.path.splitext(file_path)[1].lower(),
            'width':         video.get('width'),
            'height':        video.get('height'),
            'video_codec':   video.get('codec_name'),
            'audio_codec':   audio.get('codec_name'),
            'bitrate':       f'{bitrate} kbps',
            'has_subtitles': has_sub,
            'filesize':      int(fmt.get('size', 0)),
            'date_added':    mtime,
        }
    except subprocess.TimeoutExpired:
        return {'error': 'ffprobe timed out'}
    except Exception as e:
        return {'error': str(e)}


def scan_video_files(section_path):
    """Return dict of {filename: full_path} for all video files under path."""
    files = {}
    for root, dirs, fnames in os.walk(section_path):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith('@') and not d.startswith('.'))
        for fname in fnames:
            if os.path.splitext(fname)[1].lower() in get_config().video_exts:
                fpath = os.path.join(root, fname).replace('\\', '/')
                if fname in files:
                    # Duplicate filename in different subdirs — keep both with paths
                    existing = files[fname]
                    if isinstance(existing, str):
                        files[fname] = [existing, fpath]
                    else:
                        existing.append(fpath)
                else:
                    files[fname] = fpath
    return files
