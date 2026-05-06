import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import aiohttp
from datetime import datetime, time, timedelta
import pytz

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
DATA_FILE = "data.json"
TIMEZONE = pytz.timezone("Asia/Ho_Chi_Minh")

REMINDER_TIMES = [time(9, 0), time(14, 0), time(20, 0)]
WARNING_TIME = time(23, 0)

LEETCODE_API = "https://leetcode.com/graphql"
LEETCODE_RECENT_QUERY = """
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id
    title
    titleSlug
    timestamp
  }
}
"""
LEETCODE_CALENDAR_QUERY = """
query userProfileCalendar($username: String!, $year: Int) {
  matchedUser(username: $username) {
    userCalendar(year: $year) {
      activeYears
      streak
      submissionCalendar
    }
  }
}
"""

# ─── BOT SETUP ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ─── DATA HELPERS ─────────────────────────────────────────────────────────────
def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"guilds": {}}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_guild_data(guild_id: int) -> dict:
    data = load_data()
    gid = str(guild_id)
    if gid not in data["guilds"]:
        data["guilds"][gid] = {
            "channel_id": None,
            "min_problems": 1,
            "tracked_users": [],
            "leetcode_usernames": {},   # { discord_uid: lc_username }
            "submissions": {},          # { "YYYY-MM-DD": { uid: [titles] } }
            "warnings": {},             # { uid: count }
            "streaks": {},              # { uid: { current, best, last_date } }
        }
        save_data(data)
    return data["guilds"][gid]

def update_guild_data(guild_id: int, gd: dict):
    data = load_data()
    data["guilds"][str(guild_id)] = gd
    save_data(data)

def today_str() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")

def yesterday_str() -> str:
    return (datetime.now(TIMEZONE) - timedelta(days=1)).strftime("%Y-%m-%d")

