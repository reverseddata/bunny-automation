"""Async client for Bhopp's seller API (unofficial).

These are the calls bhopp.com itself makes, read off the site's own code.
Undocumented: Bhopp can change them at any time. Prices are in CENTS.
Authentication is your seller login token (bhopp.com -> F12 ->
`localStorage.getItem("bhopp.token")`), a JWT valid for 7 days.

    from bunny import Client
    c = Client(token="eyJ...")              # or token_fn=lambda: read_it_somewhere()
    orders = (await c.seller_orders())["orders"]
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Callable

import httpx

BASE = "https://api.sublimelistings.com:5000"


class BhoppHTTPError(RuntimeError):
    def __init__(self, status: int, text: str):
        super().__init__(f"Bhopp answered {status}: {text[:200]}")
        self.status = status


def token_expiry(token: str) -> datetime | None:
    """When a Bhopp login token expires (from its JWT `exp`), or None."""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        exp = json.loads(base64.urlsafe_b64decode(body)).get("exp")
        return datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None
    except Exception:  # noqa: BLE001
        return None


def token_seller_id(token: str) -> str | None:
    """Your seller id, from the token's `user` claim."""
    try:
        body = token.split(".")[1]
        body += "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(body)).get("user")
    except Exception:  # noqa: BLE001
        return None


class Client:
    def __init__(self, token: str | None = None, *, token_fn: Callable[[], str] | None = None,
                 base: str = BASE, http: httpx.AsyncClient | None = None):
        """Pass `token`, or `token_fn` to re-read it on every call (a new
        token then works without restarting)."""
        if token is None and token_fn is None:
            raise ValueError("give token= or token_fn=")
        self._token_fn = token_fn or (lambda: token)
        self.base = base.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0),
                                               headers={"User-Agent": "bunny-automation"})

    def token(self) -> str:
        return (self._token_fn() or "").strip().strip('"')

    async def close(self):
        await self._http.aclose()

    async def _call(self, method: str, path: str, *, auth: bool = True, **kw):
        headers = kw.pop("headers", {})
        if auth:
            t = self.token()
            if not t:
                raise BhoppHTTPError(401, "no Bhopp token")
            headers["Authorization"] = f"Bearer {t}"
        r = await self._http.request(method, self.base + path, headers=headers, **kw)
        if r.status_code >= 400:
            raise BhoppHTTPError(r.status_code, r.text)
        return r.json() if r.content else {}

    # ---- catalogue / market (no login) --------------------------------------
    async def products_page(self, page: int = 1, search: str | None = None) -> dict:
        """{products: [{id, slug, name, rarity, category, ...}], page, pages, total} (20 per page)."""
        params = {"page": page}
        if search:
            params["search"] = search
        return await self._call("GET", "/products", auth=False, params=params)

    async def product(self, slug: str) -> dict:
        return (await self._call("GET", f"/products/{slug}", auth=False)).get("product") or {}

    async def product_offers(self, slug: str) -> list[dict]:
        """Every live offer for one product: [{id, amount, quantity, seller}]."""
        return (await self._call("GET", f"/products/{slug}/offers", auth=False)).get("offers") or []

    async def lowest_price(self, slug: str, exclude_seller: str | None = None) -> int | None:
        """Cheapest live offer for a product in cents (in stock), optionally
        ignoring your own offers. None if nobody sells it."""
        prices = [o["amount"] for o in await self.product_offers(slug)
                  if int(o.get("quantity") or 0) > 0
                  and (exclude_seller is None or (o.get("seller") or {}).get("id") != exclude_seller)]
        return min(prices) if prices else None

    async def presence(self, seller_ids: list[str]) -> dict:
        """{seller_id: {status: "online" | "away", ago}}."""
        return (await self._call("GET", "/market/presence", auth=False,
                                 params={"sellers": ",".join(seller_ids)})).get("sellers") or {}

    # ---- your offers --------------------------------------------------------
    async def my_offers(self) -> list[dict]:
        return (await self._call("GET", "/seller/offers")).get("offers") or []

    async def create_offer(self, product_id: str, amount: int, quantity: int) -> dict:
        """List a product: `amount` is the price each in cents (fee included)."""
        return (await self._call("POST", "/seller/offers",
                                 json={"productId": product_id, "amount": int(amount), "quantity": int(quantity)})
                ).get("offer") or {}

    async def update_offer(self, offer_id: str, **fields) -> dict:
        """e.g. update_offer(id, amount=350, quantity=2) -- price and/or stock."""
        return (await self._call("PATCH", f"/seller/offers/{offer_id}", json=fields)).get("offer") or {}

    async def delete_offer(self, offer_id: str) -> dict:
        """Remove an offer from the Offers page completely."""
        return await self._call("DELETE", f"/seller/offers/{offer_id}")

    async def set_vacation(self, enabled: bool) -> dict:
        return (await self._call("PUT", "/seller/vacation", json={"enabled": bool(enabled)})).get("user") or {}

    async def set_auto_vacation(self, enabled: bool) -> dict:
        return (await self._call("PUT", "/seller/vacation/auto", json={"enabled": bool(enabled)})).get("user") or {}

    # ---- orders ---------------------------------------------------------------
    async def seller_orders(self, page: int = 1, state: str = "open") -> dict:
        """state "open" (pending + delivered) or "past" (completed + cancelled).
        Without a state Bhopp returns every order, past ones included."""
        return await self._call("GET", "/seller/orders", params={"page": page, "state": state})

    async def all_open_orders(self, max_pages: int = 20) -> list[dict]:
        out, page, pages = [], 1, 1
        while page <= pages and page <= max_pages:
            d = await self.seller_orders(page)
            pages = int(d.get("pages") or 1)
            out += d.get("orders") or []
            page += 1
        return out

    async def mark_delivered(self, order_id: str) -> dict:
        """Complete an order on your side (Bhopp's "Mark delivered")."""
        return (await self._call("PATCH", f"/orders/{order_id}/delivered")).get("order") or {}

    async def submit_proof(self, order_id: str, files: dict) -> dict:
        """Delivery proof upload (POST /orders/{id}/proof), multipart -- e.g.
        files={"file": ("trade.jpg", jpeg_bytes, "image/jpeg")}. The field
        names the site uses aren't confirmed; check before relying on it."""
        return (await self._call("POST", f"/orders/{order_id}/proof", files=files)).get("order") or {}

    # ---- chat -------------------------------------------------------------------
    async def messages(self, conversation_id: str) -> list[dict]:
        """Oldest first: [{id, sender, kind: "text" | "system", event, body, createdAt}]."""
        msgs = (await self._call("GET", f"/conversations/{conversation_id}/messages")).get("messages") or []
        return sorted(msgs, key=lambda m: m.get("createdAt") or "")

    async def send(self, conversation_id: str, body: str) -> dict:
        return (await self._call("POST", f"/conversations/{conversation_id}/messages", json={"body": body})
                ).get("message") or {}

    async def mark_read(self, conversation_id: str) -> dict:
        return await self._call("POST", f"/conversations/{conversation_id}/read")

    async def archive(self, conversation_id: str, archived: bool = True) -> dict:
        """Archive (or un-archive) a finished chat."""
        return (await self._call("PATCH", f"/conversations/{conversation_id}/archive",
                                 json={"archived": bool(archived)})).get("conversation") or {}
