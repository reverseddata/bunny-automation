"""The order chat: what the bot says and how it reads the buyer.

Every text is a template you can replace: `Chat(texts={...})`. Placeholders:
{seller} your name, {trader} your trading account, {link} your private
server link, {name} the buyer's Roblox name, {minimum} the minimum order.
"""
from __future__ import annotations

import re

DEFAULT_TEXTS = {
    "welcome": ('Hey, thanks for your purchase! Would you like automated delivery (5 min. average) '
                'or human delivery (up to 1 hour or more).\nReply "automated" or "human" :)'),
    "choice_again": 'Sorry, I didn\'t get that. Please reply with just "automated" or "human" :)',
    "ask_username": "Your automated delivery is being processed, please provide your username.",
    "username_again": ("I couldn't find that Roblox account. Please send your exact Roblox username "
                       "(not your display name) :)"),
    "confirm": 'Type yes to confirm the user "{name}", if wrong, type the correct user now',
    "auto_confirm": 'Do you want to switch to automated delivery? Reply "yes" to confirm :)',
    "switched": "Switched to automated delivery! What's your Roblox username? :)",
    # The autodelivery push: your bot is ready, here's where to go.
    "autodelivery": ('Automated delivery is ready! Join "{trader}" in this private server and trade him, '
                     'he hands everything over: {link}'),
    "human": "{seller} has been notified and will deliver your order as soon as possible :)",
    "stop_hint": 'If you would like human help or to cancel, say "stop"',
    "issue": "Something went wrong with the automation, {seller} has been notified",
    "below_minimum": "Sorry, the minimum order is {minimum}. {seller} has been notified.",
    "thanks": "Thank you!",
}

DANGER = re.compile(
    r"\b(cancel\w*|refund\w*|scam\w*|dispute\w*|chargeback\w*|report\w*|fraud\w*|"
    r"stole|steal\w*|not\s+receiv\w*|never\s+(?:got|receiv\w*)|did\s*n[o']?t\s+(?:get|receiv\w*)|"
    r"didnt\s+(?:get|receiv\w*)|wrong\s+item\w*|money\s+back|paypal)\b", re.I)
STOP = re.compile(r"^\W*stop\W*$", re.I)
YES = re.compile(r"^\W*(yes|yeah|yep|yup|y|correct|confirm|confirmed)\W*$", re.I)
AUTOMATED = re.compile(r"\b(auto|automated|automatic|automatically|bot)\b", re.I)
HUMAN = re.compile(r"\b(human|person|manual|manually|real\s+person)\b", re.I)
ROBLOX_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_]{1,18})[A-Za-z0-9]$")
_FILLER = {"my", "username", "user", "name", "is", "its", "it's", "roblox", "rbx", "here", "the", "ign",
           "thanks", "thank", "you", "ty", "pls", "please", "and", "hi", "hey", "hello", "user:", "it"}


def read_choice(text: str) -> str | None:
    """"automated" / "human" / None (both or neither -> ask again, never guess)."""
    a, h = bool(AUTOMATED.search(text or "")), bool(HUMAN.search(text or ""))
    if a and not h:
        return "automated"
    if h and not a:
        return "human"
    return None


def read_username(text: str) -> str | None:
    """The one Roblox-username-looking word in a message, or None if there are
    none or several (ask again instead of guessing)."""
    words = [w.strip("@\"'`.,!?:;()[]") for w in (text or "").split()]
    cands = [w for w in words if ROBLOX_NAME.match(w) and w.lower() not in _FILLER]
    return cands[0] if len(cands) == 1 else None


class Chat:
    def __init__(self, texts: dict | None = None, *, seller: str = "The seller", trader: str = "the trader",
                 link: str = ""):
        self.texts = {**DEFAULT_TEXTS, **(texts or {})}
        self.seller, self.trader, self.link = seller, trader, link

    def text(self, key: str, **kw) -> str:
        fields = {"seller": self.seller, "trader": self.trader, "link": self.link, "name": "", "minimum": "", **kw}
        return self.texts[key].format(**fields)

    def is_bot_text(self, body: str) -> bool:
        """True if `body` is one of our own messages (templates with a
        per-buyer part are recognised by their fixed start)."""
        b = (body or "").strip()
        for tpl in self.texts.values():
            fixed = tpl.replace("{seller}", self.seller).replace("{trader}", self.trader)
            head = re.split(r"\{(name|link|minimum)\}", fixed)[0]
            if b == fixed or (head and len(head) > 12 and b.startswith(head)):
                return True
        return False
