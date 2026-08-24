"""Reading and writing the generated index files."""

import csv
import json

from .config import get_config
from .probe import classify_resolution


LOW_RES_COLS = ['name', 'width', 'height', 'resolution_class',
                'extension', 'audio_codec', 'video_codec',
                'bitrate', 'filesize', 'date_added']


def load_movies_json():
    path = get_config().indexes['movies']
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_movies_json(data):
    path = get_config().indexes['movies']
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _check_low_res_sync(movies):
    """Warn if movies_below_720p.csv is out of sync with movies.json."""
    try:
        with open(get_config().indexes['movies_low_res'], newline='', encoding='utf-8') as f:
            csv_names = {r['name'] for r in csv.DictReader(f)}
        expected = {m['name'] for m in movies
                    if (m.get('height') or 0) < 720 and (m.get('width') or 0) < 1280}
        if csv_names != expected:
            print(f"\nWARNING: movies_below_720p.csv is out of sync "
                  f"({len(csv_names)} rows vs {len(expected)} expected).")
            print("Run:  python media_agent.py rebuild-lowres")
        else:
            print(f"\nmovies_below_720p.csv is in sync ({len(csv_names)} entries).")
    except Exception as e:
        print(f"\nCould not verify movies_below_720p.csv: {e}")


def _rebuild_low_res(movies, verbose=False):
    below = []
    for m in movies:
        w, h = m.get('width'), m.get('height')
        if w is not None and h is not None and h < 720 and w < 1280:
            res = classify_resolution(w, h)
            below.append({
                'name':             m['name'],
                'width':            w,
                'height':           h,
                'resolution_class': res,
                'extension':        m.get('extension', ''),
                'audio_codec':      m.get('audio_codec', ''),
                'video_codec':      m.get('video_codec', ''),
                'bitrate':          m.get('bitrate', ''),
                'filesize':         m.get('filesize', ''),
                'date_added':       m.get('date_added', ''),
            })
    below.sort(key=lambda r: (r['height'], r['width']))
    path = get_config().indexes['movies_low_res']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=LOW_RES_COLS)
        writer.writeheader()
        writer.writerows(below)
    if verbose:
        print(f"\nmovies_below_720p.csv rebuilt: {len(below)} entries.")
    return len(below)
