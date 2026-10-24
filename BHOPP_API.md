# Bhopp's seller API (unofficial reference)

What bhopp.com itself calls, read off the site's own JavaScript (October
2026). Nothing here is documented or promised by Bhopp; it can change at
any time. Base URL `https://api.sublimelistings.com:5000`. Seller calls need
`Authorization: Bearer <token>`.

**Money:** offer prices (`amount`) are in **cents**. Order totals
(`totals.gross/fee/net`) are in **1/100 of a cent** (`1700` = $0.17).

## Catalogue and market (no login)

| Call | Returns |
|---|---|
| `GET /products?page=N&search=` | `{products: [{id, slug, name, rarity, category}], page, pages, total}`, 20 per page |
| `GET /products/{slug}` | `{product}` |
| `GET /products/{slug}/offers` | `{offers: [{id, amount, currency, quantity, seller}]}` |
| `GET /market/presence?sellers=id1,id2` | `{sellers: {id: {status: "online" \| "away", ago}}}` |

## Your offers

| Call | Body / returns |
|---|---|
| `GET /seller/offers` | `{offers: [...]}` |
| `POST /seller/offers` | `{productId, amount, quantity}` -> `{offer}` |
| `PATCH /seller/offers/{id}` | any of `{amount, quantity}` -> `{offer}` |
| `DELETE /seller/offers/{id}` | removes it from the Offers page |
| `PUT /seller/vacation` | `{enabled}` |
| `PUT /seller/vacation/auto` | `{enabled}` |

## Orders

| Call | Notes |
|---|---|
| `GET /seller/orders?state=open&page=N` | open = pending + delivered; `state=past` for finished; no state = everything |
| `PATCH /orders/{id}/delivered` | mark delivered |
| `PATCH /orders/{id}/cancel` | found in the site's code with the buyer's actions; not tested as a seller, bunny does not use it |
| `POST /orders/{id}/proof` | delivery proof upload (multipart; field names unconfirmed) |

Order: `{id, state, buyer: {id, username}, seller: {id, username}, items:
[{offerId, productId, name, quantity, unitAmount}], totals, conversationId,
createdAt}`. States seen: `pending`, `delivered`, `completed`, `cancelled`.
Bhopp's fee is 5% (`feeBps` 500).

## Chat

| Call | Notes |
|---|---|
| `GET /conversations/{id}/messages` | newest first: `{messages: [{id, sender, kind: "text" \| "system", event, body, createdAt}]}` |
| `POST /conversations/{id}/messages` | `{body}` -> `{message}` |
| `POST /conversations/{id}/read` | mark read |
| `PATCH /conversations/{id}/archive` | `{archived: true \| false}` |

## Staying online

socket.io v4 on the base URL, namespace `/user`, `auth: {token}`; emit
`presence:ping` every 60 s.

## The token

`localStorage["bhopp.token"]` on bhopp.com: a JWT with `user` (your seller
id) and `exp`. Valid 7 days, not refreshed while you use the site.
