"""The built wheel and sdist must actually contain what the docs promise.

Found by the fifth-pass reviewer: THIRD-PARTY-LICENSES.md was added and
README.md pointed users at it, but the wheel -- what most people actually get
via `pip install` -- silently didn't include it. The sdist did, which made the
gap easy to miss without literally opening the built artifact.

These tests build a real wheel and sdist (no-isolation, since build/hatchling
are already dev dependencies -- keeps this fast, under two seconds) and
inspect their actual contents, rather than trusting pyproject.toml
configuration to mean what it says.

The METADATA checks below exist because "the file is in the archive" and
"pip/PyPI know the file is a license" are two different claims. Found by the
eighth-pass reviewer: hatchling 1.26.x bundles both license files into the
wheel (which is what the tests above check) but emits Core Metadata 2.3 --
no `License-File` header at all, for either file. Only 1.27.0+ emits Core
Metadata 2.4 with a `License-File` header per declared file. A wheel with
the file physically present but no metadata header is invisible to any
tool that reads declared licenses from METADATA rather than unzipping the
archive, so file-presence checks alone can't catch this class of gap.
"""

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_LICENSE_FILES = {"LICENSE", "THIRD-PARTY-LICENSES.md"}


@pytest.fixture(scope="module")
def built_artifacts(tmp_path_factory):
    """Build both the wheel and the sdist once, real subprocess calls."""
    out_dir = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "-o", str(out_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"build failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    wheels = list(out_dir.glob("*.whl"))
    sdists = list(out_dir.glob("*.tar.gz"))
    assert len(wheels) == 1, f"expected exactly one wheel, found {wheels}"
    assert len(sdists) == 1, f"expected exactly one sdist, found {sdists}"
    return {"wheel": wheels[0], "sdist": sdists[0]}


def _wheel_names(path):
    with zipfile.ZipFile(path) as z:
        return {Path(n).name for n in z.namelist()}


def _sdist_names(path):
    with tarfile.open(path, "r:gz") as t:
        return {Path(n).name for n in t.getnames()}


def _wheel_metadata_text(path):
    with zipfile.ZipFile(path) as z:
        matches = [n for n in z.namelist() if Path(n).name == "METADATA"]
        assert matches, "wheel has no *.dist-info/METADATA at all"
        return z.read(matches[0]).decode("utf-8")


class TestWheelContainsLicenseFiles:
    def test_wheel_includes_every_required_license_file(self, built_artifacts):
        names = _wheel_names(built_artifacts["wheel"])
        missing = REQUIRED_LICENSE_FILES - names
        assert not missing, (
            f"wheel is missing {missing} -- most users install via the wheel, "
            "not the sdist, so a license file present only in the sdist is "
            "effectively undisclosed for them"
        )

    def test_sdist_includes_every_required_license_file(self, built_artifacts):
        names = _sdist_names(built_artifacts["sdist"])
        missing = REQUIRED_LICENSE_FILES - names
        assert not missing, f"sdist is missing {missing}"

    def test_wheel_third_party_notice_is_not_empty(self, built_artifacts):
        with zipfile.ZipFile(built_artifacts["wheel"]) as z:
            matches = [n for n in z.namelist()
                      if Path(n).name == "THIRD-PARTY-LICENSES.md"]
            assert matches, "THIRD-PARTY-LICENSES.md not found in wheel at all"
            content = z.read(matches[0]).decode("utf-8")
            assert "mutagen" in content and "GPL" in content, (
                "the packaged notice doesn't look like the real disclosure "
                "-- got a stub or truncated file"
            )


class TestWheelMetadataDeclaresLicenseFiles:
    """A file bundled in the archive isn't the same as pip/PyPI knowing it's
    a license file -- that requires a `License-File` header in METADATA
    itself, which only exists under Core Metadata 2.4+ (hatchling 1.27+).
    """

    def test_metadata_version_is_at_least_2_4(self, built_artifacts):
        text = _wheel_metadata_text(built_artifacts["wheel"])
        match = [line for line in text.splitlines()
                 if line.startswith("Metadata-Version:")]
        assert match, "METADATA has no Metadata-Version header"
        version = tuple(int(p) for p in match[0].split(":", 1)[1].strip().split("."))
        assert version >= (2, 4), (
            f"got Metadata-Version {'.'.join(map(str, version))} -- below 2.4, "
            "License-File headers aren't emitted at all under this backend "
            "version even though the files are physically in the archive"
        )

    def test_metadata_declares_every_required_license_file(self, built_artifacts):
        text = _wheel_metadata_text(built_artifacts["wheel"])
        declared = {
            line.split(":", 1)[1].strip()
            for line in text.splitlines()
            if line.startswith("License-File:")
        }
        missing = REQUIRED_LICENSE_FILES - declared
        assert not missing, (
            f"{missing} present in the archive but not declared via "
            "License-File in METADATA -- undisclosed to any tool that reads "
            "declared licenses from metadata instead of unzipping the wheel"
        )
