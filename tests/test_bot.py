import asyncio
from types import SimpleNamespace

import discord
import pytest

import bot


EXPECTED_COMMANDS = {
    "status",
    "linkroblox",
    "inventory",
    "ps99inventory",
    "combine",
    "event",
    "goals",
    "league",
    "clanbattle",
    "unlinkroblox",
}


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def send_message(self, content=None, **kwargs):
        self.messages.append({"content": content, **kwargs})


class FakeInteraction:
    def __init__(self, user_id=123, guild_id=456):
        self.user = SimpleNamespace(id=user_id)
        self.guild = SimpleNamespace(id=guild_id)
        self.response = FakeResponse()
        self.edits = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


def test_command_registration():
    commands = {command.name for command in bot.bot.tree.get_commands()}
    assert commands == EXPECTED_COMMANDS


def test_command_descriptions_are_present():
    commands = {command.name: command for command in bot.bot.tree.get_commands()}
    for name in EXPECTED_COMMANDS:
        assert commands[name].description


def test_bot_version():
    assert bot.BOT_VERSION == "6.1.3"


def test_max_crafts():
    assert bot.max_crafts(
        {"A": 10, "B": 7},
        {"A": 2, "B": 3},
    ) == 2
    assert bot.max_crafts({"A": 10}, {"A": 2, "B": 1}) == 0
    assert bot.max_crafts({}, {}) == 0


def test_normalize_and_extract_inventory():
    raw = [
        {"_id": "A", "name": "Moonstone Gem", "amount": 12},
        {"id": "B", "displayName": "Star Ruby Gem", "quantity": 4},
    ]
    normalized = bot.normalize_items(raw)
    assert normalized
    assert all("displayName" in item and "count" in item for item in normalized)

    payload = {"data": {"inventory": raw}}
    extracted = bot.extract_inventory_items(payload)
    assert isinstance(extracted, list)


def test_event_embeds_contain_expected_targets():
    items = [
        {"displayName": "Moonstone Gem", "count": 12},
        {"displayName": "Huge Alien Cat", "count": 1},
    ]
    event_embed = bot.make_event_embed("Tester", items)
    goals_embed = bot.make_goals_embed("Tester", items)
    inventory_embed = bot.make_inventory_embed("Tester", items)

    for embed in (event_embed, goals_embed, inventory_embed):
        assert isinstance(embed, discord.Embed)
        assert embed.title


def test_database_link_and_clan_helpers(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "DB_FILE", tmp_path / "test.sqlite3")
    bot.init_db()

    assert bot.get_link(1) is None
    bot.save_link(1, "PlayerOne", "999")
    row = bot.get_link(1)
    assert row["roblox_username"] == "PlayerOne"
    assert row["roblox_user_id"] == "999"

    bot.remove_link(1)
    assert bot.get_link(1) is None

    bot.save_clan_setting(10, 20, "TestClan")
    setting = bot.get_clan_setting(10)
    assert setting["channel_id"] == 20
    assert setting["clan_name"] == "TestClan"
    assert setting["enabled"] == 1

    bot.update_clan_runtime(10, "battle-1", 3, 12345)
    setting = bot.get_clan_setting(10)
    assert setting["battle_id"] == "battle-1"
    assert setting["last_rank"] == 3
    assert setting["last_points"] == 12345

    bot.disable_clan_setting(10)
    assert bot.get_clan_setting(10)["enabled"] == 0

    bot.clear_clan_runtime(10)
    setting = bot.get_clan_setting(10)
    assert setting["battle_id"] is None




def test_parse_db_active_clan_html():
    html = "<html><body>Active Battle Place #12 Active Battle Points 1,234,567 Live · GuildBattle_Test</body></html>"
    parsed = bot.parse_db_active_clan_html("TestClan", html, "https://db.biggames.io/clans/TestClan")
    assert parsed["rank"] == 12
    assert parsed["points"] == 1234567
    assert parsed["battle"] == "GuildBattle_Test"
    assert parsed["source"] == "BIG Games DB"

def test_league_api_helpers_are_mockable(monkeypatch):
    calls = []

    async def fake_api_get_json(url, params=None):
        calls.append((url, params))
        if "/leagues/Test%20League" in url:
            return {"data": {"Name": "Test League", "Points": 5000, "ID": "L1"}}
        return {"data": {"leagues": [{"Name": "Test League", "Points": 5000, "ID": "L1"}], "total": 1}}

    monkeypatch.setattr(bot, "api_get_json", fake_api_get_json)

    detail = run(bot.api_get_league("Test League"))
    matches = run(bot.api_search_leagues("Test"))
    assert detail["ID"] == "L1"
    assert matches[0]["Name"] == "Test League"
    assert calls


