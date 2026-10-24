"""End to end against a fake Bhopp. No network, no token, no real account.

    python tests/test_bunny.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from fake_bhopp import SELLER, FakeBhopp, fake_token  # noqa: E402

from bunny import Chat, Client, OrderFlow, order_cents, pricing  # noqa: E402

LOOP = asyncio.new_event_loop()
run = LOOP.run_until_complete


def check(cond, what):
    assert cond, what
    print("ok -", what)


async def known(name):
    return {"newbuyer": "NewBuyer", "mind": "Mind"}.get((name or "").lower())


def setup(**kw):
    fake = FakeBhopp()
    client = Client(token=fake_token(), http=fake.http())
    chat = Chat(seller="Sam", trader="OurBot", link="https://www.roblox.com/share?code=abc")
    events = []
    flow = OrderFlow(client, chat, db_path=os.path.join(tempfile.mkdtemp(), "t.sqlite"), lookup=known,
                     on_event=lambda e, p: events.append((e, p)), **kw)
    return fake, client, chat, flow, events


# ------------------------------------------------------------- client: items / prices
fake, client, chat, flow, events = setup()
check(run(client.lowest_price("harvester")) == 500, "lowest price skips out-of-stock offers")
o = run(client.create_offer("p1", 480, 3))
check(o["amount"] == 480 and o["quantity"] == 3, "add item (create offer)")
check(run(client.lowest_price("harvester", exclude_seller=SELLER)) == 500
      and run(client.lowest_price("harvester")) == 480, "lowest price with / without your own offer")
run(client.update_offer(o["id"], quantity=5, amount=470))
check(fake.offers[o["id"]]["quantity"] == 5 and fake.offers[o["id"]]["amount"] == 470, "change stock and push price")
run(client.delete_offer(o["id"]))
check(o["id"] not in fake.offers, "remove an offer")
check(pricing.undercut(500, 2) == 490 and pricing.floor_cents(400) == 422, "pricing helpers")

# ------------------------------------------------------------- the order flow
fake, client, chat, flow, events = setup()
order = fake.buy(cents=800)
check(order_cents(order) == 800, "order totals read in cents (Bhopp sends 1/100 cent)")
run(flow.tick())
check(fake.ours(order) == [chat.text("welcome")] and events[0][0] == "order.new", "new order: welcome + event")
fake.say(order, "automated")
run(flow.tick())
fake.say(order, "my user is newbuyer")
run(flow.tick())
check(fake.ours(order)[-1] == chat.text("confirm", name="NewBuyer"), "real Roblox spelling confirmed")
fake.say(order, "yes")
run(flow.tick())
check(fake.ours(order)[-1] == chat.text("autodelivery") and "share?code=abc" in fake.ours(order)[-1]
      and events[-1][0] == "order.confirmed", "yes: autodelivery message with the private server link + event")
check(events[-1][1]["roblox_name"] == "NewBuyer", "...the event names the confirmed account")
run(flow.complete(order["id"], archive=True))
check(fake.orders[order["id"]]["state"] == "delivered" and fake.ours(order)[-1] == chat.text("thanks")
      and order["conversationId"] in fake.archived, "complete: marked delivered, thanks, chat archived")
n = len(fake.ours(order))
for _ in range(3):
    run(flow.tick())
check(len(fake.ours(order)) == n, "nothing more is ever sent on a finished order")
check(all(fake.ours(order).count(b) == 1 for b in fake.ours(order)), "no message sent twice")

# unclear replies stop; nothing for messages from before the welcome
fake, client, chat, flow, events = setup()
order = fake.buy()
fake.say(order, "hi")
fake.say(order, "hello??")
run(flow.tick())
check(fake.ours(order) == [chat.text("welcome")], "messages from before the welcome get no 'didn't get that'")
for txt in ("what", "huh", "eh", "?"):
    fake.say(order, txt)
    run(flow.tick())
check(fake.ours(order).count(chat.text("choice_again")) == 2 and fake.ours(order)[-1] == chat.text("stop_hint"),
      "two 'didn't get that' at most, then the stop hint, then quiet")

# human, then automated after all
fake, client, chat, flow, events = setup()
order = fake.buy()
run(flow.tick())
fake.say(order, "human")
run(flow.tick())
fake.say(order, "yes")
run(flow.tick())
check(flow.get(order["id"])["state"] == "human", "a 'yes' nobody asked for changes nothing")
fake.say(order, "automated")
run(flow.tick())
fake.say(order, "yes")
run(flow.tick())
check(flow.get(order["id"])["state"] == "username" and fake.ours(order)[-1] == chat.text("switched"),
      "human -> 'automated' asks once to confirm -> yes switches back")

# stop and danger words
fake, client, chat, flow, events = setup()
order = fake.buy()
run(flow.tick())
fake.say(order, "i want a refund")
run(flow.tick())
check(fake.ours(order)[-1] == chat.text("stop_hint") and events[-1][0] == "order.flagged", "refund -> stop hint + flagged")
fake.say(order, "stop")
run(flow.tick())
check(flow.get(order["id"])["state"] == "human" and events[-1][0] == "order.human", "stop -> human")

# minimum order
fake, client, chat, flow, events = setup(min_order_cents=500)
small = fake.buy(cents=200)
run(flow.tick())
check(fake.orders[small["id"]]["state"] == "pending" and "$5.00" in fake.ours(small)[0]
      and flow.get(small["id"])["state"] == "below_minimum" and events[-1][0] == "order.below_minimum",
      "below the minimum: told once, event, never cancelled by us")
run(flow.tick())
check(len(fake.ours(small)) == 1, "...and never messaged again")

# autodelivery push with a fresh link, and the one problem line
fake, client, chat, flow, events = setup()
order = fake.buy()
run(flow.tick())
run(flow.autodelivery(order["id"], "https://www.roblox.com/share?code=NEW"))
check("code=NEW" in fake.ours(order)[-1], "push the autodelivery message with a fresh server link")
check(run(flow.problem(order["id"])) and not run(flow.problem(order["id"])), "the issue line goes out once")

# order left Bhopp's open list
fake, client, chat, flow, events = setup()
order = fake.buy()
run(flow.tick())
fake.orders[order["id"]]["state"] = "cancelled"
run(flow.tick())
check(flow.get(order["id"])["state"] == "closed" and events[-1][0] == "order.closed", "cancelled on Bhopp: stops")
print("ALL PASSED")
