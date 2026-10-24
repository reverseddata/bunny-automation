"""Send each chat message exactly once, even across crashes and timeouts.

Bhopp sometimes posts a message and then times out answering. Re-sending on
an error therefore doubles messages. `Outbox` records (order, step) in SQLite
BEFORE posting and marks it sent after; if an earlier attempt's outcome is
unknown, it reads the conversation first and only posts if the text isn't
already there.

    box = Outbox("bhopp_outbox.sqlite", client)
    await box.send_once(order_id, conversation_id, seller_id, "welcome", text)
"""
from __future__ import annotations

import sqlite3


class Outbox:
    def __init__(self, path: str, client):
        self.client = client
        self.db = sqlite3.connect(path, check_same_thread=False)   # see OrderFlow
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS sent (order_id TEXT NOT NULL, step TEXT NOT NULL, body TEXT NOT NULL, "
            "status TEXT NOT NULL, message_id TEXT, at TEXT NOT NULL DEFAULT (datetime('now')), "
            "UNIQUE (order_id, step))")
        self.db.commit()

    def count(self, order_id: str, prefix: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM sent WHERE order_id = ? AND step LIKE ?",
                               (order_id, prefix + "%")).fetchone()[0]

    async def send_once(self, order_id: str, conversation_id: str, seller_id: str, step: str, body: str) -> bool:
        """True if posted now; False if this step was already sent."""
        row = self.db.execute("SELECT status FROM sent WHERE order_id = ? AND step = ?", (order_id, step)).fetchone()
        if row and row[0] == "sent":
            return False
        if row is None:
            self.db.execute("INSERT INTO sent (order_id, step, body, status) VALUES (?, ?, ?, 'sending')",
                            (order_id, step, body))
            self.db.commit()
        else:
            # Unknown outcome last time: look in the chat before posting again.
            for m in await self.client.messages(conversation_id):
                if m.get("sender") == seller_id and (m.get("body") or "").strip() == body.strip():
                    self._mark(order_id, step, m.get("id"))
                    return False
        sent = await self.client.send(conversation_id, body)
        self._mark(order_id, step, sent.get("id"))
        return True

    def _mark(self, order_id, step, mid):
        self.db.execute("UPDATE sent SET status = 'sent', message_id = ? WHERE order_id = ? AND step = ?",
                        (mid, order_id, step))
        self.db.commit()
