"""Module packages: believed only when signed, unpacked safely, installed, put back on failure."""

import io
import json
import sys
import zipfile
from collections.abc import AsyncGenerator, Iterator
from importlib.metadata import version
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules import packages, releases
from p42cloud.modules.install import states
from p42cloud.modules.install_package import Source, install_package

PRIVATE, PUBLIC = releases.new_key()
KEYS = (PUBLIC,)
REGISTRY = "https://registry.test/registry"
MODULE = "parcel"


def _wheel(
    module_version: str = "1.0.0",
    *,
    module: str = MODULE,
    requires: str = "p42cloud",
    extra: dict[str, str] | None = None,
) -> bytes:
    """A module package as the release workflow builds one, for a small module."""
    info = f"p42cloud_{module}-{module_version}.dist-info"
    files = {
        f"p42cloud_{module}/__init__.py": (
            "from p42cloud.sdk import Module\n"
            f"module = Module(id='{module}', name='Parcel', version='{module_version}',"
            f" depends_on={{'sys': '>={version('p42cloud')}'}})\n"
        ),
        f"{info}/METADATA": (
            f"Metadata-Version: 2.4\nName: p42cloud-{module}\n"
            f"Version: {module_version}\nRequires-Dist: {requires}\n"
        ),
        f"{info}/entry_points.txt": f"[p42cloud.modules]\n{module} = p42cloud_{module}:module\n",
        f"{info}/RECORD": "",
        **(extra or {}),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _index(
    *wheels: tuple[str, bytes], signed_by: str = PRIVATE, module: str = MODULE
) -> dict[str, object]:
    index = releases.empty_index()
    for module_version, data in wheels:
        releases.add_release(
            index,
            module,
            module_version,
            depends_on={"sys": ">=0.1"},
            entry=releases.Entry(module_version, False, ""),
            package=f"https://registry.test/download/{module}-{module_version}.whl",
            digest=releases.sha256(data),
            signature=releases.sign(signed_by, data),
        )
    return index


def _registry(index: dict[str, object], files: dict[str, bytes]) -> httpx.AsyncClient:
    """The registry: the signed index, and its packages behind a redirect, as GitHub serves them."""
    body = json.dumps(index).encode()

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/index.json"):
            return httpx.Response(200, content=body)
        if path.endswith("/index.json.sig"):
            return httpx.Response(200, text=releases.sign(PRIVATE, body))
        if path.startswith("/download/"):
            name = path.rsplit("/", 1)[1]
            return httpx.Response(302, headers={"location": f"https://files.test/{name}"})
        if request.url.host == "files.test" and path[1:] in files:
            return httpx.Response(200, content=files[path[1:]])
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


@pytest.fixture(scope="module")
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def folder(tmp_path: Path) -> Iterator[Path]:
    """A modules folder; afterwards this process forgets it, so other tests don't find parcel."""
    yield tmp_path / "modules"
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]
    for name in [n for n in sys.modules if n.startswith("p42cloud_parcel")]:
        del sys.modules[name]


async def _install(
    owner: AsyncEngine,
    folder: Path,
    source: Source,
    http: httpx.AsyncClient,
    module: str = MODULE,
) -> list[str]:
    with working_as(Context(channel="test")):
        return await install_package(owner, module, folder, source, http, keys=KEYS)


async def test_a_signed_package_installs_and_updates_the_module(
    owner: AsyncEngine, folder: Path
) -> None:
    first, second = _wheel("1.0.0"), _wheel("1.1.0")
    http = _registry(
        _index(("1.0.0", first), ("1.1.0", second)),
        {"parcel-1.0.0.whl": first, "parcel-1.1.0.whl": second},
    )
    done = await _install(owner, folder, Source(REGISTRY, "1.0.0"), http)
    assert "parcel new → 1.0.0" in done
    assert str((await states(owner))[MODULE].version) == "1.0.0"
    done = await _install(owner, folder, Source(REGISTRY), http)  # the newest
    assert "parcel 1.0.0 → 1.1.0" in done
    assert str((await states(owner))[MODULE].version) == "1.1.0"
    assert sorted(p.name for p in folder.iterdir()) == [MODULE]  # nothing left beside it
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            logged = (
                await conn.execute(
                    text(
                        "SELECT changes ->> 'version', changes ->> 'sha256' FROM sys.audit_log"
                        " WHERE operation = 'module_package_installed'"
                        " AND changes ->> 'module' = 'parcel' ORDER BY created"
                    )
                )
            ).all()
    assert [tuple(r) for r in logged][-2:] == [
        ("1.0.0", releases.sha256(first)),
        ("1.1.0", releases.sha256(second)),
    ]


