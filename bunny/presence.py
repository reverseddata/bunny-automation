"""Keep a Bhopp seller account shown as ONLINE (what an open bhopp.com tab does).

bhopp.com does it from the open browser tab (read off its bundle,
2026-10-20): a socket.io connection to api.sublimelistings.com:5000 on the
"/user" namespace, authenticated with the login token, emitting
"presence:ping" every 60 s (the site stops after an hour of inactivity, and
the seller drops to away). Anyone can read it back from
GET /market/presence?sellers=<id> -> {"status": "online" | "away", "ago"}.

`Presence(token_fn, wanted_fn)` does the same from a script: while
`wanted_fn()` is true and there is a token, it stays connected and pings
every 60 s; it reconnects on its own and follows a new token. Never raises.
Needs `python-socketio[asyncio_client]`.
"""
import asyncio
import logging

log = logging.getLogger("bhopp")
URL = "https://api.sublimelistings.com:5000"
NAMESPACE = "/user"
PING_EVERY_S = 60
RETRY_S = 30


class Presence:
    def __init__(self, token_fn, wanted_fn, client_factory=None):
        """token_fn() -> current token or ""; wanted_fn() -> bool (bot on, token valid)."""
        self.token_fn = token_fn
        self.wanted_fn = wanted_fn
        self.client_factory = client_factory or _socketio_client
        self.sio = None
        self.token = None
        self.pings = 0
        self.last_error = None

    async def _close(self):
        if self.sio is not None:
            try:
                await self.sio.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.sio, self.token = None, None

    async def tick(self) -> None:
        """One pass: be connected (or not) as wanted, and ping."""
        want = False
        try:
            want = bool(self.wanted_fn())
        except Exception:  # noqa: BLE001
            want = False
        tok = self.token_fn() if want else ""
        if not want or not tok:
            await self._close()
            return
        if self.sio is not None and (tok != self.token or not self.sio.connected):
            await self._close()                      # new token, or the connection dropped
        if self.sio is None:
            sio = self.client_factory()
            try:
                await sio.connect(URL, namespaces=[NAMESPACE], auth={"token": tok},
                                  transports=["websocket", "polling"], wait_timeout=15)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
                log.warning("bhopp presence: couldn't connect (%s)", self.last_error)
                return
            self.sio, self.token = sio, tok
            log.info("bhopp presence: connected, showing online")
        try:
            await self.sio.emit("presence:ping", namespace=NAMESPACE)
            self.pings += 1
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            await self._close()

    async def run(self) -> None:
        while True:
            await self.tick()
            await asyncio.sleep(PING_EVERY_S if self.sio is not None else RETRY_S)


def _socketio_client():
    import socketio
    return socketio.AsyncClient(reconnection=False, logger=False, engineio_logger=False)
