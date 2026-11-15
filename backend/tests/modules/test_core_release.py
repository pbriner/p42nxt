"""The core's releases: an image by digest, signed, never back; published into the index."""

import json
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Any

import httpx
import pytest
from packaging.version import Version

from p42cloud.kernel.errors import Problem
from p42cloud.modules import release_tool, releases
from p42cloud.modules.core_release import image_of, verified_image

PRIVATE, PUBLIC = releases.new_key()
KEYS = (PUBLIC,)
NAME = "ghcr.io/owner/repo"
RUNNING = Version("0.23.0")


def _digest(n: int) -> str:
    return f"{n:064x}"


def _index(
    *found: tuple[str, str], signed_by: str = PRIVATE, digest: str | None = None
) -> dict[str, Any]:
    index = releases.empty_index()
    for version, image in found:
        releases.add_release(
            index,
            "sys",
            version,
            depends_on={},
            entry=releases.Entry(version, False, ""),
            package=image,
            digest=digest or image.rsplit("@sha256:", 1)[-1],
            signature=releases.sign(signed_by, image.encode()),
        )
    return index


def test_the_newest_signed_image_or_the_one_asked_for() -> None:
    old, new = f"{NAME}@sha256:{_digest(1)}", f"{NAME}@sha256:{_digest(2)}"
    index = _index(("0.23.0", old), ("0.24.0", new))
    assert image_of(index, None, RUNNING, KEYS) == ("0.24.0", new)
    assert image_of(index, "0.23.0", RUNNING, KEYS) == ("0.23.0", old)


@pytest.mark.parametrize(
    ("index", "code"),
    [
        (_index(("0.22.0", f"{NAME}@sha256:{_digest(1)}")), "never_back"),
        (_index(("0.24.0", f"{NAME}:0.24.0")), "bad_index"),  # a tag can be moved
        (_index(("0.24.0", f"{NAME}@sha256:{_digest(1)}"), digest=_digest(2)), "bad_index"),
        (
            _index(("0.24.0", f"{NAME}@sha256:{_digest(1)}"), signed_by=releases.new_key()[0]),
            "unsigned_package",
        ),
        (_index(), "no_release"),
    ],
)
def test_an_image_not_vouched_for_is_refused(index: dict[str, object], code: str) -> None:
    with pytest.raises(Problem) as refused:
        image_of(index, None, RUNNING, KEYS)
    assert refused.value.code == code


async def test_without_a_registry_nothing_is_asked() -> None:
    with pytest.raises(Problem) as refused:
        await verified_image(httpx.AsyncClient(), None, keys=KEYS)
    assert refused.value.code == "no_registry"


def test_publishing_the_core_signs_its_image_into_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("P42_RELEASE_KEY", PRIVATE)
    index_path = tmp_path / "index.json"
    image = f"{NAME}@sha256:{_digest(7)}"
    release_tool.publish_image(image, index_path)
    body = index_path.read_bytes()
    index = releases.read_index(body, (tmp_path / "index.json.sig").read_text(), KEYS)
    version, found = image_of(index, None, Version("0.0.1"), KEYS)
    assert found == image
    assert version == installed_version("p42cloud")  # the core's version, from its pyproject
    with pytest.raises(SystemExit, match="by its digest"):
        release_tool.publish_image(f"{NAME}:latest", index_path)
    assert json.loads(body)["modules"]["sys"][0]["sha256"] == _digest(7)
