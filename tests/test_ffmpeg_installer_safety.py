"""Regression tests for scripts/install_ffmpeg.sh and scripts/install_ffmpeg.ps1.

These scripts download and execute a third-party binary (ffprobe/ffmpeg) with
no signature available. The reviewed fix adds: a pinned version (no more
"latest" alias), checksum verification before extraction, and archive-member
path validation before extraction (reject absolute paths / ".." traversal).

We don't hit the network here -- these tests exercise the verification and
safe-extraction helper functions directly, against local fixture archives,
by sourcing/dot-sourcing the real scripts (guarded so the installer body
itself does not run) rather than reimplementing the logic under test.
"""

import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

import test_ffmpeg_installer_safety as mod

REPO_ROOT = Path(__file__).resolve().parent.parent
SH_SCRIPT = REPO_ROOT / "scripts" / "install_ffmpeg.sh"
PS1_SCRIPT = REPO_ROOT / "scripts" / "install_ffmpeg.ps1"


def _all_bash_candidates():
    """Every "bash" found on PATH, in PATH order.

    shutil.which() only returns the first match -- on Windows that can be
    the unusable WSL launcher stub (C:\\Windows\\System32\\bash.exe) with a
    real, working bash (Git Bash, MSYS2) sitting later on PATH. Stopping at
    the first candidate (as an earlier version of _find_usable_bash did)
    then reports "no usable bash" and skips every test in this file, even
    though a working one was one directory further down PATH the whole
    time -- reproduced directly on a real Windows host with both installed.
    """
    seen = set()
    candidates = []
    exts = os.environ.get("PATHEXT", ".EXE").split(os.pathsep) if os.name == "nt" else [""]
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        for ext in exts:
            candidate = os.path.join(directory, "bash" + ext.lower())
            key = os.path.normcase(candidate)
            if key in seen:
                continue
            seen.add(key)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                candidates.append(candidate)
    return candidates


# check_archive_paths (install_ffmpeg.sh) requires GNU tar's --force-local.
# Windows ships a bsdtar at C:\Windows\System32\tar.exe that rejects that
# flag outright ("Option --force-local is not supported"). A bash candidate
# that runs `echo` fine can still inherit a PATH where `tar` resolves to
# that bsdtar instead of a real GNU tar -- reproduced directly: a non-login
# MSYS2 bash can resolve `tar` to the Windows one depending on PATH order,
# passing a trivial echo probe while every archive-hardening test then fails
# with "could not list archive contents" for a reason unrelated to the
# scripts under test.
#
# `tar --force-local --version` alone -- no archive, no temp file --
# distinguishes them cleanly: GNU tar accepts the flag and exits 0; bsdtar
# rejects the flag itself, before ever looking at what --version would have
# printed, and exits nonzero. An earlier version of this probe created a
# real empty archive via shell `mktemp` to list instead; the thirteenth-pass
# reviewer found that fails closed for the wrong reason in a restricted
# environment where the shell's own /tmp isn't writable (mktemp itself
# fails, so every Bash test silently skips) and can leak the temp file if
# the child is killed on a timeout before either cleanup branch runs. This
# version needs no filesystem access at all.
def _tar_probe_script(tar_cmd="tar"):
    """`tar_cmd` defaults to a bare PATH lookup, matching real usage. Tests
    that need to exercise an *incompatible* tar deterministically pass an
    explicit path instead -- see the comment on test_probe_bash_rejects_...
    below for why PATH injection alone isn't reliable for that.
    """
    return f'{tar_cmd} --force-local --version >/dev/null 2>&1 && echo __BASH_USABLE__\n'


_TAR_PROBE_SCRIPT = _tar_probe_script()


