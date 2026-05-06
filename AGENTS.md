# Repository Guidelines

## Project Structure & Module Organization
- `bot.py`: main Discord bot application. Contains slash commands, scheduled reminder/warning tasks, LeetCode GraphQL integration, and error handling.
- `requirements.txt`: runtime Python dependencies.
- Runtime data is persisted to `data.json` (auto-created in repo root). Do not hardcode test data into `bot.py`.
- Keep new modules flat and purpose-based (for example: `leetcode_client.py`, `storage.py`, `commands_admin.py`) when splitting logic.

## Build, Test, and Development Commands
- `python3 -m venv .venv && source .venv/bin/activate`: create and activate local virtualenv.
- `pip install -r requirements.txt`: install Discord + HTTP + timezone dependencies.
- `DISCORD_TOKEN=<your_token> python3 bot.py`: run bot locally.
- `python3 -m py_compile bot.py`: quick syntax validation before commit.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and readable line lengths.
- Use `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants (`LEETCODE_API`, `WARNING_TIME`).
- Preserve type hints for public helpers (`-> dict`, `-> tuple[bool, list[str]]`).
- Keep command handlers focused; move reusable logic into helper functions.
- Prefer explicit timezone-aware datetime handling (`TIMEZONE = pytz.timezone("Asia/Ho_Chi_Minh")`).

## Testing Guidelines
- No automated test suite exists yet. Minimum check for every change:
- `python3 -m py_compile bot.py`
- Manual slash-command verification in a test Discord server (`/register`, `/check`, `/status`, `/leaderboard`).
- For new logic, add small unit-testable helpers first, then introduce `tests/` with `pytest` using names like `test_streak_update.py`.

## Commit & Pull Request Guidelines
- Current history is minimal (`First commit`), so adopt clear imperative commits now:
- Example: `feat: add cooldown for /check command`
- Example: `fix: prevent duplicate daily warnings`
- PRs should include:
- Summary of behavior changes
- Local verification steps and command output
- Screenshots of Discord embeds for UI/text changes
- Any config or migration notes (for example `data.json` shape changes)

## Security & Configuration Tips
- Never commit real tokens. Use `DISCORD_TOKEN` environment variable.
- Keep `YOUR_BOT_TOKEN_HERE` as placeholder only.
- If data structure changes, include backward-compatible defaults in `get_guild_data()`.
