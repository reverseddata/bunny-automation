"""The order flow: Bhopp chat -> a confirmed Roblox name -> YOUR delivery.

    flow = OrderFlow(client, chat, db_path="bunny.sqlite", on_event=handler,
                     min_order_cents=500)
    await flow.tick()          # call every ~15 s (the server does it for you)

Per order:
  new order          -> below the minimum? "below_minimum" once, event order.below_minimum, nothing else
                     -> else "welcome" (automated or human?)
  "automated"        -> "ask_username" -> a real Roblox name -> "confirm" (type yes)
  "yes"              -> "autodelivery" with your private server link, event order.confirmed
                        {order, roblox_name}: your bot (Lua or anything) trades them,
                        then calls complete() -> marked delivered on Bhopp + "thanks"
  "human"            -> "human", event order.human; a later "automated" asks to confirm
                        ("auto_confirm") and a "yes" switches back
  "stop"             -> "human" at any step
  cancel / refund / scam / not received ... -> "stop_hint" (twice at most) + event order.flagged

Never spams: every message goes through the Outbox (once per order and
step, crash-safe), "didn't get that" replies stop after two, nothing is sent
for messages written before the welcome, and an order that left Bhopp's open
list stops being worked on. State lives in SQLite, so a restart resumes.
"""
from __future__ import annotations

import inspect
import logging
import sqlite3
from typing import Awaitable, Callable

from .chat import DANGER, STOP, YES, Chat, read_choice, read_username
from .client import BhoppHTTPError, Client, token_seller_id
from .outbox import Outbox

log = logging.getLogger("bunny.flow")

OPEN = ("choice", "username", "confirm", "ready", "human")
MAX_AGAIN = 2


def order_cents(order: dict) -> int | None:
    """What the buyer paid, in cents. Bhopp's order totals are in 1/100 of a
    cent ("1700" = $0.17), unlike offer prices."""
    g = (order.get("totals") or {}).get("gross")
    try:
        return int(round(float(g) / 100))
    except (TypeError, ValueError):
        return None


