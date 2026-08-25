"""ffprobe invocation and resolution classification."""

import json
import os
import shutil
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


def video_path_key(fpath, section_path):
    """A stable identity for a video file: its path below its section folder.

    A filename is not an identity. Two folders can each hold a "Movie.mkv", and
    they may be different films or different cuts of one. Keying by basename
    collapsed them into a single entry, so one copy went unindexed and renames
    acted on whichever path happened to come first.
    """
    try:
        rel = os.path.relpath(fpath, section_path)
    except ValueError:
        rel = fpath           # different drive; fall back to the absolute path
    return rel.replace(os.sep, '/').replace('\\', '/')


def scan_video_files(section_path):
    """Return {path_key: full_path} for every video file under section_path.

    One entry per real file on disk. Duplicate basenames in different folders
    stay distinct, because their path keys differ.
    """
    files = {}
    for root, dirs, fnames in os.walk(section_path):
        dirs[:] = sorted(d for d in dirs
                         if not d.startswith('@') and not d.startswith('.'))
        for fname in fnames:
            if os.path.splitext(fname)[1].lower() in get_config().video_exts:
                fpath = os.path.join(root, fname).replace('\\', '/')
                files[video_path_key(fpath, section_path)] = fpath
    return files


def group_by_basename(disk_files):
    """Group a scan result by filename: {basename: [path_key, ...]}.

    Used to migrate index entries written before paths were recorded, and to
    spot basenames that are genuinely ambiguous.
    """
    by_name = {}
    for key in disk_files:
        by_name.setdefault(os.path.basename(key), []).append(key)
    return by_name


def is_case_only_rename(old_path, new_path):
    """True if new_path names the same file as old_path, differing only in
    letter case (or path-separator style) -- not a different file that
    already happens to occupy that name.

    os.path.exists(new_path) is True for both on a case-insensitive
    filesystem (Windows, and macOS by default), which otherwise makes a
    pure case correction ("the.matrix.mkv" -> "The.Matrix.mkv") look like a
    real conflict and get skipped and reported as "target already exists"
    -- nothing was wrong, and nothing happened.
    """
    return (old_path != new_path
            and os.path.normcase(os.path.normpath(old_path))
                == os.path.normcase(os.path.normpath(new_path)))


def rename_case_only(old_path, new_path):
    """Perform a case-only rename via a temporary intermediate name.

    A direct move/rename between two paths that differ only in case can
    silently no-op on some filesystem/OS combinations, since the OS
    considers them the same file already. Renaming to a distinct
    intermediate name first, then to the final name, forces the case
    change to actually land regardless of that.

    If the second move fails -- a network share hiccup, an antivirus lock, a
    permissions change, all realistic on the same shares this tool targets --
    the file would otherwise be stranded at tmp_path, a name nothing else in
    the system knows about: the index still says old_path, and the file is at
    neither old_path nor new_path. This rolls back to old_path on failure, so
    the caller's normal "rename failed, nothing changed" assumption holds. If
    the rollback itself fails too, the raised error says exactly where the
    file actually is -- that location must never be silently lost.
    """
    tmp_path = new_path + '.case-rename-tmp'
    n = 1
    while os.path.exists(tmp_path):
        n += 1
        tmp_path = f"{new_path}.case-rename-tmp{n}"
    shutil.move(old_path, tmp_path)
    try:
        shutil.move(tmp_path, new_path)
    except Exception as exc:
        try:
            shutil.move(tmp_path, old_path)
        except Exception as rollback_exc:
            raise OSError(
                f"case-only rename failed and the automatic rollback also "
                f"failed -- the file is currently at: {tmp_path}\n"
                f"    original error: {exc}\n"
                f"    rollback error: {rollback_exc}"
            ) from exc
        raise
