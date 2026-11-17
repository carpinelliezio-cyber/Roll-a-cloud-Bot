"""
Roll a Cloud — Official Discord Server Builder & Bot
====================================================
One bot that:
  • /setup  -> builds the whole server: roles, categories, channels, permissions,
               and fills every channel with its content (rules, FAQ, how-to-play...)
  • Roblox verification (code in Roblox "About" bio) -> Verified role + nickname
  • VIP role auto-given if the player owns the VIP gamepass (optional)
  • Live Roblox stats in voice-channel counters (Playing / Visits / Favorites / Likes / Members)
  • /stats /whois /update /unlink /refreshstats
  • Self-roles (ping roles) with buttons
  • Ticket system (support, bug/data loss, creator application, player report) + transcripts
  • Auto reactions + threads in #suggestions and #bug-reports
  • Mod logs

Config via environment variables (see README).
"""

import asyncio
import datetime as dt
import io
import json
import logging
import os
import random
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# ============================================================ CONFIG
TOKEN = os.getenv("DISCORD_TOKEN", "")
PLACE_ID = int(os.getenv("ROBLOX_PLACE_ID", "0") or 0)          # number in your game URL
GROUP_ID = int(os.getenv("ROBLOX_GROUP_ID", "0") or 0)          # optional
VIP_GAMEPASS_ID = int(os.getenv("VIP_GAMEPASS_ID", "0") or 0)   # optional
TIKTOK_URL = os.getenv("TIKTOK_URL", "")                        # optional
YOUTUBE_URL = os.getenv("YOUTUBE_URL", "")                      # optional

