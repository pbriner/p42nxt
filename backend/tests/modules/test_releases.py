"""Releases: signatures, changelogs, the signed index, updates that fit, and the release check."""

import base64
import json
import subprocess
from collections.abc import AsyncGenerator
from importlib.metadata import version
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import Version
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from p42cloud.kernel.config import Settings
from p42cloud.kernel.context import Context, working_as
from p42cloud.kernel.db import create_engine, owner_transaction
from p42cloud.kernel.errors import Problem
from p42cloud.modules import release_tool, releases
from p42cloud.modules.install import settle_updates
from p42cloud.modules.registry import discover
from p42cloud.modules.updates import refresh

PRIVATE, PUBLIC = releases.new_key()
KEYS = (PUBLIC,)


def _release(index: dict[str, object], module: str, version: str, **extra: object) -> None:
    entry = releases.Entry(version, bool(extra.pop("security", False)), f"Notes {version}")
    depends = extra.pop("depends_on", {"sys": ">=0.1"})
    releases.add_release(
        index,
        module,
        version,
        depends_on=depends,  # type: ignore[arg-type]
        entry=entry,
        package=f"https://example.test/{module}-{version}.whl",
        digest="0" * 64,
        signature="sig",
    )


# ---- the parts ----


def test_signatures_hold_only_for_trusted_keys() -> None:
    signed = releases.sign(PRIVATE, b"package")
    _, stranger = releases.new_key()
    assert releases.verify(b"package", signed, KEYS)
    assert not releases.verify(b"package!", signed, KEYS)  # changed
    assert not releases.verify(b"package", signed, (stranger,))  # someone else's key
    assert not releases.verify(b"package", signed, ())  # no trusted key: nothing verifies
    assert not releases.verify(b"package", "not base64!", KEYS)


def test_every_trusted_key_is_an_ed25519_public_key() -> None:
    assert releases.TRUSTED_KEYS
    for key in releases.TRUSTED_KEYS:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True))


def test_changelog_entries_and_security_releases() -> None:
    log = releases.changelog(
        "# Changelog\n\n## 1.1.0 (security)\n\n- Fixed.\n\n## 1.0.0\n\n- First.\n"
    )
    assert log["1.1.0"] == releases.Entry("1.1.0", True, "- Fixed.")
    assert log["1.0.0"] == releases.Entry("1.0.0", False, "- First.")


def test_updates_say_what_fits_and_what_it_needs() -> None:
    index = releases.empty_index()
    _release(index, "task", "1.1.0", security=True)
    _release(index, "task", "1.2.0")
    _release(index, "cmdb", "2.0.0", depends_on={"sys": ">=0.5"})
    _release(index, "ipam", "0.1.0")  # not installed: not offered
    found = releases.updates(
        index, {"sys": Version("0.2.0"), "task": Version("1.0.0"), "cmdb": Version("1.0.0")}
    )
    assert [(u.module, u.available, u.security, u.fits) for u in found] == [
        ("task", "1.2.0", True, True),  # a security release on the way: listed first
        ("cmdb", "2.0.0", False, False),
    ]
    assert found[1].needs == "needs sys >=0.5"


def test_an_index_is_believed_only_with_a_trusted_signature() -> None:
    body = json.dumps(releases.empty_index()).encode()
    assert releases.read_index(body, releases.sign(PRIVATE, body), KEYS)["modules"] == {}
    _, stranger = releases.new_key()
    with pytest.raises(ValueError, match="trusted"):
        releases.read_index(body, releases.sign(PRIVATE, body), (stranger,))


# ---- learning about updates ----


@pytest.fixture(scope="module")
async def owner(upgraded: Settings) -> AsyncGenerator[AsyncEngine]:
    engine = create_engine(upgraded.owner_database_url)
    yield engine
    await engine.dispose()