# ─── LEETCODE API ─────────────────────────────────────────────────────────────
async def fetch_lc_submissions(username: str, limit: int = 20) -> list | None:
    payload = {
        "query": LEETCODE_RECENT_QUERY,
        "variables": {"username": username, "limit": limit}
    }
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
        "User-Agent": "Mozilla/5.0"
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(LEETCODE_API, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return data.get("data", {}).get("recentAcSubmissionList", [])
    except Exception:
        return None

async def verify_today(username: str, min_p: int = 1) -> tuple[bool, list[str]]:
    """Returns (met_goal, [problem titles solved today])"""
    subs = await fetch_lc_submissions(username)
    if subs is None:
        return False, []

    now = datetime.now(TIMEZONE)
    today_ts = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    done, seen = [], set()
    for sub in subs:
        ts = int(sub.get("timestamp", 0))
        slug = sub.get("titleSlug", "")
        if ts >= today_ts and slug not in seen:
            done.append(sub.get("title", slug))
            seen.add(slug)

    return len(done) >= min_p, done

async def fetch_recent_daily_ac(username: str, days: int = 30) -> dict[str, list[str]]:
    """Builds YYYY-MM-DD -> unique accepted problem titles from recent AC list."""
    subs = await fetch_lc_submissions(username, limit=200)
    if subs is None:
        return {}

    today = datetime.now(TIMEZONE).date()
    earliest = today - timedelta(days=days - 1)
    daily: dict[str, dict[str, str]] = {}

    for sub in subs:
        ts = int(sub.get("timestamp", 0))
        if ts <= 0:
            continue
        solved_date = datetime.fromtimestamp(ts, tz=TIMEZONE).date()
        if solved_date < earliest or solved_date > today:
            continue
        day_key = solved_date.strftime("%Y-%m-%d")
        slug = sub.get("titleSlug", "")
        title = sub.get("title", slug)
        if not slug:
            continue
        daily.setdefault(day_key, {})
        daily[day_key][slug] = title

    return {d: list(slug_map.values()) for d, slug_map in daily.items()}

async def fetch_calendar_submissions(username: str) -> tuple[dict[str, int], int | None]:
    """Returns (YYYY-MM-DD -> submission_count, reported_streak)."""
    payload = {
        "query": LEETCODE_CALENDAR_QUERY,
        "variables": {"username": username, "year": None}
    }
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com",
        "User-Agent": "Mozilla/5.0"
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                LEETCODE_API,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                if r.status != 200:
                    return {}, None
                data = await r.json()
    except Exception:
        return {}, None

    cal = (
        data.get("data", {})
        .get("matchedUser", {})
        .get("userCalendar", {})
    )
    streak = cal.get("streak")
    raw = cal.get("submissionCalendar")
    if not raw:
        return {}, streak

    try:
        epoch_map = json.loads(raw)
    except Exception:
        return {}, streak

    by_day: dict[str, int] = {}
    for epoch_str, count in epoch_map.items():
        try:
            ts = int(epoch_str)
            n = int(count)
        except (ValueError, TypeError):
            continue
        day = datetime.fromtimestamp(ts, tz=TIMEZONE).strftime("%Y-%m-%d")
        by_day[day] = n
    return by_day, streak

# ─── STREAK HELPERS ───────────────────────────────────────────────────────────
def update_streak(gd: dict, uid: str, completed: bool) -> dict:
    streaks = gd.setdefault("streaks", {})
    today = today_str()
    yesterday = yesterday_str()

    if uid not in streaks:
        streaks[uid] = {"current": 0, "best": 0, "last_date": None}

    s = streaks[uid]
    if completed:
        if s["last_date"] == today:
            pass
        elif s["last_date"] == yesterday:
            s["current"] += 1
        else:
            s["current"] = 1
        s["last_date"] = today
        s["best"] = max(s["best"], s["current"])
    else:
        if s["last_date"] not in (today, yesterday):
            s["current"] = 0
    return s

def streak_emoji(n: int) -> str:
    if n >= 30: return "🏆"
    if n >= 14: return "🔥"
    if n >= 7:  return "⚡"
    if n >= 3:  return "✨"
    return "🌱"

def recalc_streak_for_user(gd: dict, uid: str) -> dict:
    """Rebuild streak from stored submissions for one user."""
    streaks = gd.setdefault("streaks", {})
    solved_dates = []
    for day, users in gd.get("submissions", {}).items():
        if uid in users:
            solved_dates.append(day)

    if not solved_dates:
        streaks[uid] = {"current": 0, "best": 0, "last_date": None}
        return streaks[uid]

    solved_dates = sorted(set(solved_dates))
    best = cur_run = 1
    for i in range(1, len(solved_dates)):
        prev = datetime.strptime(solved_dates[i - 1], "%Y-%m-%d").date()
        cur = datetime.strptime(solved_dates[i], "%Y-%m-%d").date()
        if (cur - prev).days == 1:
            cur_run += 1
            best = max(best, cur_run)
        else:
            cur_run = 1

    last_date = solved_dates[-1]
    last_dt = datetime.strptime(last_date, "%Y-%m-%d").date()
    today = datetime.now(TIMEZONE).date()
    yesterday = today - timedelta(days=1)
    current = 0
    if last_dt in (today, yesterday):
        current = 1
        idx = len(solved_dates) - 1
        while idx > 0:
            d1 = datetime.strptime(solved_dates[idx], "%Y-%m-%d").date()
            d0 = datetime.strptime(solved_dates[idx - 1], "%Y-%m-%d").date()
            if (d1 - d0).days == 1:
                current += 1
                idx -= 1
            else:
                break

    streaks[uid] = {"current": current, "best": best, "last_date": last_date}
    return streaks[uid]

# ─── EVENTS ───────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ {bot.user} online")
    try:
        synced = await tree.sync()
        print(f"📡 Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Sync error: {e}")
    daily_reminder.start()
    daily_warning.start()

# ─── COMMANDS ─────────────────────────────────────────────────────────────────

@tree.command(name="setup", description="Cài đặt channel thông báo LeetCode")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, channel: discord.TextChannel):
    gd = get_guild_data(interaction.guild_id)
    gd["channel_id"] = channel.id
    update_guild_data(interaction.guild_id, gd)
    await interaction.response.send_message(
        f"✅ Channel: {channel.mention}\n"
        f"⏰ Nhắc lúc **9:00 • 14:00 • 20:00** | Warning **23:00** (GMT+7)\n"
        f"📌 Yêu cầu hiện tại: **{gd.get('min_problems', 1)} bài/ngày**",
        ephemeral=True
    )

@tree.command(name="setmin", description="Đặt số bài tối thiểu phải làm mỗi ngày (Admin)")
@app_commands.checks.has_permissions(administrator=True)
async def setmin(interaction: discord.Interaction, so_bai: int):
    if not 1 <= so_bai <= 10:
        await interaction.response.send_message("❌ Số bài phải từ 1–10!", ephemeral=True)
        return
    gd = get_guild_data(interaction.guild_id)
    old = gd.get("min_problems", 1)
    gd["min_problems"] = so_bai
    update_guild_data(interaction.guild_id, gd)
    await interaction.response.send_message(
        f"✅ Yêu cầu mỗi ngày: **{old} bài** → **{so_bai} bài**"
    )

