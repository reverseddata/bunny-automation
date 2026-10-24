"""bunny HTTP API: your own small server between Bhopp and your bot.

    uvicorn server.app:app --host 127.0.0.1 --port 8787

Every request needs the header `X-API-Key: <BUNNY_API_KEY>`. Configuration is
environment variables (see .env.example). In the background it runs the
order flow (OrderFlow) every BUNNY_POLL_S seconds, keeps you shown online on
Bhopp, and POSTs events to BUNNY_WEBHOOK_URL. See HTTP_API.md.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bunny import BhoppHTTPError, Chat, Client, OrderFlow, Presence, token_expiry, token_seller_id  # noqa: E402

log = logging.getLogger("bunny.server")
logging.basicConfig(level=os.environ.get("BUNNY_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")


# --------------------------------------------------------------------------- config
def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def read_token() -> str:
    """BHOPP_TOKEN, or the file named by BHOPP_TOKEN_FILE (re-read every call,
    so you can drop a new token in without restarting)."""
    path = env("BHOPP_TOKEN_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip().strip('"')
        except OSError:
            return ""
    return env("BHOPP_TOKEN").strip('"')


API_KEY = env("BUNNY_API_KEY")
WEBHOOK_URL = env("BUNNY_WEBHOOK_URL")
WEBHOOK_SECRET = env("BUNNY_WEBHOOK_SECRET")
POLL_S = float(env("BUNNY_POLL_S", "15") or 15)
FLOW_ON = env("BUNNY_FLOW", "on").lower() not in ("off", "0", "false", "no")
PRESENCE_ON = env("BUNNY_PRESENCE", "on").lower() not in ("off", "0", "false", "no")

client = Client(token_fn=read_token)
chat = Chat(seller=env("BUNNY_SELLER_NAME", "The seller"), trader=env("BUNNY_TRADER", "our trader"),
            link=env("BUNNY_SERVER_LINK"))
if env("BUNNY_TEXTS_FILE"):
    chat.texts.update(json.loads(Path(env("BUNNY_TEXTS_FILE")).read_text(encoding="utf-8")))


async def webhook(event: str, payload: dict) -> None:
    """POST {event, ...} to your bot. Signed with HMAC-SHA256 of the body in
    X-Bunny-Signature when BUNNY_WEBHOOK_SECRET is set."""
    if not WEBHOOK_URL:
        log.info("event %s (no webhook set)", event)
        return
    body = json.dumps({"event": event, **payload}, default=str).encode()
    headers = {"Content-Type": "application/json"}
    if WEBHOOK_SECRET:
        headers["X-Bunny-Signature"] = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=10) as h:
            await h.post(WEBHOOK_URL, content=body, headers=headers)
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook %s failed: %s", event, exc)


flow = OrderFlow(client, chat, db_path=env("BUNNY_DB", "bunny.sqlite"), on_event=webhook,
                 min_order_cents=int(env("BUNNY_MIN_ORDER_CENTS", "0") or 0))
runtime = {"flow_on": FLOW_ON, "last_tick": None, "last_error": None}


async def loop():
    while True:
        if runtime["flow_on"] and read_token():
            try:
                await flow.tick()
                runtime["last_tick"], runtime["last_error"] = asyncio.get_event_loop().time(), None
            except Exception as exc:  # noqa: BLE001
                runtime["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("flow tick failed: %s", runtime["last_error"])
        await asyncio.sleep(POLL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not API_KEY:
        raise RuntimeError("set BUNNY_API_KEY (any long random string) before starting")
    tasks = [asyncio.create_task(loop())]
    if PRESENCE_ON:
        tasks.append(asyncio.create_task(Presence(read_token, lambda: bool(read_token())).run()))
    yield
    for t in tasks:
        t.cancel()
    await client.close()


app = FastAPI(title="bunny", version="0.2.0", lifespan=lifespan)


def need_key(x_api_key: str = Header(default="")):
    if not API_KEY or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "bad or missing X-API-Key")


def bhopp(fn):
    """Run a Bhopp call; Bhopp's own error comes back as the same status."""
    @functools.wraps(fn)          # keeps the signature, so FastAPI sees the parameters
    async def run(*a, **k):
        try:
            return await fn(*a, **k)
        except BhoppHTTPError as exc:
            raise HTTPException(exc.status if 400 <= exc.status < 600 else 502, str(exc)) from None
    return run


# --------------------------------------------------------------------------- status
@app.get("/health", dependencies=[Depends(need_key)])
async def health():
    tok = read_token()
    exp = token_expiry(tok) if tok else None
    return {"ok": True, "token_set": bool(tok), "token_expires": exp.isoformat() if exp else None,
            "seller_id": token_seller_id(tok) if tok else None, "flow_on": runtime["flow_on"],
            "last_error": runtime["last_error"], "min_order_cents": flow.min_order_cents}


class FlowIn(BaseModel):
    on: bool


@app.put("/flow", dependencies=[Depends(need_key)])
async def set_flow(body: FlowIn):
    """Switch the automatic order chat on or off (the API keeps working)."""
    runtime["flow_on"] = body.on
    return {"flow_on": body.on}


