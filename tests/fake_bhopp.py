"""A fake Bhopp at the HTTP level (httpx.MockTransport): the real Client,
OrderFlow and server run against it, so the tests check what Bhopp would
actually receive. Only the endpoints bhopp.com uses are implemented."""
import base64
import itertools
import json
import re
from datetime import datetime, timedelta, timezone

import httpx

SELLER = "seller-1"


def fake_token(user=SELLER) -> str:
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    exp = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())
    return f"{b64({'alg': 'HS256'})}.{b64({'user': user, 'exp': exp})}.sig"


class FakeBhopp:
    def __init__(self):
        self._n = itertools.count(1)
        self.t = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
        self.products = {"harvester": {"id": "p1", "slug": "harvester", "name": "Harvester"}}
        self.market = {"harvester": [{"id": "x1", "amount": 500, "quantity": 2, "seller": {"id": "other"}},
                                     {"id": "x2", "amount": 450, "quantity": 0, "seller": {"id": "other2"}}]}
        self.offers, self.orders, self.convos, self.archived = {}, {}, {}, set()
        self.calls = []

    def ts(self):
        self.t += timedelta(seconds=1)
        return self.t.isoformat().replace("+00:00", "Z")

    # ---- test helpers ------------------------------------------------------
    def buy(self, cents=500, buyer="b1", name="Harvester", qty=1):
        oid, conv = f"o{next(self._n)}", f"c{next(self._n)}"
        self.orders[oid] = {"id": oid, "state": "pending", "conversationId": conv, "createdAt": self.ts(),
                            "buyer": {"id": buyer, "username": "buyer"}, "seller": {"id": SELLER},
                            "items": [{"name": name, "quantity": qty}],
                            "totals": {"gross": cents * 100, "currency": "USD"}}
        self.convos[conv] = [{"id": f"m{next(self._n)}", "sender": None, "kind": "system", "event": "placed",
                              "body": "Order placed", "createdAt": self.ts()}]
        return self.orders[oid]

    def say(self, order, text):
        self.convos[order["conversationId"]].append(
            {"id": f"m{next(self._n)}", "sender": order["buyer"]["id"], "kind": "text", "body": text,
             "createdAt": self.ts()})

    def ours(self, order):
        return [m["body"] for m in self.convos[order["conversationId"]] if m["sender"] == SELLER]

    # ---- HTTP --------------------------------------------------------------
    def handler(self, req: httpx.Request) -> httpx.Response:
        m, path = req.method, req.url.path
        self.calls.append((m, path))
        body = json.loads(req.content) if req.content and req.headers.get("content-type", "").startswith(
            "application/json") else {}
        auth = req.headers.get("authorization", "").startswith("Bearer ")
        ok = lambda d: httpx.Response(200, json=d)  # noqa: E731
        if path == "/products":
            q = (req.url.params.get("search") or "").lower()
            return ok({"products": [p for p in self.products.values() if q in p["name"].lower()], "page": 1,
                       "pages": 1})
        if r := re.fullmatch(r"/products/([^/]+)/offers", path):
            mine = [{"id": o["id"], "amount": o["amount"], "quantity": o["quantity"], "seller": {"id": SELLER}}
                    for o in self.offers.values() if o["product"]["slug"] == r[1]]
            return ok({"offers": self.market.get(r[1], []) + mine})
        if r := re.fullmatch(r"/products/([^/]+)", path):
            return ok({"product": self.products.get(r[1], {})})
        if not auth:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "/seller/offers" and m == "GET":
            return ok({"offers": list(self.offers.values())})
        if path == "/seller/offers" and m == "POST":
            p = next(p for p in self.products.values() if p["id"] == body["productId"])
            o = {"id": f"of{next(self._n)}", "amount": body["amount"], "quantity": body["quantity"], "product": p}
            self.offers[o["id"]] = o
            return ok({"offer": o})
        if r := re.fullmatch(r"/seller/offers/([^/]+)", path):
            if m == "PATCH":
                self.offers[r[1]].update(body)
                return ok({"offer": self.offers[r[1]]})
            self.offers.pop(r[1], None)
            return ok({})
        if path == "/seller/orders":
            st = req.url.params.get("state")
            pick = [o for o in self.orders.values()
                    if st != "open" or o["state"] in ("pending", "delivered")]
            return ok({"orders": pick, "page": 1, "pages": 1})
        if r := re.fullmatch(r"/orders/([^/]+)/(delivered|cancel)", path):
            self.orders[r[1]]["state"] = "delivered" if r[2] == "delivered" else "cancelled"
            return ok({"order": self.orders[r[1]]})
        if r := re.fullmatch(r"/conversations/([^/]+)/messages", path):
            if m == "POST":
                msg = {"id": f"m{next(self._n)}", "sender": SELLER, "kind": "text", "body": body["body"],
                       "createdAt": self.ts()}
                self.convos[r[1]].append(msg)
                return ok({"message": msg})
            return ok({"messages": list(reversed(self.convos[r[1]]))})     # newest first, like Bhopp
        if r := re.fullmatch(r"/conversations/([^/]+)/archive", path):
            (self.archived.add if body.get("archived") else self.archived.discard)(r[1])
            return ok({"conversation": {"id": r[1], "archived": body.get("archived")}})
        return httpx.Response(404, json={"error": f"no route {m} {path}"})

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