@tree.command(name="register", description="Đăng ký username LeetCode để bot tự kiểm tra")
async def register(interaction: discord.Interaction, leetcode_username: str):
    await interaction.response.defer(ephemeral=True)
    gd = get_guild_data(interaction.guild_id)
    uid = str(interaction.user.id)

    result = await fetch_lc_submissions(leetcode_username)
    if result is None:
        await interaction.followup.send(
            f"❌ Không tìm thấy **{leetcode_username}** trên LeetCode.\n"
            f"Kiểm tra lại username và đảm bảo profile là **Public**.",
            ephemeral=True
        )
        return

    gd["leetcode_usernames"][uid] = leetcode_username
    if uid not in gd["tracked_users"]:
        gd["tracked_users"].append(uid)
    update_guild_data(interaction.guild_id, gd)

    await interaction.followup.send(
        f"✅ Đã đăng ký! LeetCode: **[{leetcode_username}](https://leetcode.com/{leetcode_username})**\n"
        f"Bot sẽ tự verify bài của bạn mỗi ngày lúc 23:00.",
        ephemeral=True
    )
    channel = bot.get_channel(gd["channel_id"])
    if channel:
        embed = discord.Embed(
            description=f"👋 {interaction.user.mention} vừa tham gia thử thách!\nLeetCode: **[{leetcode_username}](https://leetcode.com/{leetcode_username})**",
            color=0x5865F2
        )
        await channel.send(embed=embed)

@tree.command(name="track", description="Thêm thành viên vào danh sách (Admin)")
@app_commands.checks.has_permissions(administrator=True)
async def track(interaction: discord.Interaction, member: discord.Member):
    gd = get_guild_data(interaction.guild_id)
    uid = str(member.id)
    if uid not in gd["tracked_users"]:
        gd["tracked_users"].append(uid)
        update_guild_data(interaction.guild_id, gd)
        await interaction.response.send_message(f"✅ Đã thêm {member.mention}!")
    else:
        await interaction.response.send_message(f"ℹ️ {member.mention} đã có trong danh sách.", ephemeral=True)

@tree.command(name="untrack", description="Xóa thành viên khỏi danh sách (Admin)")
@app_commands.checks.has_permissions(administrator=True)
async def untrack(interaction: discord.Interaction, member: discord.Member):
    gd = get_guild_data(interaction.guild_id)
    uid = str(member.id)
    if uid in gd["tracked_users"]:
        gd["tracked_users"].remove(uid)
        update_guild_data(interaction.guild_id, gd)
        await interaction.response.send_message(f"✅ Đã xóa {member.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"ℹ️ Không tìm thấy {member.mention}.", ephemeral=True)

@tree.command(name="check", description="Kiểm tra ngay bài LeetCode của bạn hôm nay")
async def check(interaction: discord.Interaction):
    await interaction.response.defer()
    gd = get_guild_data(interaction.guild_id)
    uid = str(interaction.user.id)
    username = gd["leetcode_usernames"].get(uid)

    if not username:
        await interaction.followup.send(
            "⚠️ Bạn chưa đăng ký. Dùng `/register <username>` nhé!", ephemeral=True
        )
        return

    min_p = gd.get("min_problems", 1)
    completed, problems = await verify_today(username, min_p)
    today = today_str()

    if completed:
        if today not in gd["submissions"]:
            gd["submissions"][today] = {}
        gd["submissions"][today][uid] = problems
        s = update_streak(gd, uid, True)
        update_guild_data(interaction.guild_id, gd)

        embed = discord.Embed(
            title="✅ Đã xác minh!",
            description=f"**[{username}](https://leetcode.com/{username})** hôm nay solve **{len(problems)} bài**!",
            color=0x00d26a,
            timestamp=datetime.now(TIMEZONE)
        )
        txt = "\n".join(f"• {p}" for p in problems[:5])
        if len(problems) > 5:
            txt += f"\n• ...và {len(problems)-5} bài khác"
        embed.add_field(name="Bài đã làm hôm nay", value=txt, inline=False)
        embed.add_field(
            name="🔥 Streak",
            value=f"{streak_emoji(s['current'])} **{s['current']} ngày** liên tiếp  |  🏆 Kỷ lục: **{s['best']} ngày**",
            inline=False
        )
        await interaction.followup.send(embed=embed)
    else:
        solved = len(problems)
        embed = discord.Embed(
            title="❌ Chưa đủ bài!",
            description=f"**[{username}](https://leetcode.com/{username})** mới làm **{solved}/{min_p} bài** hôm nay.",
            color=0xff6b35
        )
        if problems:
            embed.add_field(name="Đã làm", value="\n".join(f"• {p}" for p in problems), inline=False)
        embed.set_footer(text=f"Cần {min_p - solved} bài nữa!")
        await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="sync_history", description="Đồng bộ lịch sử gần đây từ LeetCode để sửa streak bị lệch")
