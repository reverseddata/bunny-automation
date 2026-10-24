# bunny HTTP API

Every request needs `X-API-Key: <BUNNY_API_KEY>` (401 otherwise). Bodies are
JSON. Prices are in **cents**. When Bhopp itself refuses a call, you get
Bhopp's status code and message back unchanged.

## Status and settings

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/health` | | `{ok, token_set, token_expires, seller_id, flow_on, last_error, min_order_cents}` |
| PUT | `/flow` | `{on: bool}` | `{flow_on}`: pauses or resumes the automatic order chat |
| PUT | `/settings` | `{min_order_cents?, server_link?}` | the current values |

## Products and prices

| Method | Path | Returns |
|---|---|---|
| GET | `/products?search=&page=1` | Bhopp's product page: `{products: [{id, slug, name, ...}], page, pages, total}` |
| GET | `/products/{slug}/lowest?include_mine=false` | `{slug, lowest_cents}`: cheapest in-stock offer, yours left out unless `include_mine=true`; `null` if none |

## Your items (offers)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/offers` | | `{offers: [...]}` |
| POST | `/offers` | `{slug or product_id, price_cents, quantity}` | the new offer |
| PATCH | `/offers/{id}` | `{price_cents?, quantity?}` (at least one) | the updated offer |
| DELETE | `/offers/{id}` | | `{ok: true}`: gone from your Offers page |

## Orders

| Method | Path | Body | What it does |
|---|---|---|---|
| GET | `/orders?state=open&page=1` | | Bhopp's orders (`open` or `past`), each with `flow`: the chat state, or `null` |
| GET | `/orders/{id}/messages` | | the order chat, oldest first |
| POST | `/orders/{id}/complete` | `{thanks: true, archive: false}` | marks delivered on Bhopp, sends "thanks", optionally archives the chat |
| POST | `/orders/{id}/message` | `{text}` | sends your own message |
| POST | `/orders/{id}/autodelivery` | `{server_link?}` | sends the autodelivery message (default link if none given) |
| POST | `/orders/{id}/problem` | | the one "something went wrong" line, once per order |
| POST | `/orders/{id}/archive` | | archives the order chat |

Calls that need the chat return 404 until the flow has seen the order (one
poll, `BUNNY_POLL_S`).

### Flow states

`choice` (asked automated or human) -> `username` -> `confirm` -> `ready`
(autodelivery sent, your bot's turn) -> `done`. Also `human` (you deliver),
`below_minimum`, and `closed` (finished or gone on Bhopp's side).

## Webhook events

POSTed as JSON to `BUNNY_WEBHOOK_URL`: `{"event": "...", ...}`. With
`BUNNY_WEBHOOK_SECRET` set, header `X-Bunny-Signature` is the hex
HMAC-SHA256 of the raw body.

| Event | Extra fields | Means |
|---|---|---|
| `order.new` | `order` | a new order, welcome is on its way |
| `order.confirmed` | `order`, `roblox_name` | **trade them now**, then call `/complete` |
| `order.human` | `order`, `reason` | the buyer wants you (chose human, or said stop) |
| `order.flagged` | `order`, `text` | cancel / refund / scam / not received ... look at it |
| `order.below_minimum` | `order`, `cents` | under your minimum; the bot sent one message and stopped, you decide |
| `order.closed` | `order_id`, `bhopp_state` | delivered, cancelled or gone on Bhopp; the bot stopped |

`order` is Bhopp's order object: `{id, state, buyer: {id, username}, items:
[{name, quantity, unitAmount}], totals, conversationId, createdAt}`.

Checking the signature (Python):

```python
import hmac, hashlib
ok = hmac.compare_digest(request.headers["X-Bunny-Signature"],
                         hmac.new(SECRET.encode(), raw_body, hashlib.sha256).hexdigest())
```