async def test_a_package_not_signed_by_a_trusted_key_is_refused(
    owner: AsyncEngine, folder: Path
) -> None:
    data = _wheel("1.0.0")
    stranger, _ = releases.new_key()
    signed_elsewhere = _registry(
        _index(("1.0.0", data), signed_by=stranger), {"parcel-1.0.0.whl": data}
    )
    with pytest.raises(Problem) as refused:
        await _install(owner, folder, Source(REGISTRY), signed_elsewhere)
    assert refused.value.code == "unsigned_package"
    swapped = _registry(
        _index(("1.0.0", data)),
        {"parcel-1.0.0.whl": _wheel("1.0.0", extra={"p42cloud_parcel/x.py": ""})},
    )
    with pytest.raises(Problem) as refused:
        await _install(owner, folder, Source(REGISTRY), swapped)
    assert refused.value.code == "wrong_package"
    assert not folder.exists()  # nothing was unpacked


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (_wheel(extra={"p42cloud/kernel/config.py": "x = 1\n"}), "bad_package"),  # the platform's
        (_wheel(extra={"../outside.py": ""}), "bad_package"),
        (_wheel(extra={"loose.py": ""}), "bad_package"),
        (_wheel(requires="left-pad>=1"), "needs_libraries"),
        (_wheel(requires="p42cloud>=999"), "needs_libraries"),
        (b"not a zip", "bad_package"),
    ],
)
def test_only_a_package_holding_just_its_module_is_unpacked(
    data: bytes, code: str, tmp_path: Path
) -> None:
    with pytest.raises(Problem) as refused:
        packages.unpack(tmp_path, MODULE, data)
    assert refused.value.code == code
    assert list(tmp_path.iterdir()) == []


async def test_a_failed_install_puts_the_package_before_back(
    owner: AsyncEngine, folder: Path
) -> None:
    good, wrong = _wheel("1.0.0", module="parcelb"), _wheel("1.2.0", module="parcelb")
    http = _registry(_index(("1.0.0", good), module="parcelb"), {"parcelb-1.0.0.whl": good})
    await _install(owner, folder, Source(REGISTRY, "1.0.0"), http, "parcelb")
    lying = _index(("1.0.0", good), module="parcelb")  # says 1.3.0, holds 1.2.0
    releases.add_release(
        lying,
        "parcelb",
        "1.3.0",
        depends_on={"sys": ">=0.1"},
        entry=releases.Entry("1.3.0", False, ""),
        package="https://registry.test/download/parcelb-1.3.0.whl",
        digest=releases.sha256(wrong),
        signature=releases.sign(PRIVATE, wrong),
    )
    with pytest.raises(Problem, match="version isn't the registry's"):
        await _install(
            owner,
            folder,
            Source(REGISTRY),
            _registry(lying, {"parcelb-1.3.0.whl": wrong}),
            "parcelb",
        )
    assert (
        "version='1.0.0'" in (folder / "parcelb" / "p42cloud_parcelb" / "__init__.py").read_text()
    )
    assert sorted(p.name for p in folder.iterdir()) == ["parcelb"]


async def test_a_package_file_needs_its_signature(
    owner: AsyncEngine, folder: Path, tmp_path: Path
) -> None:
    file = tmp_path / "p42cloud_parcelc-1.0.0-py3-none-any.whl"
    file.write_bytes(_wheel("1.0.0", module="parcelc"))
    offline = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    with pytest.raises(Problem) as refused:
        await _install(owner, folder, Source(file=file), offline, "parcelc")
    assert refused.value.code == "no_package"
    stranger, _ = releases.new_key()
    signature = file.with_name(f"{file.name}.sig")
    signature.write_text(releases.sign(stranger, file.read_bytes()))
    with pytest.raises(Problem) as refused:
        await _install(owner, folder, Source(file=file), offline, "parcelc")
    assert refused.value.code == "unsigned_package"
    signature.write_text(releases.sign(PRIVATE, file.read_bytes()))
    await _install(owner, folder, Source(file=file), offline, "parcelc")  # no registry needed
    assert (folder / "parcelc" / "p42cloud_parcelc").is_dir()


async def test_without_a_modules_folder_nothing_is_installed(owner: AsyncEngine) -> None:
    with pytest.raises(Problem) as refused:
        await _install(owner, None, Source(REGISTRY), httpx.AsyncClient())  # type: ignore[arg-type]
    assert refused.value.code == "no_modules_folder"


def test_github_registries_may_send_to_githubs_download_hosts_only() -> None:
    assert packages.hosts_for("https://github.com/o/r/releases/download/registry") == (
        "github.com",
        "*.githubusercontent.com",
    )
    assert packages.hosts_for("https://registry.example.com/x") == ("registry.example.com",)


async def test_downloads_stop_after_a_few_redirects() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": str(request.url)})

    loop = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    with pytest.raises(Problem, match="redirects too often"):
        await packages.download(loop, "https://registry.test/x")