def _registry(index: dict[str, object], key: str = PRIVATE) -> httpx.AsyncClient:
    body = json.dumps(index).encode()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/index.json"):
            return httpx.Response(200, content=body)
        if request.url.path.endswith("/index.json.sig"):
            return httpx.Response(200, text=releases.sign(key, body))
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


async def test_updates_are_recorded_from_the_signed_index(owner: AsyncEngine) -> None:
    index = releases.empty_index()
    _release(index, "sys", "9.0.0", security=True)
    with working_as(Context(channel="test")):
        found = await refresh(owner, _registry(index), "https://registry.test", KEYS)
        async with owner_transaction(owner) as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT module, available, kind, fits FROM sys.module_update"
                        " WHERE deleted IS NULL"
                    )
                )
            ).all()
    assert [u.available for u in found] == ["9.0.0"]
    assert [tuple(r) for r in rows] == [("sys", "9.0.0", "security", True)]


async def test_an_installed_update_leaves_the_list_at_once(owner: AsyncEngine) -> None:
    with working_as(Context(channel="test")):
        async with owner_transaction(owner) as conn:
            await conn.execute(
                text(
                    "INSERT INTO sys.module_update (module, installed, available, kind, fits)"
                    " VALUES ('settle', '1.0.0', '1.1.0', 'normal', true),"
                    " ('settle', '1.0.0', '2.0.0', 'normal', true)"
                )
            )
            await settle_updates(conn, "settle", "1.1.0")
            left = (
                await conn.execute(
                    text(
                        "SELECT installed, available FROM sys.module_update"
                        " WHERE module = 'settle' AND deleted IS NULL"
                    )
                )
            ).all()
            await settle_updates(conn, "settle", None)  # removed: none left
            gone = await conn.scalar(
                text(
                    "SELECT count(*) FROM sys.module_update"
                    " WHERE module = 'settle' AND deleted IS NULL"
                )
            )
    assert [tuple(r) for r in left] == [("1.1.0", "2.0.0")]
    assert gone == 0


async def test_a_registry_signed_by_someone_else_is_refused(owner: AsyncEngine) -> None:
    other, _ = releases.new_key()
    with working_as(Context(channel="test")), pytest.raises(Problem) as refused:
        await refresh(
            owner, _registry(releases.empty_index(), other), "https://registry.test", KEYS
        )
    assert refused.value.code == "registry"


async def test_without_a_registry_nothing_is_asked(owner: AsyncEngine) -> None:
    with working_as(Context(channel="test")), pytest.raises(Problem) as off:
        await refresh(owner, httpx.AsyncClient(), None, KEYS)
    assert off.value.code == "no_registry"


# ---- the release check ----


def _repo(tmp_path: Path) -> Path:
    """A small repository with the core and one module, committed as the base."""

    def write(path: str, body: str) -> None:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(body)

    write("backend/pyproject.toml", '[project]\nname = "p42cloud"\nversion = "1.0.0"\n')
    write("backend/CHANGELOG.md", "## 1.0.0\n- First.\n")
    write("backend/src/core.py", "x = 1\n")
    write("modules/task/pyproject.toml", '[project]\nname = "p42cloud-task"\nversion = "1.0.0"\n')
    write("modules/task/CHANGELOG.md", "## 1.0.0\n- First.\n")
    write("modules/task/src/task.py", "y = 1\n")
    write("modules/task/tests/test_task.py", "z = 1\n")
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"],
        ["git", "checkout", "-q", "-b", "change"],
    ):
        subprocess.run(command, cwd=tmp_path, check=True)  # noqa: S603
    return tmp_path


def _commit(repo: Path, changes: dict[str, str]) -> list[str]:
    for path, body in changes.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(body)
    for command in (
        ["git", "add", "-A"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "change"],
    ):
        subprocess.run(command, cwd=repo, check=True)  # noqa: S603
    return release_tool.check("main")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    made = _repo(tmp_path)
    monkeypatch.setattr(release_tool, "ROOT", made)
    return made