def _probe_bash(candidate, tar_cmd="tar"):
    """Run a real, non-interactive script through `candidate` and report
    whether it actually works -- both the shell itself and, specifically,
    a GNU-tar-compatible `tar` (see _tar_probe_script above). Any failure
    -- non-zero exit, unexpected output, or a hang (some broken WSL
    launcher configurations block waiting for interactive setup) -- counts
    as unusable.
    """
    try:
        result = subprocess.run(
            [candidate, "-c", _tar_probe_script(tar_cmd)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "__BASH_USABLE__" in result.stdout


def _find_usable_bash():
    """The first candidate on PATH that actually runs a command, trying
    every "bash" found rather than giving up after the first unusable one.
    """
    for candidate in _all_bash_candidates():
        if _probe_bash(candidate):
            return candidate
    return None


BASH = _find_usable_bash()
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def _run_bash(snippet: str) -> subprocess.CompletedProcess:
    script = f'set -e\nsource "{SH_SCRIPT.as_posix()}"\n{snippet}\n'
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_powershell(snippet: str) -> subprocess.CompletedProcess:
    script = f'. "{PS1_SCRIPT}" -NoRun\n{snippet}\n'
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# _find_usable_bash itself -- the bug this file's own bash-detection had
# ---------------------------------------------------------------------------


def _fake_run_for(outcomes):
    """Build a fake subprocess.run that maps each candidate path (the
    first element of the invoked command list) to a canned outcome:
    a CompletedProcess, or an exception instance/class to raise.
    """
    def fake_run(cmd, **kw):
        outcome = outcomes[cmd[0]]
        if isinstance(outcome, BaseException) or (
                isinstance(outcome, type) and issubclass(outcome, BaseException)):
            raise outcome
        return outcome
    return fake_run


_USABLE = subprocess.CompletedProcess([], 0, stdout="__BASH_USABLE__\n", stderr="")
_WSL_NO_DISTRO = subprocess.CompletedProcess(
    [], 1, stdout="", stderr="Windows Subsystem for Linux has no installed distributions.\n")
_UNEXPECTED_OUTPUT = subprocess.CompletedProcess([], 0, stdout="", stderr="")


class TestFindUsableBash:
    def test_none_when_nothing_on_path(self, monkeypatch):
        monkeypatch.setattr(mod, "_all_bash_candidates", lambda: [])
        assert _find_usable_bash() is None

    def test_returns_the_path_when_it_runs_successfully(self, monkeypatch):
        monkeypatch.setattr(mod, "_all_bash_candidates", lambda: ["/fake/working/bash"])
        monkeypatch.setattr(subprocess, "run", _fake_run_for({"/fake/working/bash": _USABLE}))

        assert _find_usable_bash() == "/fake/working/bash"

    def test_none_when_the_only_candidate_exits_nonzero(self, monkeypatch):
        """Reproduces the reported WSL failure mode: a launcher stub that's
        present on PATH but exits with an error because no distribution is
        registered."""
        monkeypatch.setattr(mod, "_all_bash_candidates", lambda: ["/fake/wsl/bash.exe"])
        monkeypatch.setattr(subprocess, "run", _fake_run_for({"/fake/wsl/bash.exe": _WSL_NO_DISTRO}))

        assert _find_usable_bash() is None

    def test_none_when_the_only_candidate_hangs(self, monkeypatch):
        """Some broken WSL configurations block waiting for interactive
        setup instead of exiting -- must not hang the whole test collection
        waiting on it; a timeout is treated the same as "unusable"."""
        monkeypatch.setattr(mod, "_all_bash_candidates", lambda: ["/fake/hanging/bash.exe"])
        monkeypatch.setattr(subprocess, "run", _fake_run_for({
            "/fake/hanging/bash.exe": subprocess.TimeoutExpired(["/fake/hanging/bash.exe"], 10)}))

        assert _find_usable_bash() is None

    def test_none_when_the_only_candidate_output_is_unexpected(self, monkeypatch):
        """Exit code 0 alone isn't enough -- confirm it actually ran *our*
        command rather than, say, silently succeeding at something else."""
        monkeypatch.setattr(mod, "_all_bash_candidates", lambda: ["/fake/odd/bash"])
        monkeypatch.setattr(subprocess, "run", _fake_run_for({"/fake/odd/bash": _UNEXPECTED_OUTPUT}))

        assert _find_usable_bash() is None

    def test_falls_through_an_unusable_wsl_stub_to_a_working_bash_later_on_path(self, monkeypatch):
        """The eleventh-pass reviewer's exact reproduction: an unusable WSL
        launcher earlier on PATH must not make the whole probe give up --
        a real, working bash sitting further down PATH is still found."""
        monkeypatch.setattr(mod, "_all_bash_candidates", lambda: [
            r"C:\Windows\System32\bash.exe", r"C:\msys64\usr\bin\bash.exe"])
        monkeypatch.setattr(subprocess, "run", _fake_run_for({
            r"C:\Windows\System32\bash.exe": _WSL_NO_DISTRO,
            r"C:\msys64\usr\bin\bash.exe": _USABLE,
        }))

        assert _find_usable_bash() == r"C:\msys64\usr\bin\bash.exe"

    def test_gives_up_only_after_every_candidate_fails(self, monkeypatch):
        monkeypatch.setattr(mod, "_all_bash_candidates", lambda: [
            "/fake/wsl1/bash.exe", "/fake/wsl2/bash.exe"])
        monkeypatch.setattr(subprocess, "run", _fake_run_for({
            "/fake/wsl1/bash.exe": _WSL_NO_DISTRO,
            "/fake/wsl2/bash.exe": _WSL_NO_DISTRO,
        }))

        assert _find_usable_bash() is None


# ---------------------------------------------------------------------------
# _probe_bash against a REAL shell (not mocked) -- the twelfth-pass reviewer's
# toolchain-mismatch finding is specifically about real inherited PATH
# behavior, which a mocked subprocess.run can't reproduce.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_probe_bash_accepts_the_real_selected_shell():
    """BASH was already selected by _find_usable_bash() at import time, so
    if this fails, every other test in this file is running (or skipping)
    against a shell that shouldn't have passed in the first place."""
    assert _probe_bash(BASH) is True


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_probe_bash_rejects_a_shell_whose_resolved_tar_is_incompatible(tmp_path):
    """Reproduction of the twelfth-pass reviewer's finding: a bash whose
    resolved `tar` rejects --force-local (as Windows' bundled bsdtar does)
    must fail the probe -- a bare `echo` through the same shell would still
    succeed, which is exactly how the previous version of this probe could
    select a shell that runs fine but can't actually list an archive the
    way check_archive_paths needs.

    An earlier version of this test hardcoded the literal Windows path
    "C:\\Windows\\System32" and joined it with os.pathsep -- on POSIX,
    os.pathsep is ":", and that path string contains its own ":" (after
    "C"), so the "poisoning" had no effect at all on Linux CI, a
    deterministic failure there. The fix (prepending a fake tar's own
    directory to PATH via os.pathsep) was portable in principle, but
    turned out not to reliably win PATH-resolution precedence against
    every Git-for-Windows bash variant either: GitHub's windows-latest
    runner resolves bash to a different binary (Git\\bin\\bash.exe) than
    this project's dev machine (Git\\usr\\bin\\bash.exe), and that variant
    evidently prepends its own bundled GNU tar ahead of anything set via
    the parent process's PATH, so the fake tar never got resolved there
    either -- confirmed by the CI log showing _probe_bash() returned True
    despite the fake tar being on PATH.

    Sidesteps PATH-resolution precedence entirely instead of trying to win
    it: _probe_bash()'s tar_cmd parameter lets this test tell the probe
    script exactly which tar to invoke, by its own absolute path, the same
    way a real incompatible tar would be invoked once actually resolved --
    this tests the probe's REACTION to an incompatible tar (the thing that
    actually matters) without depending on any particular bash build's
    PATH-merging behavior to get there.
    """
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_tar = fake_bin / "tar"
    fake_tar.write_text("#!/usr/bin/env bash\nexit 1\n", encoding='utf-8')
    fake_tar.chmod(0o755)

    assert _probe_bash(BASH, tar_cmd=fake_tar.as_posix()) is False, (
        "the probe must fail when the resolved tar can't do --force-local, "
        "not just when the shell itself doesn't run"
    )


# ---------------------------------------------------------------------------
# bash script (Linux fallback path)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_sourcing_does_not_run_installer(tmp_path):
    """Sourcing the script for its functions must not attempt a real install
    (no network access, no vendor/ffmpeg mutation) -- this is what lets the
    other tests here probe the real verification logic safely."""
    result = _run_bash("echo SOURCED_OK")
    assert result.returncode == 0, result.stderr
    assert "SOURCED_OK" in result.stdout
    # None of the installer's own progress/status lines should appear.
    assert "Installing to" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_verify_checksum_accepts_matching_hash(tmp_path):
    f = tmp_path / "payload.bin"
    f.write_bytes(b"hello world")
    result = _run_bash(f'''
good=$(compute_md5 "{f.as_posix()}")
verify_checksum "{f.as_posix()}" "$good" && echo VERIFIED
''')
    assert result.returncode == 0, result.stderr
    assert "VERIFIED" in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_verify_checksum_rejects_mismatched_hash(tmp_path):
    """This is the actual P1 bug reproduction: before the fix, there was no
    verify_checksum function at all -- any downloaded bytes were extracted
    and run unconditionally. Confirm a wrong hash is now refused."""
    f = tmp_path / "payload.bin"
    f.write_bytes(b"hello world")
    result = _run_bash(f'''
if verify_checksum "{f.as_posix()}" "0000000000000000000000000000000"; then
    echo ACCEPTED_BAD_HASH
else
    echo REJECTED_BAD_HASH
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_BAD_HASH" in result.stdout
    assert "ACCEPTED_BAD_HASH" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_accepts_safe_archive(tmp_path):
    src = tmp_path / "safe"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "file.txt").write_text("ok")
    archive = tmp_path / "safe.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        tf.add(src, arcname=".")

    result = _run_bash(f'check_archive_paths "{archive.as_posix()}" && echo SAFE')
    assert result.returncode == 0, result.stderr
    assert "SAFE" in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_rejects_traversal(tmp_path):
    """Reproduction: craft an archive (as a hostile/corrupted download would)
    with a member path that escapes the extraction directory, and confirm
    the guard added for P2-5 refuses it."""
    payload = tmp_path / "payload.txt"
    payload.write_text("pwned")
    archive = tmp_path / "evil.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        info = tarfile.TarInfo(name="../../evil_escape.txt")
        data = payload.read_bytes()
        info.size = len(data)
        import io

        tf.addfile(info, io.BytesIO(data))

    result = _run_bash(f'''
if check_archive_paths "{archive.as_posix()}"; then
    echo ACCEPTED_TRAVERSAL
else
    echo REJECTED_TRAVERSAL
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_TRAVERSAL" in result.stdout
    assert "ACCEPTED_TRAVERSAL" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_rejects_absolute_path(tmp_path):
    payload = tmp_path / "payload.txt"
    payload.write_text("pwned")
    archive = tmp_path / "evil_abs.tar.xz"
    with tarfile.open(archive, "w:xz") as tf:
        info = tarfile.TarInfo(name="/etc/evil_absolute.txt")
        data = payload.read_bytes()
        info.size = len(data)
        import io

        tf.addfile(info, io.BytesIO(data))

    result = _run_bash(f'''
if check_archive_paths "{archive.as_posix()}"; then
    echo ACCEPTED_ABSOLUTE
else
    echo REJECTED_ABSOLUTE
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_ABSOLUTE" in result.stdout
    assert "ACCEPTED_ABSOLUTE" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_rejects_symlink_member(tmp_path):
    """A symlink's target isn't validated by the path-traversal check --
    only its own entry name is safe-looking. A later member extracted
    "through" the symlink could still escape the destination, so symlink
    members are refused outright rather than have their targets validated.
    """
    archive = tmp_path / "evil_symlink.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo(name="innocuous_name")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)

    result = _run_bash(f'''
if check_archive_paths "{archive.as_posix()}"; then
    echo ACCEPTED_SYMLINK
else
    echo REJECTED_SYMLINK
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_SYMLINK" in result.stdout
    assert "ACCEPTED_SYMLINK" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_rejects_hardlink_member(tmp_path):
    archive = tmp_path / "evil_hardlink.tar"
    with tarfile.open(archive, "w") as tf:
        data = b"hello"
        info = tarfile.TarInfo(name="regular.txt")
        info.size = len(data)
        import io
        tf.addfile(info, io.BytesIO(data))
        hard = tarfile.TarInfo(name="hardlink_name")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "regular.txt"
        tf.addfile(hard)

    result = _run_bash(f'''
if check_archive_paths "{archive.as_posix()}"; then
    echo ACCEPTED_HARDLINK
else
    echo REJECTED_HARDLINK
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_HARDLINK" in result.stdout
    assert "ACCEPTED_HARDLINK" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_rejects_too_many_members(tmp_path):
    """A decompression-bomb-style guard: an archive with an implausible
    number of members is refused rather than extracted unconditionally.
    The real default (5000) is far above anything a real ffmpeg build
    needs, so this exercises the override rather than waiting for a
    thousands-of-entries fixture.
    """
    archive = tmp_path / "many.tar"
    import io
    with tarfile.open(archive, "w") as tf:
        for i in range(10):
            data = f"f{i}".encode()
            info = tarfile.TarInfo(name=f"file{i}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    result = _run_bash(f'''
export MEDIA_AGENT_MAX_ARCHIVE_MEMBERS=5
if check_archive_paths "{archive.as_posix()}"; then
    echo ACCEPTED_TOO_MANY
else
    echo REJECTED_TOO_MANY
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_TOO_MANY" in result.stdout
    assert "ACCEPTED_TOO_MANY" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_accepts_reasonable_member_count(tmp_path):
    """The count guard must not reject an ordinary, real-shaped archive --
    only one that actually violates the (overridden, for this test) limit."""
    archive = tmp_path / "few.tar"
    import io
    with tarfile.open(archive, "w") as tf:
        for i in range(3):
            data = f"f{i}".encode()
            info = tarfile.TarInfo(name=f"file{i}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))

    result = _run_bash(f'''
export MEDIA_AGENT_MAX_ARCHIVE_MEMBERS=5
check_archive_paths "{archive.as_posix()}" && echo ACCEPTED_OK
''')
    assert result.returncode == 0, result.stderr
    assert "ACCEPTED_OK" in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_rejects_declared_size_over_limit(tmp_path):
    """A decompression-bomb-style guard on total declared uncompressed size,
    summed across every member's size field in the verbose listing."""
    archive = tmp_path / "sized.tar"
    with tarfile.open(archive, "w") as tf:
        data = b"hello world"  # 11 bytes
        info = tarfile.TarInfo(name="file.txt")
        info.size = len(data)
        import io
        tf.addfile(info, io.BytesIO(data))

    result = _run_bash(f'''
export MEDIA_AGENT_MAX_ARCHIVE_BYTES=5
if check_archive_paths "{archive.as_posix()}"; then
    echo ACCEPTED_OVERSIZE
else
    echo REJECTED_OVERSIZE
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_OVERSIZE" in result.stdout
    assert "ACCEPTED_OVERSIZE" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_fails_closed_on_unparseable_size(tmp_path):
    """The eleventh-pass reviewer's finding: an empty or non-numeric size
    field from `tar -tvf` was silently converted to 0, letting that
    member's real (unknown) size pass uncounted -- the size guard fails
    open exactly where it matters most, on the field it can't read.

    No real archive makes a conforming `tar -tvf` emit a non-numeric size
    for a regular file, so this is faked by shadowing `tar` itself with a
    shell function that returns a safe path listing but a garbage verbose
    size field -- reproducing "a tar output format this parser can't read"
    without needing an actually-nonconformant tar binary.
    """
    result = _run_bash('''
tar() {
    case "$2" in
        -tf)  echo "file.txt" ;;
        -tvf) echo "-rw-r--r-- 0/0 NOT-A-NUMBER 1969-12-31 19:00 file.txt" ;;
    esac
}
if check_archive_paths "irrelevant.tar"; then
    echo ACCEPTED_UNPARSEABLE
else
    echo REJECTED_UNPARSEABLE
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_UNPARSEABLE" in result.stdout
    assert "ACCEPTED_UNPARSEABLE" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_fails_closed_on_arithmetic_overflow(tmp_path):
    """The twelfth-pass reviewer's finding: a declared size of 2^63 (one
    past bash's signed-64-bit arithmetic range) wraps total_size NEGATIVE
    on addition, which then passes even a tiny configured limit -- fail-open
    on exactly the case the size guard exists to catch, just via arithmetic
    instead of a non-numeric field. Verified directly before writing this
    test: `total=$((0 + 9223372036854775808))` really does evaluate to
    -9223372036854775808 in bash. Faked the same way as the unparseable-size
    case above, since no real archive can declare a size this large without
    actually containing that many bytes.
    """
    result = _run_bash('''
tar() {
    case "$2" in
        -tf)  echo "huge.bin" ;;
        -tvf) echo "-rw-r--r-- 0/0 9223372036854775808 1969-12-31 19:00 huge.bin" ;;
    esac
}
export MEDIA_AGENT_MAX_ARCHIVE_BYTES=1000
if check_archive_paths "irrelevant.tar"; then
    echo ACCEPTED_OVERFLOW
else
    echo REJECTED_OVERFLOW
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_OVERFLOW" in result.stdout
    assert "ACCEPTED_OVERFLOW" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_check_archive_paths_rejects_mismatched_listing_lengths(tmp_path):
    """The twelfth-pass reviewer's finding: the paired-read loop stops as
    soon as either the plain or verbose listing runs out, so if the two
    listings ever disagree on member count, any member past the shorter
    stream's end is silently never visited by any check at all -- including
    the path-traversal check. Faked with a plain listing one entry longer
    than its paired verbose listing (the extra entry is itself a traversal
    attempt, to make unmistakable what slipping through would mean), since
    both real listings come from the same tar binary listing the same file
    twice and can't disagree in practice.
    """
    result = _run_bash('''
tar() {
    case "$2" in
        -tf)  echo "safe.txt"; echo "../escape.txt" ;;
        -tvf) echo "-rw-r--r-- 0/0 5 1969-12-31 19:00 safe.txt" ;;
    esac
}
if check_archive_paths "irrelevant.tar"; then
    echo ACCEPTED_MISMATCH
else
    echo REJECTED_MISMATCH
fi
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_MISMATCH" in result.stdout
    assert "ACCEPTED_MISMATCH" not in result.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_pinned_version_and_checksums_present():
    """The installer must not fall back to a mutable 'latest' URL alias --
    guard against a future edit silently reintroducing it."""
    text = SH_SCRIPT.read_text()
    assert "FFMPEG_STATIC_VERSION=" in text
    assert "FFMPEG_STATIC_MD5_AMD64=" in text
    assert "ffmpeg-release-amd64-static.tar.xz" not in text  # old mutable alias
    assert "${FFMPEG_STATIC_VERSION}-${arch_tag}-static.tar.xz" in text


# ---------------------------------------------------------------------------
# PowerShell script (Windows path)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_dot_sourcing_with_norun_does_not_run_installer():
    result = _run_powershell("Write-Host SOURCED_OK")
    assert result.returncode == 0, result.stderr
    assert "SOURCED_OK" in result.stdout
    assert "Installing to" not in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_checksum_accepts_matching_hash(tmp_path):
    f = tmp_path / "payload.bin"
    f.write_bytes(b"hello world")
    result = _run_powershell(f'''
$hash = (Get-FileHash -Path "{f}" -Algorithm SHA256).Hash
if (Test-Sha256Checksum -Path "{f}" -Expected $hash) {{ Write-Host VERIFIED }}
''')
    assert result.returncode == 0, result.stderr
    assert "VERIFIED" in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_checksum_rejects_mismatched_hash(tmp_path):
    f = tmp_path / "payload.bin"
    f.write_bytes(b"hello world")
    result = _run_powershell(f'''
if (-not (Test-Sha256Checksum -Path "{f}" -Expected "0000000000000000000000000000000000000000000000000000000000000000")) {{
    Write-Host REJECTED_BAD_HASH
}} else {{
    Write-Host ACCEPTED_BAD_HASH
}}
''')
    assert result.returncode == 0, result.stderr
    assert "REJECTED_BAD_HASH" in result.stdout
    assert "ACCEPTED_BAD_HASH" not in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_rejects_and_does_not_extract_traversal_zip(tmp_path):
    """Reproduction of P2-5 on Windows: a zip entry using '../' should be
    rejected by Test-SafeZipEntries, and Expand-ZipSafely must refuse to
    write outside the destination even if called directly on it."""
    dest = tmp_path / "dest"
    dest.mkdir()
    evil_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil_zip, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")

    result = _run_powershell(f'''
$safe = Test-SafeZipEntries -Path "{evil_zip}"
Write-Host "SAFE_RESULT=$safe"
try {{
    Expand-ZipSafely -Path "{evil_zip}" -Destination "{dest}"
    Write-Host DID_NOT_THROW
}} catch {{
    Write-Host THREW
}}
''')
    assert result.returncode == 0, result.stderr
    assert "SAFE_RESULT=False" in result.stdout
    assert "THREW" in result.stdout
    assert "DID_NOT_THROW" not in result.stdout
    # The escape target must not exist anywhere under tmp_path.
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_extracts_safe_zip_correctly(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    good_zip = tmp_path / "good.zip"
    with zipfile.ZipFile(good_zip, "w") as zf:
        zf.writestr("topdir/file.txt", "ok")

    result = _run_powershell(f'''
$safe = Test-SafeZipEntries -Path "{good_zip}"
Write-Host "SAFE_RESULT=$safe"
Expand-ZipSafely -Path "{good_zip}" -Destination "{dest}"
''')
    assert result.returncode == 0, result.stderr
    assert "SAFE_RESULT=True" in result.stdout
    assert (dest / "topdir" / "file.txt").exists()


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_rejects_unix_symlink_entry(tmp_path):
    """.NET's ZipFile doesn't create real filesystem symlinks on extraction
    -- ExtractToFile just writes the entry's raw bytes as an ordinary file
    -- so this isn't a live traversal vector the way it is for tar. Rejected
    anyway: a Unix-mode symlink entry (as Info-Zip's `zip -y` would write)
    shouldn't silently land as a file full of an unrelated path string with
    no indication anything unusual happened.
    """
    import stat

    zip_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("innocuous_name")
        mode = stat.S_IFLNK | 0o777
        info.external_attr = (mode << 16)
        zf.writestr(info, "/etc/passwd")

    result = _run_powershell(f'''
$safe = Test-SafeZipEntries -Path "{zip_path}"
Write-Host "SAFE_RESULT=$safe"
''')
    assert result.returncode == 0, result.stderr
    assert "SAFE_RESULT=False" in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_rejects_too_many_entries(tmp_path):
    zip_path = tmp_path / "many.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(10):
            zf.writestr(f"file{i}.txt", f"f{i}")

    result = _run_powershell(f'''
$env:MEDIA_AGENT_MAX_ARCHIVE_MEMBERS = "5"
$safe = Test-SafeZipEntries -Path "{zip_path}"
Write-Host "SAFE_RESULT=$safe"
''')
    assert result.returncode == 0, result.stderr
    assert "SAFE_RESULT=False" in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_accepts_reasonable_entry_count(tmp_path):
    zip_path = tmp_path / "few.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(3):
            zf.writestr(f"file{i}.txt", f"f{i}")

    result = _run_powershell(f'''
$env:MEDIA_AGENT_MAX_ARCHIVE_MEMBERS = "5"
$safe = Test-SafeZipEntries -Path "{zip_path}"
Write-Host "SAFE_RESULT=$safe"
''')
    assert result.returncode == 0, result.stderr
    assert "SAFE_RESULT=True" in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_rejects_declared_size_over_limit(tmp_path):
    zip_path = tmp_path / "sized.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("file.txt", "hello world")  # 11 bytes uncompressed

    result = _run_powershell(f'''
$env:MEDIA_AGENT_MAX_ARCHIVE_BYTES = "5"
$safe = Test-SafeZipEntries -Path "{zip_path}"
Write-Host "SAFE_RESULT=$safe"
''')
    assert result.returncode == 0, result.stderr
    assert "SAFE_RESULT=False" in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="powershell/pwsh not available")
def test_ps1_pinned_version_and_checksum_present():
    text = PS1_SCRIPT.read_text(encoding="utf-8-sig")
    assert "$FfmpegVersion" in text
    assert "$FfmpegExpectedSha256" in text
    assert "packages/ffmpeg-" in text  # pinned package URL, not the mutable "release" alias
