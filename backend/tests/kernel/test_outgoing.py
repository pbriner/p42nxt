"""The local exception of the safe client: only the ranges named, by address or by a name whose
addresses are all in them, http or https; and a device's own certificate pinned."""

import asyncio
import ipaddress
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from p42cloud.kernel import outgoing
from p42cloud.kernel.outgoing import (
    Refused,
    is_name,
    never_local,
    pinned_context,
    safe_client,
    within,
)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"host": request.url.host})


def test_addresses_are_within_ranges_or_not() -> None:
    assert within("192.168.1.50", ["192.168.1.0/24"])
    assert not within("192.168.2.50", ["192.168.1.0/24"])
    assert not within("shelly.local", ["192.168.1.0/24"])  # names aren't, only addresses


def _names(monkeypatch: pytest.MonkeyPatch, known: dict[str, list[str]]) -> list[str]:
    """Stands in for DNS: each name's addresses. Returns the names looked up, in order."""
    asked: list[str] = []

    async def lookup(host: str) -> list[str]:
        asked.append(host)
        return known.get(host, ["93.184.216.34"])

    monkeypatch.setattr(outgoing, "lookup", lookup)
    return asked


async def test_a_local_client_reaches_only_its_ranges(monkeypatch: pytest.MonkeyPatch) -> None:
    _names(monkeypatch, {})
    client = safe_client(local_ranges=["192.168.1.0/24"], transport=httpx.MockTransport(_ok))
    async with client:
        answer = await client.get("http://192.168.1.50/rpc/Shelly.GetStatus")
        assert answer.json() == {"host": "192.168.1.50"}
        for elsewhere in ("http://192.168.2.1/", "http://10.0.0.1/", "https://example.com/"):
            with pytest.raises(Refused):
                await client.get(elsewhere)


async def test_without_ranges_inside_stays_refused() -> None:
    client = safe_client(transport=httpx.MockTransport(_ok))
    async with client:
        with pytest.raises(Refused):
            await client.get("http://192.168.1.50/")


@pytest.mark.parametrize(
    "given",
    [
        "127.0.0.0/16",
        "0.0.0.0/16",
        "169.254.0.0/16",
        "::ffff:0:0/96",
        "::/128",
        "fe80::/64",
        "64:ff9b::/96",
        "8.8.8.0/24",
    ],
)
def test_some_ranges_are_never_local_devices(given: str) -> None:
    assert never_local(ipaddress.ip_network(given))


def test_this_machine_is_never_within_whatever_is_named() -> None:
    assert not within("[::ffff:127.0.0.1]", ["::ffff:0:0/96"])
    assert not within("0.0.0.0", ["0.0.0.0/8"])  # noqa: S104 - an address checked, not bound
    assert not within("169.254.169.254", ["169.254.0.0/16"])
    assert within("fd00::5", ["fd00::/48"]) and not never_local(ipaddress.ip_network("10.1.0.0/16"))


def test_names_are_told_from_addresses() -> None:
    assert is_name("ubi.home.example.net")
    assert is_name("shelly-plug")
    assert not is_name("192.168.1.1")
    assert not is_name("fd00::1")
    assert not is_name("bad_name.example")
    assert not is_name("a" * 64 + ".example")


async def test_a_name_is_reached_at_its_checked_address(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _names(monkeypatch, {"ubi.home.example.net": ["192.168.1.7"]})
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    client = safe_client(local_ranges=["192.168.1.0/24"], transport=httpx.MockTransport(answer))
    async with client:
        await client.get("https://ubi.home.example.net/proxy/network/integration/v1/sites")
        await client.get("https://ubi.home.example.net/again")
    assert asked == ["ubi.home.example.net"] * 2  # looked up on every request
    (first, _) = seen
    assert first.url.host == "192.168.1.7"  # to the address just checked, not a new lookup
    assert first.headers["host"] == "ubi.home.example.net"
    assert first.extensions["sni_hostname"] == "ubi.home.example.net"  # its certificate's name


async def test_a_name_leading_outside_its_ranges_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _names(
        monkeypatch,
        {
            "split.example.net": ["192.168.1.7", "203.0.113.9"],  # one address outside is enough
            "metadata.example.net": ["169.254.169.254"],
            "here.example.net": ["127.0.0.1"],
            "other.example.net": ["192.168.2.7"],
        },
    )
    client = safe_client(local_ranges=["192.168.1.0/24"], transport=httpx.MockTransport(_ok))
    async with client:
        for name in ("split", "metadata", "here", "other"):
            with pytest.raises(Refused):
                await client.get(f"https://{name}.example.net/")


def _self_made(*, authority: bool) -> tuple[str, str]:
    """A certificate and key like a device makes itself: it signs certificates, and says it's an
    authority or (like a Dream Machine's) says nothing about it."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unifi.local")])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=True,
                data_encipherment=True,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=False,
        )
    )
    if authority:
        builder = builder.add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
    cert = builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()
    pem_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert, pem_key


async def _handshake(served: tuple[str, str], trusted: str, folder: Path) -> None:
    """Serves TLS with one certificate on this machine and connects trusting another."""
    (folder / "cert.pem").write_text(served[0])
    (folder / "key.pem").write_text(served[1])
    server_side = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_side.load_cert_chain(folder / "cert.pem", folder / "key.pem")

    async def greet(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(greet, "127.0.0.1", 0, ssl=server_side)
    port = server.sockets[0].getsockname()[1]
    try:
        _, writer = await asyncio.open_connection(
            "127.0.0.1", port, ssl=pinned_context(trusted), server_hostname="192.168.1.1"
        )
        writer.close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("authority", [True, False], ids=["authority", "dream-machine"])
async def test_a_devices_own_certificate_is_trusted_when_pinned(
    authority: bool, tmp_path: Path
) -> None:
    device = _self_made(authority=authority)
    await _handshake(device, device[0], tmp_path)


async def test_only_the_pinned_certificate_is_trusted(tmp_path: Path) -> None:
    with pytest.raises(ssl.SSLCertVerificationError):
        await _handshake(_self_made(authority=False), _self_made(authority=False)[0], tmp_path)