@app_commands.describe(days="Số ngày gần đây cần đồng bộ (1-90)")
async def sync_history(
    interaction: discord.Interaction,
    days: app_commands.Range[int, 1, 90] = 30
):
    await interaction.response.defer(ephemeral=True)
    gd = get_guild_data(interaction.guild_id)
    uid = str(interaction.user.id)
    username = gd["leetcode_usernames"].get(uid)
    if not username:
        await interaction.followup.send(
            "⚠️ Bạn chưa đăng ký. Dùng `/register <username>` trước nhé!",
            ephemeral=True
        )
        return

    daily_counts, lc_streak = await fetch_calendar_submissions(username)
    if not daily_counts:
        await interaction.followup.send(
            "❌ Không lấy được calendar từ LeetCode. Thử lại sau.",
            ephemeral=True
        )
        return

    today = datetime.now(TIMEZONE).date()
    range_days = {
        (today - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(days)
    }
    min_p = gd.get("min_problems", 1)
    gd.setdefault("submissions", {})

    updated_days = 0
    for day in sorted(range_days):
        count = daily_counts.get(day, 0)
        users_for_day = gd["submissions"].setdefault(day, {})
        if count >= min_p:
            # userCalendar only returns counts, not solved-title list.
            # Keep existing detailed titles when available.
            existing = users_for_day.get(uid, [])
            if existing:
                users_for_day[uid] = existing
            else:
                users_for_day[uid] = [f"[sync] {count} submissions from LeetCode calendar"]
            updated_days += 1
        elif uid in users_for_day:
            del users_for_day[uid]
            if not users_for_day:
                del gd["submissions"][day]

    s = recalc_streak_for_user(gd, uid)
    update_guild_data(interaction.guild_id, gd)

    await interaction.followup.send(
        f"✅ Đã đồng bộ **{days} ngày** cho **{username}**.\n"
        f"📅 Ngày đạt mục tiêu sau sync: **{updated_days}** ngày\n"
        f"🔥 Streak hiện tại: **{s['current']}** | 🏆 Kỷ lục: **{s['best']}**\n"
        f"📌 LeetCode calendar streak báo: **{lc_streak if lc_streak is not None else 'N/A'}**",
        ephemeral=True
    )

@tree.command(name="status", description="Xem tiến độ hôm nay của cả nhóm")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    gd = get_guild_data(interaction.guild_id)
    today = today_str()
    done_today = gd["submissions"].get(today, {})
    min_p = gd.get("min_problems", 1)

    embed = discord.Embed(title="📊 Tiến Độ LeetCode Hôm Nay", color=0x5865F2,
                          timestamp=datetime.now(TIMEZONE))
    embed.set_footer(text=f"Mục tiêu: {min_p} bài/ngày • {today}")

    done_list, pending_list = [], []
    for uid in gd["tracked_users"]:
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else f"User {uid}"
        lc = gd["leetcode_usernames"].get(uid)
        streak_n = gd.get("streaks", {}).get(uid, {}).get("current", 0)

        if uid in done_today:
            n = len(done_today[uid])
            stk = f" {streak_emoji(streak_n)} **{streak_n}🔥**" if streak_n > 1 else ""
            done_list.append(f"✅ **{name}** ({n} bài){stk}")
        else:
            warns = gd["warnings"].get(uid, 0)
            w = f" ⚠️×{warns}" if warns else ""
            lc_txt = f" • [{lc}](https://leetcode.com/{lc})" if lc else " • chưa đăng ký"
            pending_list.append(f"❌ **{name}**{lc_txt}{w}")

    if done_list:
        embed.add_field(name=f"✅ Đã làm ({len(done_list)})", value="\n".join(done_list), inline=False)
    if pending_list:
        embed.add_field(name=f"❌ Chưa làm ({len(pending_list)})", value="\n".join(pending_list), inline=False)

    total = len(gd["tracked_users"])
    done = len(done_today)
    pct = int(done / total * 100) if total else 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    embed.description = f"**{done}/{total}** người đạt mục tiêu\n`{bar}` {pct}%"
    await interaction.followup.send(embed=embed)

@tree.command(name="streak", description="Xem streak của bạn hoặc một thành viên")
async def streak_cmd(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    gd = get_guild_data(interaction.guild_id)
    uid = str(target.id)
    s = gd.get("streaks", {}).get(uid, {"current": 0, "best": 0, "last_date": None})
    lc = gd["leetcode_usernames"].get(uid)

    embed = discord.Embed(title=f"🔥 Streak của {target.display_name}", color=0xff6b35)
    if lc:
        embed.description = f"LeetCode: **[{lc}](https://leetcode.com/{lc})**"

    cur, best = s.get("current", 0), s.get("best", 0)
    embed.add_field(name=f"{streak_emoji(cur)} Streak hiện tại", value=f"**{cur} ngày**", inline=True)
    embed.add_field(name="🏆 Kỷ lục", value=f"**{best} ngày**", inline=True)
    embed.add_field(name="📅 Làm gần nhất", value=s.get("last_date") or "Chưa có", inline=True)

    milestone = 7 if cur < 7 else (14 if cur < 14 else 30)
    bar = "█" * min(cur, milestone) + "░" * max(0, milestone - cur)
    embed.add_field(name=f"Tiến tới {milestone} ngày", value=f"`{bar}` {cur}/{milestone}", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="leaderboard", description="Bảng xếp hạng streak của cả nhóm")
async def leaderboard(interaction: discord.Interaction):
    gd = get_guild_data(interaction.guild_id)
    ranked = sorted(
        [(uid, s) for uid, s in gd.get("streaks", {}).items() if uid in gd["tracked_users"]],
        key=lambda x: x[1].get("current", 0), reverse=True
    )
    embed = discord.Embed(title="🏆 Bảng Xếp Hạng Streak", color=0xffd700)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, s) in enumerate(ranked[:10]):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else f"User {uid}"
        cur, best = s.get("current", 0), s.get("best", 0)
        m = medals[i] if i < 3 else f"**{i+1}.**"
        lines.append(f"{m} {streak_emoji(cur)} **{name}** — {cur} ngày 🔥  *(kỷ lục: {best})*")
    embed.description = "\n".join(lines) if lines else "Chưa có dữ liệu streak!"
    embed.set_footer(text="Dùng /check để cập nhật streak")
    await interaction.response.send_message(embed=embed)

@tree.command(name="warnings", description="Xem bảng cảnh cáo (hall of shame 😅)")
async def warnings_cmd(interaction: discord.Interaction):
    gd = get_guild_data(interaction.guild_id)
    if not gd["warnings"]:
        await interaction.response.send_message("🎉 Chưa có ai bị warning!")
        return
    sorted_w = sorted(gd["warnings"].items(), key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="⚠️ Hall of Shame", color=0xff6b35)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, count) in enumerate(sorted_w[:10]):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else f"User {uid}"
        m = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{m} **{name}** — {count} lần bị warning")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Consistent beats perfect 💪")
    await interaction.response.send_message(embed=embed)

