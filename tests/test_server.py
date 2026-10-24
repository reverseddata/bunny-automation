"""The HTTP API against a fake Bhopp. Run: python tests/test_server.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from fake_bhopp import FakeBhopp, fake_token  # noqa: E402

os.environ.update(BHOPP_TOKEN=fake_token(), BUNNY_API_KEY="k" * 32, BUNNY_FLOW="off", BUNNY_PRESENCE="off",
                  BUNNY_DB=os.path.join(tempfile.mkdtemp(), "s.sqlite"), BUNNY_TRADER="OurBot",
                  BUNNY_SERVER_LINK="https://www.roblox.com/share?code=abc")
from fastapi.testclient import TestClient  # noqa: E402

from server import app as srv  # noqa: E402

fake = FakeBhopp()
srv.client._http = fake.http()
H = {"X-API-Key": "k" * 32}


def check(cond, what):
    assert cond, what
    print("ok -", what)


with TestClient(srv.app) as cl:
    check(cl.get("/health").status_code == 401 and cl.get("/health", headers={"X-API-Key": "nope"}).status_code == 401,
          "every call needs the API key")
    h = cl.get("/health", headers=H).json()
    check(h["token_set"] and h["seller_id"] == "seller-1", "health: token read, seller id")
    check(cl.get("/products/harvester/lowest", headers=H).json()["lowest_cents"] == 500, "fetch lowest price")
    of = cl.post("/offers", headers=H, json={"slug": "harvester", "price_cents": 480, "quantity": 2}).json()
    check(of["amount"] == 480, "add item by slug")
    cl.patch(f"/offers/{of['id']}", headers=H, json={"quantity": 7})
    cl.patch(f"/offers/{of['id']}", headers=H, json={"price_cents": 460})
    check(fake.offers[of["id"]]["quantity"] == 7 and fake.offers[of["id"]]["amount"] == 460,
          "change stock, push price")
    check(cl.patch(f"/offers/{of['id']}", headers=H, json={}).status_code == 400, "an empty change is refused")

    order = fake.buy(cents=900)
    import asyncio
    asyncio.run(srv.flow.tick())          # the background loop is off in tests: one pass by hand
    lst = cl.get("/orders", headers=H).json()["orders"]
    check(lst[0]["flow"]["state"] == "choice", "orders come with the flow state")
    check(cl.post(f"/orders/{order['id']}/message", headers=H, json={"text": "hi from our bot"}).status_code == 200
          and fake.ours(order)[-1] == "hi from our bot", "send your own chat message")
    r = cl.post(f"/orders/{order['id']}/autodelivery", headers=H, json={"server_link": "https://x.test/srv"})
    check(r.json()["sent"] and "https://x.test/srv" in fake.ours(order)[-1], "push the autodelivery message")
    check(cl.post(f"/orders/{order['id']}/complete", headers=H, json={"archive": True}).status_code == 200
          and fake.orders[order["id"]]["state"] == "delivered", "complete an order")
    s = cl.put("/settings", headers=H, json={"min_order_cents": 500, "server_link": "https://y.test"}).json()
    check(s == {"min_order_cents": 500, "server_link": "https://y.test"}, "minimum order and link set at runtime")
print("ALL PASSED")
