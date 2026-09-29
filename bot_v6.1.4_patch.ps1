$ErrorActionPreference = "Stop"

$bot = Join-Path (Get-Location) "bot.py"
if (!(Test-Path $bot)) {
    throw "Nu am găsit bot.py în folderul curent."
}

$src = Get-Content $bot -Raw

# Version
$src = $src -replace 'BOT_VERSION\s*=\s*"[^"]+"', 'BOT_VERSION = "6.1.4"'

# Inject Roblox resolver after api_get_player().
$marker = 'def get_nested(data: Any, *keys: str):'
if (!$src.Contains($marker)) {
    throw "Nu am găsit punctul de inserare pentru Roblox resolver."
}

$resolver = @'
# ============================================================
# Roblox DisplayName resolver
# ============================================================

ROBLOX_USERS_API = "https://users.roblox.com/v1/users"
ROBLOX_NAME_CACHE: dict[str, str] = {}

async def resolve_roblox_names(user_ids: list[Any]) -> dict[str, str]:
    """Resolve Roblox UserIDs to DisplayNames using Roblox Users API.
    Results are cached for the lifetime of the bot process.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for value in user_ids:
        if value is None:
            continue
        uid = str(value).strip()
        if not uid or not uid.isdigit() or uid in seen:
            continue
        seen.add(uid)
        normalized.append(uid)

    if not normalized:
        return {}

    result = {uid: ROBLOX_NAME_CACHE[uid] for uid in normalized if uid in ROBLOX_NAME_CACHE}
    missing = [uid for uid in normalized if uid not in ROBLOX_NAME_CACHE]

    if not missing:
        return result

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Roblox accepts a batch of user IDs. Keep batches conservative.
            for start in range(0, len(missing), 50):
                batch = missing[start:start + 50]
                async with session.post(
                    ROBLOX_USERS_API,
                    json={
                        "userIds": [int(uid) for uid in batch],
                        "excludeBannedUsers": False,
                    },
                    headers={"User-Agent": f"PS99-Discord-Bot/{BOT_VERSION}"},
                ) as response:
                    if response.status != 200:
                        print(f"Roblox Users API returned HTTP {response.status}")
                        continue
                    payload = await response.json()
                    for user in payload.get("data", []) or []:
                        if not isinstance(user, dict):
                            continue
                        uid = str(user.get("id") or "").strip()
                        display_name = str(user.get("displayName") or "").strip()
                        username = str(user.get("name") or "").strip()
                        resolved = display_name or username
                        if uid and resolved:
                            ROBLOX_NAME_CACHE[uid] = resolved
                            result[uid] = resolved
    except Exception as exc:
        print(f"Roblox DisplayName lookup failed: {exc}")

    return result

def league_user_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("UserID", "UserId", "userId", "userID", "Id", "ID", "id"):
            candidate = value.get(key)
            if candidate is not None and str(candidate).strip():
                return str(candidate).strip()
    elif value is not None and str(value).strip():
        return str(value).strip()
    return None

'@

$src = $src.Replace($marker, $resolver + "`r`n" + $marker)

# Replace the existing /league command through the next /clanbattle decorator.
$startMarker = '@bot.tree.command(name="league"'
$endMarker = '@bot.tree.command(name="clanbattle"'
$start = $src.IndexOf($startMarker)
$end = $src.IndexOf($endMarker)

if ($start -lt 0 -or $end -lt 0 -or $end -le $start) {
    throw "Nu am găsit blocul /league pentru înlocuire."
}

$newLeague = @'
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

    # Acknowledge immediately so slow API calls never hit Discord's 3-second limit.
    await interaction.response.send_message(f"🔎 Searching PS99 leagues for `{query}`...")

    try:
        matches = await api_search_leagues(query)
        if not matches:
            await interaction.edit_original_response(
                content=f"❌ No league found for `{query}`. Search is prefix-based, so try the beginning of the league name."
            )
            return

        exact = next(
            (x for x in matches if str(x.get("Name", "")).casefold() == query.casefold()),
            None,
        )
        selected = exact or matches[0]
        league_name = str(selected.get("Name") or query)

        detail = await api_get_league(league_name)
        if not detail:
            detail = selected

        points = int(detail.get("Points") or selected.get("Points") or 0)
        league_id = detail.get("ID") or selected.get("ID")

        rank = await api_get_league_rank(
            points,
            league_id=league_id,
            league_name=league_name,
        )

        owner = detail.get("Owner") or {}
        members = detail.get("Members") or []
        contributions = detail.get("PointContributions") or []

        # BIG Games sometimes returns only UserID. Resolve those IDs through
        # Roblox's Users API so Discord shows actual DisplayNames.
        ids_to_resolve: list[Any] = []
        owner_id = league_user_id(owner)
        if owner_id:
            ids_to_resolve.append(owner_id)

        for member in members:
            uid = league_user_id(member)
            if uid:
                ids_to_resolve.append(uid)

        for entry in contributions:
            uid = league_user_id(entry)
            if uid:
                ids_to_resolve.append(uid)

        names = await resolve_roblox_names(ids_to_resolve)

        def display_for(value: Any) -> str:
            uid = league_user_id(value)
            if uid and uid in names:
                return names[uid]
            if isinstance(value, dict):
                existing = value.get("DisplayName") or value.get("Username") or value.get("Name")
                if existing:
                    return str(existing)
                if uid:
                    return uid
            return str(value) if value is not None else "Unknown"

        owner_name = display_for(owner) if owner else "Unknown"

        member_names: list[str] = []
        seen_member_ids: set[str] = set()
        for member in members:
            uid = league_user_id(member)
            if uid and uid in seen_member_ids:
                continue
            if uid:
                seen_member_ids.add(uid)
            member_names.append(display_for(member))

        # Some API responses include the owner separately from Members.
        if owner_id and owner_id not in seen_member_ids:
            member_names.insert(0, owner_name)
            seen_member_ids.add(owner_id)
        elif not owner_id and owner:
            member_names.insert(0, owner_name)

        # Keep the real member count when the API provides a member list.
        member_count = len(members)
        if owner and owner_id and owner_id not in {
            league_user_id(member) for member in members if league_user_id(member)
        }:
            member_count += 1
        if not members and owner:
            member_count = 1

        capacity = int(
            detail.get("MemberCapacity")
            or selected.get("MemberCapacity")
            or 4
        )

        embed = discord.Embed(
            title=f"🏆 League — {league_name}",
            description="Full PS99 League leaderboard lookup",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🏅 Global Rank",
            value=f"**#{rank:,}**" if rank else "Not available",
            inline=True,
        )
        embed.add_field(name="⭐ Points", value=f"**{fmt(points)}**", inline=True)
        embed.add_field(
            name="👥 Members",
            value=f"**{member_count}/{capacity}**",
            inline=True,
        )
        embed.add_field(
            name="📈 Level",
            value=f"**{detail.get('Level', selected.get('Level', '—'))}**",
            inline=True,
        )
        embed.add_field(name="👑 Owner", value=f"**{owner_name}**", inline=True)

        if member_names:
            embed.add_field(
                name="👥 Player Names",
                value=trim(
                    "\n".join(f"• **{name}**" for name in member_names),
                    1024,
                ),
                inline=False,
            )

        embed.add_field(
            name="🆔 League ID",
            value=f"`{league_id or '—'}`",
            inline=False,
        )

        if contributions:
            lines = []
            for entry in contributions[:5]:
                contribution_name = display_for(entry)
                lines.append(
                    f"**{contribution_name}** — {fmt(int(entry.get('Points') or 0))}"
                )
            embed.add_field(
                name="📊 Top Contributions",
                value=trim("\n".join(lines), 1024),
                inline=False,
            )

        if rank:
            embed.add_field(
                name="🌐 Leaderboard",
                value=(
                    "This rank is calculated from the complete "
                    "`full`-league Points leaderboard — leagues above top "
                    "100/1k are supported."
                ),
                inline=False,
            )

        embed.set_footer(text=f"PS99 Discord Bot v{BOT_VERSION} • BIG Games API")
        await interaction.edit_original_response(content=None, embed=embed)

    except Exception as exc:
        await interaction.edit_original_response(
            content=f"❌ League lookup failed.\n`{trim(exc, 800)}`"
        )

'@

$src = $src.Substring(0, $start) + $newLeague + "`r`n" + $src.Substring($end)

Set-Content -Path $bot -Value $src -Encoding UTF8

Write-Host ""
Write-Host "v6.1.4 aplicat în bot.py" -ForegroundColor Green
Write-Host "Rulează acum:"
Write-Host "  python -m py_compile bot.py"
Write-Host "  python -m pytest -q"
Write-Host ""
Write-Host "Apoi:"
Write-Host "  python bot.py"
Write-Host ""
