"""Tests for the guacd wire-protocol handshake used by Connect mode
(services/remote_session.py). A tiny in-process fake guacd speaks just enough
of the protocol over a real localhost socket to exercise the REAL
open_guacd_connection code path end to end - no guacd container needed - so
the framing, the arg alignment, and specifically the VERSION-pseudo-arg fix
(answering guacd's VERSION_* slot with a real version instead of "", which
otherwise silently downgrades the session to protocol 1.0.0) are all covered.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services import remote_session


def _parse_instruction(raw: bytes) -> list[str]:
    """Decode one full guacd instruction (LENGTH.VALUE,...;) into its parts."""
    text = raw.decode()
    assert text.endswith(";"), f"instruction not terminated: {text!r}"
    parts = []
    body = text[:-1]  # strip trailing ';'
    i = 0
    while i < len(body):
        dot = body.index(".", i)
        length = int(body[i:dot])
        value = body[dot + 1 : dot + 1 + length]
        parts.append(value)
        i = dot + 1 + length
        if i < len(body) and body[i] == ",":
            i += 1
    return parts


async def _read_one_instruction(reader: asyncio.StreamReader) -> list[str]:
    data = b""
    while not data.endswith(b";"):
        chunk = await reader.read(1)
        if not chunk:
            break
        data += chunk
    return _parse_instruction(data)


def _encode(*parts: str) -> bytes:
    return (",".join(f"{len(p)}.{p}" for p in parts) + ";").encode()


class _FakeGuacd:
    """Speaks the server side of guacd's select/args/connect/ready handshake.
    Records the connect instruction it received so tests can assert on it."""

    def __init__(self, *, arg_names: list[str], respond_ready: bool = True, error_reason: str | None = None):
        self.arg_names = arg_names
        self.respond_ready = respond_ready
        self.error_reason = error_reason
        self.select: list[str] | None = None
        self.connect: list[str] | None = None
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            self.select = await _read_one_instruction(reader)  # ["select", "rdp"]
            writer.write(_encode("args", *self.arg_names))
            await writer.drain()
            self.connect = await _read_one_instruction(reader)  # ["connect", <value per arg_name>...]
            if self.error_reason is not None:
                writer.write(_encode("error", self.error_reason, "769"))
            elif self.respond_ready:
                writer.write(_encode("ready", "$fake-connection-id"))
            await writer.drain()
            # Keep the connection open a beat so the client's reader doesn't
            # see an unexpected EOF before it has parsed "ready".
            await asyncio.sleep(0.1)
        except Exception:  # noqa: BLE001 - fake server, best effort
            pass
        finally:
            writer.close()


def _point_settings_at(monkeypatch, port: int) -> None:
    monkeypatch.setattr(
        remote_session,
        "get_settings",
        lambda: SimpleNamespace(guacd_host="127.0.0.1", guacd_port=port),
    )


def test_encode_read_instruction_roundtrip():
    """The hand-rolled instruction codec round-trips, including empty args and
    multibyte content (guacd length prefixes count characters)."""
    encoded = remote_session._encode_instruction("connect", "", "hello", "café")
    parts = _parse_instruction(encoded)
    assert parts == ["connect", "", "hello", "café"]


@pytest.mark.asyncio
async def test_open_guacd_connection_echoes_version_and_aligns_args(monkeypatch):
    arg_names = [
        "VERSION_1_5_0", "hostname", "port", "username", "password",
        "width", "height", "dpi", "resize-method", "ignore-cert",
        "enable-drive", "disable-audio", "security",
    ]
    fake = _FakeGuacd(arg_names=arg_names)
    await fake.start()
    _point_settings_at(monkeypatch, fake.port)
    try:
        reader, writer, ready_instruction, tunnel_host = await remote_session.open_guacd_connection(
            host="api", port=54321, username="Administrator", password="s3cret",
            width=1280, height=800,
        )
        writer.close()
    finally:
        await fake.stop()

    # The "ready" instruction must come back out so the route can replay it to
    # the browser: this backend consumes it during its own handshake, but
    # Guacamole.Client only reaches STATE_CONNECTED when IT receives "ready".
    # Swallowing it was the live "Server timeout" bug.
    assert _parse_instruction(ready_instruction.encode()) == ["ready", "$fake-connection-id"]

    assert fake.select == ["select", "rdp"]
    assert fake.connect is not None
    # connect[0] == "connect"; the rest align positionally with arg_names.
    values = dict(zip(arg_names, fake.connect[1:]))
    # The fix: the VERSION_* slot is answered with the version, NOT "" (which
    # would downgrade guacd to protocol 1.0.0).
    assert values["VERSION_1_5_0"] == "VERSION_1_5_0"
    # The hostname handed to guacd is deliberately NOT the caller's value: it's
    # the local address of the socket guacd is already connected on, which is
    # by construction an address guacd can dial back on. Telling it the literal
    # service name instead was why guacd silently never reached the tunnel
    # listener (the agent saw "local-RDP->agent leg ended after 0 bytes"). The
    # fake guacd here listens on loopback, so that address is 127.0.0.1 - the
    # point of the assertion is that it is the CONNECTION's address, not the
    # "api" string passed in.
    assert values["hostname"] == "127.0.0.1"
    # Same address is handed back to the caller, so a tunnel that never gets
    # dialed can name the exact address guacd was told to use.
    assert tunnel_host == "127.0.0.1"
    assert values["port"] == "54321"
    assert values["username"] == "Administrator"
    assert values["password"] == "s3cret"
    assert values["width"] == "1280"
    assert values["height"] == "800"
    assert values["security"] == "any"
    assert values["disable-audio"] == "true"


@pytest.mark.asyncio
async def test_open_guacd_connection_surfaces_guacd_error(monkeypatch):
    fake = _FakeGuacd(arg_names=["VERSION_1_5_0", "hostname", "port"], error_reason="Connection failed")
    await fake.start()
    _point_settings_at(monkeypatch, fake.port)
    try:
        with pytest.raises(remote_session.RemoteSessionError) as excinfo:
            await remote_session.open_guacd_connection(
                host="api", port=1, username=None, password=None, width=800, height=600,
            )
        assert "Connection failed" in str(excinfo.value)
    finally:
        await fake.stop()
