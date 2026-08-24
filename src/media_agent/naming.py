"""Pure filename and title parsing.

Nothing in this module touches the filesystem or the config, which makes it the
easiest part of the tool to test.
"""

import os
import re


_LOWER_WORDS = {
    'a', 'an', 'the',
    'and', 'but', 'or', 'nor', 'for', 'so', 'yet',
    'at', 'by', 'in', 'of', 'on', 'to', 'up', 'as', 'from', 'into', 'with',
}


def _case_word(word, is_first, is_last):
    """Case a single word: preserve existing uppercase, capitalise lowercase starts."""
    if not word:
        return word
    # Hyphenated compound — case each part (e.g. x-men → X-Men, web-dl stays)
    if '-' in word:
        parts = word.split('-')
        cased = [_case_word(p, i == 0 and is_first, i == len(parts) - 1 and is_last)
                 for i, p in enumerate(parts)]
        return '-'.join(cased)
    lower = word.lower()
    # Small word in the middle: only lowercase it if it's already all-lowercase
    if not is_first and not is_last and lower in _LOWER_WORDS and word == lower:
        return lower
    # Word starts lowercase → capitalise first letter, preserve the rest
    if word[0].islower():
        return word[0].upper() + word[1:]
    # Already starts with uppercase (includes acronyms like BBC, RSC) → keep as-is
    return word


def smart_title_case(s):
    """
    Title-case a string while:
    - Preserving existing uppercase (acronyms, release tags already capitalised)
    - Only capitalising words that start lowercase
    - Lowercasing small words (articles, short prepositions) in the middle
    - Handling hyphenated compounds word-by-word
    """
    words = s.split()
    return ' '.join(
        _case_word(w, i == 0, i == len(words) - 1)
        for i, w in enumerate(words)
    )


_STRIP_RE = re.compile(
    r'[\s._-]*\b('
    r'2160p|1080p|720p|480p|360p|4k|uhd'
    r'|bluray|blu-ray|bdrip|brrip|webrip|web[-\s]?dl|webdl|hdtv'
    r'|dvdrip|dvdscr|hdcam|hdrip|hdzip'
    r'|x264|x265|h264|h265|hevc|xvid|divx|avc'
    r'|aac|ac3|dts|mp3|dd5\.1|truehd|atmos|flac|eac3'
    r'|remux|repack|extended|unrated|theatrical|directors\.cut'
    r'|yify|yts|rarbg|ettv|etrg|galaxyrg|galleryrg'
    r')[\s._-]*.*$',
    re.IGNORECASE
)

_YEAR_RE  = re.compile(r'[\[(]?(19[5-9]\d|20[0-2]\d)[\])]?')
_QUAL_RE  = re.compile(r'\b(2160p|1080p|720p|480p|4k)\b', re.IGNORECASE)

# Filenames that are clearly extras/trailers — skip normalization
_SKIP_RE  = re.compile(
    r'\b(trailer|teaser|featurette|behind.the.scenes|deleted.scene|'
    r'interview|short|sample|extra|bonus|special)\b', re.IGNORECASE)


def parse_movie_filename(filename):
    """
    Attempt to extract (title, year, quality) from a raw filename.
    Returns None if the name already looks clean or can't be parsed.
    """
    stem = os.path.splitext(filename)[0]
    ext  = os.path.splitext(filename)[1].lower()

    # Skip extras and trailers
    if _SKIP_RE.search(stem):
        return None

    # Already in clean format "Title (Year)" or "Title (Year) [Quality]"
    if re.match(r'^[^[]+\(\d{4}\)', stem):
        return None

    # Find year — allow [YYYY] or (YYYY) or bare YYYY
    year_m = _YEAR_RE.search(stem)
    year   = year_m.group(1) if year_m else None

    # Find quality tag
    qual_m = _QUAL_RE.search(stem)
    qual   = qual_m.group(1).lower() if qual_m else None

    # Extract title: everything before the year (or quality tag)
    if year_m:
        raw_title = stem[:year_m.start()]
    elif qual_m:
        raw_title = stem[:qual_m.start()]
    else:
        raw_title = _STRIP_RE.sub('', stem)

    # Clean up separators (dots, underscores → spaces)
    title = re.sub(r'[._]+', ' ', raw_title)
    # Remove stray brackets/parens left at the end (e.g. from "[2003]" parsing)
    title = re.sub(r'[\[(]+\s*$', '', title)
    # Remove non-year parentheticals at the end like "(Unrated)", "(UnCut)", "(ReQ)"
    title = re.sub(r'\s*\([^)]*\)\s*$', '', title)
    title = re.sub(r'\s{2,}', ' ', title).strip().rstrip(' -').strip()

    if not title:
        return None

    return {'title': title, 'year': year, 'quality': qual, 'ext': ext}


def build_clean_name(parsed):
    """Build canonical filename from parsed components."""
    name = smart_title_case(parsed['title'])
    if parsed['year']:
        name += f" ({parsed['year']})"
    if parsed['quality']:
        name += f" [{parsed['quality']}]"
    name += parsed['ext']
    return name


def _sanitize_path_component(name):
    """Strip characters illegal in Windows file/folder names."""
    # Replace illegal chars with similar safe alternatives or spaces
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    # Strip leading/trailing dots and spaces (Windows quirk)
    name = name.strip('. ')
    return name or '_'


_WIN_INVALID_RE = re.compile(r'[\\/:*?"<>|]')


def _canonical_filename(tmdb_title, tmdb_year, tmdb_id, ext):
    """
    Build a Plex-canonical filename:
        Title (Year) {tmdb-XXXXX}.ext
    Replaces Windows-invalid characters in the title with safe equivalents.
    """
    # Replace colon-space and standalone colon with " -" (common subtitle separator)
    safe_title = re.sub(r':\s*', ' - ', tmdb_title)
    # Remove any remaining Windows-invalid chars
    safe_title = _WIN_INVALID_RE.sub('', safe_title)
    # Collapse multiple spaces
    safe_title = re.sub(r'  +', ' ', safe_title).strip()
    if tmdb_year:
        stem = f"{safe_title} ({tmdb_year}) {{tmdb-{tmdb_id}}}"
    else:
        stem = f"{safe_title} {{tmdb-{tmdb_id}}}"
    return stem + ext
