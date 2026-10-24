# bunny-automation

An unofficial toolkit for selling on [Bhopp](https://bhopp.com): a Python
library and a small HTTP API that sits between Bhopp and your own trade bot.

- **Items:** add an item, change its stock, push a price, remove it, fetch the lowest price on the market
- **Orders:** list them, complete (mark delivered), archive the chat
- **Chat:** an automatic order chat (automated or human delivery, Roblox name, confirm), your own messages, an autodelivery message with your private server link
- **Minimum order:** orders below e.g. $5 get one message and are flagged for you
- **Webhooks:** your bot is told when an order is confirmed, so it (your Lua, or anything else) does the trade
- **Stays online** on Bhopp while it runs

Not made by or affiliated with Bhopp. It uses the same calls bhopp.com's own
pages make (see [BHOPP_API.md](BHOPP_API.md)), which can change at any time.

## What the buyer sees

Your buyer orders, and the chat runs by itself (names here are placeholders):

> **Order placed**
>
> **You:** Hey, thanks for your purchase! Would you like automated delivery (5 min. average) or human delivery (up to 1 hour or more).
> Reply "automated" or "human" :)
>
> **Buyer:** hi my username is Buyer123
>
> **Buyer:** automated
>
> **You:** Your automated delivery is being processed, please provide your username.
>
> **Buyer:** Buyer123
>
> **You:** Type yes to confirm the user "Buyer123", if wrong, type the correct user now
>
> **Buyer:** yes
>
> **You:** Automated delivery is ready! Join "YourTradeBot" in this private server and trade him, he hands everything over: https://www.roblox.com/share?code=...
>
> *(your bot trades them, then calls `/complete`)*
>
> **Order marked as delivered**
>
> **You:** Thank you!

Every line is a template you can change (see [Chat texts](#chat-texts)).
The name is taken as the buyer types it and confirmed with "yes"; checking
it, finding the player and trading is your own bot's job.

### It never spams

- Every message goes out **once per order and step**. The record is saved before sending, so a crash or restart never sends it twice.
- "Sorry, I didn't get that" is sent at most twice, then one hint to say "stop". After that, silence.
- Only the buyer's newest message can get a "didn't get that", and messages written before the welcome never do.
- "stop" at any step hands the order to you. Words like cancel, refund, scam or "not received" are reported to you (`order.flagged`), never argued with.
- When an order is delivered, cancelled or gone on Bhopp, the bot stops talking in it.
- `/problem` sends the one line "Something went wrong with the automation, YourName has been notified", once per order.

## Setup

You need Python 3.10+ and a machine that stays on (a small VPS is fine).

```bash
git clone https://github.com/reverseddata/bunny-automation
cd bunny-automation
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then fill it in
```

**1. Your Bhopp token.** Log in on bhopp.com, press F12, open Console and run
`localStorage.getItem("bhopp.token")`. Copy it (without the quotes) into
`BHOPP_TOKEN`, or into a file named by `BHOPP_TOKEN_FILE`. It lasts **7 days**;
`GET /health` shows when it expires. With a token file you can swap it
without a restart.

**2. An API key** for your own bot, any long random string:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**3. Your names and link:** `BUNNY_SELLER_NAME`, `BUNNY_TRADER` (the Roblox
account that trades) and `BUNNY_SERVER_LINK` (your private server).

**4. Optional:** `BUNNY_WEBHOOK_URL` (+ `BUNNY_WEBHOOK_SECRET`) so your bot
hears about orders, and `BUNNY_MIN_ORDER_CENTS=500` for a $5 minimum.

**5. Run it:**

```bash
set -a; . ./.env; set +a
uvicorn server.app:app --host 127.0.0.1 --port 8787
```

Keep it on `127.0.0.1` (or behind a reverse proxy with HTTPS). Then:

```bash
curl -H "X-API-Key: $BUNNY_API_KEY" http://127.0.0.1:8787/health
```

## Using it

Full list with request and response shapes: [HTTP_API.md](HTTP_API.md).

| You want to | Call |
|---|---|
| Find a product | `GET /products?search=harvester` |
| Fetch the lowest price | `GET /products/{slug}/lowest` |
| Add an item | `POST /offers {"slug": "harvester", "price_cents": 480, "quantity": 2}` |
| Change stock / push a price | `PATCH /offers/{id} {"quantity": 5, "price_cents": 450}` |
| Remove an item | `DELETE /offers/{id}` |
| See orders | `GET /orders` (each with its chat `flow` state) |
| Complete an order | `POST /orders/{id}/complete {"archive": true}` |
| Send a message | `POST /orders/{id}/message {"text": "..."}` |
| Push the autodelivery message | `POST /orders/{id}/autodelivery {"server_link": "..."}` |
| Tell the buyer it failed | `POST /orders/{id}/problem` |
| Minimum order / link at runtime | `PUT /settings {"min_order_cents": 500}` |
| Pause the automatic chat | `PUT /flow {"on": false}` |

### The usual loop with your trade bot

1. Buyer confirms their name: you get the webhook `order.confirmed` with `order` and `roblox_name`.
2. Your bot (e.g. your Lua script in the private server) waits for that player and trades them.
3. Done: `POST /orders/{id}/complete`. Failed: `POST /orders/{id}/problem` and handle it yourself.

[examples/lua_autodelivery.lua](examples/lua_autodelivery.lua) shows the Lua side.

## Minimum order

Bhopp has no setting for a minimum order, so bunny checks it: an order
under `BUNNY_MIN_ORDER_CENTS` gets the `below_minimum` message once
("Sorry, the minimum order is $5.00. YourName has been notified.") and you get
`order.below_minimum`. The chat flow then leaves that order alone. bunny does
not cancel orders: there is no seller cancel on Bhopp that is known to work,
so cancelling stays with you (or the buyer) on bhopp.com.

## Chat texts

Put any of these keys in a JSON file and point `BUNNY_TEXTS_FILE` at it:

`welcome`, `choice_again`, `ask_username`, `username_again`, `confirm`,
`auto_confirm`, `switched`, `autodelivery`, `human`, `stop_hint`, `issue`,
`below_minimum`, `thanks`

Placeholders: `{seller}`, `{trader}`, `{link}`, `{name}` (buyer's Roblox name),
`{minimum}`. Defaults are in [bunny/chat.py](bunny/chat.py).

```json
{"autodelivery": "Your order has been scheduled. Please add and join \"{trader}\" and trade him in game :D {link}"}
```

## As a Python library

```python
from bunny import Client, Chat, OrderFlow

client = Client(token="...")
print(await client.lowest_price("harvester"))
await client.create_offer(product_id, amount=480, quantity=2)

flow = OrderFlow(client, Chat(seller="YourName", trader="YourTradeBot", link="https://..."),
                 on_event=lambda event, data: print(event, data), min_order_cents=500)
while True:
    await flow.tick()
    await asyncio.sleep(15)
```

## Safety

- **Never share or commit your token or `.env`.** The token is your Bhopp login. `.gitignore` already leaves them out.
- The API key is checked on every call. Don't open the port to the internet without HTTPS.
- Webhooks are signed: check `X-Bunny-Signature` = HMAC-SHA256 of the body with your secret.
- State lives in `bunny.sqlite`, so a restart picks up where it left off.

## Tests

```bash
python tests/test_bunny.py
python tests/test_server.py
```

Both run against a fake Bhopp; nothing touches the real site.