class SettingsIn(BaseModel):
    min_order_cents: int | None = Field(default=None, ge=0, le=1_000_000)
    server_link: str | None = Field(default=None, max_length=500)


@app.put("/settings", dependencies=[Depends(need_key)])
async def settings(body: SettingsIn):
    """Change the minimum order or the private server link without a restart."""
    if body.min_order_cents is not None:
        flow.min_order_cents = body.min_order_cents
    if body.server_link is not None:
        chat.link = body.server_link
    return {"min_order_cents": flow.min_order_cents, "server_link": chat.link}


# --------------------------------------------------------------------------- catalogue / prices
@app.get("/products", dependencies=[Depends(need_key)])
@bhopp
async def products(search: str = "", page: int = 1):
    return await client.products_page(page, search or None)


@app.get("/products/{slug}/lowest", dependencies=[Depends(need_key)])
@bhopp
async def lowest(slug: str, include_mine: bool = False):
    """The cheapest live price for a product, in cents (yours left out unless include_mine)."""
    mine = None if include_mine else token_seller_id(read_token())
    return {"slug": slug, "lowest_cents": await client.lowest_price(slug, exclude_seller=mine)}


# --------------------------------------------------------------------------- offers (items)
class OfferIn(BaseModel):
    product_id: str | None = None
    slug: str | None = None
    price_cents: int = Field(ge=1, le=10_000_000)
    quantity: int = Field(ge=0, le=10_000)


class OfferPatch(BaseModel):
    price_cents: int | None = Field(default=None, ge=1, le=10_000_000)
    quantity: int | None = Field(default=None, ge=0, le=10_000)


@app.get("/offers", dependencies=[Depends(need_key)])
@bhopp
async def offers():
    return await client.my_offers()


@app.post("/offers", dependencies=[Depends(need_key)])
@bhopp
async def add_offer(body: OfferIn):
    """Add an item: list a product (by id or slug) at a price with a stock."""
    pid = body.product_id
    if not pid and body.slug:
        pid = (await client.product(body.slug)).get("id")
    if not pid:
        raise HTTPException(400, "give product_id or a valid slug")
    return await client.create_offer(pid, body.price_cents, body.quantity)


@app.patch("/offers/{offer_id}", dependencies=[Depends(need_key)])
@bhopp
async def edit_offer(offer_id: str, body: OfferPatch):
    """Push a price and/or change the stock."""
    fields = {k: v for k, v in (("amount", body.price_cents), ("quantity", body.quantity)) if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to change")
    return await client.update_offer(offer_id, **fields)


@app.delete("/offers/{offer_id}", dependencies=[Depends(need_key)])
@bhopp
async def remove_offer(offer_id: str):
    await client.delete_offer(offer_id)
    return {"ok": True}


# --------------------------------------------------------------------------- orders
@app.get("/orders", dependencies=[Depends(need_key)])
@bhopp
async def orders(state: str = "open", page: int = 1):
    """state=open (pending + delivered) or past."""
    d = await client.seller_orders(page, state)
    for o in d.get("orders") or []:
        o["flow"] = flow.get(o["id"])
    return d


@app.get("/orders/{order_id}/messages", dependencies=[Depends(need_key)])
@bhopp
async def order_messages(order_id: str):
    row = flow.get(order_id)
    if not row:
        raise HTTPException(404, "unknown order (not seen by the flow yet)")
    return await client.messages(row["conversation_id"])


class CompleteIn(BaseModel):
    thanks: bool = True
    archive: bool = False


@app.post("/orders/{order_id}/complete", dependencies=[Depends(need_key)])
@bhopp
async def complete(order_id: str, body: CompleteIn | None = None):
    """Your bot handed the items over: mark delivered (+ "thanks", optional archive)."""
    body = body or CompleteIn()
    return await flow.complete(order_id, thanks=body.thanks, archive=body.archive)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.post("/orders/{order_id}/message", dependencies=[Depends(need_key)])
@bhopp
async def message(order_id: str, body: MessageIn):
    """Send your own text in the order chat."""
    row = flow.get(order_id)
    if not row:
        raise HTTPException(404, "unknown order (not seen by the flow yet)")
    return await client.send(row["conversation_id"], body.text)


class AutoIn(BaseModel):
    server_link: str | None = Field(default=None, max_length=500)


@app.post("/orders/{order_id}/autodelivery", dependencies=[Depends(need_key)])
@bhopp
async def autodelivery(order_id: str, body: AutoIn | None = None):
    """Push the autodelivery message with a private server link (yours, or the default)."""
    try:
        sent = await flow.autodelivery(order_id, (body or AutoIn()).server_link)
    except KeyError:
        raise HTTPException(404, "unknown order (not seen by the flow yet)") from None
    return {"sent": sent}


@app.post("/orders/{order_id}/problem", dependencies=[Depends(need_key)])
@bhopp
async def problem(order_id: str):
    """Your delivery failed: the one "issue" line to the buyer (once per order)."""
    return {"sent": await flow.problem(order_id)}


@app.post("/orders/{order_id}/archive", dependencies=[Depends(need_key)])
@bhopp
async def archive(order_id: str):
    row = flow.get(order_id)
    if not row:
        raise HTTPException(404, "unknown order (not seen by the flow yet)")
    return await client.archive(row["conversation_id"])