@tree.command(name="reset_warnings", description="Reset toàn bộ warnings (Admin)")
@app_commands.checks.has_permissions(administrator=True)
async def reset_warnings(interaction: discord.Interaction):
    gd = get_guild_data(interaction.guild_id)
    gd["warnings"] = {}
    update_guild_data(interaction.guild_id, gd)
    await interaction.response.send_message("✅ Đã reset warnings! Fresh start! 🌟")

# ─── SCHEDULED TASKS ──────────────────────────────────────────────────────────

@tasks.loop(minutes=1)
async def daily_reminder():
    now = datetime.now(TIMEZONE)
    ct = now.time().replace(second=0, microsecond=0)
    for rt in REMINDER_TIMES:
        if ct.hour == rt.hour and ct.minute == rt.minute:
            await send_reminders(now)
            break

@tasks.loop(minutes=1)
async def daily_warning():
    now = datetime.now(TIMEZONE)
    ct = now.time().replace(second=0, microsecond=0)
    if ct.hour == WARNING_TIME.hour and ct.minute == WARNING_TIME.minute:
        await run_daily_check()

async def send_reminders(now: datetime):
    data = load_data()
    today = today_str()
    msgs = {
        9:  ("☀️ Chào buổi sáng!", "Bắt đầu ngày mới với LeetCode nào! 🧠"),
        14: ("🌤️ Giờ nghỉ trưa!", "Tranh thủ solve một bài đi! 💡"),
        20: ("🌙 Tối rồi đó!", "Sắp hết ngày — chưa làm thì nhanh lên! ⏰"),
    }
    title, desc = msgs.get(now.hour, ("⏰ Nhắc nhở!", "Đừng quên LeetCode hôm nay!"))

    for gid, gd in data["guilds"].items():
        if not gd["channel_id"] or not gd["tracked_users"]:
            continue
        channel = bot.get_channel(gd["channel_id"])
        if not channel:
            continue
        done_today = gd["submissions"].get(today, {})
        pending = [uid for uid in gd["tracked_users"] if uid not in done_today]
        if not pending:
            continue
        guild = bot.get_guild(int(gid))
        if not guild:
            continue

        mentions = [guild.get_member(int(uid)).mention for uid in pending if guild.get_member(int(uid))]
        min_p = gd.get("min_problems", 1)
        embed = discord.Embed(
            title=title,
            description=f"{desc}\n\n**Chưa đạt mục tiêu:** {' '.join(mentions)}",
            color=0xffd700, timestamp=now
        )
        embed.set_footer(text=f"Mục tiêu: {min_p} bài/ngày • /check để cập nhật")
        embed.add_field(name="🔗 Link nhanh",
            value="[Daily Challenge](https://leetcode.com/problems/daily-challenge/) • [NeetCode 150](https://neetcode.io/practice)",
            inline=False)
        await channel.send(embed=embed)