def test_a_changed_package_needs_a_release(repo: Path) -> None:
    problems = _commit(repo, {"modules/task/src/task.py": "y = 2\n"})
    assert problems == ["modules/task: changed, so raise its version (it's 1.0.0)."]


def test_a_release_with_version_and_changelog_passes(repo: Path) -> None:
    problems = _commit(
        repo,
        {
            "modules/task/src/task.py": "y = 2\n",
            "modules/task/pyproject.toml": '[project]\nname = "p42cloud-task"\nversion = "1.0.1"\n',
            "modules/task/CHANGELOG.md": "## 1.0.1\n- Fixed.\n\n## 1.0.0\n- First.\n",
        },
    )
    assert problems == []


def test_tests_alone_need_no_release(repo: Path) -> None:
    assert _commit(repo, {"modules/task/tests/test_task.py": "z = 2\n"}) == []


def test_the_core_is_checked_too(repo: Path) -> None:
    problems = _commit(repo, {"backend/src/core.py": "x = 2\n"})
    assert any(p.startswith("backend: changed") for p in problems)


def test_the_browser_app_belongs_to_the_core(repo: Path) -> None:
    tests_only = _commit(repo, {"frontend/e2e/a.spec.ts": "x\n"})
    shipped = _commit(repo, {"frontend/src/main.ts": "x\n"})
    assert any(p.startswith("backend: changed") for p in shipped)
    assert tests_only == []


def test_publishing_signs_the_package_and_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("P42_RELEASE_KEY", PRIVATE)
    hello = version("p42cloud-hello")  # what's installed is what gets published
    wheel = tmp_path / f"p42cloud_hello-{hello}-py3-none-any.whl"
    wheel.write_bytes(b"a package")
    index = tmp_path / "index.json"
    release_tool.publish("hello", wheel, index, "https://example.test/hello.whl")
    body = index.read_bytes()
    found = releases.read_index(body, (tmp_path / "index.json.sig").read_text(), KEYS)
    (release,) = found["modules"]["hello"]
    assert release["version"] == hello
    assert releases.verify(b"a package", (tmp_path / f"{wheel.name}.sig").read_text(), KEYS)
    assert release["sha256"] == releases.sha256(b"a package")


def test_signing_by_hand_refuses_a_key_the_platform_doesnt_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "p42cloud_x-1.0.0-py3-none-any.whl"
    package.write_bytes(b"package")
    monkeypatch.setenv("P42_RELEASE_KEY", PRIVATE)  # a test key, not a trusted one
    with pytest.raises(SystemExit, match="doesn't trust"):
        release_tool.sign_file(package)
    monkeypatch.setenv("P42_RELEASE_KEY", "not a key")
    with pytest.raises(SystemExit, match="isn't a release key"):
        release_tool.sign_file(package)
    assert not package.with_name(f"{package.name}.sig").exists()


def test_every_module_says_the_version_its_package_has() -> None:
    """A manifest's version is what gets published: it must be the package's, or a release goes
    out under an old version."""
    for module in discover():
        assert module.version == version(f"p42cloud-{module.id}"), module.id


def test_an_entry_whose_package_is_another_version_is_dropped() -> None:
    index = releases.empty_index()
    for v, wheel in (("0.4.0", "p42cloud_dca-0.4.0"), ("0.5.0", "p42cloud_dca-0.7.1")):
        _release(index, "dca", v)
        index["modules"]["dca"][-1]["package"] = f"https://x.test/{wheel}-py3-none-any.whl"
    kept = releases.drop_mismatched(index)
    assert [e["version"] for e in kept["modules"]["dca"]] == ["0.4.0"]
    assert releases.wheel_version("p42cloud_dca-0.7.2-py3-none-any.whl") == "0.7.2"
    assert releases.wheel_version("ghcr.io/x/p42cloud@sha256:abc") is None