class OrderFlow:
    def __init__(self, client: Client, chat: Chat, *, db_path: str = "bunny.sqlite",
                 lookup: Callable[[str], Awaitable[str | None]] | None = None,
                 on_event: Callable[[str, dict], object] | None = None,
                 min_order_cents: int = 0):
        self.client, self.chat = client, chat
        self.lookup = lookup
        self.on_event = on_event
        self.min_order_cents = int(min_order_cents or 0)
        # One event loop does all the work; the connection is only created in
        # another thread (at import), so the same-thread check is lifted.
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY, conversation_id TEXT, buyer_id TEXT, buyer_name TEXT, state TEXT NOT NULL,
            roblox_name TEXT, seen TEXT, chose_human INTEGER NOT NULL DEFAULT 0, switches INTEGER NOT NULL DEFAULT 0,
            cents INTEGER, items TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        self.db.commit()
        self.box = Outbox(db_path, client)

    # ------------------------------------------------------------------ helpers
    async def _emit(self, event: str, payload: dict) -> None:
        if not self.on_event:
            return
        try:
            r = self.on_event(event, payload)
            if inspect.isawaitable(r):
                await r
        except Exception:  # noqa: BLE001
            log.exception("event handler failed for %s", event)

    def get(self, oid: str) -> dict | None:
        r = self.db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
        return dict(r) if r else None

    def _set(self, oid: str, **cols) -> None:
        sets = ", ".join(f"{k} = ?" for k in cols)
        self.db.execute(f"UPDATE orders SET {sets}, updated_at = datetime('now') WHERE id = ?", (*cols.values(), oid))
        self.db.commit()

    def _seller(self) -> str:
        return token_seller_id(self.client.token()) or ""

    async def _say(self, row: dict, step: str, key: str, **kw) -> bool:
        return await self.box.send_once(row["id"], row["conversation_id"], self._seller(), step,
                                        self.chat.text(key, **kw))

    # --------------------------------------------------------------------- tick
    async def tick(self) -> None:
        live = {o["id"]: o for o in await self.client.all_open_orders()}
        for o in live.values():
            if o.get("state") == "pending" and self.get(o["id"]) is None:
                await self._new(o)
        for row in [dict(r) for r in self.db.execute(
                f"SELECT * FROM orders WHERE state IN ({','.join('?' * len(OPEN))})", OPEN)]:
            o = live.get(row["id"])
            if o is None or o.get("state") != "pending":
                # Delivered, completed, cancelled or gone on Bhopp's side: stop.
                self._set(row["id"], state="closed")
                await self._emit("order.closed", {"order_id": row["id"], "bhopp_state": (o or {}).get("state", "gone")})
                continue
            try:
                await self._work(row, o)
            except BhoppHTTPError as exc:
                log.warning("order %s: %s", row["id"], exc)

    async def _new(self, o: dict) -> None:
        import json
        cents = order_cents(o)
        self.db.execute(
            "INSERT OR IGNORE INTO orders (id, conversation_id, buyer_id, buyer_name, state, seen, cents, items) "
            "VALUES (?, ?, ?, ?, 'choice', ?, ?, ?)",
            (o["id"], o.get("conversationId"), (o.get("buyer") or {}).get("id"),
             (o.get("buyer") or {}).get("username"), "", cents,
             json.dumps([{"name": i.get("name"), "qty": i.get("quantity")} for i in o.get("items") or []])))
        self.db.commit()
        row = self.get(o["id"])
        if self.min_order_cents and cents is not None and cents < self.min_order_cents:
            await self._say(row, "below_minimum", "below_minimum", minimum=f"${self.min_order_cents / 100:.2f}")
            # Bhopp has no seller cancel that we know works: you decide what to do.
            self._set(o["id"], state="below_minimum")
            await self._emit("order.below_minimum", {"order": o, "cents": cents})
            return
        await self._emit("order.new", {"order": o})

    async def _work(self, row: dict, o: dict) -> None:
        just_welcomed = False
        if row["state"] == "choice":
            just_welcomed = await self._say(row, "welcome", "welcome")
        msgs = await self.client.messages(row["conversation_id"])
        seen = row["seen"] or ""
        new = [m for m in msgs if m.get("sender") == row["buyer_id"] and m.get("kind") == "text"
               and (m.get("createdAt") or "") > seen]
        for i, m in enumerate(new):
            # One "didn't get that" at most per pass, for the newest message, and
            # none for messages written before the buyer saw the welcome.
            self._may_ask = (i == len(new) - 1) and not just_welcomed
            await self._handle(self.get(row["id"]), o, (m.get("body") or "").strip())
            self._set(row["id"], seen=m["createdAt"])

    async def _again(self, row: dict, step: str, key: str) -> None:
        if not getattr(self, "_may_ask", True):
            return
        n = self.box.count(row["id"], step + ":")
        if n < MAX_AGAIN:
            await self._say(row, f"{step}:{n + 1}", key)
        elif n == MAX_AGAIN:
            await self._say(row, f"{step}:hint", "stop_hint")

    async def _handle(self, row: dict, o: dict, text: str) -> None:
        st = row["state"]
        if st == "ready":
            if DANGER.search(text):
                await self._emit("order.flagged", {"order": o, "text": text})
            return
        if st == "human":
            # Chose human, then "automated": confirm once, "yes" switches back.
            step = f"auto_confirm:{row['switches']}"
            if row["chose_human"] and read_choice(text) == "automated":
                await self._say(row, step, "auto_confirm")
            elif row["chose_human"] and YES.match(text) and self.box.count(row["id"], step) > 0:
                self._set(row["id"], state="username", switches=row["switches"] + 1, chose_human=0)
                await self._say(self.get(row["id"]), f"switched:{row['switches'] + 1}", "switched")
            return
        if STOP.match(text):
            self._set(row["id"], state="human")
            await self._say(row, "human", "human")
            await self._emit("order.human", {"order": o, "reason": "buyer said stop"})
            return
        if DANGER.search(text):
            n = self.box.count(row["id"], "stop_hint")
            if n < 2:
                await self._say(row, f"stop_hint:{n + 1}", "stop_hint")
            await self._emit("order.flagged", {"order": o, "text": text})
            return
        if st == "choice":
            c = read_choice(text)
            if c == "automated":
                self._set(row["id"], state="username")
                await self._say(row, "ask_username", "ask_username")
            elif c == "human":
                self._set(row["id"], state="human", chose_human=1)
                await self._say(row, "human", "human")
                await self._emit("order.human", {"order": o, "reason": "buyer chose human"})
            else:
                await self._again(row, "choice_again", "choice_again")
            return
        if st in ("username", "confirm"):
            if st == "confirm" and YES.match(text):
                self._set(row["id"], state="ready")
                await self._say(row, "autodelivery", "autodelivery", name=row["roblox_name"])
                await self._emit("order.confirmed", {"order": o, "roblox_name": row["roblox_name"]})
                return
            name = read_username(text)
            real = (await self.lookup(name)) if (name and self.lookup) else name
            if real:
                self._set(row["id"], state="confirm", roblox_name=real)
                k = self.box.count(row["id"], "confirm:") + 1
                await self._say(row, f"confirm:{k}", "confirm", name=real)
            else:
                await self._again(row, "username_again", "username_again")

    # ------------------------------------------------------------ your actions
    async def complete(self, order_id: str, thanks: bool = True, archive: bool = False) -> dict:
        """Your bot finished the trade: mark delivered on Bhopp, say thanks."""
        order = await self.client.mark_delivered(order_id)
        row = self.get(order_id)
        if row:
            self._set(order_id, state="done")
            if thanks:
                await self._say(row, "thanks", "thanks")
            if archive and row["conversation_id"]:
                try:
                    await self.client.archive(row["conversation_id"])
                except BhoppHTTPError:
                    pass
        return order

    async def autodelivery(self, order_id: str, link: str | None = None) -> bool:
        """Push the autodelivery message now (e.g. with a fresh server link)."""
        row = self.get(order_id)
        if row is None:
            raise KeyError(order_id)
        n = self.box.count(order_id, "autodelivery")
        return await self._say(row, f"autodelivery:{n + 1}" if n else "autodelivery", "autodelivery",
                               name=row["roblox_name"] or "", **({"link": link} if link else {}))

    async def problem(self, order_id: str) -> bool:
        """Something went wrong on your side: the one "issue" line, once per order."""
        row = self.get(order_id)
        return bool(row) and await self._say(row, "issue", "issue")
