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

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SH_SCRIPT = REPO_ROOT / "scripts" / "install_ffmpeg.sh"
PS1_SCRIPT = REPO_ROOT / "scripts" / "install_ffmpeg.ps1"

BASH = shutil.which("bash")
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
# bash script (Linux/macOS fallback path)
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
