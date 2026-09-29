# 🎮 PS99 Discord Bot

A feature-rich Discord bot for **Pet Simulator 99 (PS99)** powered by the public **BIG Games API**.

> **Current version: 6.1.0**

The bot combines Roblox account linking, PS99 inventory lookup, Space Forge tracking, Clan Battle monitoring, and full PS99 League lookup in one Discord application.

## ✨ Features

### 🔗 Roblox account linking

- `/linkroblox username` — link a Roblox username to a Discord user.
- `/unlinkroblox` — remove the saved Roblox link.
- Linked accounts can be used by the inventory and event features.

### 🎒 PS99 inventory

- `/ps99inventory` — read a linked player's PS99 inventory.
- `/inventory` — legacy/compatibility inventory command.
- Displays item names and quantities.
- Uses item icon information supplied by the PS99 API.
- Converts supported Roblox asset IDs through the BIG Games image proxy for Discord embeds.

### ⛏️ Space Forge / event tools

- `/event` — event information and documented targets.
- `/goals` — check useful event materials against the linked inventory.
- `/combine` — Combine-o-Matic progress planner.
- `recipes.json` is intentionally safe/empty by default. The bot does not invent unverified crafting ratios.

### 🏆 Clan Battle monitor

`/clanbattle` supports:

- `setup` — configure a Discord channel and clan name.
- `status` — show monitor configuration.
- `now` — manually check the active battle.
- `stop` — disable automatic monitoring.

When configured, the monitor:

1. Detects an active Clan Battle.
2. Posts a start notification.
3. Checks the battle approximately every 10 minutes.
4. Posts updates when the tracked clan's rank or points change.
5. Displays available rank, points, medal, members, gap information, and leaderboard data.
6. Posts a final update when the battle ends.

The official battle leaderboard endpoint exposes the top 100 clans. For clans outside that API sample, the bot can fall back to the public BIG Games clan database page to retrieve the active battle rank and points when available.

### 🏅 League lookup — NEW in 6.1.0

`/league name:<league>` searches PS99 leagues and displays league details including:

- Global leaderboard rank
- Points
- Member count / capacity when available
- League level
- Owner
- League ID
- Top member contributions when available

The League API is paginated, so the bot calculates the league's position from the full Points leaderboard instead of limiting results to the first 100 or 1,000 leagues.

## 📋 Commands

| Command | Purpose |
|---|---|
| `/linkroblox` | Link a Roblox username |
| `/unlinkroblox` | Remove the Roblox link |
| `/ps99inventory` | View PS99 inventory |
| `/inventory` | Legacy inventory command |
| `/combine` | Space Forge / Combine-o-Matic planner |
| `/event` | Event information |
| `/goals` | Event material goals |
| `/league` | Search a League and show its global rank/details |
| `/clanbattle` | Configure and monitor Clan Battles |
| `/status` | Bot/API/monitor status |

## 🧱 Repository structure

```text
PS99-Discord-Bot/
├── .github/
│   └── workflows/
│       └── python-check.yml
├── .env.example
├── .gitignore
├── VERSION
├── README.md
├── bot.py
├── recipes.json
└── requirements.txt
```

### Files that are intentionally NOT in Git

The bot creates local runtime files such as:

```text
.env
ps99_bot.sqlite3
__pycache__/
*.log
```

These are covered by `.gitignore` and should remain local. **Never commit your Discord bot token or your local SQLite database.**

GitHub provides `.gitignore` support specifically for files that should not be committed. If a file is already tracked, remove it from Git's index before the ignore rule can protect future commits.

## 🚀 Installation on Windows

### 1. Clone the repository

```powershell
git clone https://github.com/YOUR-USERNAME/PS99-Discord-Bot.git
cd PS99-Discord-Bot
```

### 2. Create a virtual environment (recommended)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 4. Create `.env`

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Then edit `.env`:

```env
DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
```

**Do not put the token in `bot.py`, `README.md`, Git commits, screenshots, or public Discord messages.**

### 5. Start the bot

```powershell
python bot.py
```

## ⚙️ First Discord setup

1. Invite the bot to your Discord server with the required bot/application permissions.
2. Start `bot.py`.
3. Run `/status`.
4. Run `/linkroblox <your Roblox username>`.
5. Test `/ps99inventory`.
6. Test `/league <league name>`.
7. Configure Clan Battle monitoring with:

```text
/clanbattle action:setup channel:#clan-battle clan:YOUR_CLAN_NAME
```

8. Use `/clanbattle action:now` to perform an immediate battle check.

## 🔐 Security

### Never commit secrets

The following should stay local:

- `.env`
- Discord bot token
- API credentials, if added in the future
- SQLite database
- local logs
- personal exports/backups

If a Discord bot token is ever exposed, **reset it immediately in the Discord Developer Portal** and replace the local `.env` value.

### If a secret was already committed

Adding it to `.gitignore` does not remove it from Git history. If a secret was already committed, removing it from `.gitignore` is not enough because the old commit can still contain it.

For an exposed token, rotate/revoke the token first, then clean the Git history if necessary.

## 🧪 Development

Compile-check the bot locally:

```powershell
python -m py_compile bot.py
```

The repository also includes a GitHub Actions workflow that performs a Python compile check on pushes and pull requests.

## 📦 Dependencies

- Python 3.10+
- `discord.py`
- `aiohttp`
- `python-dotenv`

Install with:

```powershell
python -m pip install -r requirements.txt
```

## 🌐 Data sources

This project uses public BIG Games / PS99 endpoints for PS99-related data. See the BIG Games public API documentation for the current endpoint definitions and limitations.

- BIG Games PS99 public API documentation: https://github.com/BIG-Games-LLC/ps99-public-api-docs
- BIG Games database: https://db.biggames.io/

## 🤝 Contributing

Pull requests and issue reports are welcome.

When contributing:

1. Do not commit `.env` or local databases.
2. Keep secrets out of source code and documentation.
3. Run `python -m py_compile bot.py` before opening a pull request.
4. Keep API-dependent features defensive: public endpoints can change or be temporarily unavailable.

This README is intended to provide the project overview, setup instructions, security notes, and contribution guidance.

## 📄 License

No license has been selected for this repository yet. Add a license file if you decide how you want others to use, modify, and redistribute the project.