GAME_NAME = "Roll a Cloud"
GAME_URL = f"https://www.roblox.com/games/{PLACE_ID}" if PLACE_ID else "https://www.roblox.com/discover"
GROUP_URL = f"https://www.roblox.com/groups/{GROUP_ID}" if GROUP_ID else ""
COLOR = 0x8FD3FF
STATS_INTERVAL_MIN = 10        # Discord allows 2 renames / 10 min per channel
DATA_DIR = os.getenv("DATA_DIR", ".")          # on Railway: mount a volume at /data and set DATA_DIR=/data
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "server_state.json")
LINKS_FILE = os.path.join(DATA_DIR, "linked_accounts.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("rollacloud")


# ============================================================ STORAGE
def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


STATE: dict = load_json(STATE_FILE, {})
LINKS: dict = load_json(LINKS_FILE, {})


def gstate(guild_id: int) -> dict:
    g = STATE.setdefault(str(guild_id), {})
    g.setdefault("roles", {})
    g.setdefault("channels", {})
    return g


def save_state() -> None:
    save_json(STATE_FILE, STATE)


def get_role(guild: discord.Guild, key: str) -> Optional[discord.Role]:
    rid = gstate(guild.id)["roles"].get(key)
    return guild.get_role(rid) if rid else None


def get_channel(guild: discord.Guild, key: str):
    cid = gstate(guild.id)["channels"].get(key)
    return guild.get_channel(cid) if cid else None


def mention(guild: discord.Guild, key: str) -> str:
    cid = gstate(guild.id)["channels"].get(key)
    return f"<#{cid}>" if cid else f"#{key}"


def fmt_num(n) -> str:
    if n is None:
        return "—"
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            return (f"{v:.1f}".rstrip("0").rstrip(".")) + suf
    return str(int(n))


# ============================================================ ROLES
P = discord.Permissions
# key, name, color, hoist, permissions      (listed top -> bottom)
ROLES = [
    ("owner", "👑 Owner", 0xFFD166, True, P(administrator=True)),
    ("admin", "🛡️ Admin", 0xFF6B6B, True, P(administrator=True)),
    ("mod", "🔨 Moderator", 0x4D96FF, True, P(kick_members=True, ban_members=True, moderate_members=True,
                                              manage_messages=True, manage_nicknames=True, manage_threads=True,
                                              mute_members=True, deafen_members=True, move_members=True,
                                              view_audit_log=True)),
    ("helper", "🤝 Helper", 0x6BCB77, True, P(manage_messages=True, moderate_members=True)),
    ("dev", "💻 Developer", 0xB983FF, True, P.none()),
    ("bots", "🤖 Bots", 0x99AAB5, True, P.none()),
    ("creator", "🎬 Content Creator", 0xFF8FAB, True, P.none()),
    ("tester", "🧪 Tester", 0x00C2A8, False, P.none()),
    ("vip", "💎 VIP", 0x00E5FF, True, P.none()),
    ("verified", "☁️ Verified", 0xDDF3FF, False, P.none()),
    ("unverified", "🔒 Unverified", 0x747F8D, False, P.none()),
]

# key, name, emoji, description
PING_ROLES = [
    ("ping_updates", "📢 Updates Ping", "📢", "New updates & patch notes"),
    ("ping_events", "🎉 Events Ping", "🎉", "In-game events & luck boosts"),
    ("ping_giveaways", "🎁 Giveaways Ping", "🎁", "Giveaways"),
    ("ping_peeks", "👀 Sneak Peeks Ping", "👀", "Previews of upcoming content"),
    ("ping_codes", "🎟️ Codes Ping", "🎟️", "New in-game codes"),
    ("ping_videos", "🎥 Videos Ping", "🎥", "New TikTok / YouTube videos"),
]

STAFF_KEYS = ("owner", "admin", "mod", "helper")


# ============================================================ LAYOUT
# (category_key, category_name, category_preset, [(key, name, kind, preset), ...])
LAYOUT = [
    ("cat_start", "📌 START HERE", "start", [
        ("welcome", "👋・welcome", "text", "start"),
        ("rules", "📜・rules", "text", "start"),
        ("verify", "✅・verify", "text", "verify"),
    ]),
    ("cat_stats", "📊 GAME STATS", "stats", [
        ("stat_playing", "🟢 Playing: —", "voice", "stats"),
        ("stat_visits", "👁️ Visits: —", "voice", "stats"),
        ("stat_favorites", "⭐ Favorites: —", "voice", "stats"),
        ("stat_likes", "👍 Likes: —", "voice", "stats"),
        ("stat_members", "👥 Members: —", "voice", "stats"),
    ] + ([("stat_group", "🏠 Group: —", "voice", "stats")] if GROUP_ID else [])),
    ("cat_info", "📢 INFORMATION", "info", [
        ("announcements", "📢・announcements", "news", "info"),
        ("updates", "🆕・updates", "news", "info"),
        ("sneak_peeks", "👀・sneak-peeks", "text", "info"),
        ("codes", "🎟️・codes", "text", "info"),
        ("events", "🎉・events", "text", "info"),
        ("giveaways", "🎁・giveaways", "text", "info"),
        ("how_to_play", "🎮・how-to-play", "text", "info"),
        ("faq", "❓・faq", "text", "info"),
        ("roles", "🎭・roles", "text", "info"),
        ("links", "🔗・links", "text", "info"),
    ]),
    ("cat_community", "💬 COMMUNITY", "chat", [
        ("general", "💬・general", "text", "chat"),
        ("game_chat", "☁️・roll-a-cloud-chat", "text", "chat"),
        ("lucky_rolls", "🍀・lucky-rolls", "text", "media"),
        ("showcase", "📸・showcase", "text", "media"),
        ("fan_art", "🎨・fan-art", "text", "media"),
        ("memes", "😂・memes", "text", "media"),
        ("videos", "🎥・videos", "text", "videos"),
        ("bot_commands", "🤖・bot-commands", "text", "chat"),
    ]),
    ("cat_feedback", "💡 FEEDBACK & SUPPORT", "chat", [
        ("suggestions", "💡・suggestions", "text", "chat"),
        ("bug_reports", "🐛・bug-reports", "text", "media"),
        ("support", "🎫・support", "text", "readonly"),
    ]),
    ("cat_voice", "🔊 VOICE", "voice", [
        ("vc_general", "🔊 General", "voice", "voice"),
        ("vc_gaming1", "🎮 Gaming 1", "voice", "voice"),
        ("vc_gaming2", "🎮 Gaming 2", "voice", "voice"),
        ("vc_chill", "☁️ Chill Clouds", "voice", "voice"),
    ]),
    ("cat_staff", "🛠️ STAFF", "staff", [
        ("staff_announcements", "📋・staff-announcements", "text", "staff"),
        ("staff_chat", "💬・staff-chat", "text", "staff"),
        ("mod_logs", "🔒・mod-logs", "text", "mod_logs"),
        ("dev_chat", "💻・dev-chat", "text", "dev"),
        ("testing", "🧪・testing", "text", "testing"),
        ("vc_staff", "🔊 Staff Voice", "voice", "staff"),
    ]),
    ("cat_tickets", "🎫 TICKETS", "tickets", []),
]

TOPICS = {
    "welcome": "Welcome to the official Roll a Cloud Discord! ☁️",
    "rules": "Read these before chatting. Breaking them = punishment.",
    "verify": "Link your Roblox account to unlock the server.",
    "announcements": "Official news about Roll a Cloud.",
    "updates": "Patch notes & changelogs for every update.",
    "sneak_peeks": "Previews of what's coming next. Don't leak!",
    "codes": "Active in-game codes. Redeem them fast!",
    "events": "In-game events, luck boosts & community events.",
    "giveaways": "Giveaways for the community.",
    "how_to_play": "New to Roll a Cloud? Start here.",
    "faq": "Frequently asked questions.",
    "roles": "Pick the notifications you want.",
    "links": "All official Roll a Cloud links.",
    "general": "Chat about anything — English only, keep it friendly.",
    "game_chat": "Strategies, luck, best clouds — all things Roll a Cloud.",
    "lucky_rolls": "Post your craziest rolls 🍀",
    "showcase": "Show off your clouds & setups (images / clips).",
    "fan_art": "Roll a Cloud fan art. Always credit the artist.",
    "memes": "Roll a Cloud memes. Keep it clean.",
    "videos": "Official & creator videos. Content Creators only.",
    "bot_commands": "Use /stats, /whois, /update here.",
    "suggestions": "One suggestion per message. Vote with ✅ / ❌.",
    "bug_reports": "Report bugs using the template. Screenshots help!",
    "support": "Open a private ticket with the staff team.",
    "staff_announcements": "Important info for the staff team.",
    "staff_chat": "Staff discussion.",
    "mod_logs": "Automatic logs: verifications, tickets, unlinks.",
    "dev_chat": "Development talk.",
    "testing": "Test builds & QA feedback.",
}

SLOWMODE = {"suggestions": 60, "bug_reports": 60, "lucky_rolls": 10, "showcase": 10, "general": 2}


def build_overwrites(guild: discord.Guild, R: dict, preset: str) -> dict:
    O = discord.PermissionOverwrite
    everyone = guild.default_role
    staff = [R[k] for k in STAFF_KEYS]
    top = [R["owner"], R["admin"]]
    ow = {guild.me: O(view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                      read_message_history=True, manage_messages=True, manage_channels=True,
                      add_reactions=True, connect=True)}

    def staff_full():
        for s in staff:
            ow[s] = O(view_channel=True, send_messages=True, read_message_history=True,
                      connect=True, speak=True, attach_files=True, embed_links=True)

    if preset == "start":
        ow[everyone] = O(view_channel=True, send_messages=False, add_reactions=False,
                         create_public_threads=False, create_private_threads=False, read_message_history=True)
        for s in top:
            ow[s] = O(send_messages=True)
    elif preset == "verify":
        ow[everyone] = O(view_channel=True, send_messages=False, add_reactions=False, read_message_history=True)
        ow[R["verified"]] = O(view_channel=False)
        for s in staff:
            ow[s] = O(view_channel=True)
    elif preset == "stats":
        ow[everyone] = O(view_channel=True, connect=False)
    elif preset == "info":
        ow[everyone] = O(view_channel=False)
        ow[R["verified"]] = O(view_channel=True, send_messages=False, add_reactions=True,
                              create_public_threads=False, read_message_history=True)
        for s in staff:
            ow[s] = O(view_channel=True)
        for s in top + [R["dev"]]:
            ow[s] = O(view_channel=True, send_messages=True, mention_everyone=True, embed_links=True,
                      attach_files=True)
    elif preset in ("chat", "media"):
        ow[everyone] = O(view_channel=False)
        ow[R["verified"]] = O(view_channel=True, send_messages=True, read_message_history=True,
                              embed_links=True, attach_files=(preset == "media"), add_reactions=True)
        ow[R["unverified"]] = O(view_channel=False)
        staff_full()
    elif preset == "readonly":
        ow[everyone] = O(view_channel=False)
        ow[R["verified"]] = O(view_channel=True, send_messages=False, add_reactions=False, read_message_history=True)
        staff_full()
    elif preset == "videos":
        ow[everyone] = O(view_channel=False)
        ow[R["verified"]] = O(view_channel=True, send_messages=False, add_reactions=True, read_message_history=True)
        ow[R["creator"]] = O(view_channel=True, send_messages=True, embed_links=True, attach_files=True)
        staff_full()
    elif preset == "voice":
        ow[everyone] = O(view_channel=False)
        ow[R["verified"]] = O(view_channel=True, connect=True, speak=True, stream=True, use_voice_activation=True)
        staff_full()
    elif preset == "staff":
        ow[everyone] = O(view_channel=False)
        staff_full()
    elif preset == "mod_logs":
        ow[everyone] = O(view_channel=False)
        for s in (R["owner"], R["admin"], R["mod"]):
            ow[s] = O(view_channel=True, send_messages=False, read_message_history=True)
    elif preset == "dev":
        ow[everyone] = O(view_channel=False)
        staff_full()
        ow[R["dev"]] = O(view_channel=True, send_messages=True, attach_files=True, embed_links=True)
    elif preset == "testing":
        ow[everyone] = O(view_channel=False)
        staff_full()
        for s in (R["dev"], R["tester"]):
            ow[s] = O(view_channel=True, send_messages=True, attach_files=True, embed_links=True)
    elif preset == "tickets":
        ow[everyone] = O(view_channel=False)
        staff_full()
    return ow


# ============================================================ ROBLOX API
class RobloxAPI:
    def __init__(self, session: aiohttp.ClientSession):
        self.s = session

    async def _req(self, method: str, url: str, **kw):
        for attempt in range(3):
            try:
                async with self.s.request(method, url, timeout=aiohttp.ClientTimeout(total=10), **kw) as r:
                    if r.status == 429:
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    if r.status != 200:
                        return None
                    return await r.json()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await asyncio.sleep(1)
        return None

    async def user_by_name(self, username: str) -> Optional[dict]:
        d = await self._req("POST", "https://users.roblox.com/v1/usernames/users",
                            json={"usernames": [username], "excludeBannedUsers": True})
        return d["data"][0] if d and d.get("data") else None

    async def user(self, user_id: int) -> Optional[dict]:
        return await self._req("GET", f"https://users.roblox.com/v1/users/{user_id}")

    async def headshot(self, user_id: int) -> Optional[str]:
        d = await self._req("GET", "https://thumbnails.roblox.com/v1/users/avatar-headshot",
                            params={"userIds": user_id, "size": "150x150", "format": "Png"})
        return d["data"][0].get("imageUrl") if d and d.get("data") else None

    async def universe_from_place(self, place_id: int) -> Optional[int]:
        d = await self._req("GET", f"https://apis.roblox.com/universes/v1/places/{place_id}/universe")
        return d.get("universeId") if d else None

    async def game(self, universe_id: int) -> Optional[dict]:
        d = await self._req("GET", "https://games.roblox.com/v1/games", params={"universeIds": universe_id})
        return d["data"][0] if d and d.get("data") else None

    async def votes(self, universe_id: int) -> Optional[dict]:
        d = await self._req("GET", "https://games.roblox.com/v1/games/votes", params={"universeIds": universe_id})
        return d["data"][0] if d and d.get("data") else None

    async def game_icon(self, universe_id: int) -> Optional[str]:
        d = await self._req("GET", "https://thumbnails.roblox.com/v1/games/icons",
                            params={"universeIds": universe_id, "size": "512x512", "format": "Png"})
        return d["data"][0].get("imageUrl") if d and d.get("data") else None

    async def group(self, group_id: int) -> Optional[dict]:
        return await self._req("GET", f"https://groups.roblox.com/v1/groups/{group_id}")

    async def owns_gamepass(self, user_id: int, gamepass_id: int) -> Optional[bool]:
        d = await self._req("GET", f"https://inventory.roblox.com/v1/users/{user_id}/items/GamePass/{gamepass_id}")
        if d is None:
            return None  # private inventory / API error
        return bool(d.get("data"))


# ============================================================ BOT
class RollACloudBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # enable "Server Members Intent" in the Developer Portal
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.session: Optional[aiohttp.ClientSession] = None
        self.rbx: Optional[RobloxAPI] = None
        self.universe_id: Optional[int] = None
        self.pending: dict[int, dict] = {}
        self._stats_cache: tuple[float, Optional[dict]] = (0.0, None)

    async def setup_hook(self):
        self.session = aiohttp.ClientSession(headers={"User-Agent": "RollACloudDiscordBot/1.0"})
        self.rbx = RobloxAPI(self.session)
        self.add_view(VerifyView())
        self.add_view(RolesView())
        self.add_view(TicketPanelView())
        self.add_view(TicketCloseView())
        await self.tree.sync()
        self.stats_loop.start()

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

    async def fetch_stats(self) -> Optional[dict]:
        if not PLACE_ID:
            return None
        ts, cached = self._stats_cache
        if cached and time.time() - ts < 60:
            return cached
        if self.universe_id is None:
            self.universe_id = await self.rbx.universe_from_place(PLACE_ID)
        if not self.universe_id:
            return None
        game = await self.rbx.game(self.universe_id)
        votes = await self.rbx.votes(self.universe_id)
        if not game:
            return cached
        up = (votes or {}).get("upVotes", 0)
        down = (votes or {}).get("downVotes", 0)
        stats = {
            "name": game.get("name", GAME_NAME),
            "playing": game.get("playing", 0),
            "visits": game.get("visits", 0),
            "favorites": game.get("favoritedCount", 0),
            "up": up, "down": down,
            "likes_pct": round(up / (up + down) * 100) if (up + down) else None,
            "updated": game.get("updated"),
            "created": game.get("created"),
            "max_players": game.get("maxPlayers"),
            "group_members": None,
        }
        if GROUP_ID:
            g = await self.rbx.group(GROUP_ID)
            stats["group_members"] = g.get("memberCount") if g else None
        self._stats_cache = (time.time(), stats)
        return stats

    @tasks.loop(minutes=STATS_INTERVAL_MIN)
    async def stats_loop(self):
        for guild in self.guilds:
            try:
                await update_stat_channels(guild)
            except Exception as e:  # never kill the loop
                log.warning("stats update failed in %s: %s", guild.name, e)

    @stats_loop.before_loop
    async def _before_stats(self):
        await self.wait_until_ready()


bot = RollACloudBot()


def base_embed(title: str, desc: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=COLOR)
    e.set_footer(text="Roll a Cloud • Official Discord")
    return e


def play_button_view() -> discord.ui.View:
    v = discord.ui.View()
    v.add_item(discord.ui.Button(label="Play Roll a Cloud", emoji="🎮", url=GAME_URL))
    return v


def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    ids = {get_role(member.guild, k).id for k in STAFF_KEYS if get_role(member.guild, k)}
    return any(r.id in ids for r in member.roles)


async def mod_log(guild: discord.Guild, embed: discord.Embed, file: Optional[discord.File] = None):
    ch = get_channel(guild, "mod_logs")
    if ch:
        try:
            await ch.send(embed=embed, file=file) if file else await ch.send(embed=embed)
        except discord.HTTPException:
            pass


# ============================================================ STATS CHANNELS
async def update_stat_channels(guild: discord.Guild):
    s = await bot.fetch_stats()
    names = {"stat_members": f"👥 Members: {fmt_num(guild.member_count)}"}
    if s:
        names.update({
            "stat_playing": f"🟢 Playing: {fmt_num(s['playing'])}",
            "stat_visits": f"👁️ Visits: {fmt_num(s['visits'])}",
            "stat_favorites": f"⭐ Favorites: {fmt_num(s['favorites'])}",
            "stat_likes": f"👍 Likes: {s['likes_pct']}%" if s["likes_pct"] is not None else "👍 Likes: —",
        })
        if GROUP_ID:
            names["stat_group"] = f"🏠 Group: {fmt_num(s['group_members'])}"
    for key, name in names.items():
        ch = get_channel(guild, key)
        if ch and ch.name != name:
            try:
                await asyncio.wait_for(ch.edit(name=name), timeout=15)
            except (discord.HTTPException, asyncio.TimeoutError):
                pass


# ============================================================ CHANNEL CONTENT
async def post_content(guild: discord.Guild, key: str, ch: discord.TextChannel):
    m = lambda k: mention(guild, k)  # noqa: E731
    R = {k: get_role(guild, k) for k in [r[0] for r in ROLES] + [p[0] for p in PING_ROLES]}
    rm = lambda k: R[k].mention if R.get(k) else k  # noqa: E731
    msg = None

    if key == "welcome":
        e = base_embed("☁️ Welcome to Roll a Cloud!",
            "This is the **official Discord server** of **Roll a Cloud [EARN OFFLINE]** on Roblox.\n\n"
            "Roll for clouds, collect the rarest ones in the sky and keep earning — **even while you're offline!** ☁️💰\n\n"
            "**🚀 Get started**\n"
            f"> 1️⃣ Read the rules in {m('rules')}\n"
            f"> 2️⃣ Link your Roblox account in {m('verify')}\n"
            f"> 3️⃣ Pick your notifications in {m('roles')}\n"
            f"> 4️⃣ Say hi in {m('general')}!\n\n"
            f"**📢 Stay updated:** {m('announcements')} • {m('updates')} • {m('codes')}\n"
            f"**🆘 Need help?** Open a ticket in {m('support')}")
        msg = await ch.send(embed=e, view=play_button_view())

    elif key == "rules":
        e = base_embed("📜 Server Rules",
            "By being in this server you agree to follow these rules. Staff can act on anything that harms the community, even if it isn't listed.")
        rules = [
            ("1. Be respectful", "No harassment, hate speech, racism, threats or bullying. Treat everyone kindly."),
            ("2. No spam", "No message spam, emoji/caps spam, mass pings or flooding channels."),
            ("3. Keep it safe for everyone", "No NSFW, gore or disturbing content — in messages, names, avatars or statuses."),
            ("4. No scams or account trading", "No selling/trading Roblox accounts, Robux scams, fake links or \"free Robux\" offers."),
            ("5. No exploits or cheats", "Don't share, ask for or promote exploits, scripts or glitches. Report bugs in " + m("bug_reports") + " instead."),
            ("6. No advertising", "No self-promo, server invites or DM advertising (except Content Creators in " + m("videos") + ")."),
            ("7. English only", "Please use English in public channels so everyone can understand and moderate."),
            ("8. Use the right channels", "Keep topics in their channel. Bot commands go in " + m("bot_commands") + "."),
            ("9. Don't ping staff for nothing", "Need help? Open a ticket in " + m("support") + "."),
            ("10. Follow the ToS", "Follow the [Discord ToS](https://discord.com/terms) and [Roblox Community Standards](https://en.help.roblox.com/hc/en-us/articles/203313410)."),
        ]
        for n, v in rules:
            e.add_field(name=n, value=v, inline=False)
        e.add_field(name="⚖️ Punishments",
                    value="Warning → Timeout (1h) → Timeout (24h) → Kick → Ban.\nSerious offences (scams, NSFW, threats) = instant ban.\nStaff decisions are final.",
                    inline=False)
        msg = await ch.send(embed=e)

    elif key == "verify":
        e = base_embed("✅ Verify your Roblox account",
            "To unlock the server, link your **Roblox account**. It takes 30 seconds!\n\n"
            "**How it works**\n"
            "> 1️⃣ Click **Verify with Roblox** below\n"
            "> 2️⃣ Type your Roblox username\n"
            "> 3️⃣ Paste the code you get into your Roblox profile **About** section\n"
            "> 4️⃣ Click **Done** — you're in! ☁️\n\n"
            "You can remove the code from your bio afterwards.\n"
            f"🔒 We never ask for your password. Never give it to anyone!\n\n"
            f"💎 Own the **VIP** gamepass? You'll get the {rm('vip')} role automatically.")
        msg = await ch.send(embed=e, view=VerifyView())

    elif key == "roles":
        lines = "\n".join(f"{emo} **{name.split(' ', 1)[1]}** — {desc}" for _, name, emo, desc in PING_ROLES)
        e = base_embed("🎭 Notification Roles",
            "Click a button to **get or remove** a role. Only get pinged for what you care about!\n\n" + lines +
            "\n\n**Other roles**\n"
            f"{rm('vip')} — owns the VIP gamepass (auto)\n"
            f"{rm('creator')} — approved content creators\n"
            f"{rm('tester')} — trusted testers\n"
            f"{rm('verified')} — linked Roblox account")
        msg = await ch.send(embed=e, view=RolesView())

    elif key == "how_to_play":
        e = base_embed("🎮 How to Play Roll a Cloud", "New to the sky? Here's everything you need to know.")
        steps = [
            ("1️⃣ Roll for clouds", "Hit **Roll** to get a random cloud. Every cloud has a rarity — from common puffs to ultra-rare legendary clouds."),
            ("2️⃣ Build your collection", "Keep your best clouds and place them in your sky. Rarer clouds earn you more."),
            ("3️⃣ Earn — even offline", "Your clouds keep producing while you're away. Come back and collect your **offline earnings**! 💰"),
            ("4️⃣ Upgrade your luck", "Spend your earnings on upgrades and boosts to roll rarer clouds faster."),
            ("5️⃣ Join events", f"Watch {m('events')} for luck boosts and limited clouds, and redeem codes from {m('codes')}."),
        ]
        for n, v in steps:
            e.add_field(name=n, value=v, inline=False)
        e.add_field(name="💡 Tip", value=f"Show your best pulls in {m('lucky_rolls')}!", inline=False)
        msg = await ch.send(embed=e, view=play_button_view())

    elif key == "faq":
        e = base_embed("❓ Frequently Asked Questions")
        faq = [
            ("Is Roll a Cloud free?", "Yes! The game is free to play. Gamepasses are optional."),
            ("What does EARN OFFLINE mean?", "Your clouds keep earning while you're not playing. Join back to collect everything you made."),
            ("How do I get rarer clouds?", "Keep rolling, upgrade your luck and play during boost events."),
            ("Are there codes?", f"Yes — all active codes are posted in {m('codes')}. Get {rm('ping_codes')} to never miss one."),
            ("When is the next update?", f"Follow {m('updates')} and {m('sneak_peeks')}, and grab {rm('ping_updates')}."),
            ("I found a bug!", f"Report it in {m('bug_reports')} using the template."),
            ("I lost my data / items", f"Open a **Bug / Data loss** ticket in {m('support')} with your Roblox username."),
            ("Why can't I see the channels?", f"You need to verify first in {m('verify')}."),
            ("How do I become a Content Creator?", f"Open a **Creator application** ticket in {m('support')}."),
            ("Can I be staff?", "Staff applications open from time to time — watch the announcements."),
        ]
        for q, a in faq:
            e.add_field(name=q, value=a, inline=False)
        msg = await ch.send(embed=e)

    elif key == "links":
        lines = [f"🎮 **Play the game:** [Roll a Cloud on Roblox]({GAME_URL})"]
        if GROUP_URL:
            lines.append(f"🏠 **Roblox group:** [Join the group]({GROUP_URL})")
        if TIKTOK_URL:
            lines.append(f"🎵 **TikTok:** [Follow us]({TIKTOK_URL})")
        if YOUTUBE_URL:
            lines.append(f"▶️ **YouTube:** [Subscribe]({YOUTUBE_URL})")
        lines.append("\n⚠️ These are the **only** official links. Anything else is fake.")
        msg = await ch.send(embed=base_embed("🔗 Official Links", "\n".join(lines)), view=play_button_view())

    elif key == "support":
        e = base_embed("🎫 Support Tickets",
            "Need help from the staff team? Open a **private ticket** below.\n\n"
            "🛠️ **Support** — general questions & help\n"
            "🐛 **Bug / Data loss** — lost items, progress or Robux purchases\n"
            "🎬 **Creator application** — get the Content Creator role\n"
            "⚠️ **Report a player** — exploiters, scammers, rule breakers\n\n"
            "Please don't open tickets for nothing — abuse = timeout.")
        msg = await ch.send(embed=e, view=TicketPanelView())

    elif key == "bug_reports":
        e = base_embed("🐛 How to report a bug",
            "Copy this template and fill it in:\n```\n"
            "Roblox username:\nDevice: PC / Mobile / Tablet / Console\n"
            "What happened:\nSteps to reproduce:\nScreenshot / video:\n```\n"
            "A thread will open on your report so the devs can ask questions. 🙏\n"
            f"Lost data or items? Use a ticket in {m('support')} instead.")
        msg = await ch.send(embed=e)

    elif key == "suggestions":
        e = base_embed("💡 Suggestions",
            "Got an idea to make Roll a Cloud better? Post it here!\n\n"
            "• **One suggestion per message**\n• Be clear and detailed\n• Vote with ✅ / ❌\n"
            "• Discuss in the thread under each suggestion\n\n"
            "The most popular ideas may be added to the game! ☁️")
        msg = await ch.send(embed=e)

    elif key == "codes":
        e = base_embed("🎟️ Active Codes",
            "No codes yet — the first one drops soon! 👀\n\n"
            "**How to redeem**\n> Open the game → click the **Codes** button → type the code → **Redeem**\n\n"
            f"Get {rm('ping_codes')} in {m('roles')} to be pinged for new codes.")
        msg = await ch.send(embed=e)

    elif key == "announcements":
        e = base_embed("📢 Welcome to the official Roll a Cloud server!",
            "This is where all official news is posted: updates, events, giveaways and more.\n\n"
            f"Want pings? Grab {rm('ping_updates')} in {m('roles')}.")
        msg = await ch.send(embed=e, view=play_button_view())

    elif key == "updates":
        e = base_embed("🆕 Patch Notes",
            "Every update's changelog is posted here.\n\n"
            "**Format**\n```\n☁️ Update X — Name\n🆕 New: ...\n⚙️ Changes: ...\n🐛 Fixes: ...\n```\n"
            f"Pinged with {rm('ping_updates')}.")
        msg = await ch.send(embed=e)

    elif key == "staff_chat":
        e = base_embed("🛠️ Staff Guide",
            "**Moderation ladder**\n> Warning → Timeout 1h → Timeout 24h → Kick → Ban\n"
            "> Scams / NSFW / threats → instant ban\n\n"
            "**Tools**\n> `/whois @user` — see a member's Roblox account\n"
            "> `/unlink @user` — remove a Roblox link\n> `/refreshstats` — force stats update\n\n"
            "**Rules for staff**\n> Stay calm and fair • Always give a reason • Don't abuse powers\n"
            f"> Tickets: claim them by replying first, close with the 🔒 button. Logs go to {m('mod_logs')}.")
        msg = await ch.send(embed=e)

    elif key in TOPICS:
        intros = {
            "sneak_peeks": f"👀 Exclusive previews of upcoming clouds, features and maps.\nPlease **don't leak** them outside the server! Ping: {rm('ping_peeks')}",
            "events": f"🎉 In-game events, luck boosts, limited clouds and community events are announced here.\nPing: {rm('ping_events')}",
            "giveaways": f"🎁 Giveaways for the community (gamepasses, in-game rewards & more).\nTo join, just react to the giveaway message. Ping: {rm('ping_giveaways')}",
            "general": "💬 Chat about anything! Keep it friendly and in English. ☁️",
            "game_chat": "☁️ Talk about Roll a Cloud: strategies, best clouds, luck tips, what you want next…",
            "lucky_rolls": "🍀 Rolled something insane? Post your screenshot or clip here!",
            "showcase": "📸 Show off your collection and your sky setup! Images & clips only.",
            "fan_art": "🎨 Share your Roll a Cloud fan art. Always credit the original artist.",
            "memes": "😂 Roll a Cloud memes. Keep them clean and respectful.",
            "videos": f"🎥 Official and creator videos (TikTok / YouTube).\nOnly {rm('creator')} can post here — apply with a ticket in {m('support')}. Ping: {rm('ping_videos')}",
            "bot_commands": "🤖 Use bot commands here:\n> `/stats` — live game stats\n> `/whois @user` — someone's Roblox account\n> `/update` — refresh your nickname & VIP role",
            "staff_announcements": "📋 Important info for the staff team. Read every message here.",
            "mod_logs": "🔒 Automatic logs: verifications, unlinks, tickets and transcripts.",
            "dev_chat": "💻 Development discussion: features, scripts, bugs and planning.",
            "testing": "🧪 Test builds and QA feedback. Report what you tested, on which device, and what broke.",
        }
        if key in intros:
            msg = await ch.send(embed=base_embed(ch.name.split("・")[-1].replace("-", " ").title(), intros[key]))

    if msg:
        try:
            await msg.pin()
        except discord.HTTPException:
            pass


# ============================================================ VERIFICATION
WORDS = ["cloud", "sky", "rain", "sunny", "breeze", "storm", "rainbow", "misty", "thunder", "snowy",
         "drizzle", "fluffy", "puffy", "nimbus", "cirrus", "sunset", "dawn", "windy", "starry", "moon"]


def make_code() -> str:
    return "rac " + " ".join(random.sample(WORDS, 5))


async def apply_member_roles(guild: discord.Guild, member: discord.Member, link: dict) -> list[str]:
    notes = []
    verified, unverified, vip = get_role(guild, "verified"), get_role(guild, "unverified"), get_role(guild, "vip")
    try:
        if verified and verified not in member.roles:
            await member.add_roles(verified, reason="Roblox verified")
        if unverified and unverified in member.roles:
            await member.remove_roles(unverified, reason="Roblox verified")
    except discord.Forbidden:
        notes.append("⚠️ I couldn't edit your roles (my role must be above them).")
    nick = f"{link['display']} (@{link['name']})" if link["display"] != link["name"] else link["name"]
    try:
        if member.nick != nick[:32]:
            await member.edit(nick=nick[:32], reason="Roblox verified")
    except discord.Forbidden:
        notes.append("ℹ️ I couldn't change your nickname (server owner or higher role).")
    if VIP_GAMEPASS_ID and vip:
        owns = await bot.rbx.owns_gamepass(link["roblox_id"], VIP_GAMEPASS_ID)
        try:
            if owns and vip not in member.roles:
                await member.add_roles(vip, reason="Owns VIP gamepass")
                notes.append("💎 VIP gamepass detected — VIP role added!")
            elif owns is False and vip in member.roles:
                await member.remove_roles(vip, reason="No VIP gamepass")
            elif owns is None:
                notes.append("ℹ️ Couldn't check VIP (make your Roblox inventory public, then `/update`).")
        except discord.Forbidden:
            pass
    return notes


async def complete_verification(inter: discord.Interaction, rbx_user: dict):
    guild, member = inter.guild, inter.user
    link = {"roblox_id": rbx_user["id"], "name": rbx_user["name"],
            "display": rbx_user.get("displayName") or rbx_user["name"],
            "verified_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    LINKS[str(member.id)] = link
    save_json(LINKS_FILE, LINKS)
    notes = await apply_member_roles(guild, member, link)

    e = base_embed("✅ You're verified!",
                   f"Linked to **[{link['display']} (@{link['name']})](https://www.roblox.com/users/{link['roblox_id']}/profile)**.\n"
                   f"Welcome to the sky! ☁️ Head to {mention(guild, 'general')}.\n\n"
                   "You can now remove the code from your Roblox bio." + ("\n\n" + "\n".join(notes) if notes else ""))
    head = await bot.rbx.headshot(link["roblox_id"])
    if head:
        e.set_thumbnail(url=head)
    await inter.followup.send(embed=e, ephemeral=True)

    le = base_embed("✅ Member verified",
                    f"{member.mention} → [{link['name']}](https://www.roblox.com/users/{link['roblox_id']}/profile) (`{link['roblox_id']}`)")
    await mod_log(guild, le)
    general = get_channel(guild, "general")
    if general:
        try:
            await general.send(f"☁️ Welcome {member.mention} to the sky! (Roblox: **{link['name']}**)")
        except discord.HTTPException:
            pass


class VerifyModal(discord.ui.Modal, title="Link your Roblox account"):
    username = discord.ui.TextInput(label="Roblox username", placeholder="e.g. Builderman",
                                    min_length=3, max_length=20)

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        rbx_user = await bot.rbx.user_by_name(str(self.username.value).strip())
        if not rbx_user:
            return await inter.followup.send("❌ Roblox user not found. Check the spelling (username, not display name).", ephemeral=True)
        for did, info in LINKS.items():
            if info["roblox_id"] == rbx_user["id"] and did != str(inter.user.id) and inter.guild.get_member(int(did)):
                return await inter.followup.send("❌ This Roblox account is already linked to another member. Open a ticket if it's yours.", ephemeral=True)
        code = make_code()
        bot.pending[inter.user.id] = {"user": rbx_user, "code": code, "expires": time.time() + 900}
        e = base_embed("🔑 Almost done!",
                       f"Roblox account: **{rbx_user.get('displayName')} (@{rbx_user['name']})**\n\n"
                       f"**1.** Open your profile: [roblox.com/users/{rbx_user['id']}/profile](https://www.roblox.com/users/{rbx_user['id']}/profile)\n"
                       "**2.** Edit your **About** section and paste this code:\n"
                       f"```\n{code}\n```"
                       "**3.** Save, then click **Done** below.\n\n"
                       "⏱️ The code expires in 15 minutes. If Roblox tags it with ####, click **New code**.")
        await inter.followup.send(embed=e, view=CheckView(inter.user.id), ephemeral=True)


class CheckView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=900)
        self.owner_id = owner_id

    @discord.ui.button(label="Done — check my profile", emoji="✅", style=discord.ButtonStyle.success)
    async def done(self, inter: discord.Interaction, _):
        await inter.response.defer(ephemeral=True, thinking=True)
        p = bot.pending.get(inter.user.id)
        if not p or p["expires"] < time.time():
            return await inter.followup.send("⏱️ Your code expired. Click **Verify with Roblox** again.", ephemeral=True)
        info = await bot.rbx.user(p["user"]["id"])
        if not info:
            return await inter.followup.send("⚠️ Roblox didn't answer. Try again in a few seconds.", ephemeral=True)
        if p["code"].lower() not in (info.get("description") or "").lower():
            return await inter.followup.send("❌ Code not found in your About section. Make sure you **saved** it, wait 10 seconds and try again.", ephemeral=True)
        bot.pending.pop(inter.user.id, None)
        await complete_verification(inter, info)
        self.stop()

    @discord.ui.button(label="New code", emoji="🔄", style=discord.ButtonStyle.secondary)
    async def new_code(self, inter: discord.Interaction, _):
        p = bot.pending.get(inter.user.id)
        if not p:
            return await inter.response.send_message("Click **Verify with Roblox** again.", ephemeral=True)
        p["code"], p["expires"] = make_code(), time.time() + 900
        await inter.response.send_message(f"🔄 New code — paste this in your About section:\n```\n{p['code']}\n```", ephemeral=True)


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify with Roblox", emoji="✅", style=discord.ButtonStyle.success, custom_id="rac:verify")
    async def verify(self, inter: discord.Interaction, _):
        if str(inter.user.id) in LINKS:
            await inter.response.defer(ephemeral=True, thinking=True)
            notes = await apply_member_roles(inter.guild, inter.user, LINKS[str(inter.user.id)])
            return await inter.followup.send("✅ You're already verified — roles refreshed!" + ("\n" + "\n".join(notes) if notes else ""), ephemeral=True)
        await inter.response.send_modal(VerifyModal())

    @discord.ui.button(label="Help", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="rac:verify_help")
    async def help(self, inter: discord.Interaction, _):
        await inter.response.send_message(
            "**Trouble verifying?**\n"
            "• Use your **username**, not your display name\n"
            "• On mobile: Profile → ✏️ next to *About* → paste → Save\n"
            "• If Roblox shows `####`, click **New code**\n"
            f"• Still stuck? Open a ticket in {mention(inter.guild, 'support')}", ephemeral=True)


# ============================================================ SELF ROLES
class RolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, name, emoji, _ in PING_ROLES:
            b = discord.ui.Button(label=name.split(" ", 1)[1], emoji=emoji,
                                  style=discord.ButtonStyle.secondary, custom_id=f"rac:role:{key}")
            b.callback = self._cb(key)
            self.add_item(b)

    @staticmethod
    def _cb(key: str):
        async def callback(inter: discord.Interaction):
            role = get_role(inter.guild, key)
            if not role:
                return await inter.response.send_message("Role not found — ask an admin to run /setup.", ephemeral=True)
            try:
                if role in inter.user.roles:
                    await inter.user.remove_roles(role, reason="Self role")
                    await inter.response.send_message(f"➖ Removed {role.mention}", ephemeral=True)
                else:
                    await inter.user.add_roles(role, reason="Self role")
                    await inter.response.send_message(f"➕ Added {role.mention}", ephemeral=True)
            except discord.Forbidden:
                await inter.response.send_message("I can't manage that role (move my role higher).", ephemeral=True)
        return callback


# ============================================================ TICKETS
TICKET_TYPES = {
    "support": ("🛠️", "Support", "Explain your question or problem. A staff member will reply soon."),
    "bug": ("🐛", "Bug / Data loss", "Please send:\n• Roblox username\n• What you lost / what happened\n• When it happened\n• Device\n• Screenshots or video"),
    "creator": ("🎬", "Creator application", "Please send:\n• Your channel link(s) (TikTok / YouTube)\n• Follower count & average views\n• Have you made Roll a Cloud videos? Links!"),
    "report": ("⚠️", "Report a player", "Please send:\n• Roblox or Discord username of the player\n• What they did\n• Proof (screenshots / video)"),
}


async def open_ticket(inter: discord.Interaction, kind: str):
    guild, member = inter.guild, inter.user
    emoji, label, intro = TICKET_TYPES[kind]
    cat = get_channel(guild, "cat_tickets")
    if not isinstance(cat, discord.CategoryChannel):
        return await inter.response.send_message("Tickets aren't set up — ask an admin to run /setup.", ephemeral=True)
    for ch in cat.text_channels:
        if ch.topic and f"owner:{member.id}" in ch.topic:
            return await inter.response.send_message(f"You already have an open ticket: {ch.mention}", ephemeral=True)
    await inter.response.defer(ephemeral=True, thinking=True)
    O = discord.PermissionOverwrite
    ow = {guild.default_role: O(view_channel=False),
          guild.me: O(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True),
          member: O(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True)}
    for k in STAFF_KEYS:
        r = get_role(guild, k)
        if r:
            ow[r] = O(view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
    name = f"{kind}-{member.name}".lower()[:90]
    ch = await guild.create_text_channel(name, category=cat, overwrites=ow,
                                         topic=f"{emoji} {label} ticket by {member} | owner:{member.id}")
    helper = get_role(guild, "helper")
    e = base_embed(f"{emoji} {label}", f"Hey {member.mention}! Thanks for opening a ticket.\n\n{intro}\n\nClick 🔒 **Close** when you're done.")
    await ch.send(content=f"{member.mention} {helper.mention if helper else ''}", embed=e, view=TicketCloseView(),
                  allowed_mentions=discord.AllowedMentions(users=True, roles=True))
    await inter.followup.send(f"✅ Ticket created: {ch.mention}", ephemeral=True)
    await mod_log(guild, base_embed("🎫 Ticket opened", f"{member.mention} opened {ch.mention} ({label})"))


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        styles = {"support": discord.ButtonStyle.primary, "bug": discord.ButtonStyle.danger,
                  "creator": discord.ButtonStyle.success, "report": discord.ButtonStyle.secondary}
        for kind, (emoji, label, _) in TICKET_TYPES.items():
            b = discord.ui.Button(label=label, emoji=emoji, style=styles[kind], custom_id=f"rac:ticket:{kind}")
            b.callback = self._cb(kind)
            self.add_item(b)

    @staticmethod
    def _cb(kind: str):
        async def callback(inter: discord.Interaction):
            await open_ticket(inter, kind)
        return callback


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="rac:ticket_close")
    async def close(self, inter: discord.Interaction, _):
        ch = inter.channel
        owner_ok = ch.topic and f"owner:{inter.user.id}" in ch.topic
        if not (owner_ok or is_staff(inter.user)):
            return await inter.response.send_message("Only staff or the ticket owner can close this.", ephemeral=True)
        await inter.response.send_message("🔒 Closing this ticket in 5 seconds… saving transcript.")
        lines = []
        async for m in ch.history(limit=1000, oldest_first=True):
            ts = m.created_at.strftime("%Y-%m-%d %H:%M")
            content = m.content or ""
            if m.embeds:
                content += " [embed: " + (m.embeds[0].title or "") + "]"
            if m.attachments:
                content += " " + " ".join(a.url for a in m.attachments)
            lines.append(f"[{ts}] {m.author}: {content}")
        file = discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"{ch.name}.txt")
        await mod_log(inter.guild, base_embed("🔒 Ticket closed", f"`{ch.name}` closed by {inter.user.mention}"), file=file)
        await asyncio.sleep(5)
        try:
            await ch.delete(reason=f"Ticket closed by {inter.user}")
        except discord.HTTPException:
            pass


