"""The static hub advertises only complete, valid platform packages."""

import importlib.util
from pathlib import Path
import zipfile
import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_download_site",
    Path(__file__).resolve().parents[1] / "tools/build_download_site.py",
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def test_four_platform_hub_and_checksums(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    for platform, (_, _, launcher) in builder.PLATFORMS.items():
        with zipfile.ZipFile(downloads / f"DaariDeepa-{platform}.zip", "w") as package:
            package.writestr("DaariDeepa/" + launcher, "synthetic packaging fixture")
    output = tmp_path / "site"
    builder.build(downloads, output, "0123456789abcdef" * 2 + "01234567")
    page = (output / "index.html").read_text()
    for source in downloads.glob("*.zip"):
        assert source.name in page
        assert (output / source.name).read_bytes() == source.read_bytes()
        assert len((output / (source.name + ".sha256")).read_text().split()[0]) == 64
    assert len((output / "SHA256SUMS.txt").read_text().splitlines()) == 4


def test_missing_platform_refuses_publication(tmp_path):
    with pytest.raises(FileNotFoundError):
        builder.build(tmp_path, tmp_path / "site", "0" * 40)
    assert not (tmp_path / "site").exists()