def test_league_rank_finds_target_on_paginated_leaderboard(monkeypatch):
    async def fake_api_get_json(url, params=None):
        page = int((params or {}).get("page", 1))
        page_size = int((params or {}).get("pageSize", 100))
        if page == 1:
            rows = [
                {"ID": f"ID-{i}", "Name": f"League {i}", "Points": 1000 - i}
                for i in range(100)
            ]
        elif page == 2:
            rows = [
                {"ID": "TARGET", "Name": "Target League", "Points": 700}
            ] + [
                {"ID": f"ID-{100 + i}", "Name": f"League {100 + i}", "Points": 699 - i}
                for i in range(99)
            ]
        else:
            rows = []
        assert page_size == 100
        return {"data": {"leagues": rows, "total": 200}}

    monkeypatch.setattr(bot, "api_get_json", fake_api_get_json)

    rank = run(bot.api_get_league_rank(
        700,
        league_id="TARGET",
        total=200,
    ))
    assert rank == 101


def test_league_command_rejects_empty_search():
    interaction = FakeInteraction()
    run(bot.league.callback(interaction, "   "))
    assert interaction.response.messages
    assert "Please enter a league name" in interaction.response.messages[0]["content"]


def test_league_command_rejects_too_long_search():
    interaction = FakeInteraction()
    run(bot.league.callback(interaction, "x" * 65))
    assert "64 characters" in interaction.response.messages[0]["content"]


def test_league_command_success_with_mock_api(monkeypatch):
    async def fake_search(query):
        return [{"Name": "Test League", "Points": 5000, "ID": "L1"}]

    async def fake_detail(name):
        return {
            "Name": "Test League",
            "Points": 5000,
            "ID": "L1",
            "Level": 10,
            "Owner": {"DisplayName": "Owner"},
            "Members": [{"UserID": "1"}, {"UserID": "2"}],
            "MemberCapacity": 4,
            "PointContributions": [
                {"DisplayName": "Owner", "Points": 3000},
                {"DisplayName": "Member", "Points": 2000},
            ],
        }

    async def fake_rank(points, league_id=None, league_name=None, total=None):
        return 42

    monkeypatch.setattr(bot, "api_search_leagues", fake_search)
    monkeypatch.setattr(bot, "api_get_league", fake_detail)
    monkeypatch.setattr(bot, "api_get_league_rank", fake_rank)

    interaction = FakeInteraction()
    run(bot.league.callback(interaction, "Test"))

    assert interaction.response.messages
    assert interaction.edits
    embed = interaction.edits[-1]["embed"]
    assert embed.title == "🏆 League — Test League"
    assert any(field.name == "🏅 Global Rank" and "#42" in field.value for field in embed.fields)
    assert any(field.name == "👥 Player Names" and "Owner" in field.value for field in embed.fields)


def test_link_inventory_commands_are_registered_separately():
    commands = {command.name for command in bot.bot.tree.get_commands()}
    assert "inventory" in commands
    assert "ps99inventory" in commands


def test_clanbattle_choices():
    command = next(c for c in bot.bot.tree.get_commands() if c.name == "clanbattle")
    choices = command.parameters[0].choices
    assert {choice.value for choice in choices} == {"setup", "status", "stop", "now"}


class FakeTree:
    def __init__(self):
        self.commands = [SimpleNamespace(name="status"), SimpleNamespace(name="league")]
        self.global_sync_calls = 0
        self.guild_sync_calls = []
        self.cleared = []
        self.copied = []

    def get_commands(self):
        return list(self.commands)

    def clear_commands(self, guild=None):
        self.cleared.append(guild)
        if guild is None:
            self.commands = []

    async def sync(self, guild=None):
        if guild is None:
            self.global_sync_calls += 1
            return []
        self.guild_sync_calls.append(guild)
        return list(self.commands)

    def add_command(self, command):
        self.commands.append(command)

    def copy_global_to(self, guild):
        self.copied.append(guild)


def test_sync_strategy_deletes_global_and_syncs_guilds():
    fake_tree = FakeTree()
    fake_guild = SimpleNamespace(id=99, name="Test Guild")

    run(bot.sync_commands_cleanly(tree=fake_tree, guilds=[fake_guild]))

    assert fake_tree.global_sync_calls == 1
    assert fake_tree.copied == [fake_guild]
    assert fake_tree.guild_sync_calls == [fake_guild]
    assert [c.name for c in fake_tree.commands] == ["status", "league"]


def test_league_response_is_public(monkeypatch):
    async def fake_search(query):
        return [{"Name": "Test League", "Points": 5000, "ID": "L1"}]

    async def fake_detail(name):
        return {
            "Name": "Test League", "Points": 5000, "ID": "L1",
            "Owner": {"DisplayName": "Owner"},
            "Members": [{"DisplayName": "Alice", "UserID": "1"}],
            "MemberCapacity": 4,
        }

    async def fake_rank(*args, **kwargs):
        return 5

    monkeypatch.setattr(bot, "api_search_leagues", fake_search)
    monkeypatch.setattr(bot, "api_get_league", fake_detail)
    monkeypatch.setattr(bot, "api_get_league_rank", fake_rank)

    interaction = FakeInteraction()
    run(bot.league.callback(interaction, "Test"))

    first = interaction.response.messages[0]
    assert first.get("ephemeral", False) is False
    embed = interaction.edits[-1]["embed"]
    names_field = next(field for field in embed.fields if field.name == "👥 Player Names")
    assert "Owner" in names_field.value
    assert "Alice" in names_field.value
    assert "UserID" not in names_field.value
