"""Which app image an installation may move to: the core's releases in the signed index.

The core is released as an image; its release in the index names the image by its digest
(`ghcr.io/<owner>/<repo>@sha256:…`), signed by the release key. The running image checks that
with the keys it trusts, so an update is only ever to an image the release key vouched for, and
never back to a lower version.
"""

import re
from importlib.metadata import version as installed_version
from typing import Any

import httpx
from packaging.version import Version

from p42cloud.kernel.errors import Problem
from p42cloud.modules import packages, releases
from p42cloud.modules.manifest import CORE
from p42cloud.modules.updates import fetch_index

# A name and a digest; a tag could be moved, a digest can't.
IMAGE = re.compile(r"^[a-z0-9][a-z0-9._/:-]*@sha256:(?P<digest>[0-9a-f]{64})$")


def image_of(
    index: dict[str, Any],
    wanted: str | None,
    running: Version,
    keys: tuple[str, ...] = releases.TRUSTED_KEYS,
) -> tuple[str, str]:
    """The release's version and image: the one asked for, else the newest."""
    release = packages.choose(index, CORE, wanted)
    found = packages.version_of(release)
    if found < running:
        raise Problem(
            409,
            "never_back",
            f"This is {running}; {found} would go back. The core never goes back.",
        )
    image = str(release.get("package", ""))
    match = IMAGE.match(image)
    if match is None or match["digest"] != release.get("sha256"):
        raise Problem(422, "bad_index", "The core's release doesn't name its image by digest.")
    if not releases.verify(image.encode(), str(release.get("signature", "")), keys):
        raise Problem(422, "unsigned_package", "No trusted key signed the core's image.")
    return str(found), image


async def verified_image(
    http: httpx.AsyncClient,
    registry: str | None,
    wanted: str | None = None,
    keys: tuple[str, ...] = releases.TRUSTED_KEYS,
) -> tuple[str, str]:
    """The core's release from the registry, checked by this image."""
    if registry is None:
        raise Problem(
            409, "no_registry", "No release address is set (Platform, Settings, Release address)."
        )
    index = await fetch_index(http, registry, keys)
    return image_of(index, wanted, Version(installed_version("p42cloud")), keys)
