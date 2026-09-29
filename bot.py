import os
import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# ============================================================
# PS99 Discord Bot — FINAL
# Build: 4.0.0
# ============================================================

BOT_VERSION = "6.1.0"
API_BASE = "https://ps99.biggamesapi.io/v1"
LEGACY_API_BASE = "https://ps99.biggamesapi.io/api"
DB_FILE = Path(__file__).with_name("ps99_bot.sqlite3")

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

# The live Combine-o-Matic ratios are deliberately NOT guessed here.
# Add verified recipes later when the current in-game machine confirms them.
# Verified numeric recipes are intentionally kept separate from the code.
# The public BIG Games API exposes player inventory/game data, but does not
# document the live Combine-o-Matic recipe table. We therefore never guess
# ratios. The planner can consume a local recipes.json when verified values
# are supplied from the live machine.
RECIPES_FILE = Path(__file__).with_name("recipes.json")

def load_recipes() -> dict[str, dict[str, int]]:
    if not RECIPES_FILE.exists():
        return {}
    try:
        import json
        raw = json.loads(RECIPES_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as exc:
        print(f"Could not load recipes.json: {exc}")
        return {}

RECIPES = load_recipes()

# Current Space Forge event targets (verified against BIG Games Update 95).
EVENT_NAME = "Space Forge"
EVENT_GEMS = [
    "Moonstone Gem",
    "Star Ruby Gem",
    "Helium-3 Gem",
    "Nebulite Gem",
    "Dark Matter Gem",
    "Starlight Quartz Gem",
    "Sunstone Gem",
    "Eclipse Onyx Gem",
]
EVENT_HUGES = [
    ("Huge Alien Cat", "Combine-o-Matic — first craft"),
    ("Huge Zeebo Alien", "Combine-o-Matic — combine Alien Cats"),
    ("Huge Dark Energy Unicorn", "Combine-o-Matic — rarest craft"),
    ("Huge Moon Mining Husky", "Mining Chests / ores"),
    ("Huge Bright Energy Cobra", "Mining League placement rewards"),
]
EVENT_TITANICS = [
    ("Titanic Bright Energy Bat", "Top of the Combine-o-Matic"),
    ("Titanic Thermal Jackal", "Break ores and Mining Chests"),
    ("Titanic Dark Energy Eagle", "Mining League placement"),
]
EVENT_GARGANTUAN = ("Gargantuan Moonrock Golem", "Combine-o-Matic; fewer than 100 can be crafted")
EVENT_USEFUL_KEYWORDS = (
    "gem", "ore egg", "combine egg", "space mine gift", "space coins",
    "mining chest", "pickaxe", "tnt", "rover charge", "mining boost", "enchant"
)

SPACE_MINE_NAMES = {
    "Moonstone Gem",
    "Star Ruby Gem",
    "Helium-3 Gem",
    "Nebulite Gem",
    "Dark Matter Gem",
    "Starlight Quartz Gem",
    "Sunstone Gem",
    "Eclipse Onyx Gem",
}

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
commands_synced_once = False


# ============================================================
# Database
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                discord_id INTEGER PRIMARY KEY,
                roblox_username TEXT NOT NULL,
                roblox_user_id TEXT,
                linked_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clan_settings (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                clan_name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                battle_id TEXT,
                last_rank INTEGER,
                last_points INTEGER,
                last_post_at TEXT
            )
            """
        )
        conn.commit()


def get_link(discord_id: int):
    with db_connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE discord_id = ?",
            (discord_id,),
        ).fetchone()


def save_link(discord_id: int, username: str, roblox_user_id: str | None):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO users (discord_id, roblox_username, roblox_user_id, linked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                roblox_username = excluded.roblox_username,
                roblox_user_id = excluded.roblox_user_id,
                linked_at = excluded.linked_at
            """,
            (
                discord_id,
                username,
                roblox_user_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()


def remove_link(discord_id: int):
    with db_connect() as conn:
        conn.execute("DELETE FROM users WHERE discord_id = ?", (discord_id,))
        conn.commit()


# ============================================================
# Clan Battle configuration
# ============================================================

def get_clan_setting(guild_id: int):
    with db_connect() as conn:
        return conn.execute(
            "SELECT * FROM clan_settings WHERE guild_id = ?", (guild_id,)
        ).fetchone()


def save_clan_setting(guild_id: int, channel_id: int, clan_name: str):
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO clan_settings (guild_id, channel_id, clan_name, enabled)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id=excluded.channel_id, clan_name=excluded.clan_name, enabled=1
            """,
            (guild_id, channel_id, clan_name),
        )
        conn.commit()


def disable_clan_setting(guild_id: int):
    with db_connect() as conn:
        conn.execute("UPDATE clan_settings SET enabled=0 WHERE guild_id=?", (guild_id,))
        conn.commit()


def update_clan_runtime(guild_id: int, battle_id: str | None, rank: int | None, points: int | None):
    with db_connect() as conn:
        conn.execute(
            "UPDATE clan_settings SET battle_id=?, last_rank=?, last_points=?, last_post_at=? WHERE guild_id=?",
            (battle_id, rank, points, datetime.now(timezone.utc).isoformat(), guild_id),
        )
        conn.commit()


def clear_clan_runtime(guild_id: int):
    with db_connect() as conn:
        conn.execute(
            "UPDATE clan_settings SET battle_id=NULL, last_rank=NULL, last_points=NULL, last_post_at=? WHERE guild_id=?",
            (datetime.now(timezone.utc).isoformat(), guild_id),
        )
        conn.commit()


def list_enabled_clan_settings():
    with db_connect() as conn:
        return conn.execute("SELECT * FROM clan_settings WHERE enabled=1").fetchall()


# ============================================================
# BIG Games API
# ============================================================

async def api_get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url, params=params, headers={"User-Agent": f"PS99-Discord-Bot/{BOT_VERSION}"}
        ) as response:
            text = await response.text()
            if response.status != 200:
                raise RuntimeError(f"BIG Games API returned HTTP {response.status}: {text[:500]}")
            try:
                data = await response.json()
            except Exception as exc:
                raise RuntimeError("BIG Games API returned invalid JSON.") from exc
            if not isinstance(data, dict):
                raise RuntimeError("BIG Games API returned an unexpected response.")
            return data


async def api_get_active_battle() -> dict[str, Any] | None:
    payload = await api_get_json(f"{LEGACY_API_BASE}/activeClanBattle")
    data = payload.get("data")
    return data if isinstance(data, dict) else None


async def api_get_battle(battle_id: str) -> dict[str, Any]:
    safe_id = str(battle_id).strip()
    payload = await api_get_json(f"{API_BASE}/clans/battles/{safe_id}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Battle detail was not available.")
    return data


async def api_get_clan(clan_name: str) -> dict[str, Any] | None:
    from urllib.parse import quote
    payload = await api_get_json(f"{LEGACY_API_BASE}/clan/{quote(clan_name, safe='')}")
    data = payload.get("data")
    return data if isinstance(data, dict) else None


async def api_get_league(league_name: str) -> dict[str, Any] | None:
    from urllib.parse import quote
    payload = await api_get_json(f"{API_BASE}/leagues/{quote(league_name.strip(), safe='')}")
    data = payload.get("data")
    return data if isinstance(data, dict) else None


async def api_search_leagues(search: str, *, page_size: int = 100) -> list[dict[str, Any]]:
    payload = await api_get_json(
        f"{API_BASE}/leagues",
        params={"page": 1, "pageSize": min(100, page_size), "sort": "Points", "sortOrder": "desc", "search": search.strip()[:64]},
    )
    data = payload.get("data") or {}
    leagues = data.get("leagues")
    return leagues if isinstance(leagues, list) else []


async def api_get_league_rank(points: int, league_id: str | None = None, league_name: str | None = None, total: int | None = None) -> int | None:
    """Find the league's 1-based position in the complete Points leaderboard.

    The official API exposes every league through pagination. We binary-search
    the page containing the target point band, then inspect nearby pages. This
    avoids walking tens of thousands of leagues for normal lookups. Ties use the
    API's own leaderboard order.
    """
    page_size = 100
    if total is None:
        probe = await api_get_json(
            f"{API_BASE}/leagues",
            params={"page": 1, "pageSize": page_size, "sort": "Points", "sortOrder": "desc"},
        )
        pdata = probe.get("data") or {}
        total = int(pdata.get("total") or 0)
    if total <= 0:
        return None

    max_page = min(10000, (total + page_size - 1) // page_size)
    lo, hi = 1, max_page
    candidate_page = None
    while lo <= hi:
        mid = (lo + hi) // 2
        payload = await api_get_json(
            f"{API_BASE}/leagues",
            params={"page": mid, "pageSize": page_size, "sort": "Points", "sortOrder": "desc"},
        )
        data = payload.get("data") or {}
        rows = data.get("leagues") or []
        if not rows:
            hi = mid - 1
            continue
        first_points = int(rows[0].get("Points") or 0)
        last_points = int(rows[-1].get("Points") or 0)
        if first_points >= points >= last_points:
            candidate_page = mid
            break
        if last_points < points:
            hi = mid - 1
        else:
            lo = mid + 1

    if candidate_page is None:
        return None

    # Inspect the candidate and adjacent pages to account for point ties and
    # API page boundaries. Usually this is only 2–3 requests.
    for page in range(max(1, candidate_page - 1), min(max_page, candidate_page + 1) + 1):
        payload = await api_get_json(
            f"{API_BASE}/leagues",
            params={"page": page, "pageSize": page_size, "sort": "Points", "sortOrder": "desc"},
        )
        rows = (payload.get("data") or {}).get("leagues") or []
        for index, row in enumerate(rows):
            if league_id and str(row.get("ID")) == str(league_id):
                return (page - 1) * page_size + index + 1
            if league_name and str(row.get("Name", "")).casefold() == league_name.casefold():
                return (page - 1) * page_size + index + 1

    # If a very large tie spans more than the inspected pages, report the
    # points-based band position rather than pretending an exact tie order.
    return None


async def db_get_active_clan(clan_name: str) -> dict[str, Any] | None:
    """Read the clan's public BIG Games DB page. This is the fallback for clans
    outside the battle API's top-100 sample. The DB page exposes Active Battle
    Place and Active Battle Points for the currently active battle.
    """
    from urllib.parse import quote
    url = f"https://db.biggames.io/clans/{quote(clan_name.strip(), safe='')}"
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": f"PS99-Discord-Bot/{BOT_VERSION}"}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            if response.status != 200:
                return None
            html = await response.text()

    import html as html_lib
    from re import search
    text_content = html_lib.unescape(re.sub(r"<[^>]+>", " ", html))
    text_content = re.sub(r"\s+", " ", text_content).strip()

    place_match = search(r"Active Battle Place\s*#?([0-9][0-9,]*)", text_content, re.I)
    points_match = search(r"Active Battle Points\s*([0-9][0-9,]*)", text_content, re.I)
    live_match = search(r"Live\s*[·•]\s*([^ ]+)", text_content, re.I)
    if not place_match and not points_match:
        return None

    return {
        "name": clan_name,
        "rank": int(place_match.group(1).replace(",", "")) if place_match else None,
        "points": int(points_match.group(1).replace(",", "")) if points_match else 0,
        "battle": live_match.group(1) if live_match else None,
        "db_url": url,
        "source": "BIG Games DB",
    }


async def api_get_player(username: str) -> dict[str, Any]:
    url = f"{API_BASE}/players/{username}"
    timeout = aiohttp.ClientTimeout(total=25)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url,
            params={"include": "profile,inventory"},
            headers={"User-Agent": f"PS99-Discord-Bot/{BOT_VERSION}"},
        ) as response:
            text = await response.text()
            if response.status != 200:
                raise RuntimeError(
                    f"BIG Games API returned HTTP {response.status}: {text[:500]}"
                )
            try:
                return await response.json()
            except Exception as exc:
                raise RuntimeError("BIG Games API returned invalid JSON.") from exc


def get_nested(data: Any, *keys: str):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def normalize_items(raw_items: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    aggregate: dict[tuple[str, str], dict[str, Any]] = {}

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            continue

        try:
            count = int(raw.get("count", 0))
        except (TypeError, ValueError):
            continue
        if count < 0:
            continue

        display_name = str(raw.get("displayName") or item_id).strip()
        stack_key = str(raw.get("stackKey") or "")
        key = (item_id, stack_key)

        if key not in aggregate:
            aggregate[key] = {
                "id": item_id,
                "displayName": display_name,
                "count": count,
                "class": raw.get("class"),
                "collection": raw.get("collection"),
                "category": raw.get("category"),
                "icon": raw.get("icon"),
                "stackKey": stack_key,
            }
        else:
            aggregate[key]["count"] += count

    result = list(aggregate.values())
    result.sort(key=lambda item: item["displayName"].lower())
    return result


def extract_inventory_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Canonical current BIG Games API shape:
    # data -> views -> inventory -> data -> items
    direct_paths = [
        ("data", "views", "inventory", "data", "items"),
        ("data", "views", "inventory", "items"),
        ("data", "inventory", "data", "items"),
        ("data", "inventory", "items"),
        ("inventory", "data", "items"),
        ("inventory", "items"),
    ]

    for path in direct_paths:
        value = get_nested(payload, *path)
        if isinstance(value, list):
            items = normalize_items(value)
            if items:
                return items

    # Conservative compatibility fallback.
    candidates: list[dict[str, Any]] = []

    def walk(value: Any):
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            good = [
                x for x in value
                if isinstance(x, dict)
                and isinstance(x.get("id"), str)
                and isinstance(x.get("count"), (int, float))
            ]
            if len(good) >= 3:
                candidates.extend(good)
            else:
                for child in value:
                    walk(child)

    walk(payload)
    return normalize_items(candidates)


def extract_player_info(payload: dict[str, Any], fallback_username: str):
    # Current API puts identity under data.account.
    account = get_nested(payload, "data", "account")
    if isinstance(account, dict):
        username = str(account.get("username") or fallback_username).strip()
        user_id = account.get("robloxUserId")
        return username or fallback_username, str(user_id) if user_id else None

    # Compatibility fallbacks for older response shapes.
    for player in (
        get_nested(payload, "data", "player"),
        get_nested(payload, "data"),
        payload.get("player"),
    ):
        if not isinstance(player, dict):
            continue
        username = str(
            player.get("username") or player.get("userName") or fallback_username
        ).strip()
        for key in ("robloxUserId", "userId", "userID"):
            value = player.get(key)
            if value is not None and str(value).strip():
                return username or fallback_username, str(value).strip()

    return fallback_username, None


async def fetch_inventory(username: str) -> dict[str, Any]:
    payload = await api_get_player(username)

    # Handle explicit unavailable/private inventory view.
    inventory_view = get_nested(payload, "data", "views", "inventory")
    if isinstance(inventory_view, dict) and inventory_view.get("available") is False:
        reason = inventory_view.get("reason", "not_public")
        raise RuntimeError(f"Inventory is unavailable through the API: {reason}")

    items = extract_inventory_items(payload)
    if not items:
        raise RuntimeError(
            "The BIG Games API responded, but the inventory contains no readable items."
        )

    username_out, roblox_user_id = extract_player_info(payload, username)
    return {
        "username": username_out,
        "roblox_user_id": roblox_user_id,
        "items": items,
    }


# ============================================================
# Images / Clan Battle helpers
# ============================================================

def asset_image_url(asset: Any) -> str | None:
    if not asset:
        return None
    raw = str(asset).strip()
    if raw.startswith("rbxassetid://"):
        raw = raw.split("rbxassetid://", 1)[1]
    if raw.isdigit():
        return f"{LEGACY_API_BASE}/image/{raw}"
    return None


def item_icon(items: list[dict[str, Any]], names: list[str]) -> str | None:
    wanted = {x.lower() for x in names}
    for item in items:
        if str(item.get("displayName", "")).lower() in wanted or str(item.get("id", "")).lower() in wanted:
            return asset_image_url(item.get("icon"))
    return None


def battle_clan_info(battle: dict[str, Any], clan_name: str) -> dict[str, Any] | None:
    wanted = clan_name.casefold().strip()
    for clan in battle.get("topClans", []) or []:
        if str(clan.get("name", "")).casefold().strip() == wanted:
            return clan
    return None


def fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "unknown"
    minutes = max(0, int(seconds)) // 60
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def make_clan_battle_embed(
    clan_name: str, battle: dict[str, Any], clan: dict[str, Any] | None,
    *, start_notice: bool = False, final_notice: bool = False
) -> discord.Embed:
    meta = battle.get("meta") or {}
    title = meta.get("title") or meta.get("id") or "Clan Battle"
    if final_notice:
        embed_title = f"🏁 Clan Battle ended — {clan_name}"
    elif start_notice:
        embed_title = f"🚀 Clan Battle started — {clan_name}"
    else:
        embed_title = f"🏆 Clan Battle — {clan_name}"

    embed = discord.Embed(title=embed_title, description=f"**{title}**", color=discord.Color.gold())
    if clan:
        rank = clan.get("rank")
        points = int(clan.get("points") or 0)
        medal = clan.get("medal") or "—"
        members = clan.get("members")
        capacity = clan.get("memberCapacity")
        source = clan.get("source")
        source_line = f"\nSource: **{source}**" if source else ""
        embed.add_field(name="📊 Clan", value=f"**Rank:** #{rank}\n**Points:** {fmt(points)}\n**Medal:** {medal}{source_line}", inline=True)
        if members is not None and capacity:
            embed.add_field(name="👥 Members", value=f"{members}/{capacity}", inline=True)
        top = battle.get("topClans", []) or []
        if rank and rank > 1 and rank <= len(top):
            above = top[rank - 2]
            gap = max(0, int(above.get("points") or 0) - points)
            embed.add_field(name="⬆️ Next rank", value=f"**{above.get('name', 'Unknown')}**\nGap: **{fmt(gap)}** points", inline=False)
        elif rank == 1 and len(top) > 1:
            below = top[1]
            gap = max(0, points - int(below.get("points") or 0))
            embed.add_field(name="🥇 #1", value=f"Ahead of **{below.get('name', 'Unknown')}** by **{fmt(gap)}** points", inline=False)
        elif rank and rank > 100:
            embed.add_field(name="🔎 DB lookup", value="Rank and points were found from the BIG Games clan database because the battle API leaderboard is capped at the top 100.", inline=False)
    else:
        embed.add_field(name="📊 Clan", value="Clan rank/points could not be found in the battle API or BIG Games DB.", inline=False)

    top_lines = []
    for c in (battle.get("topClans", []) or [])[:10]:
        top_lines.append(f"**#{c.get('rank')}** {c.get('name')} — {fmt(int(c.get('points') or 0))}")
    embed.add_field(name="🏅 Top 10", value=trim("\n".join(top_lines) or "No leaderboard data.", 1024), inline=False)

    if final_notice:
        embed.add_field(name="ℹ️ Status", value="The active battle endpoint no longer reports this battle as active.", inline=False)
    else:
        embed.add_field(name="⏱️ Battle", value=f"Duration: **{fmt_duration(meta.get('durationSeconds'))}**", inline=False)

    embed.set_footer(text=f"PS99 Discord Bot v{BOT_VERSION} • BIG Games API")
    return embed


# ============================================================
# Presentation/helpers
# ============================================================

def fmt(number: int) -> str:
    return f"{number:,}"


def trim(text: str, limit: int = 1024) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def get_space_mine_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item for item in items
        if item["displayName"] in SPACE_MINE_NAMES or item["id"] in SPACE_MINE_NAMES
    ]


def get_event_useful_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    useful = []
    for item in items:
        name = item["displayName"].lower()
        if any(keyword in name for keyword in EVENT_USEFUL_KEYWORDS):
            useful.append(item)
    return sorted(useful, key=lambda x: (-x["count"], x["displayName"].lower()))


def find_item_count(items: list[dict[str, Any]], name: str) -> int:
    for item in items:
        if item["displayName"].lower() == name.lower():
            return int(item["count"])
    return 0


def make_event_embed(username: str, items: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🚀 {EVENT_NAME} — Event Tracker",
        description=(
            f"**{username}**\n"
            "Tracks event materials and the current Huge/Titanic targets.\n"
            "Crafting ratios are not guessed; the tracker uses the documented event chain."
        ),
        color=discord.Color.blurple(),
    )

    gem_lines = []
    for name in EVENT_GEMS:
        count = find_item_count(items, name)
        gem_lines.append(f"💎 {name} × **{fmt(count)}**")
    embed.add_field(name="🧱 Event Gems", value="\n".join(gem_lines), inline=False)

    huge_lines = [f"⭐ **{name}** — {source}" for name, source in EVENT_HUGES]
    embed.add_field(name="⭐ Huge targets", value=trim("\n".join(huge_lines), 1024), inline=False)

    titanic_lines = [f"👑 **{name}** — {source}" for name, source in EVENT_TITANICS]
    embed.add_field(name="👑 Titanic targets", value=trim("\n".join(titanic_lines), 1024), inline=False)

    embed.add_field(
        name="🌌 Final target",
        value=f"**{EVENT_GARGANTUAN[0]}** — {EVENT_GARGANTUAN[1]}",
        inline=False,
    )

    useful = get_event_useful_items(items)
    useful_lines = [f"• {x['displayName']} × {fmt(x['count'])}" for x in useful[:20]]
    embed.add_field(
        name="🎯 Useful event items found in inventory",
        value=trim("\n".join(useful_lines) if useful_lines else "No recognized Space Forge items found.", 1024),
        inline=False,
    )
    embed.set_footer(text=f"PS99 Inventory Bot v{BOT_VERSION} • Space Forge")
    return embed


def make_goals_embed(username: str, items: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title=f"🎯 {EVENT_NAME} — What matters in your inventory",
        description=(
            "This is a progress/utility view, not a guessed recipe calculator. "
            "It shows the event items you currently own and the documented targets."
        ),
        color=discord.Color.orange(),
    )

    # Dark Matter is explicitly mentioned by BIG Games as something that can be
    # combined for the ultimate pickaxe or saved for Huge crafting.
    dm = find_item_count(items, "Dark Matter Gem")
    eo = find_item_count(items, "Eclipse Onyx Gem")
    sq = find_item_count(items, "Starlight Quartz Gem")
    ss = find_item_count(items, "Sunstone Gem")

    embed.add_field(
        name="🔥 Priority materials",
        value=(
            f"**Eclipse Onyx Gem:** {fmt(eo)}\n"
            f"**Sunstone Gem:** {fmt(ss)}\n"
            f"**Starlight Quartz Gem:** {fmt(sq)}\n"
            f"**Dark Matter Gem:** {fmt(dm)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="🧭 Current documented path",
        value=(
            "Gems → Combine-o-Matic Huge chain → Titanic Bright Energy Bat → "
            "Gargantuan Moonrock Golem.\n"
            "After the available crafts sell out, the machine switches to Combine Eggs."
        ),
        inline=False,
    )
    embed.add_field(
        name="💡 Bot behavior",
        value=(
            "When exact live recipes are verified, this command can calculate crafts, "
            "leftovers and egg progress automatically. For now it deliberately does not invent ratios."
        ),
        inline=False,
    )
    embed.set_footer(text=f"PS99 Inventory Bot v{BOT_VERSION}")
    return embed


def make_inventory_embed(username: str, items: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title=f"📦 Inventory — {username}",
        description=(
            f"**{fmt(len(items))} inventory stacks** found.\n"
            "Source: BIG Games Public API"
        ),
        color=discord.Color.blurple(),
    )

    space_items = sorted(get_space_mine_items(items), key=lambda x: x["displayName"].lower())
    if space_items:
        lines = [
            f"💎 **{trim(item['displayName'], 55)}** × **{fmt(item['count'])}**"
            for item in space_items
        ]
        embed.add_field(
            name="🚀 Space Mine Gems",
            value=trim("\n".join(lines), 1024),
            inline=False,
        )
    else:
        embed.add_field(
            name="🚀 Space Mine Gems",
            value="No Space Mine gems were found.",
            inline=False,
        )

    preview = items[:30]
    preview_lines = [
        f"• {trim(item['displayName'], 55)} × {fmt(item['count'])}"
        for item in preview
    ]
    embed.add_field(
        name="📋 Inventory Preview",
        value=trim("\n".join(preview_lines), 1024),
        inline=False,
    )

    embed.set_footer(text=f"PS99 Inventory Bot v{BOT_VERSION}")
    return embed


def max_crafts(inventory: dict[str, int], recipe: dict[str, int]) -> int:
    if not recipe:
        return 0
    possible = [
        inventory.get(name, 0) // required
        for name, required in recipe.items()
        if required > 0
    ]
    return min(possible) if possible else 0


# ============================================================
# Discord command registration / cleanup
# ============================================================

async def sync_commands_cleanly():
    # Global sync overwrites the app's global command list.
    global_commands = await bot.tree.sync()
    print(f"Global slash commands synced: {len(global_commands)}")

    # Guild commands update instantly. Copying our global tree into each guild
    # and bulk-syncing removes stale guild-scoped commands belonging to THIS app.
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            guild_commands = await bot.tree.sync(guild=guild)
            print(f"Guild sync: {guild.name} ({guild.id}) -> {len(guild_commands)} commands")
        except Exception as exc:
            print(f"Guild sync failed for {guild.id}: {exc}")


@bot.event
async def setup_hook():
    init_db()


@bot.event
async def on_ready():
    global commands_synced_once
    app_id = getattr(bot.user, "id", "unknown")
    print(f"Connected as {bot.user} | Application ID: {app_id} | Build {BOT_VERSION}")
    if not commands_synced_once:
        await sync_commands_cleanly()
        commands_synced_once = True
    if not clan_battle_monitor.is_running():
        clan_battle_monitor.start()
    print("Use /ps99inventory, /event, /goals, /league or /clanbattle for the public event tracker.")


@bot.event
async def on_interaction(interaction: discord.Interaction):
    # Diagnostic logging only. It lets us see which application receives a command.
    if interaction.type == discord.InteractionType.application_command:
        name = getattr(interaction.command, "name", None)
        if name in {"inventory", "ps99inventory"}:
            print(
                "Inventory interaction received: "
                f"name={name!r} id={interaction.id} "
                f"guild={getattr(interaction.guild, 'id', None)} "
                f"expired={interaction.is_expired()}"
            )


# ============================================================
# Automatic Clan Battle monitor
# ============================================================

async def post_clan_update(setting, battle: dict[str, Any], *, start_notice=False, final_notice=False):
    channel = bot.get_channel(int(setting["channel_id"]))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(setting["channel_id"]))
        except Exception as exc:
            print(f"Clan Battle: could not fetch channel {setting['channel_id']}: {exc}")
            return
    clan = battle_clan_info(battle, setting["clan_name"])
    if clan is None and not final_notice:
        try:
            clan = await db_get_active_clan(setting["clan_name"])
        except Exception as exc:
            print(f"Clan Battle DB lookup failed for {setting['clan_name']}: {exc}")
    await channel.send(embed=make_clan_battle_embed(setting["clan_name"], battle, clan, start_notice=start_notice, final_notice=final_notice))
    rank = int(clan["rank"]) if clan and clan.get("rank") is not None else None
    points = int(clan.get("points") or 0) if clan else None
    update_clan_runtime(int(setting["guild_id"]), (battle.get("meta") or {}).get("id"), rank, points)


@tasks.loop(minutes=10)
async def clan_battle_monitor():
    try:
        active = await api_get_active_battle()
    except Exception as exc:
        print(f"Clan Battle monitor error: {exc}")
        return

    active_id = str(active.get("configName")) if active and active.get("configName") else None
    settings = list_enabled_clan_settings()
    for setting in settings:
        try:
            previous_id = setting["battle_id"]
            if active_id:
                battle = await api_get_battle(active_id)
                clan = battle_clan_info(battle, setting["clan_name"])
                if clan is None:
                    try:
                        clan = await db_get_active_clan(setting["clan_name"])
                    except Exception as exc:
                        print(f"Clan Battle DB lookup failed for {setting['clan_name']}: {exc}")
                rank = int(clan["rank"]) if clan and clan.get("rank") is not None else None
                points = int(clan.get("points") or 0) if clan else None
                changed_battle = previous_id != active_id
                changed_score = previous_id == active_id and (
                    setting["last_rank"] != rank or setting["last_points"] != points
                )
                if changed_battle:
                    await post_clan_update(setting, battle, start_notice=True)
                elif changed_score:
                    await post_clan_update(setting, battle)
            elif previous_id:
                try:
                    final_battle = await api_get_battle(str(previous_id))
                    await post_clan_update(setting, final_battle, final_notice=True)
                except Exception as exc:
                    print(f"Clan Battle final lookup failed for {setting['clan_name']}: {exc}")
                clear_clan_runtime(int(setting["guild_id"]))
        except Exception as exc:
            print(f"Clan Battle guild {setting['guild_id']} error: {exc}")


@clan_battle_monitor.before_loop
async def before_clan_battle_monitor():
    await bot.wait_until_ready()


# ============================================================
# Commands
# ============================================================

@bot.tree.command(name="status", description="Check bot version and API status.")
async def status(interaction: discord.Interaction):
    embed = discord.Embed(title="✅ Bot Status", color=discord.Color.green())
    embed.add_field(name="Version", value=BOT_VERSION, inline=True)
    embed.add_field(name="Bot", value=str(bot.user), inline=True)
    embed.add_field(name="Application ID", value=str(bot.user.id), inline=False)
    embed.add_field(name="BIG Games API", value="Configured", inline=True)
    embed.add_field(name="Clan Battle Monitor", value="Running" if clan_battle_monitor.is_running() else "Starting", inline=True)
    embed.set_footer(text="PS99 Inventory Bot")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def handle_link(interaction: discord.Interaction, username: str):
    username = username.strip()
    if not username:
        await interaction.response.send_message("❌ Enter a valid Roblox username.", ephemeral=True)
        return

    # Immediate acknowledgement: no network request occurs before Discord gets a response.
    await interaction.response.send_message("🔎 Verifying Roblox account and reading inventory...", ephemeral=True)

    try:
        data = await fetch_inventory(username)
    except Exception as exc:
        await interaction.edit_original_response(
            content=f"❌ I could not verify that Roblox account.\n`{trim(exc, 800)}`"
        )
        return

    save_link(interaction.user.id, data["username"], data["roblox_user_id"])
    await interaction.edit_original_response(
        content=(
            "✅ **Roblox account linked!**\n"
            f"**Roblox:** `{data['username']}`\n"
            f"**User ID:** `{data['roblox_user_id'] or 'Unknown'}`\n"
            f"**Inventory stacks:** `{fmt(len(data['items']))}`"
        )
    )


@bot.tree.command(name="linkroblox", description="Link your Discord account to Roblox.")
@app_commands.describe(username="Your Roblox username")
async def linkroblox(interaction: discord.Interaction, username: str):
    await handle_link(interaction, username)


async def handle_inventory(interaction: discord.Interaction):
    # Immediate acknowledgement avoids doing ANY work before the 3-second window.
    await interaction.response.send_message("📦 Reading your Roblox inventory...")

    row = get_link(interaction.user.id)
    if not row:
        await interaction.edit_original_response(
            content="❌ You have not linked a Roblox account yet. Use `/linkroblox username`."
        )
        return

    try:
        data = await fetch_inventory(row["roblox_username"])
    except Exception as exc:
        await interaction.edit_original_response(
            content=f"❌ I could not read your inventory.\n`{trim(exc, 800)}`"
        )
        return

    save_link(
        interaction.user.id,
        data["username"],
        data["roblox_user_id"] or row["roblox_user_id"],
    )

    await interaction.edit_original_response(
        content=None,
        embed=make_inventory_embed(data["username"], data["items"]),
    )


@bot.tree.command(name="inventory", description="Show your linked Roblox inventory.")
async def inventory(interaction: discord.Interaction):
    await handle_inventory(interaction)


@bot.tree.command(name="ps99inventory", description="Show your linked PS99 inventory (v4).")
async def ps99inventory(interaction: discord.Interaction):
    await handle_inventory(interaction)


@bot.tree.command(name="combine", description="Calculate your Space Forge crafting progress from your inventory.")
async def combine(interaction: discord.Interaction):
    await interaction.response.send_message("🧪 Calculating your Space Forge crafting progress...")

    row = get_link(interaction.user.id)
    if not row:
        await interaction.edit_original_response(
            content="❌ You have not linked a Roblox account yet. Use `/linkroblox username`."
        )
        return

    try:
        data = await fetch_inventory(row["roblox_username"])
    except Exception as exc:
        await interaction.edit_original_response(
            content=f"❌ I could not read your inventory.\n`{trim(exc, 800)}`"
        )
        return

    counts = {x["displayName"]: int(x["count"]) for x in data["items"]}
    gem_lines = []
    for gem in EVENT_GEMS:
        qty = counts.get(gem, 0)
        if qty:
            gem_lines.append(f"💎 {gem}: **{fmt(qty)}**")

    # Track actual event pets/items already owned. This is useful even when
    # live numeric recipes are not published by the API.
    owned_targets = []
    for pet, source in EVENT_HUGES + EVENT_TITANICS + [EVENT_GARGANTUAN]:
        qty = counts.get(pet, 0)
        if qty:
            owned_targets.append(f"{pet}: **{fmt(qty)}**")

    # If verified recipes.json exists, calculate craft counts recursively.
    planner_lines = []
    working = dict(counts)
    if RECIPES:
        for output, recipe in RECIPES.items():
            craftable = max_crafts(working, recipe)
            planner_lines.append(f"{output}: **{craftable}** craftable")
            if craftable:
                for ingredient, required in recipe.items():
                    working[ingredient] = working.get(ingredient, 0) - required * craftable
                working[output] = working.get(output, 0) + craftable
    else:
        planner_lines = [
            "⚠️ **Live recipe ratios are not hardcoded.**",
            "The bot will not invent a craft count and risk giving you a wrong answer.",
            "Add verified ratios to `recipes.json` and `/combine` will calculate the full chain automatically.",
        ]

    # Practical next-action detector. It prioritizes the Combine-o-Matic chain
    # and then points to other documented event routes.
    if counts.get("Huge Alien Cat", 0) == 0:
        next_action = "Collect the inputs for **Huge Alien Cat**, the first Combine-o-Matic Huge."
    elif counts.get("Huge Zeebo Alien", 0) == 0:
        next_action = "Your next Combine-o-Matic target is **Huge Zeebo Alien**."
    elif counts.get("Huge Dark Energy Unicorn", 0) == 0:
        next_action = "Your next Combine-o-Matic target is **Huge Dark Energy Unicorn**."
    elif counts.get("Titanic Bright Energy Bat", 0) == 0:
        next_action = "Your next Combine-o-Matic target is **Titanic Bright Energy Bat**."
    elif counts.get("Gargantuan Moonrock Golem", 0) == 0:
        next_action = "You have reached the Gargantuan stage: **Moonrock Golem** is the final Combine-o-Matic target."
    else:
        next_action = "You already own the listed Combine-o-Matic targets; the bot will track remaining event resources."

    embed = discord.Embed(
        title=f"🧪 Space Forge — Crafting Planner",
        description=f"Player: **{data['username']}**\n🎯 **Next:** {next_action}",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="💎 Event Gems",
        value=trim("\n".join(gem_lines) if gem_lines else "No Space Forge gems found in the inventory.", 1024),
        inline=False,
    )
    embed.add_field(
        name="⭐ Combine-o-Matic Targets",
        value=trim(
            "\n".join(
                f"{pet} — {source}" for pet, source in EVENT_HUGES[:3] + EVENT_TITANICS[:1]
            ) + "\nGargantuan Moonrock Golem — final target",
            1024,
        ),
        inline=False,
    )
    if owned_targets:
        embed.add_field(name="📦 Already Owned", value=trim("\n".join(owned_targets), 1024), inline=False)
    embed.add_field(name="📊 Craft Calculation", value=trim("\n".join(planner_lines), 1024), inline=False)
    embed.add_field(
        name="🥚 After Crafts",
        value="BIG Games says that once the available Combine-o-Matic crafts sell out, the machine switches to Combine Eggs.",
        inline=False,
    )
    icon = item_icon(data["items"], ["Eclipse Onyx Gem", "Sunstone Gem", "Starlight Quartz Gem"])
    if icon:
        embed.set_thumbnail(url=icon)
    embed.set_footer(text=f"PS99 Space Forge Planner v{BOT_VERSION}")
    await interaction.edit_original_response(content=None, embed=embed)


@bot.tree.command(name="event", description="Show the current Space Forge Huge/Titanic targets and useful items.")
async def event(interaction: discord.Interaction):
    await interaction.response.send_message("🚀 Reading your Space Forge progress...")
    row = get_link(interaction.user.id)
    if not row:
        await interaction.edit_original_response(
            content="❌ You have not linked a Roblox account yet. Use `/linkroblox username`."
        )
        return
    try:
        data = await fetch_inventory(row["roblox_username"])
    except Exception as exc:
        await interaction.edit_original_response(content=f"❌ I could not read your inventory.\n`{trim(exc, 800)}`")
        return
    await interaction.edit_original_response(content=None, embed=make_event_embed(data["username"], data["items"]))


@bot.tree.command(name="goals", description="Show your Space Forge crafting materials and goals.")
async def goals(interaction: discord.Interaction):
    await interaction.response.send_message("🎯 Calculating your Space Forge goals...")
    row = get_link(interaction.user.id)
    if not row:
        await interaction.edit_original_response(
            content="❌ You have not linked a Roblox account yet. Use `/linkroblox username`."
        )
        return
    try:
        data = await fetch_inventory(row["roblox_username"])
    except Exception as exc:
        await interaction.edit_original_response(content=f"❌ I could not read your inventory.\n`{trim(exc, 800)}`")
        return
    await interaction.edit_original_response(content=None, embed=make_goals_embed(data["username"], data["items"]))


@bot.tree.command(name="league", description="Search a PS99 League and show its full leaderboard rank.")
@app_commands.describe(name="League name to search (prefixes are supported)")
async def league(interaction: discord.Interaction, name: str):
    query = name.strip()
    if not query:
        await interaction.response.send_message("❌ Please enter a league name.", ephemeral=True)
        return
    if len(query) > 64:
        await interaction.response.send_message("❌ League name/search must be 64 characters or fewer.", ephemeral=True)
        return

    await interaction.response.send_message(f"🔎 Searching PS99 leagues for `{query}`...", ephemeral=True)
    try:
        matches = await api_search_leagues(query)
        if not matches:
            await interaction.edit_original_response(content=f"❌ No league found for `{query}`. Search is prefix-based, so try the beginning of the league name.")
            return

        # Prefer an exact name; otherwise show the first matching league.
        exact = next((x for x in matches if str(x.get("Name", "")).casefold() == query.casefold()), None)
        selected = exact or matches[0]
        league_name = str(selected.get("Name") or query)
        detail = await api_get_league(league_name)
        if not detail:
            detail = selected

        points = int(detail.get("Points") or selected.get("Points") or 0)
        league_id = detail.get("ID") or selected.get("ID")
        total = None
        # The search response's total is the number of matching names, not the
        # global leaderboard total, so rank lookup obtains the global total.
        rank = await api_get_league_rank(points, league_id=league_id, league_name=league_name)

        owner = detail.get("Owner") or {}
        owner_name = owner.get("DisplayName") if isinstance(owner, dict) else None
        members = detail.get("Members") or []
        member_count = len(members) + (1 if owner else 0)
        capacity = int(detail.get("MemberCapacity") or selected.get("MemberCapacity") or 4)

        embed = discord.Embed(
            title=f"🏆 League — {league_name}",
            description="Full PS99 League leaderboard lookup",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="🏅 Global Rank", value=f"**#{rank:,}**" if rank else "Not available", inline=True)
        embed.add_field(name="⭐ Points", value=f"**{fmt(points)}**", inline=True)
        embed.add_field(name="👥 Members", value=f"**{member_count}/{capacity}**", inline=True)
        embed.add_field(name="📈 Level", value=f"**{detail.get('Level', selected.get('Level', '—'))}**", inline=True)
        embed.add_field(name="👑 Owner", value=f"**{owner_name or 'Unknown'}**", inline=True)
        embed.add_field(name="🆔 League ID", value=f"`{league_id or '—'}`", inline=False)

        contributions = detail.get("PointContributions") or []
        if contributions:
            lines = []
            for entry in contributions[:5]:
                lines.append(f"**{entry.get('DisplayName', entry.get('UserID', 'Unknown'))}** — {fmt(int(entry.get('Points') or 0))}")
            embed.add_field(name="📊 Top Contributions", value=trim("\n".join(lines), 1024), inline=False)

        if rank:
            embed.add_field(name="🌐 Leaderboard", value=f"This rank is calculated from the complete `{total or 'full'}`-league Points leaderboard — leagues above top 100/1k are supported.", inline=False)
        embed.set_footer(text=f"PS99 Discord Bot v{BOT_VERSION} • BIG Games API")

        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as exc:
        await interaction.edit_original_response(content=f"❌ League lookup failed.\n`{trim(exc, 800)}`")


@bot.tree.command(name="clanbattle", description="Configure or view automatic Clan Battle reporting.")
@app_commands.describe(
    action="setup, status, stop or now",
    channel="Discord channel where automatic reports should be posted",
    clan="Your PS99 clan name exactly as shown in-game",
)
@app_commands.choices(action=[
    app_commands.Choice(name="setup", value="setup"),
    app_commands.Choice(name="status", value="status"),
    app_commands.Choice(name="stop", value="stop"),
    app_commands.Choice(name="now", value="now"),
])
async def clanbattle(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    channel: discord.TextChannel | None = None,
    clan: str | None = None,
):
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command must be used inside a Discord server.", ephemeral=True)
        return
    action_value = action.value
    if action_value == "setup":
        if channel is None or not clan or not clan.strip():
            await interaction.response.send_message("❌ For setup use: `/clanbattle action:setup channel:#channel clan:YourClan`", ephemeral=True)
            return
        save_clan_setting(interaction.guild.id, channel.id, clan.strip())
        await interaction.response.send_message(
            f"✅ Clan Battle monitor configured.\n**Clan:** `{clan.strip()}`\n**Channel:** {channel.mention}\n**Updates:** every 10 minutes while the battle is active, plus start/end notifications.",
            ephemeral=True,
        )
        return
    setting = get_clan_setting(interaction.guild.id)
    if action_value == "stop":
        if not setting:
            await interaction.response.send_message("ℹ️ No Clan Battle monitor is configured.", ephemeral=True)
            return
        disable_clan_setting(interaction.guild.id)
        await interaction.response.send_message("✅ Automatic Clan Battle reporting has been stopped.", ephemeral=True)
        return
    if not setting or not setting["enabled"]:
        await interaction.response.send_message("ℹ️ No Clan Battle monitor is configured. Use `/clanbattle action:setup` first.", ephemeral=True)
        return
    if action_value == "status":
        await interaction.response.send_message(
            f"🏆 **Clan Battle monitor**\nClan: `{setting['clan_name']}`\nChannel: <#{setting['channel_id']}>\nLast battle: `{setting['battle_id'] or 'none'}`\nLast rank: `{setting['last_rank'] or 'not available'}`\nLast points: `{fmt(int(setting['last_points'])) if setting['last_points'] is not None else 'not available'}`",
            ephemeral=True,
        )
        return
    await interaction.response.send_message("🔎 Reading current Clan Battle leaderboard...", ephemeral=True)
    try:
        active = await api_get_active_battle()
        if not active or not active.get("configName"):
            await interaction.edit_original_response(content="ℹ️ There is no active Clan Battle right now.")
            return
        battle = await api_get_battle(str(active["configName"]))
        clan_info = battle_clan_info(battle, setting["clan_name"])
        embed = make_clan_battle_embed(setting["clan_name"], battle, clan_info)
        await interaction.edit_original_response(content=None, embed=embed)
    except Exception as exc:
        await interaction.edit_original_response(content=f"❌ Clan Battle API error.\n`{trim(exc, 800)}`")


@bot.tree.command(name="unlinkroblox", description="Unlink your Roblox account.")
async def unlinkroblox(interaction: discord.Interaction):
    if not get_link(interaction.user.id):
        await interaction.response.send_message("ℹ️ No Roblox account is linked.", ephemeral=True)
        return
    remove_link(interaction.user.id)
    await interaction.response.send_message("✅ Your Roblox account has been unlinked.", ephemeral=True)


# ============================================================
# Start
# ============================================================

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing from .env")
    print("=" * 64)
    print(f"PS99 Discord Bot v{BOT_VERSION}")
    print("FINAL BUILD — inventory + Space Forge + automatic Clan Battle monitor + item images")
    print("=" * 64)
    bot.run(DISCORD_TOKEN)