# ============================================================ SLASH COMMANDS
@bot.tree.command(name="setup", description="Build the full Roll a Cloud server (roles, channels, permissions, content)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def setup_cmd(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True, thinking=True)
    guild = inter.guild
    gs = gstate(guild.id)
    made_roles = made_channels = 0
    R: dict[str, discord.Role] = {}

    # ---- roles (created top -> bottom; new roles always land just above @everyone)
    all_roles = [(k, n, c, h, p) for k, n, c, h, p in ROLES] + \
                [(k, n, 0, False, P.none()) for k, n, _, _ in PING_ROLES]
    for key, name, color, hoist, perms in all_roles:
        role = guild.get_role(gs["roles"].get(key, 0)) or discord.utils.get(guild.roles, name=name)
        if role is None:
            role = await guild.create_role(name=name, colour=discord.Colour(color), hoist=hoist, permissions=perms,
                                           mentionable=key.startswith("ping_"), reason="Roll a Cloud setup")
            made_roles += 1
        R[key] = role
        gs["roles"][key] = role.id
    save_state()
    try:
        await guild.me.add_roles(R["bots"])
    except discord.HTTPException:
        pass

    # ---- categories & channels
    new_text: list[tuple[str, discord.TextChannel]] = []
    for cat_key, cat_name, cat_preset, channels in LAYOUT:
        ow = build_overwrites(guild, R, cat_preset)
        cat = guild.get_channel(gs["channels"].get(cat_key, 0)) or discord.utils.get(guild.categories, name=cat_name)
        if cat is None:
            cat = await guild.create_category(cat_name, overwrites=ow, reason="Roll a Cloud setup")
            made_channels += 1
        else:
            await cat.edit(overwrites=ow)
        gs["channels"][cat_key] = cat.id

        for key, name, kind, preset in channels:
            ow = build_overwrites(guild, R, preset)
            ch = guild.get_channel(gs["channels"].get(key, 0)) or discord.utils.get(cat.channels, name=name)
            if ch is None:
                if kind == "voice":
                    ch = await guild.create_voice_channel(name, category=cat, overwrites=ow)
                else:
                    kw = dict(category=cat, overwrites=ow, topic=TOPICS.get(key, ""),
                              slowmode_delay=SLOWMODE.get(key, 0))
                    if kind == "news" and "NEWS" in guild.features:
                        kw["news"] = True
                    ch = await guild.create_text_channel(name, **kw)
                    new_text.append((key, ch))
                made_channels += 1
            else:
                await ch.edit(overwrites=ow)
            gs["channels"][key] = ch.id
        save_state()

    # ---- content (after every channel exists, so mentions work)
    for key, ch in new_text:
        try:
            await post_content(guild, key, ch)
        except discord.HTTPException as e:
            log.warning("content for %s failed: %s", key, e)

    # ---- safety settings
    try:
        await guild.edit(verification_level=discord.VerificationLevel.medium,
                         explicit_content_filter=discord.ContentFilter.all_members,
                         default_notifications=discord.NotificationLevel.only_mentions)
    except discord.HTTPException:
        pass

    # ---- give everyone who's not linked the Unverified role
    for m in guild.members:
        if not m.bot and str(m.id) not in LINKS and R["unverified"] not in m.roles and R["verified"] not in m.roles:
            try:
                await m.add_roles(R["unverified"])
            except discord.HTTPException:
                pass

    asyncio.create_task(update_stat_channels(guild))
    await inter.followup.send(
        f"☁️ **Roll a Cloud server ready!**\n• {made_roles} roles created\n• {made_channels} channels/categories created\n"
        f"• Content posted in {len(new_text)} channels\n\n"
        f"👉 Give yourself **{R['owner'].name}** and **{R['verified'].name}**, then check {mention(guild, 'welcome')}.\n"
        + ("" if PLACE_ID else "⚠️ `ROBLOX_PLACE_ID` isn't set — game stats are disabled."),
        ephemeral=True)


@bot.tree.command(name="stats", description="Live Roll a Cloud stats from Roblox")
async def stats_cmd(inter: discord.Interaction):
    await inter.response.defer(thinking=True)
    s = await bot.fetch_stats()
    if not s:
        return await inter.followup.send("⚠️ Stats unavailable right now (or ROBLOX_PLACE_ID not set).")
    e = base_embed(f"📊 {s['name']}", f"[Play now]({GAME_URL})")
    e.add_field(name="🟢 Playing", value=f"{s['playing']:,}")
    e.add_field(name="👁️ Visits", value=f"{s['visits']:,}")
    e.add_field(name="⭐ Favorites", value=f"{s['favorites']:,}")
    e.add_field(name="👍 Likes", value=f"{s['up']:,}  ({s['likes_pct'] if s['likes_pct'] is not None else '—'}%)")
    e.add_field(name="👎 Dislikes", value=f"{s['down']:,}")
    if s.get("group_members") is not None:
        e.add_field(name="🏠 Group members", value=f"{s['group_members']:,}")
    if s.get("updated"):
        try:
            t = dt.datetime.fromisoformat(s["updated"].replace("Z", "+00:00"))
            e.add_field(name="🆕 Last update", value=f"<t:{int(t.timestamp())}:R>")
        except ValueError:
            pass
    icon = await bot.rbx.game_icon(bot.universe_id) if bot.universe_id else None
    if icon:
        e.set_thumbnail(url=icon)
    await inter.followup.send(embed=e, view=play_button_view())


@bot.tree.command(name="whois", description="See the Roblox account linked to a member")
@app_commands.guild_only()
async def whois_cmd(inter: discord.Interaction, member: discord.Member):
    link = LINKS.get(str(member.id))
    if not link:
        return await inter.response.send_message(f"{member.mention} hasn't linked a Roblox account.", ephemeral=True)
    await inter.response.defer(thinking=True)
    e = base_embed(f"{link['display']} (@{link['name']})",
                   f"Discord: {member.mention}\nRoblox ID: `{link['roblox_id']}`\n"
                   f"[Open Roblox profile](https://www.roblox.com/users/{link['roblox_id']}/profile)")
    try:
        t = dt.datetime.fromisoformat(link["verified_at"])
        e.add_field(name="Verified", value=f"<t:{int(t.timestamp())}:R>")
    except (KeyError, ValueError):
        pass
    head = await bot.rbx.headshot(link["roblox_id"])
    if head:
        e.set_thumbnail(url=head)
    await inter.followup.send(embed=e)


@bot.tree.command(name="update", description="Refresh your nickname and VIP role from Roblox")
@app_commands.guild_only()
async def update_cmd(inter: discord.Interaction):
    link = LINKS.get(str(inter.user.id))
    if not link:
        return await inter.response.send_message(f"You're not verified yet — go to {mention(inter.guild, 'verify')}.", ephemeral=True)
    await inter.response.defer(ephemeral=True, thinking=True)
    info = await bot.rbx.user(link["roblox_id"])
    if info:
        link.update(name=info["name"], display=info.get("displayName") or info["name"])
        save_json(LINKS_FILE, LINKS)
    notes = await apply_member_roles(inter.guild, inter.user, link)
    await inter.followup.send("✅ Updated!" + ("\n" + "\n".join(notes) if notes else ""), ephemeral=True)


@bot.tree.command(name="unlink", description="[Staff] Remove a member's Roblox link")
@app_commands.default_permissions(moderate_members=True)
@app_commands.guild_only()
async def unlink_cmd(inter: discord.Interaction, member: discord.Member):
    if not LINKS.pop(str(member.id), None):
        return await inter.response.send_message("This member isn't linked.", ephemeral=True)
    save_json(LINKS_FILE, LINKS)
    v, u, vip = get_role(inter.guild, "verified"), get_role(inter.guild, "unverified"), get_role(inter.guild, "vip")
    try:
        await member.remove_roles(*[r for r in (v, vip) if r and r in member.roles], reason=f"Unlinked by {inter.user}")
        if u:
            await member.add_roles(u)
    except discord.HTTPException:
        pass
    await inter.response.send_message(f"🔓 {member.mention} unlinked.", ephemeral=True)
    await mod_log(inter.guild, base_embed("🔓 Member unlinked", f"{member.mention} by {inter.user.mention}"))


@bot.tree.command(name="refreshstats", description="[Admin] Force the stats channels to update")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def refresh_cmd(inter: discord.Interaction):
    await inter.response.send_message("🔄 Updating stats channels…", ephemeral=True)
    bot._stats_cache = (0.0, None)
    await update_stat_channels(inter.guild)


@bot.tree.error
async def on_app_error(inter: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("command error", exc_info=error)
    text = "❌ Something went wrong. Check that my role has **Administrator** and is high in the role list."
    try:
        if inter.response.is_done():
            await inter.followup.send(text, ephemeral=True)
        else:
            await inter.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass


# ============================================================ EVENTS
@bot.event
async def on_ready():
    log.info("Logged in as %s in %d server(s)", bot.user, len(bot.guilds))
    await bot.change_presence(activity=discord.Game(name="Roll a Cloud ☁️"))


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        r = get_role(member.guild, "bots")
        if r:
            await member.add_roles(r)
        return
    link = LINKS.get(str(member.id))
    if link:  # rejoining member: restore verification
        await apply_member_roles(member.guild, member, link)
    else:
        r = get_role(member.guild, "unverified")
        if r:
            try:
                await member.add_roles(r, reason="New member")
            except discord.HTTPException:
                pass


@bot.event
async def on_message(msg: discord.Message):
    if msg.author.bot or not msg.guild:
        return
    chans = gstate(msg.guild.id)["channels"]
    try:
        if msg.channel.id == chans.get("suggestions"):
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            await msg.create_thread(name=f"💬 {msg.author.display_name}'s suggestion"[:100])
        elif msg.channel.id == chans.get("bug_reports"):
            await msg.add_reaction("👀")
            await msg.create_thread(name=f"🐛 Bug by {msg.author.display_name}"[:100])
    except discord.HTTPException:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set the DISCORD_TOKEN environment variable first.")
    bot.run(TOKEN)