async def run_daily_check():
    """23:00 — tự verify qua API rồi warning ai thiếu bài."""
    data = load_data()
    today = today_str()

    for gid, gd in data["guilds"].items():
        if not gd["channel_id"] or not gd["tracked_users"]:
            continue
        channel = bot.get_channel(gd["channel_id"])
        guild = bot.get_guild(int(gid))
        if not channel or not guild:
            continue

        min_p = gd.get("min_problems", 1)
        if today not in gd["submissions"]:
            gd["submissions"][today] = {}

        # Auto-verify qua API cho người chưa /check
        for uid in gd["tracked_users"]:
            if uid in gd["submissions"][today]:
                continue
            username = gd["leetcode_usernames"].get(uid)
            if not username:
                continue
            completed, problems = await verify_today(username, min_p)
            if completed:
                gd["submissions"][today][uid] = problems
                update_streak(gd, uid, True)

        done_today = gd["submissions"][today]
        failed = [uid for uid in gd["tracked_users"] if uid not in done_today]

        for uid in failed:
            update_streak(gd, uid, False)

        if not failed:
            embed = discord.Embed(
                title="🎉 Xuất Sắc! 100% Hoàn Thành!",
                description=f"Hôm nay tất cả đạt mục tiêu **{min_p} bài**! 🔥",
                color=0x00d26a, timestamp=datetime.now(TIMEZONE)
            )
            update_guild_data(int(gid), gd)
            await channel.send(embed=embed)
            continue

        for uid in failed:
            gd["warnings"][uid] = gd["warnings"].get(uid, 0) + 1

        update_guild_data(int(gid), gd)

        mentions, details = [], []
        for uid in failed:
            member = guild.get_member(int(uid))
            if not member:
                continue
            mentions.append(member.mention)
            count = gd["warnings"][uid]
            lc = gd["leetcode_usernames"].get(uid)
            lc_txt = f" ([{lc}](https://leetcode.com/{lc}))" if lc else " *(chưa đăng ký LC)*"
            details.append(f"• {member.display_name}{lc_txt} — **{count} lần** ⚠️")

        embed = discord.Embed(
            title="⚠️ CẢNH CÁO — Chưa đạt mục tiêu hôm nay!",
            description=(
                f"Hết ngày mà những người sau chưa làm đủ **{min_p} bài**!\n\n"
                f"{chr(10).join(details)}\n\n"
                f"{' '.join(mentions)}\n\n*Đừng để xảy ra ngày mai! 💀*"
            ),
            color=0xff0000, timestamp=datetime.now(TIMEZONE)
        )
        embed.set_footer(text="/warnings • /leaderboard • /streak")
        await channel.send(embed=embed)

# ─── ERROR HANDLER ────────────────────────────────────────────────────────────
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Không có quyền!", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Lỗi: {error}", ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN)
