"""Checks one AION 2 server on aion2.gaming.tools and keeps a single Discord
embed (posted via webhook) up to date with its status."""

import json
import os
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---- SETTINGS --------------------------------------------------------------
# Server and region come from GitHub secrets (AION_SERVER, AION_REGION) so they
# don't appear in the public repo. Region is one of:
# "Europe", "NA West", "NA East", "SA", "Asia"
SERVER_NAME = os.environ["AION_SERVER"]
REGION = os.environ["AION_REGION"]
NOTIFY_ON_CHANGE = False    # True = also post a short new message on each change
                            # (edits to the embed don't notify anyone)
NOTIFICATION_EXPIRY_HOURS = 6   # delete a notification after this many hours
                                # (0 = keep until the next change replaces it)
# ---------------------------------------------------------------------------

URL = "https://aion2.gaming.tools/server-status"
STATE_FILE = Path("state.json")
WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]
HEADERS = {"User-Agent": "aion2-status-discord-bot (personal use; polls every 5 min)"}

COLORS = {"online": 0x57F287, "maintenance": 0xFEE75C, "offline": 0xED4245}
ICONS = {"online": "🟢", "maintenance": "🟠", "offline": "🔴"}
NO_MENTIONS = {"parse": []}


def fetch_server():
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for row in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) < 6:
            continue
        # Columns: Favorites | Status | Server | Faction | Region | Population | 24h
        status, server, faction, region = cells[1:5]
        words = server.split()
        if not words:
            continue
        if words[0].lower() == SERVER_NAME.lower() and region.lower() == REGION.lower():
            return {"status": status, "faction": faction}
    sys.exit(f"Could not find '{SERVER_NAME}' in region '{REGION}' on the page.")


def build_embed(current, since):
    key = current["status"].lower()
    icon = ICONS.get(key, "⚪")
    return {
        "title": f"AION 2 · {SERVER_NAME}",
        "url": URL,
        "color": COLORS.get(key, 0x95A5A6),
        "description": (
            f"## {icon} {current['status']}\n"
            f"[Server Status Information provided by aion2.gaming.tools]({URL})"
        ),
        "fields": [
            {"name": "Region", "value": REGION, "inline": True},
            {"name": "Faction", "value": current["faction"], "inline": True},
            {"name": "Status since", "value": f"<t:{since}:R>", "inline": True},
        ],
        "footer": {"text": "Checked every 5 minutes · updates when the status changes"},
    }


def upsert_message(embed, message_id):
    """Edit the existing message, or post a new one. Returns the message ID."""
    payload = {"embeds": [embed], "allowed_mentions": NO_MENTIONS}
    if message_id:
        r = requests.patch(f"{WEBHOOK}/messages/{message_id}", json=payload, timeout=30)
        if r.status_code != 404:       # 404 = message was deleted, so post a new one
            r.raise_for_status()
            return message_id
    r = requests.post(WEBHOOK, params={"wait": "true"}, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def delete_message(message_id):
    if not message_id:
        return
    r = requests.delete(f"{WEBHOOK}/messages/{message_id}", timeout=30)
    if r.status_code not in (204, 404):   # 404 = already deleted
        r.raise_for_status()


def main():
    current = fetch_server()
    prev = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    state = dict(prev)
    now = int(time.time())

    # 1. Remove an expired notification
    if (prev.get("notify_id") and NOTIFICATION_EXPIRY_HOURS
            and now - prev.get("notify_at", now) > NOTIFICATION_EXPIRY_HOURS * 3600):
        delete_message(prev["notify_id"])
        state.pop("notify_id", None)
        state.pop("notify_at", None)

    # 2. Update the embed (and notify) if the status changed
    status_changed = prev.get("status") != current["status"]
    if status_changed or not prev.get("message_id"):
        since = now if status_changed else prev.get("since", now)
        state["message_id"] = upsert_message(build_embed(current, since), prev.get("message_id"))
        state["status"] = current["status"]
        state["since"] = since

        if NOTIFY_ON_CHANGE and status_changed and prev.get("status"):
            # Replace the previous notification rather than stacking them
            delete_message(state.pop("notify_id", None))
            state.pop("notify_at", None)
            icon = ICONS.get(current["status"].lower(), "⚪")
            r = requests.post(
                WEBHOOK,
                params={"wait": "true"},
                json={"content": f"{icon} **{SERVER_NAME}** is now **{current['status']}**",
                      "allowed_mentions": NO_MENTIONS},
                timeout=30,
            )
            r.raise_for_status()
            state["notify_id"] = r.json()["id"]
            state["notify_at"] = now

    # 3. Save state only if something changed
    if state != prev:
        STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
