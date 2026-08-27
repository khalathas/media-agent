# Contributing

## Setup

```bash
git clone https://github.com/khalathas/media-agent
cd media-agent
pip install -e ".[dev]"
pytest
```

`-e` installs in editable mode, so your changes take effect without reinstalling.

## Layout

```
src/media_agent/
├── cli.py       argparse setup and command dispatch
├── config.py    config discovery, the Config dataclass, the runtime singleton
├── console.py   terminal helpers
├── probe.py     ffprobe and resolution classification
├── index.py     reading and writing the generated index files
├── naming.py    pure filename/title parsing -- no filesystem, no config
├── movies.py    rescan, rebuild-lowres, normalize, status
├── tv.py        scan-tvshows, normalize-tv, reassign-season
├── music.py     scan-music, organize-music
├── tmdb.py      the four tmdb-* commands
└── doctor.py    doctor, init
```

Dependencies flow one way:

```
config -> console/probe/index/naming -> movies/tv/music/tmdb -> cli
```

The active `Config` is a module-level singleton installed once by `cli.main()`.
Reach it with `get_config()`. Do **not** write `from .config import CONFIG` --
that captures `None` at import time.

`naming.py` touches neither the filesystem nor the config, which makes it the
easiest place to add well-tested logic.

## House rules

**Any new operation that renames, moves, or deletes files must ship with a
`--dry-run` path**, must require an explicit `--apply`, and must write a
preview file listing every intended change.

This is the tool's core promise to its users, and it is enforced by
`tests/test_safety_contract.py`. Those tests build a real library in a temp
directory and assert the files are still there afterwards. If you add a
destructive command, add it to `COMMANDS` in that file.

Be sceptical of safety tests that mock the filesystem -- an earlier version of
that file did, and passed even with the guard removed, because the commands
bailed out early for unrelated reasons and never reached the guard.

## Testing

```bash
pytest                  # everything
pytest tests/test_naming.py -v
```

Tests must not touch a real media library. Use `tmp_path`.

## Style

Match the surrounding code. Function bodies were moved verbatim out of a single
3,500-line script, so formatting varies -- follow the file you are in rather
than reformatting it.
