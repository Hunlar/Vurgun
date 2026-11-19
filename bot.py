"""
KVP GAME
========
Kurtlar Vadisi temali gizli roller oyunu
python-telegram-bot==21.6 | Python 3.10+
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, Callable, Awaitable

import aiosqlite
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest, TelegramError
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)
from languages import LANGUAGES, get_text, get_role_text, get_achievement_text

# ══════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
DB_PATH          = os.environ.get("DB_PATH", "mafia.db")
LOBBY_TIMEOUT    = 60     # seconds to join
NIGHT_TIMEOUT    = 50
DAY_TIMEOUT      = 60
VOTE_TIMEOUT     = 45
DEFENSE_TIMEOUT  = 25
MIN_PLAYERS      = 4
MAX_PLAYERS      = 15
MAX_EXTEND       = 2      # max lobby extensions
EXTEND_SECS      = 30

# Majburiy kanal a'zoligi. Heroku Config Vars orqali sozlanadi.
# REQUIRED_CHANNEL: @kanal_username yoki -100... ko'rinishidagi kanal ID.
# CHANNEL_JOIN_URL: foydalanuvchi kanalga kirishi uchun https://t.me/... havolasi.
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "").strip()
CHANNEL_JOIN_URL = os.environ.get("CHANNEL_JOIN_URL", "").strip()
# Bot sahibi Telegram ID'leri: Heroku Config Vars > OWNER_IDS.
OWNER_IDS = {
    int(value.strip())
    for value in os.environ.get("OWNER_IDS", os.environ.get("ADMIN_IDS", "")).split(",")
    if value.strip().lstrip("-").isdigit()
}

SUPPORT_URL = os.environ.get("SUPPORT_URL", "").strip()


AI_NAMES = [
    "🤖 Alice", "🤖 Bob", "🤖 Carlos", "🤖 Diana",
    "🤖 Erik", "🤖 Fatima", "🤖 George", "🤖 Hana",
]

# Kurtlar Vadisi karakter görselleri (web aramasında bulunan görsel kaynakları).
# Görsel erişilemezse bot rol metnini fotoğrafsız göndermeye geri döner.
ROLE_IMAGES = {
    "fuqaro": "https://www.bursahakimiyet.com.tr/static/2026/01/15/kurtlar-vadisi-efsanesinin-20-yili-1768470442-564_large.webp",
    "mafia": "https://cdn.karar.com/news/1389096.jpg",
    "don": "https://www.detaykibris.com/d/gallery/3148_20.jpg",
    "komissar": "https://i.pinimg.com/originals/4c/48/e7/4c48e771ea99126403624560449cc72d.jpg",
    "shifokor": "https://i.pinimg.com/736x/b0/5e/71/b05e718d3d2d94f231671eb26e348799.jpg",
    "guvoh": "https://i.pinimg.com/736x/2b/ce/24/2bce2410d1a0e038c1886937386d9a03.jpg",
    "kamikaze": "https://i1.sndcdn.com/artworks-000069787979-c3vbuc-t1080x1080.jpg",
    "suicid": "https://cdn.karar.com/news/1389096.jpg",
}

# Kurtlar Vadisi'nden ölüm/cenaze atmosferli sahne görselleri.
# Kaynaklar IMAGE_SOURCES.md içinde listelenmiştir.
DEATH_SCENE_IMAGES = [
    "https://img.memurlar.net/galeri/3980/d801bd9a-8f47-e311-a39c-14feb5cc1801.jpg?width=800",
    "https://iv.okcdn.ru/i?fn=external_8&r=BEH7dJIjJJO0pjyIf46vcZ9AfED7ECzB4qghX2xk2M0qvfFUHBkc-axEloUKxdMNjrS5CqdNleAEysdLnA60zUgQ",
    "https://image.kanald.com.tr/i/kanald/100/1200x0/56b44c756ce43628c0a1b2f2.jpg",
]

# ══════════════════════════════════════════════════════════════════
# ENUMS & MODELS
# ══════════════════════════════════════════════════════════════════
class Role(Enum):
    FUQARO   = "fuqaro"
    MAFIA    = "mafia"
    DON      = "don"
    KOMISSAR = "komissar"
    SHIFOKOR = "shifokor"
    GUVOH    = "guvoh"
    KAMIKAZE = "kamikaze"
    SUICID   = "suicid"

    @property
    def emoji(self) -> str:
        # Görsel kimlik KVP GAME'e uyarlandı; enum değerleri korunur.
        return {
            Role.FUQARO:   "👥",
            Role.MAFIA:    "🔫",
            Role.DON:      "👑",
            Role.KOMISSAR: "🕶️",
            Role.SHIFOKOR: "🛡️",
            Role.GUVOH:    "👁️",
            Role.KAMIKAZE: "💣",
            Role.SUICID:   "🎭",
        }[self]

    @property
    def uz_name(self) -> str:
        # Rol adları değiştirilirken oyun motorunun beklediği enum sabitleri korunur.
        return {
            Role.FUQARO:   "Halk",
            Role.MAFIA:    "Konsey Üyesi",
            Role.DON:      "Baron Mehmet Karahanlı",
            Role.KOMISSAR: "Polat Alemdar",
            Role.SHIFOKOR: "Aslan Akbey",
            Role.GUVOH:    "Abdülhey Çoban",
            Role.KAMIKAZE: "Memati Baş",
            Role.SUICID:   "Hain",
        }[self]

    @property
    def is_mafia_team(self) -> bool:
        return self in (Role.MAFIA, Role.DON)

    @property
    def has_night_action(self) -> bool:
        return self in (
            Role.MAFIA, Role.DON, Role.KOMISSAR,
            Role.SHIFOKOR, Role.GUVOH
        )

    @property
    def description(self) -> str:
        # Açıklamalar mevcut rol yeteneklerini aynen korur.
        return {
            Role.FUQARO:   "Özel bir yeteneğin yok. Konsey üyelerini bul ve gündüz oylamasında saf dışı bırak.",
            Role.MAFIA:    "Konsey üyesisin. Her gece Baron ile birlikte bir oyuncuyu hedef alırsın.",
            Role.DON:      "Konseyin liderisin. Her gece öldürme hedefini seçersin. Polat'ın sıradan rol kontrolünde kimliğin gizli kalır.",
            Role.KOMISSAR: "Polat Alemdar olarak her gece bir oyuncuyu soruşturursun. Konsey üyesi veya Baron olup olmadığını öğrenirsin.",
            Role.SHIFOKOR: "Aslan Akbey olarak her gece bir oyuncuyu korursun. Hedef aldığın oyuncuyu gece saldırısından kurtarabilirsin.",
            Role.GUVOH:    "Abdülhey Çoban olarak her gece bir oyuncuyu takip eder, gece yaptığı hareket hakkında bilgi edinirsin.",
            Role.KAMIKAZE: "Memati Baş olarak gündüz oylamasıyla elenirsen yanında rastgele bir oyuncuyu da götürürsün.",
            Role.SUICID:   "Hain olarak gündüz oylamasıyla elenirsen kendi zaferini kazanırsın.",
        }[self]


ROLE_POWER = {
    Role.DON: 100, Role.KOMISSAR: 90, Role.SHIFOKOR: 80,
    Role.GUVOH: 70, Role.KAMIKAZE: 60, Role.MAFIA: 50,
    Role.SUICID: 40, Role.FUQARO: 10,
}


class Phase(Enum):
    LOBBY   = auto()
    STARTING = auto()
    NIGHT   = auto()
    DAY     = auto()
    VOTING  = auto()
    DEFENSE = auto()
    ENDED   = auto()


@dataclass
class Player:
    user_id:      int
    name:         str
    role:         Role = Role.FUQARO
    is_alive:     bool = True
    is_ai:        bool = False
    night_target: Optional[int] = None
    has_acted:    bool = False

    def reset_night(self):
        self.night_target = None
        self.has_acted = False

    @property
    def tag(self) -> str:
        dead = "💀 " if not self.is_alive else ""
        return f"{dead}{self.name}"


@dataclass
class NightResult:
    killed:   list[int] = field(default_factory=list)
    healed:   list[int] = field(default_factory=list)
    checked:  dict[int, bool] = field(default_factory=dict)   # uid -> is_mafia
    witnessed: dict[int, str] = field(default_factory=dict)   # guvoh_uid -> info_text
    messages: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
# ROLE DISTRIBUTION
# ══════════════════════════════════════════════════════════════════
def build_role_list(n: int) -> list[Role]:
    """Build balanced role list for n players. Don never goes to AI."""
    if n < 4:
        raise ValueError("Kamida 4 o'yinchi kerak")

    if n == 4:
        return [Role.DON, Role.KOMISSAR, Role.FUQARO, Role.FUQARO]
    elif n == 5:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.FUQARO, Role.FUQARO]
    elif n == 6:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.FUQARO, Role.FUQARO]
    elif n == 7:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.FUQARO, Role.FUQARO]
    elif n == 8:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.FUQARO, Role.FUQARO]
    elif n == 9:
        return [Role.DON, Role.MAFIA, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.FUQARO, Role.FUQARO]
    elif n == 10:
        return [Role.DON, Role.MAFIA, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.SUICID, Role.FUQARO, Role.FUQARO]
    else:
        mafia_n = max(2, n // 4)
        roles = [Role.DON] + [Role.MAFIA] * mafia_n
        roles += [Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.SUICID]
        roles += [Role.FUQARO] * (n - len(roles))
        return roles


def assign_roles(players: list[Player]) -> None:
    """Assign roles. Don NEVER goes to AI player."""
    n = len(players)
    roles = build_role_list(n)
    random.shuffle(roles)

    humans = [p for p in players if not p.is_ai]
    ais    = [p for p in players if p.is_ai]

    # Ensure Don goes to human
    if Role.DON in roles:
        don_idx = roles.index(Role.DON)
        # If don_idx would land on AI, swap
        if don_idx >= len(humans):
            # Find a non-don role at human position
            for i in range(len(humans)):
                if roles[i] != Role.DON:
                    roles[i], roles[don_idx] = roles[don_idx], roles[i]
                    break

    # Assign: first humans then AIs
    all_ordered = humans + ais
    for p, r in zip(all_ordered, roles):
        p.role = r


# ══════════════════════════════════════════════════════════════════
# ROLE ENGINE
# ══════════════════════════════════════════════════════════════════
class RoleEngine:

    @staticmethod
    def resolve_night(players: dict[int, Player], lang: str = "tr") -> NightResult:
        res = NightResult()
        alive = {uid: p for uid, p in players.items() if p.is_alive}

        mafia_votes: dict[int, int] = {}
        heal_target: Optional[int] = None
        komissar_checks: list[tuple[int, int]] = []  # (komissar_uid, target_uid)
        guvoh_watches: list[tuple[int, int]] = []    # (guvoh_uid, target_uid)

        for uid, p in alive.items():
            if not p.has_acted or p.night_target is None:
                continue
            t = p.night_target
            if t not in players or not players[t].is_alive:
                continue

            if p.role in (Role.MAFIA, Role.DON):
                mafia_votes[t] = mafia_votes.get(t, 0) + 1
            elif p.role == Role.SHIFOKOR:
                heal_target = t
            elif p.role == Role.KOMISSAR:
                komissar_checks.append((uid, t))
            elif p.role == Role.GUVOH:
                guvoh_watches.append((uid, t))

        # Heal
        if heal_target is not None:
            res.healed.append(heal_target)

        # Mafia kill (majority)
        if mafia_votes:
            kill_target = max(mafia_votes, key=lambda k: mafia_votes[k])
            if kill_target in res.healed:
                res.messages.append(get_text(lang, "doctor_saved"))
            else:
                players[kill_target].is_alive = False
                res.killed.append(kill_target)

        # Komissar checks
        for k_uid, t_uid in komissar_checks:
            is_mafia = players[t_uid].role.is_mafia_team
            # Don is immune: komissar sees "tinch" for Don
            if players[t_uid].role == Role.DON:
                is_mafia = False
            res.checked[t_uid] = is_mafia

        # Guvoh (witness) - learns what target did last night
        for g_uid, t_uid in guvoh_watches:
            target_p = players[t_uid]
            if target_p.role.is_mafia_team:
                if target_p.night_target and target_p.night_target in players:
                    victim_name = players[target_p.night_target].name
                    info = get_text(lang, "witness_mafia", name=target_p.name, victim=victim_name)
                else:
                    info = get_text(lang, "witness_no_target", name=target_p.name)
            elif target_p.role == Role.KOMISSAR and target_p.night_target:
                tname = players.get(target_p.night_target, Player(-1,"?")).name
                info = get_text(lang, "witness_check", name=target_p.name, target=tname)
            elif target_p.role == Role.SHIFOKOR and target_p.night_target:
                tname = players.get(target_p.night_target, Player(-1,"?")).name
                info = get_text(lang, "witness_heal", name=target_p.name, target=tname)
            else:
                info = get_text(lang, "witness_idle", name=target_p.name)
            res.witnessed[g_uid] = info

        return res

    @staticmethod
    def check_win(players: dict[int, Player]) -> Optional[str]:
        alive = [p for p in players.values() if p.is_alive]
        if not alive:
            if not players:
                return "draw"
            # Son hamlede herkes elenirse de rol gücü en yüksek oyuncu galip sayılır.
            winner = max(players.values(), key=lambda p: (ROLE_POWER[p.role], p.user_id))
            if winner.role == Role.SUICID:
                return "suicid"
            return "mafia" if winner.role.is_mafia_team else "town"

        # Son iki oyuncuda sayı eşitliği oyunu kilitlemez: rol gücü yüksek olan kazanır.
        if len(alive) in (1, 2):
            winner = max(alive, key=lambda p: (ROLE_POWER[p.role], p.user_id))
            if winner.role == Role.SUICID:
                return "suicid"
            return "mafia" if winner.role.is_mafia_team else "town"

        mafia_alive = [p for p in alive if p.role.is_mafia_team]
        town_alive  = [p for p in alive if not p.role.is_mafia_team]

        # Town wins: no mafia left
        if not mafia_alive:
            return "town"

        # Mafia wins: mafia >= town
        if len(mafia_alive) >= len(town_alive):
            return "mafia"

        return None

    @staticmethod
    def last_standing_winner_id(players: dict[int, Player]) -> Optional[int]:
        alive = [p for p in players.values() if p.is_alive]
        candidates = alive if len(alive) in (1, 2) else (list(players.values()) if not alive else [])
        if not candidates:
            return None
        return max(candidates, key=lambda p: (ROLE_POWER[p.role], p.user_id)).user_id


# ══════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════
SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id      TEXT PRIMARY KEY,
    chat_id      INTEGER,
    started_at   TEXT,
    ended_at     TEXT,
    winner       TEXT,
    player_count INTEGER DEFAULT 0,
    rounds       INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS player_stats (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT DEFAULT '',
    games        INTEGER DEFAULT 0,
    wins         INTEGER DEFAULT 0,
    losses       INTEGER DEFAULT 0,
    survived     INTEGER DEFAULT 0,
    elo          INTEGER DEFAULT 1000,
    streak       INTEGER DEFAULT 0,
    best_streak  INTEGER DEFAULT 0,
    role_wins    TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_elo ON player_stats(elo DESC);
CREATE TABLE IF NOT EXISTS user_languages (
    user_id INTEGER PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'tr'
);
CREATE TABLE IF NOT EXISTS group_languages (
    chat_id INTEGER PRIMARY KEY,
    language TEXT NOT NULL DEFAULT 'tr'
);
"""

ACHIEVEMENTS_DEF = {
    "first_win":   ("🏅", "Birinchi g'alaba",   "Birinchi o'yinni yut"),
    "veteran":     ("🎖️", "Veteran",             "10 ta o'yin o'yna"),
    "streak3":     ("🔥", "O'tda yonmoq",         "3 ta ketma-ket g'alaba"),
    "streak5":     ("⚡", "To'xtatib bo'lmas",    "5 ta ketma-ket g'alaba"),
    "mafia_boss":  ("👔", "Mafia Bosi",           "Mafia sifatida 5 marta yut"),
    "survivor":    ("🛡️", "Omon qoluvchi",        "5 ta o'yinda tirik qol"),
    "don_master":  ("🧠", "Don Ustasi",           "Don sifatida 3 marta yut"),
    "komissar_pro":("🔍", "Komissar Pro",         "3 ta Mafiayani fosh qil"),
}


class DB:
    def __init__(self):
        self._db: Optional[aiosqlite.Connection] = None

    async def connect(self):
        self._db = await aiosqlite.connect(DB_PATH)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("DB ulandi: %s", DB_PATH)

    async def close(self):
        if self._db:
            await self._db.close()

    async def get_language(self, user_id: int) -> str:
        async with self._db.execute(
            "SELECT language FROM user_languages WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["language"] if row and row["language"] in LANGUAGES else "tr"

    async def set_language(self, user_id: int, language: str) -> None:
        if language not in LANGUAGES:
            language = "tr"
        await self._db.execute(
            "INSERT INTO user_languages(user_id, language) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET language=excluded.language",
            (user_id, language),
        )
        await self._db.commit()

    async def get_group_language(self, chat_id: int) -> str:
        async with self._db.execute(
            "SELECT language FROM group_languages WHERE chat_id=?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["language"] if row and row["language"] in LANGUAGES else "tr"

    async def set_group_language(self, chat_id: int, language: str) -> None:
        if language not in LANGUAGES:
            language = "tr"
        await self._db.execute(
            "INSERT INTO group_languages(chat_id, language) VALUES(?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET language=excluded.language",
            (chat_id, language),
        )
        await self._db.commit()

    async def save_game(self, game_id: str, chat_id: int, player_count: int):
        await self._db.execute(
            "INSERT OR REPLACE INTO games(game_id,chat_id,started_at,player_count) VALUES(?,?,?,?)",
            (game_id, chat_id, datetime.utcnow().isoformat(), player_count)
        )
        await self._db.commit()

    async def end_game(self, game_id: str, winner: str, rounds: int):
        await self._db.execute(
            "UPDATE games SET ended_at=?,winner=?,rounds=? WHERE game_id=?",
            (datetime.utcnow().isoformat(), winner, rounds, game_id)
        )
        await self._db.commit()

    async def update_player(self, user_id: int, username: str, won: bool,
                             survived: bool, role: str, opponent_elo: int = 1000):
        # ensure exists
        await self._db.execute(
            "INSERT OR IGNORE INTO player_stats(user_id,username) VALUES(?,?)",
            (user_id, username)
        )
        async with self._db.execute(
            "SELECT * FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = dict(await c.fetchone())

        import math
        old_elo = row["elo"]
        expected = 1 / (1 + math.pow(10, (opponent_elo - old_elo) / 400))
        elo_change = round(32 * ((1.0 if won else 0.0) - expected))
        new_elo = max(100, old_elo + elo_change)

        new_streak = (row["streak"] + 1) if won else 0
        best_streak = max(row["best_streak"], new_streak)

        rw = json.loads(row["role_wins"])
        if won:
            rw[role] = rw.get(role, 0) + 1

        await self._db.execute(
            """UPDATE player_stats SET
               username=?, games=games+1, wins=wins+?, losses=losses+?,
               survived=survived+?, elo=?, streak=?, best_streak=?, role_wins=?
               WHERE user_id=?""",
            (username, 1 if won else 0, 0 if won else 1,
             1 if survived else 0, new_elo, new_streak, best_streak,
             json.dumps(rw), user_id)
        )
        await self._db.commit()
        return elo_change

    async def get_stats(self, user_id: int) -> Optional[dict]:
        async with self._db.execute(
            "SELECT * FROM player_stats WHERE user_id=?", (user_id,)
        ) as c:
            row = await c.fetchone()
        if not row:
            return None
        d = dict(row)
        d["role_wins"] = json.loads(d["role_wins"])
        return d

    async def get_leaderboard(self, limit=10) -> list:
        async with self._db.execute(
            """SELECT username,games,wins,elo,best_streak,
               ROUND(CAST(wins AS FLOAT)/MAX(games,1)*100,1) as wr
               FROM player_stats WHERE games>0
               ORDER BY elo DESC LIMIT ?""", (limit,)
        ) as c:
            return [dict(r) for r in await c.fetchall()]

    async def get_global(self) -> dict:
        async with self._db.execute(
            """SELECT COUNT(*) as total,
               SUM(CASE WHEN winner='town'  THEN 1 ELSE 0 END) as town,
               SUM(CASE WHEN winner='mafia' THEN 1 ELSE 0 END) as mafia,
               SUM(CASE WHEN winner='suicid'THEN 1 ELSE 0 END) as suicid,
               AVG(player_count) as avg_p, AVG(rounds) as avg_r
               FROM games WHERE winner IS NOT NULL"""
        ) as c:
            row = await c.fetchone()
        return dict(row) if row else {}

    async def get_owner_stats(self) -> dict:
        async with self._db.execute(
            """SELECT COUNT(*) AS total_games,
                      COUNT(DISTINCT chat_id) AS total_groups,
                      SUM(CASE WHEN winner IS NOT NULL THEN 1 ELSE 0 END) AS completed_games,
                      SUM(CASE WHEN julianday(started_at) >= julianday('now','-1 day') THEN 1 ELSE 0 END) AS games_24h
               FROM games"""
        ) as cur:
            result = dict(await cur.fetchone())
        async with self._db.execute("SELECT COUNT(*) AS total_users FROM player_stats") as cur:
            result.update(dict(await cur.fetchone()))
        live_games = [game for game in reg._g.values() if game.phase != Phase.ENDED]
        result["running_games"] = len(live_games)
        result["active_players"] = len({
            uid for game in live_games for uid, player in game.players.items()
            if uid > 0 and player.is_alive
        })
        return result

    def _get_achievements(self, stats: dict) -> list[str]:
        earned = []
        if stats["wins"] >= 1:           earned.append("first_win")
        if stats["games"] >= 10:         earned.append("veteran")
        if stats["streak"] >= 3:         earned.append("streak3")
        if stats["streak"] >= 5:         earned.append("streak5")
        if stats["survived"] >= 5:       earned.append("survivor")
        rw = stats["role_wins"]
        if rw.get("mafia",0)+rw.get("don",0) >= 5:   earned.append("mafia_boss")
        if rw.get("don",0) >= 3:         earned.append("don_master")
        return earned


db = DB()

# ══════════════════════════════════════════════════════════════════
# VOTE TRACKER
# ══════════════════════════════════════════════════════════════════
class VoteTracker:
    def __init__(self):
        self._v: dict[int, int] = {}

    def cast(self, voter: int, target: int) -> bool:
        new = voter not in self._v
        self._v[voter] = target
        return new

    def prev(self, voter: int) -> Optional[int]:
        return self._v.get(voter)

    def tally(self) -> dict[int, int]:
        t: dict[int, int] = {}
        for v in self._v.values():
            t[v] = t.get(v, 0) + 1
        return t

    def count(self) -> int:
        return len(self._v)

    def reset(self):
        self._v.clear()


# ══════════════════════════════════════════════════════════════════
# GAME MANAGER
# ══════════════════════════════════════════════════════════════════
SendFn = Callable[..., Awaitable[None]]


class Game:
    def __init__(self, chat_id: int, send_fn: SendFn, lang: str = "tr", app: Optional[Application] = None):
        self.chat_id   = chat_id
        self.lang      = lang if lang in LANGUAGES else "tr"
        self.game_id   = str(uuid.uuid4())[:6].upper()
        self.send      = send_fn
        self.app       = app
        self.phase     = Phase.LOBBY
        self.players:  dict[int, Player] = {}
        self.round     = 0
        self.host_id:  Optional[int] = None
        self.votes     = VoteTracker()
        self._task:    Optional[asyncio.Task] = None
        self._extends  = 0           # lobby extensions used
        self._lobby_remaining = LOBBY_TIMEOUT
        self._defense_id: Optional[int] = None

    # ── Lobby ──────────────────────────────────────────────────────
    def join(self, uid: int, name: str) -> bool:
        if uid in self.players or len(self.players) >= MAX_PLAYERS or self.phase != Phase.LOBBY:
            return False
        self.players[uid] = Player(uid, name)
        if not self.host_id:
            self.host_id = uid
        return True

    def leave(self, uid: int) -> bool:
        if uid not in self.players or self.phase != Phase.LOBBY:
            return False
        del self.players[uid]
        if self.host_id == uid and self.players:
            self.host_id = next(iter(self.players))
        return True

    def add_ai(self) -> bool:
        ai_n = sum(1 for p in self.players.values() if p.is_ai)
        if ai_n >= 6 or len(self.players) >= MAX_PLAYERS:
            return False
        name = next(
            (n for n in AI_NAMES if n not in {p.name for p in self.players.values()}),
            f"🤖 Bot{random.randint(10,99)}"
        )
        ai_id = -(1000 + ai_n)
        self.players[ai_id] = Player(ai_id, name, is_ai=True)
        return True

    def n(self) -> int:
        return len(self.players)

    def alive(self) -> list[Player]:
        return [p for p in self.players.values() if p.is_alive]

    def is_ended(self) -> bool:
        return self.phase == Phase.ENDED

    # ── Start lobby countdown ──────────────────────────────────────
    async def start_lobby_timer(self) -> None:
        """60s lobby timer with warnings at 30s and 10s."""
        remaining = LOBBY_TIMEOUT
        while remaining > 0:
            await asyncio.sleep(1)
            remaining -= 1
            if remaining == 30:
                await self.send(
                    self.chat_id,
                    get_text(self.lang, "lobby_30", count=self.n())
                )
            elif remaining == 10:
                await self.send(
                    self.chat_id,
                    get_text(self.lang, "lobby_10", count=self.n())
                )
            self._lobby_remaining = remaining

        # Time's up — auto start or cancel
        if self.phase != Phase.LOBBY:
            return
        if self.n() >= MIN_PLAYERS:
            await self.send(self.chat_id, get_text(self.lang, "lobby_auto_start"))
            await self._do_start()
        else:
            self.phase = Phase.ENDED
            await self.send(
                self.chat_id,
                get_text(self.lang, "lobby_cancel", count=self.n(), minimum=MIN_PLAYERS)
            )

    async def extend_lobby(self, uid: int) -> str:
        if uid != self.host_id:
            try:
                # check admin
                pass
            except Exception:
                pass
        if self._extends >= MAX_EXTEND:
            return get_text(self.lang, "extend_limit", maximum=MAX_EXTEND)
        if self.phase != Phase.LOBBY:
            return get_text(self.lang, "lobby_not_active")
        self._extends += 1
        self._lobby_remaining += EXTEND_SECS
        return get_text(self.lang, "lobby_extended", seconds=EXTEND_SECS, count=self._extends, maximum=MAX_EXTEND)

    # ── Start game ────────────────────────────────────────────────
    async def start_game(self) -> str:
        if self.phase != Phase.LOBBY:
            return get_text(self.lang, "lobby_not_active")
        if self.n() < MIN_PLAYERS:
            return get_text(self.lang, "not_enough_players", minimum=MIN_PLAYERS, count=self.n())
        await self._do_start()
        return "ok"

    async def _do_start(self) -> None:
        if self.phase != Phase.LOBBY:
            return
        self._cancel()
        assign_roles(list(self.players.values()))
        self.phase = Phase.STARTING

        await db.save_game(self.game_id, self.chat_id, self.n())

        # Announce
        await self.send(
            self.chat_id,
            get_text(self.lang, "game_started", game_id=self.game_id, count=self.n())
        )
        # Rollar avtomatik start, qo'lda start va callback orqali start holatlarining
        # barchasida shaxsiy chatga yuboriladi.
        missing_dm = []
        delivered_dm = []
        if self.app is not None:
            for player in self.players.values():
                if player.is_ai or player.user_id < 0:
                    continue
                player_lang = await db.get_language(player.user_id)
                role_text = self.role_info(player.user_id, player_lang)
                photo = ROLE_IMAGES.get(player.role.value)
                try:
                    delivered = False
                    if photo:
                        try:
                            await self.app.bot.send_photo(
                                chat_id=player.user_id, photo=photo, caption=role_text,
                                parse_mode=ParseMode.HTML
                            )
                            delivered = True
                        except TelegramError as photo_error:
                            log.warning("Rol görseli gönderilemedi, metin deneniyor: %s", photo_error)
                    if not delivered:
                        await self.app.bot.send_message(
                            chat_id=player.user_id, text=role_text,
                            parse_mode=ParseMode.HTML
                        )
                    delivered_dm.append(player.user_id)
                except Forbidden:
                    missing_dm.append(player.name)
                except TelegramError as exc:
                    log.warning("Rol DM yuborilmadi (%s): %s", player.user_id, exc)
                    missing_dm.append(player.name)
        await self._begin_night()
        # Night state is fully initialized before action buttons are sent, avoiding a race
        # where a fast player action could be reset by the night initializer.
        if self.app is not None:
            for player_uid in delivered_dm:
                player = self.players.get(player_uid)
                if player and player.role.has_night_action and player.is_alive:
                    await send_night_dm(self.app, self, player_uid)
            if missing_dm:
                try:
                    me = await self.app.bot.get_me()
                    start_link = f"https://t.me/{me.username}?start=play"
                    await self.send(
                        self.chat_id,
                        get_text(self.lang, "dm_missing", names=', '.join(missing_dm), link=start_link)
                    )
                except TelegramError:
                    log.exception("DM yetkazish ogohlantirishi yuborilmadi")

    # ── Night ─────────────────────────────────────────────────────
    async def _begin_night(self) -> None:
        self.phase = Phase.NIGHT
        self.round += 1
        for p in self.players.values():
            p.reset_night()

        await self.send(
            self.chat_id,
            get_text(self.lang, "night_started", round=self.round, seconds=NIGHT_TIMEOUT)
        )

        # AI actions
        await self._ai_night()

        self._cancel()
        self._task = asyncio.create_task(self._night_timeout())

    async def _ai_night(self) -> None:
        alive_others_ids = [uid for uid, p in self.players.items() if p.is_alive]
        for uid, p in self.players.items():
            if not p.is_alive or not p.is_ai or not p.role.has_night_action:
                continue
            await asyncio.sleep(random.uniform(1, 3))
            candidates = [i for i in alive_others_ids if i != uid]
            if candidates:
                p.night_target = random.choice(candidates)
                p.has_acted = True

    async def _night_timeout(self) -> None:
        await asyncio.sleep(NIGHT_TIMEOUT)
        await self._resolve_night()

    async def record_action(self, uid: int, target_id: int, lang: Optional[str] = None) -> str:
        display_lang = lang if lang in LANGUAGES else self.lang
        if self.phase != Phase.NIGHT:
            return get_text(display_lang, "phase_night")
        p = self.players.get(uid)
        if not p or not p.is_alive:
            return get_text(display_lang, "not_alive")
        if not p.role.has_night_action:
            return get_text(display_lang, "no_night_action")
        t = self.players.get(target_id)
        if not t or not t.is_alive:
            return get_text(display_lang, "invalid_target")
        if target_id == uid and p.role != Role.SHIFOKOR:
            return get_text(display_lang, "self_target")
        p.night_target = target_id
        p.has_acted = True
        if self._all_acted():
            self._cancel()
            asyncio.create_task(self._resolve_night())
        return get_text(display_lang, "action_recorded", name=t.name)

    async def skip_action(self, uid: int, lang: Optional[str] = None) -> str:
        display_lang = lang if lang in LANGUAGES else self.lang
        if self.phase != Phase.NIGHT:
            return get_text(display_lang, "phase_night")
        player = self.players.get(uid)
        if not player or not player.is_alive or not player.role.has_night_action:
            return get_text(display_lang, "no_night_action")
        player.night_target = None
        player.has_acted = True
        if self._all_acted():
            self._cancel()
            asyncio.create_task(self._resolve_night())
        return get_text(display_lang, "night_skip_done")

    def _all_acted(self) -> bool:
        for p in self.players.values():
            if p.is_alive and p.role.has_night_action and not p.has_acted:
                return False
        return True

    async def _resolve_night(self) -> None:
        if self.phase != Phase.NIGHT:
            return

        res = RoleEngine.resolve_night(self.players, self.lang)
        lines = [get_text(self.lang, "dawn") + "\n"]

        if not res.killed:
            lines.append(get_text(self.lang, "nobody_died"))
        lines.extend(res.messages)
        await self.send(self.chat_id, "\n".join(lines))

        # KVP ölüm sahnesi: elenen her oyuncu grup içinde rol görseliyle duyurulur.
        for uid in res.killed:
            p = self.players[uid]
            role_name, _ = get_role_text(self.lang, p.role.value)
            death_text = get_text(
                self.lang, "player_killed", name=p.name,
                emoji=p.role.emoji, role=role_name
            )
            quips = [
                "Bu gece dosya kapandı... Mekân yine Polat'a kaldı. 🕶️",
                "Son sözü söyleyemedi; sahneden erken ayrıldı. 🎬",
                "Konsey kararını verdi. İtiraz makamı da kapalı. ⚰️",
                "Kurtlar sofrasında bu gece bir sandalye eksildi. 🐺",
            ]
            death_caption = death_text
            if self.lang == "tr":
                death_caption += "\n\n" + random.choice(quips)
            image = random.choice(DEATH_SCENE_IMAGES) if DEATH_SCENE_IMAGES else ROLE_IMAGES.get(p.role.value)
            sent = False
            if self.app is not None and image:
                try:
                    await self.app.bot.send_photo(
                        chat_id=self.chat_id, photo=image,
                        caption=death_caption, parse_mode=ParseMode.HTML
                    )
                    sent = True
                except TelegramError as exc:
                    log.warning("Ölüm sahnesi görseli gönderilemedi: %s", exc)
            if not sent:
                await self.send(self.chat_id, death_caption)

        # Private: komissar results
        for k_uid, p in self.players.items():
            if p.role == Role.KOMISSAR and p.is_alive and p.night_target in res.checked:
                is_m = res.checked[p.night_target]
                t_name = self.players[p.night_target].name
                player_lang = await db.get_language(k_uid)
                result_text = get_text(player_lang, "mafia_result" if is_m else "town_result")
                await self.send(k_uid, get_text(player_lang, "check_result", name=t_name, result=result_text))

        # Private: guvoh results
        for g_uid, info in res.witnessed.items():
            player_lang = await db.get_language(g_uid)
            await self.send(g_uid, get_text(player_lang, "witness_result", info=info))

        winner = RoleEngine.check_win(self.players)
        if winner:
            winner_id = RoleEngine.last_standing_winner_id(self.players)
            await self._end_game(winner, winner_id=winner_id)
        else:
            await self._begin_day()

    # ── Day ───────────────────────────────────────────────────────
    async def _begin_day(self) -> None:
        self.phase = Phase.DAY

        alive_list = "\n".join(
            f"  ✅ {p.tag}" for p in self.players.values() if p.is_alive
        )
        await self.send(
            self.chat_id,
            get_text(self.lang, "day_started", round=self.round, count=len(self.alive()),
                     players=alive_list, seconds=DAY_TIMEOUT)
        )

        self._cancel()
        self._task = asyncio.create_task(self._day_timeout())

    async def _day_timeout(self) -> None:
        await asyncio.sleep(DAY_TIMEOUT)
        await self._begin_voting()

    async def skip_day(self, uid: int, is_admin: bool = False) -> str:
        if uid != self.host_id and not is_admin:
            return get_text(self.lang, "skip_day_denied")
        if self.phase != Phase.DAY:
            return get_text(self.lang, "not_day")
        self._cancel()
        asyncio.create_task(self._begin_voting())
        return get_text(self.lang, "go_vote")

    # ── Voting ────────────────────────────────────────────────────
    async def _begin_voting(self) -> None:
        self.phase = Phase.VOTING
        self.votes.reset()

        alive = self.alive()
        targets = [(p.user_id, f"{p.role.emoji if False else ''} {p.name}") for p in alive]

        # Simple display without role emoji (roles hidden)
        targets_clean = [(p.user_id, p.name) for p in alive]

        buttons = [
            [InlineKeyboardButton(f"🗳 {name}", callback_data=f"vote:{uid}")]
            for uid, name in targets_clean
        ]
        buttons.append([InlineKeyboardButton(get_text(self.lang, "vote_skip"), callback_data="vote:skip")])

        await self.send(
            self.chat_id,
            get_text(self.lang, "voting_started", seconds=VOTE_TIMEOUT),
            markup=InlineKeyboardMarkup(buttons)
        )

        # AI votes
        for uid, p in self.players.items():
            if p.is_alive and p.is_ai:
                await asyncio.sleep(random.uniform(1, 3))
                candidates = [i for i in [q.user_id for q in self.alive()] if i != uid]
                if candidates:
                    self.votes.cast(uid, random.choice(candidates))

        self._cancel()
        self._task = asyncio.create_task(self._vote_timeout())

    def cast_vote(self, voter: int, target: int, lang: Optional[str] = None) -> str:
        display_lang = lang if lang in LANGUAGES else self.lang
        if self.phase != Phase.VOTING:
            return get_text(display_lang, "not_voting")
        vp = self.players.get(voter)
        tp = self.players.get(target)
        if not vp or not vp.is_alive:
            return get_text(display_lang, "cannot_vote")
        if not tp or not tp.is_alive:
            return get_text(display_lang, "invalid_target")
        if voter == target:
            return get_text(display_lang, "self_vote")
        prev = self.votes.prev(voter)
        self.votes.cast(voter, target)
        if prev and prev != target and prev in self.players:
            return get_text(display_lang, "vote_changed", name=tp.name)
        return get_text(display_lang, "vote_cast", name=tp.name)

    async def _vote_timeout(self) -> None:
        await asyncio.sleep(VOTE_TIMEOUT)
        await self._resolve_vote()

    async def _resolve_vote(self) -> None:
        if self.phase != Phase.VOTING:
            return

        tally = self.votes.tally()
        if not tally:
            await self.send(self.chat_id, get_text(self.lang, "no_votes"))
            await self._maybe_continue()
            return

        # Show results
        tally_lines = [
            get_text(self.lang, "vote_line", name=self.players[uid].name, count=v)
            for uid, v in sorted(tally.items(), key=lambda x: -x[1])
            if uid in self.players
        ]
        max_v = max(tally.values())
        leaders = [uid for uid, v in tally.items() if v == max_v and uid in self.players]

        if len(leaders) > 1:
            names = ", ".join(self.players[uid].name for uid in leaders)
            await self.send(
                self.chat_id,
                get_text(self.lang, "vote_results") + "\n" + "\n".join(tally_lines) +
                "\n\n" + get_text(self.lang, "vote_tie", names=names)
            )
            await self._maybe_continue()
            return

        condemned = leaders[0]
        p = self.players[condemned]

        # Suicid: wins immediately
        if p.role == Role.SUICID:
            p.is_alive = False
            await self.send(
                self.chat_id,
                get_text(self.lang, "vote_results") + "\n" + "\n".join(tally_lines) +
                "\n\n" + get_text(self.lang, "traitor_eliminated", name=p.name)
            )
            await self._end_game("suicid", winner_id=condemned)
            return

        # Kamikaze: kills a random extra player
        kamikaze_extra: Optional[int] = None
        if p.role == Role.KAMIKAZE:
            others = [uid for uid, q in self.players.items()
                      if q.is_alive and uid != condemned]
            if others:
                kamikaze_extra = random.choice(others)

        # Defense phase
        self._defense_id = condemned
        self.phase = Phase.DEFENSE

        msg = get_text(
            self.lang, "defense_prompt", votes="\n".join(tally_lines),
            name=p.name, seconds=DEFENSE_TIMEOUT
        )
        await self.send(self.chat_id, msg)

        self._cancel()
        self._task = asyncio.create_task(
            self._defense_timeout(condemned, kamikaze_extra)
        )

    async def _defense_timeout(self, condemned_id: int, kamikaze_extra: Optional[int]) -> None:
        await asyncio.sleep(DEFENSE_TIMEOUT)
        await self._execute(condemned_id, kamikaze_extra)

    async def _execute(self, condemned_id: int, kamikaze_extra: Optional[int]) -> None:
        if self.phase not in (Phase.DEFENSE, Phase.VOTING):
            return
        p = self.players.get(condemned_id)
        if not p:
            return

        p.is_alive = False
        role_name, _ = get_role_text(self.lang, p.role.value)
        msg = get_text(self.lang, "player_eliminated", name=p.name, role=role_name)

        if kamikaze_extra and self.players.get(kamikaze_extra):
            victim = self.players[kamikaze_extra]
            victim.is_alive = False
            msg += "\n\n" + get_text(self.lang, "kamikaze_hit", victim=victim.name)

        await self.send(self.chat_id, msg)
        await self._maybe_continue()

    async def _maybe_continue(self) -> None:
        winner = RoleEngine.check_win(self.players)
        if winner:
            winner_id = RoleEngine.last_standing_winner_id(self.players)
            await self._end_game(winner, winner_id=winner_id)
        else:
            await self._begin_night()

    # ── End ───────────────────────────────────────────────────────
    async def _end_game(self, winner: str, winner_id: Optional[int] = None) -> None:
        self.phase = Phase.ENDED
        self._cancel()

        emoji = {"town": "🏙", "mafia": "🔫", "suicid": "☠️", "draw": "⚖️"}.get(winner, "🎭")
        reveal_rows = []
        for p in self.players.values():
            role_name, _ = get_role_text(self.lang, p.role.value)
            state = get_text(self.lang, "alive" if p.is_alive else "dead")
            reveal_rows.append(f"  {p.role.emoji} <b>{p.name}</b> — {role_name} [{state}]")
        reveal = "\n".join(reveal_rows)

        end_caption = (
            f"{emoji} <b>{get_text(self.lang, 'game_over')}</b>\n<b>{get_text(self.lang, 'winner_' + winner)}</b>\n\n"
            f"<b>{get_text(self.lang, 'final_roles')}</b>\n{reveal}\n\n"
            f"📊 {get_text(self.lang, 'rounds_played', rounds=self.round)}"
        )
        # Önce metin finalini gönder; görsel/caption hatası sonucu gizleyemez.
        await self.send(self.chat_id, end_caption)

        if self.app is not None:
            image_role = next((p.role for p in self.players.values()
                               if p.user_id == winner_id), None)
            if image_role is None:
                image_role = Role.DON if winner == "mafia" else (
                    Role.KOMISSAR if winner == "town" else Role.SUICID
                )
            image = ROLE_IMAGES.get(image_role.value)
            if image:
                try:
                    await self.app.bot.send_photo(
                        chat_id=self.chat_id, photo=image,
                        caption=f"🎬 <b>KVP GAME — FİNAL SAHNESİ</b> · {get_text(self.lang, 'winner_' + winner)}",
                        parse_mode=ParseMode.HTML
                    )
                except TelegramError as exc:
                    log.warning("Final görseli gönderilemedi; metin sonucu yayınlandı: %s", exc)

        # Persist
        await db.end_game(self.game_id, winner, self.round)

        all_elos = []
        for p in self.players.values():
            if p.user_id > 0:
                s = await db.get_stats(p.user_id)
                if s:
                    all_elos.append(s["elo"])
        avg_elo = sum(all_elos) // len(all_elos) if all_elos else 1000

        for p in self.players.values():
            if p.is_ai or p.user_id < 0:
                continue
            won = (
                (winner == "town"   and not p.role.is_mafia_team) or
                (winner == "mafia"  and p.role.is_mafia_team) or
                (winner == "suicid" and p.role == Role.SUICID and p.user_id == winner_id)
            )
            elo_ch = await db.update_player(
                p.user_id, p.name, won, p.is_alive, p.role.value, avg_elo
            )
            sign = "+" if elo_ch >= 0 else ""
            try:
                await self.send(
                    p.user_id,
                    get_text(await db.get_language(p.user_id), "private_result",
                             result=get_text(await db.get_language(p.user_id), "win_label" if won else "loss_label"),
                             sign=sign, elo=elo_ch)
                )
            except Exception:
                pass

    async def force_end(self) -> None:
        """Stop the active game without awarding a winner or changing player stats."""
        self.phase = Phase.ENDED
        self._cancel()
        try:
            await db.end_game(self.game_id, "draw", self.round)
        except Exception:
            log.exception("O'yinni majburan tugatishda DB xatosi: %s", self.game_id)
    def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def role_info(self, uid: int, lang: Optional[str] = None) -> str:
        p = self.players.get(uid)
        if not p:
            return get_text(self.lang, "not_in_game")
        display_lang = lang if lang in LANGUAGES else self.lang
        role_name, role_description = get_role_text(display_lang, p.role.value)
        lines = [f"{p.role.emoji} <b>{get_text(display_lang, 'your_role', role=role_name)}</b>\n", role_description]
        if p.role.is_mafia_team:
            mates = [q.name for qid, q in self.players.items()
                     if q.role.is_mafia_team and qid != uid]
            if mates:
                lines.append(get_text(display_lang, "teammates", names=', '.join(mates)))
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# REGISTRY
# ══════════════════════════════════════════════════════════════════
class Registry:
    def __init__(self):
        self._g: dict[int, Game] = {}

    def get(self, chat_id: int) -> Optional[Game]:
        return self._g.get(chat_id)

    def new(self, chat_id: int, send_fn: SendFn, lang: str = "tr", app: Optional[Application] = None) -> Game:
        g = Game(chat_id, send_fn, lang, app=app)
        self._g[chat_id] = g
        return g

    def remove(self, chat_id: int):
        self._g.pop(chat_id, None)

    def cleanup(self) -> int:
        ended = [c for c, g in self._g.items() if g.is_ended()]
        for c in ended:
            del self._g[c]
        return len(ended)

    def find_user(self, uid: int) -> Optional[Game]:
        for g in self._g.values():
            if uid in g.players:
                return g
        return None


reg = Registry()

# ══════════════════════════════════════════════════════════════════
# SEND HELPER
# ══════════════════════════════════════════════════════════════════
async def safe_send(app: Application, chat_id: int, text: str,
                    markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await app.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    except Forbidden:
        log.warning("Forbidden: %s", chat_id)
    except BadRequest as e:
        log.warning("BadRequest %s: %s", chat_id, e)
    except TelegramError as e:
        log.error("TelegramError %s: %s", chat_id, e)


def make_send(app: Application) -> SendFn:
    async def _s(chat_id: int, text: str, **kw) -> None:
        await safe_send(app, chat_id, text, kw.get("markup"))
    return _s


# ══════════════════════════════════════════════════════════════════
# NIGHT DM
# ══════════════════════════════════════════════════════════════════
async def send_night_dm(app: Application, game: Game, uid: int) -> None:
    p = game.players.get(uid)
    if not p or not p.is_alive or not p.role.has_night_action:
        return

    alive_targets = [
        (q.user_id, q.name)
        for q in game.alive()
        if q.user_id != uid
    ]

    player_lang = await db.get_language(uid)
    verb = get_text(player_lang, "night_action_" + p.role.value)
    buttons = [
        [InlineKeyboardButton(get_text(player_lang, "select_target", name=name), callback_data=f"night:{uid2}")]
        for uid2, name in alive_targets
    ]
    buttons.append([InlineKeyboardButton(get_text(player_lang, "skip_action"), callback_data="night:skip")])

    try:
        await app.bot.send_message(
            chat_id=uid,
            text=get_text(player_lang, "night_action_title", action=verb),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except (Forbidden, BadRequest) as e:
        log.warning("Night DM fail %s: %s", uid, e)


# ══════════════════════════════════════════════════════════════════
# KEYBOARDS
# ══════════════════════════════════════════════════════════════════
def lobby_kb(n: int, is_host: bool, lang: str = "tr") -> InlineKeyboardMarkup:
    btns = [
        [InlineKeyboardButton(get_text(lang, "join_game"), callback_data="join"),
         InlineKeyboardButton(get_text(lang, "leave_game"), callback_data="leave")],
        [InlineKeyboardButton(get_text(lang, "add_ai"), callback_data="add_ai")],
    ]
    if is_host:
        if n >= MIN_PLAYERS:
            btns.append([InlineKeyboardButton(
                get_text(lang, "start_game_button", count=n), callback_data="start_now"
            )])
        else:
            btns.append([InlineKeyboardButton(
                get_text(lang, "need_players_button", count=n, minimum=MIN_PLAYERS), callback_data="noop"
            )])
        btns.append([InlineKeyboardButton(
            get_text(lang, "extend_lobby_button"), callback_data="extend_lobby"
        )])
    return InlineKeyboardMarkup(btns)


def stats_kb(lang: str = "tr") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(lang, "leaderboard_button"), callback_data="leaderboard"),
         InlineKeyboardButton(get_text(lang, "my_stats_button"), callback_data="my_stats")],
        [InlineKeyboardButton(get_text(lang, "global_stats_button"), callback_data="global_stats"),
         InlineKeyboardButton(get_text(lang, "achievements_button"), callback_data="my_achievements")],
    ])


def language_kb() -> InlineKeyboardMarkup:
    codes = list(LANGUAGES.items())
    rows = []
    for i in range(0, len(codes), 2):
        row = [InlineKeyboardButton(name, callback_data=f"lang:{code}")
               for code, name in codes[i:i + 2]]
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def group_language_kb() -> InlineKeyboardMarkup:
    codes = list(LANGUAGES.items())
    rows = []
    for i in range(0, len(codes), 2):
        rows.append([
            InlineKeyboardButton(name, callback_data=f"group_lang:{code}")
            for code, name in codes[i:i + 2]
        ])
    return InlineKeyboardMarkup(rows)


async def is_group_admin(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        member = await ctx.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("administrator", "creator")
    except TelegramError:
        log.exception("Guruh admini tekshirilmadi: chat=%s user=%s", chat_id, user_id)
        return False


def main_kb(lang: str = "tr") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(lang, "new_game"), callback_data="new_game"),
         InlineKeyboardButton(get_text(lang, "stats"), callback_data="stats_menu")],
        [InlineKeyboardButton(get_text(lang, "rules"), callback_data="howto")],
        [InlineKeyboardButton(get_text(lang, "language"), callback_data="choose_language")],
    ])


# ══════════════════════════════════════════════════════════════════
# CHANNEL MEMBERSHIP CHECK
# ══════════════════════════════════════════════════════════════════
async def user_is_subscribed(ctx: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Return True only when the user belongs to the configured channel."""
    if not REQUIRED_CHANNEL:
        return True
    try:
        member = await ctx.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=user_id,
        )
        if member.status in ("creator", "administrator", "member"):
            return True
        return member.status == "restricted" and bool(
            getattr(member, "is_member", False)
        )
    except TelegramError:
        log.exception(
            "Kanal a'zoligini tekshirib bo'lmadi. REQUIRED_CHANNEL=%s",
            REQUIRED_CHANNEL,
        )
        return False


async def require_channel_membership(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Block bot features until the user joins the required channel."""
    if not REQUIRED_CHANNEL:
        return True

    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return False

    try:
        member = await ctx.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=user.id,
        )
        subscribed = (
            member.status in ("creator", "administrator", "member")
            or (
                member.status == "restricted"
                and bool(getattr(member, "is_member", False))
            )
        )
    except TelegramError:
        log.exception("Majburiy kanal tekshiruvi bajarilmadi.")
        subscribed = False
        check_error = True
    else:
        check_error = False

    if subscribed:
        return True

    lang = await db.get_language(user.id)
    join_url = CHANNEL_JOIN_URL
    if not join_url and REQUIRED_CHANNEL.startswith("@"):
        join_url = "https://t.me/" + REQUIRED_CHANNEL.lstrip("@")
    buttons = []
    if join_url:
        buttons.append([
            InlineKeyboardButton(get_text(lang, "join_channel"), url=join_url)
        ])
    else:
        buttons.append([
            InlineKeyboardButton(
                get_text(lang, "join_channel"),
                callback_data="channel_link_missing",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            get_text(await db.get_language(user.id), "check_membership"),
            callback_data="verify_subscription",
        )
    ])
    prompt_key = "channel_check_error" if check_error else "channel_required"
    prompt = get_text(lang, prompt_key)
    if check_error:
        prompt += "\\n\\n" + get_text(lang, "channel_required")
    await message.reply_text(
        prompt,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return False


# ══════════════════════════════════════════════════════════════════
# COMMANDS
# ══════════════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await db.get_language(user.id)
    if not await require_channel_membership(update, ctx):
        return
    if update.effective_chat.type == "private":
        active_game = reg.find_user(user.id)
        if active_game and not active_game.is_ended():
            role_text = active_game.role_info(user.id, lang)
            try:
                await update.effective_message.reply_photo(
                    photo=ROLE_IMAGES.get(active_game.players[user.id].role.value),
                    caption=role_text, parse_mode=ParseMode.HTML
                )
            except TelegramError:
                await update.effective_message.reply_text(role_text, parse_mode=ParseMode.HTML)
            if active_game.phase == Phase.NIGHT:
                await send_night_dm(ctx.application, active_game, user.id)
            return
    try:
        await update.message.reply_photo(
            photo=ROLE_IMAGES["fuqaro"], caption=get_text(lang, "welcome"),
            parse_mode=ParseMode.HTML, reply_markup=main_kb(lang)
        )
    except TelegramError:
        await update.message.reply_text(
            get_text(lang, "welcome"), parse_mode=ParseMode.HTML,
            reply_markup=main_kb(lang)
        )


async def cmd_language(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show language picker; available even before channel subscription."""
    lang = await db.get_language(update.effective_user.id)
    await update.effective_message.reply_text(
        get_text(lang, "choose_language"),
        parse_mode=ParseMode.HTML,
        reply_markup=language_kb(),
    )


async def cmd_grouplanguage(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(get_text(await db.get_language(user.id), "group_only"))
        return
    if not await is_group_admin(ctx, chat.id, user.id):
        await update.effective_message.reply_text(get_text(await db.get_group_language(chat.id), "group_admin_only"))
        return
    lang = await db.get_group_language(chat.id)
    await update.effective_message.reply_text(
        get_text(lang, "group_language_prompt"),
        parse_mode=ParseMode.HTML,
        reply_markup=group_language_kb(),
    )


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat.type not in ("group", "supergroup") or not await is_group_admin(ctx, chat.id, user.id):
        lang = await db.get_group_language(chat.id) if chat.type in ("group", "supergroup") else await db.get_language(user.id)
        await update.effective_message.reply_text(get_text(lang, "admin_only"))
        return
    lang = await db.get_group_language(chat.id)
    await update.effective_message.reply_text(
        get_text(lang, "admin_help"),
        parse_mode=ParseMode.HTML,
    )


def owner_panel_kb() -> InlineKeyboardMarkup:
    support_button = (
        InlineKeyboardButton("🛟 Destek / İletişim", url=SUPPORT_URL)
        if SUPPORT_URL else
        InlineKeyboardButton("🛟 Destek / İletişim", callback_data="owner:support")
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 İstatistikleri yenile", callback_data="owner:refresh")],
        [support_button],
    ])


async def render_owner_panel() -> str:
    stats = await db.get_owner_stats()
    return (
        "👑 <b>KVP GAME — SAHİP PANELİ</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👥 Kayıtlı kullanıcı: <b>{stats.get('total_users') or 0}</b>\n"
        f"🏘️ Kayıtlı grup: <b>{stats.get('total_groups') or 0}</b>\n"
        f"🎮 Toplam oyun kaydı: <b>{stats.get('total_games') or 0}</b>\n"
        f"🏁 Tamamlanan oyun: <b>{stats.get('completed_games') or 0}</b>\n"
        f"🔥 Son 24 saatte başlayan oyun: <b>{stats.get('games_24h') or 0}</b>\n"
        f"🟢 Şu an çalışan oyun: <b>{stats.get('running_games') or 0}</b>\n"
        f"🎯 Şu an oyundaki aktif oyuncu: <b>{stats.get('active_players') or 0}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "<i>Kayıtlı grup sayısı, en az bir oyun kaydı bulunan grupları gösterir. "
        "Aktif kullanıcı sayısı için ayrıca son görülme takibi gerekir.</i>"
    )


async def cmd_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in OWNER_IDS:
        await update.effective_message.reply_text("⛔ Bu panel yalnızca bot sahibine açıktır.")
        return
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("🔐 Güvenlik için /panel komutunu botun özel mesajında kullan.")
        return
    await update.effective_message.reply_text(
        await render_owner_panel(), parse_mode=ParseMode.HTML,
        reply_markup=owner_panel_kb()
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        lang = await db.get_group_language(chat.id)
    else:
        lang = await db.get_language(update.effective_user.id)
    try:
        await update.effective_message.reply_photo(
            photo=ROLE_IMAGES["mafia"], caption=get_text(lang, "help_message"),
            parse_mode=ParseMode.HTML
        )
    except TelegramError:
        await update.effective_message.reply_text(get_text(lang, "help_message"), parse_mode=ParseMode.HTML)


async def cmd_newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    chat_id = update.effective_chat.id
    user    = update.effective_user

    reg.cleanup()
    existing = reg.get(chat_id)
    if existing and not existing.is_ended():
        await update.message.reply_text(get_text(await db.get_group_language(chat_id), "game_already"))
        return

    send_fn = make_send(ctx.application)
    group_lang = await db.get_group_language(chat_id)
    g = reg.new(chat_id, send_fn, group_lang, app=ctx.application)
    g.join(user.id, user.full_name)

    lobby_text = get_text(
        g.lang, "lobby_created", name=user.full_name,
        maximum=MAX_PLAYERS, seconds=LOBBY_TIMEOUT
    )
    try:
        msg = await update.message.reply_photo(
            photo=ROLE_IMAGES["mafia"], caption=lobby_text,
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(1, is_host=True, lang=g.lang),
        )
    except TelegramError:
        msg = await update.message.reply_text(
            lobby_text, parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(1, is_host=True, lang=g.lang),
        )

    # Start lobby countdown
    asyncio.create_task(g.start_lobby_timer())


async def cmd_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)

    if not g or g.is_ended():
        await update.message.reply_text(get_text(await db.get_group_language(chat_id), "no_lobby"))
        return
    if g.phase != Phase.LOBBY:
        await update.message.reply_text(get_text(g.lang, "game_started_wait"))
        return
    if g.join(user.id, user.full_name):
        n = g.n()
        plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values())
        await update.message.reply_text(
            get_text(g.lang, "lobby_joined", name=user.full_name, count=n, maximum=MAX_PLAYERS, players=plist),
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(n, is_host=(user.id == g.host_id), lang=g.lang),
        )
    else:
        await update.message.reply_text(get_text(g.lang, "already_joined"))


async def cmd_leave(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)
    if not g or g.phase != Phase.LOBBY:
        await update.message.reply_text(get_text(await db.get_group_language(chat_id), "no_lobby"))
        return
    if g.leave(user.id):
        n = g.n()
        await update.message.reply_text(
            get_text(g.lang, "lobby_left", name=user.full_name, count=n, maximum=MAX_PLAYERS),
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(n, is_host=(user.id == g.host_id), lang=g.lang),
        )
    else:
        await update.message.reply_text(get_text(g.lang, "not_in_lobby"))


async def cmd_startgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)
    if not g:
        await update.message.reply_text(get_text(await db.get_group_language(chat_id), "no_lobby"))
        return
    if user.id != g.host_id and not await is_group_admin(ctx, chat_id, user.id):
        await update.message.reply_text(get_text(g.lang, "start_admin"))
        return
    result = await g.start_game()
    if result != "ok":
        await update.message.reply_text(result)
        return


async def cmd_endgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    chat_id = update.effective_chat.id
    user    = update.effective_user
    g = reg.get(chat_id)
    if not g:
        await update.message.reply_text(get_text(await db.get_group_language(chat_id), "no_game"))
        return
    is_adm = await is_group_admin(ctx, chat_id, user.id)
    if user.id != g.host_id and not is_adm:
        await update.message.reply_text(get_text(g.lang, "only_host_admin"))
        return
    await g.force_end()
    reg.remove(chat_id)
    await update.message.reply_text(get_text(g.lang, "game_forced_end"))


async def cmd_players(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    g = reg.get(update.effective_chat.id)
    if not g:
        await update.message.reply_text(get_text(await db.get_group_language(update.effective_chat.id), "no_game"))
        return
    lines = [get_text(g.lang, "players_title", game_id=g.game_id, phase=get_text(g.lang, "phase_" + g.phase.name.lower())) + "\n"]
    for p in g.players.values():
        icon = "✅" if p.is_alive else "💀"
        ai = " 🤖" if p.is_ai else ""
        lines.append(f"  {icon} {p.name}{ai}")
    lines.append("\n⏱ " + get_text(g.lang, "round_label", round=g.round))
    players_text = "\n".join(lines)
    try:
        await update.message.reply_photo(
            photo=ROLE_IMAGES["komissar"], caption=players_text,
            parse_mode=ParseMode.HTML
        )
    except TelegramError:
        await update.message.reply_text(players_text, parse_mode=ParseMode.HTML)


async def cmd_myrole(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    uid = update.effective_user.id
    g = reg.find_user(uid)
    if not g:
        await update.effective_message.reply_text(get_text(await db.get_language(uid), "not_in_game"))
        return
    if update.effective_chat.type != "private":
        me = await ctx.bot.get_me()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            get_text(await db.get_language(uid), "open_private_role"),
            url=f"https://t.me/{me.username}?start=play"
        )]])
        await update.effective_message.reply_text(get_text(g.lang, "role_private_only"), reply_markup=kb)
        return
    player_lang = await db.get_language(uid)
    role_text = g.role_info(uid, player_lang)
    try:
        await update.effective_message.reply_photo(
            photo=ROLE_IMAGES.get(g.players[uid].role.value), caption=role_text,
            parse_mode=ParseMode.HTML
        )
    except TelegramError:
        await update.effective_message.reply_text(role_text, parse_mode=ParseMode.HTML)
    if g.phase == Phase.NIGHT:
        await send_night_dm(ctx.application, g, uid)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    lang = await db.get_group_language(update.effective_chat.id) if update.effective_chat.type in ("group", "supergroup") else await db.get_language(update.effective_user.id)
    try:
        await update.effective_message.reply_photo(
            photo=ROLE_IMAGES["fuqaro"], caption=get_text(lang, "stats_title"),
            parse_mode=ParseMode.HTML, reply_markup=stats_kb(lang)
        )
    except TelegramError:
        await update.effective_message.reply_text(
            get_text(lang, "stats_title"), parse_mode=ParseMode.HTML, reply_markup=stats_kb(lang)
        )


# ══════════════════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    data = q.data or ""
    uid  = q.from_user.id
    name = q.from_user.full_name
    cid  = q.message.chat_id if q.message else None

    if data == "channel_link_missing":
        lang = await db.get_language(uid)
        await q.answer(get_text(lang, "channel_url_missing"), show_alert=True)
        return

    if data.startswith("owner:"):
        if uid not in OWNER_IDS or q.message is None or q.message.chat.type != "private":
            await q.answer("⛔ Yetkin yok.", show_alert=True)
            return
        action = data.split(":", 1)[1]
        if action == "refresh":
            await q.edit_message_text(
                await render_owner_panel(), parse_mode=ParseMode.HTML,
                reply_markup=owner_panel_kb()
            )
            await q.answer("İstatistikler yenilendi.")
        elif action == "support":
            await q.answer("Destek iletişim bilgisi henüz yapılandırılmadı.", show_alert=True)
        return

    # Group language selection is restricted to group administrators.
    if data.startswith("group_lang:"):
        if q.message is None or q.message.chat.type not in ("group", "supergroup"):
            await q.answer(get_text(await db.get_language(uid), "group_only"), show_alert=True)
            return
        if not await is_group_admin(ctx, q.message.chat_id, uid):
            await q.answer(get_text(await db.get_group_language(q.message.chat_id), "group_admin_only"), show_alert=True)
            return
        selected = data.split(":", 1)[1]
        if selected not in LANGUAGES:
            await q.answer(get_text(await db.get_group_language(q.message.chat_id), "unsupported_language"), show_alert=True)
            return
        await db.set_group_language(q.message.chat_id, selected)
        current_game = reg.get(q.message.chat_id)
        if current_game:
            current_game.lang = selected
        await q.answer(get_text(selected, "group_language_saved"), show_alert=True)
        await q.edit_message_text(
            get_text(selected, "group_language_saved"),
            parse_mode=ParseMode.HTML,
        )
        return

    # Language selection is available before channel-membership checks.
    if data.startswith("lang:"):
        selected = data.split(":", 1)[1]
        if selected not in LANGUAGES:
            await q.answer(get_text(await db.get_language(uid), "unsupported_language"), show_alert=True)
            return
        await db.set_language(uid, selected)
        await q.answer()
        await q.edit_message_text(
            get_text(selected, "language_saved"),
            parse_mode=ParseMode.HTML,
            reply_markup=main_kb(selected),
        )
        return

    if data == "choose_language":
        lang = await db.get_language(uid)
        await q.edit_message_text(
            get_text(lang, "choose_language"),
            parse_mode=ParseMode.HTML,
            reply_markup=language_kb(),
        )
        await q.answer()
        return

    # A'zolikni tekshirish tugmasi boshqa barcha cheklovlardan oldin ishlaydi.
    if data == "verify_subscription":
        if await user_is_subscribed(ctx, uid):
            lang = await db.get_language(uid)
            await q.answer(get_text(lang, "subscribed"), show_alert=True)
            try:
                await q.edit_message_text(
                    get_text(lang, "subscribed"),
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
        else:
            lang = await db.get_language(uid)
            await q.answer(get_text(lang, "not_subscribed"), show_alert=True)
        return

    if not await require_channel_membership(update, ctx):
        await q.answer(get_text(await db.get_language(uid), "channel_required_alert"), show_alert=True)
        return

    try:
        # ── static ─────────────────────────────────────────────
        if data == "noop":
            await q.answer()
            return

        elif data == "new_game":
            await q.answer(get_text(await db.get_group_language(cid), "newgame_group"), show_alert=True)
            return

        elif data == "howto":
            await q.answer(get_text(await db.get_group_language(cid), "help_message")[:190], show_alert=True)
            return

        elif data == "stats_menu":
            await q.answer()
            await q.edit_message_text(
                get_text(await db.get_group_language(cid), "stats_title"),
                parse_mode=ParseMode.HTML,
                reply_markup=stats_kb(await db.get_group_language(cid)),
            )
            return

        # ── stats ──────────────────────────────────────────────
        elif data == "leaderboard":
            rows = await db.get_leaderboard()
            if not rows:
                await q.answer(get_text(await db.get_language(uid), "no_stats"), show_alert=True)
                return
            medals = ["🥇","🥈","🥉"] + ["🏅"]*20
            lines = [get_text(await db.get_language(uid), "leaderboard_title") + "\n"]
            for i, r in enumerate(rows):
                lines.append(
                    f"{medals[i]} <b>{r['username']}</b> — " +
                    get_text(await db.get_language(uid), "leaderboard_row", elo=r['elo'], wins=r['wins'], games=r['games'], wr=r['wr'])
                )
            try:
                await ctx.application.bot.send_message(
                    uid, "\n".join(lines), parse_mode=ParseMode.HTML
                )
                await q.answer(get_text(await db.get_language(uid), "dm_leaderboard"))
            except Forbidden:
                await q.answer("\n".join(lines)[:200], show_alert=True)

        elif data == "my_stats":
            s = await db.get_stats(uid)
            if not s:
                await q.answer(get_text(await db.get_language(uid), "not_played"), show_alert=True)
                return
            wr = round(s["wins"] / max(s["games"], 1) * 100, 1)
            rw_rows = []
            lang = await db.get_language(uid)
            for role_code, count in sorted(s["role_wins"].items(), key=lambda x: -x[1]):
                role_name, _ = get_role_text(lang, role_code)
                try:
                    role_emoji = Role(role_code).emoji
                except ValueError:
                    role_emoji = "🎭"
                rw_rows.append(f"  {role_emoji} {role_name}: {count}")
            rw_lines = "\n".join(rw_rows) or "  —"
            msg = get_text(lang, "personal_stats", username=s['username'], games=s['games'],
                           wins=s['wins'], wr=wr, losses=s['losses'], survived=s['survived'],
                           elo=s['elo'], streak=s['best_streak'], roles=rw_lines)
            try:
                await ctx.application.bot.send_message(uid, msg, parse_mode=ParseMode.HTML)
                await q.answer(get_text(lang, "dm_stats"))
            except Forbidden:
                await q.answer(msg[:200], show_alert=True)

        elif data == "global_stats":
            g_data = await db.get_global()
            if not g_data or not g_data.get("total"):
                await q.answer(get_text(await db.get_language(uid), "no_stats"), show_alert=True)
                return
            lang = await db.get_language(uid)
            await q.answer(get_text(lang, "global_stats", total=int(g_data['total'] or 0),
                                     town=int(g_data['town'] or 0), mafia=int(g_data['mafia'] or 0),
                                     suicid=int(g_data['suicid'] or 0), average=round(g_data['avg_p'] or 0, 1)),
                           show_alert=True)

        elif data == "my_achievements":
            s = await db.get_stats(uid)
            if not s:
                await q.answer(get_text(await db.get_language(uid), "not_played"), show_alert=True)
                return
            earned = db._get_achievements(s)
            lines = ["🏅 <b>" + get_text(await db.get_language(uid), "achievements_title") + "</b>\n"]
            lang = await db.get_language(uid)
            for key, (emoji, _title, _desc) in ACHIEVEMENTS_DEF.items():
                title, desc = get_achievement_text(lang, key)
                if key in earned:
                    lines.append(f"{emoji} <b>{title}</b> — {desc}")
                else:
                    lines.append(f"🔒 <i>{title}</i> — {desc}")
            try:
                await ctx.application.bot.send_message(
                    uid, "\n".join(lines), parse_mode=ParseMode.HTML
                )
                await q.answer(get_text(await db.get_language(uid), "dm_achievements"))
            except Forbidden:
                await q.answer("\n".join(lines)[:200], show_alert=True)

        # ── lobby ──────────────────────────────────────────────
        elif data == "join":
            g = reg.get(cid)
            if not g or g.phase != Phase.LOBBY:
                await q.answer(get_text(await db.get_group_language(cid), "active_lobby"), show_alert=True)
                return
            if g.join(uid, name):
                await q.answer()
                n = g.n()
                plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values())
                await q.edit_message_text(
                    get_text(g.lang, "lobby_joined", name=name, count=n, maximum=MAX_PLAYERS, players=plist),
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=(uid == g.host_id), lang=g.lang),
                )
            else:
                await q.answer(get_text(g.lang, "already_joined"), show_alert=True)

        elif data == "leave":
            g = reg.get(cid)
            if g and g.leave(uid):
                await q.answer()
                n = g.n()
                plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values()) or "(bo'sh)"
                await q.edit_message_text(
                    get_text(g.lang, "lobby_joined", name=name, count=n, maximum=MAX_PLAYERS, players=plist),
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=(uid == g.host_id), lang=g.lang),
                )
            else:
                await q.answer(get_text(g.lang if g else await db.get_group_language(cid), "not_in_lobby"), show_alert=True)

        elif data == "add_ai":
            g = reg.get(cid)
            if not g or g.phase != Phase.LOBBY:
                await q.answer(get_text(await db.get_group_language(cid), "active_lobby"), show_alert=True)
                return
            if uid != g.host_id and not await is_group_admin(ctx, cid, uid):
                await q.answer(get_text(g.lang, "ai_admin"), show_alert=True)
                return
            if g.add_ai():
                await q.answer()
                n = g.n()
                plist = "\n".join(f"  ✅ {p.name}" for p in g.players.values())
                await q.edit_message_text(
                    get_text(g.lang, "lobby_joined", name=name, count=n, maximum=MAX_PLAYERS, players=plist),
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=True, lang=g.lang),
                )
            else:
                await q.answer(get_text(g.lang, "ai_limit"), show_alert=True)

        elif data == "extend_lobby":
            g = reg.get(cid)
            if not g:
                await q.answer(get_text(await db.get_group_language(cid), "active_lobby"), show_alert=True)
                return
            if uid != g.host_id and not await is_group_admin(ctx, cid, uid):
                await q.answer(get_text(g.lang, "extend_admin"), show_alert=True)
                return
            result = await g.extend_lobby(uid)
            await q.answer(result, show_alert=True)

        elif data == "start_now":
            g = reg.get(cid)
            if not g:
                await q.answer(get_text(await db.get_group_language(cid), "active_lobby"), show_alert=True)
                return
            if uid != g.host_id and not await is_group_admin(ctx, cid, uid):
                await q.answer(get_text(g.lang, "start_admin"), show_alert=True)
                return
            if g.n() < MIN_PLAYERS:
                await q.answer(get_text(g.lang, "start_min", minimum=MIN_PLAYERS), show_alert=True)
                return
            await q.answer()
            await q.edit_message_text(get_text(g.lang, "starting_game"), parse_mode=ParseMode.HTML)
            result = await g.start_game()
            if result != "ok":
                await safe_send(ctx.application, cid, result)

        # ── night action ───────────────────────────────────────
        elif data.startswith("night:"):
            ts = data.split(":", 1)[1]
            g = reg.find_user(uid)
            if not g:
                await q.answer(get_text(await db.get_language(uid), "not_in_game"), show_alert=True)
                return
            if ts == "skip":
                fb = await g.skip_action(uid, await db.get_language(uid))
                await q.answer(fb)
                try:
                    await q.edit_message_text(fb)
                except TelegramError:
                    pass
                return
            fb = await g.record_action(uid, int(ts), await db.get_language(uid))
            await q.answer()
            await q.edit_message_text(fb, parse_mode=ParseMode.HTML)

        # ── voting ─────────────────────────────────────────────
        elif data.startswith("vote:"):
            ts = data.split(":", 1)[1]
            g = reg.get(cid)
            if not g:
                await q.answer(get_text(await db.get_group_language(cid), "no_game"), show_alert=True)
                return
            if ts == "skip":
                await q.answer(get_text(await db.get_language(uid), "vote_skip"))
                return
            fb = g.cast_vote(uid, int(ts), await db.get_language(uid))
            await q.answer(fb)

        elif data == "skip_to_vote":
            g = reg.get(cid)
            if g:
                admin = await is_group_admin(ctx, cid, uid)
                res = await g.skip_day(uid, is_admin=admin)
                await q.answer(res)

    except Exception as e:
        log.exception("Callback xato (data=%s): %s", data, e)
        try:
            await q.answer(get_text(await db.get_group_language(cid), "callback_error"), show_alert=True)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ══════════════════════════════════════════════════════════════════
async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Xato: %s", ctx.error, exc_info=ctx.error)
    if isinstance(ctx.error, (Forbidden, BadRequest)):
        return
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(get_text(await db.get_language(update.effective_user.id), "unexpected_error"))
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
# POST INIT / SHUTDOWN
# ══════════════════════════════════════════════════════════════════
async def post_init(app: Application) -> None:
    await db.connect()
    tr_commands = [
        BotCommand("start", "Botu başlat"),
        BotCommand("newgame", "Yeni oyun lobisi aç"),
        BotCommand("join", "Oyuna katıl"),
        BotCommand("leave", "Lobiden ayrıl"),
        BotCommand("startgame", "Oyunu başlat (kurucu/yönetici)"),
        BotCommand("endgame", "Oyunu bitir (kurucu/yönetici)"),
        BotCommand("players", "Oyuncu listesini göster"),
        BotCommand("myrole", "Kendi rolünü göster"),
        BotCommand("stats", "İstatistikleri göster"),
        BotCommand("help", "Yardım ve oyun kuralları"),
        BotCommand("language", "Kişisel dili değiştir"),
        BotCommand("grouplanguage", "Grup dilini değiştir (yönetici)"),
        BotCommand("admin", "Yönetici komutları"),
        BotCommand("panel", "Bot sahibi yönetim paneli"),
    ]
    await app.bot.set_my_commands(tr_commands)
    await app.bot.set_my_commands(tr_commands, language_code="tr")
    command_sets = {
        "en": [BotCommand("start", "Start bot"), BotCommand("newgame", "Create game lobby"), BotCommand("join", "Join game"), BotCommand("leave", "Leave lobby"), BotCommand("startgame", "Start game"), BotCommand("endgame", "End game"), BotCommand("players", "Show players"), BotCommand("myrole", "Show your role"), BotCommand("stats", "Show statistics"), BotCommand("help", "Help and rules"), BotCommand("language", "Change language"), BotCommand("grouplanguage", "Change group language"), BotCommand("admin", "Admin commands")],
        "ru": [BotCommand("start", "Запустить бота"), BotCommand("newgame", "Создать игру"), BotCommand("join", "Войти в игру"), BotCommand("leave", "Выйти из лобби"), BotCommand("startgame", "Начать игру"), BotCommand("endgame", "Завершить игру"), BotCommand("players", "Список игроков"), BotCommand("myrole", "Моя роль"), BotCommand("stats", "Статистика"), BotCommand("help", "Помощь и правила"), BotCommand("language", "Сменить язык"), BotCommand("grouplanguage", "Язык группы"), BotCommand("admin", "Команды админа")],
        "uz": [BotCommand("start", "Botni ishga tushirish"), BotCommand("newgame", "Yangi o‘yin yaratish"), BotCommand("join", "O‘yinga qo‘shilish"), BotCommand("leave", "Lobbidan chiqish"), BotCommand("startgame", "O‘yinni boshlash"), BotCommand("endgame", "O‘yinni tugatish"), BotCommand("players", "O‘yinchilar ro‘yxati"), BotCommand("myrole", "Rolimni ko‘rish"), BotCommand("stats", "Statistika"), BotCommand("help", "Yordam va qoidalar"), BotCommand("language", "Tilni o‘zgartirish"), BotCommand("grouplanguage", "Guruh tilini o‘zgartirish"), BotCommand("admin", "Admin buyruqlari")],
        "ar": [BotCommand("start", "تشغيل البوت"), BotCommand("newgame", "إنشاء لعبة"), BotCommand("join", "الانضمام للعبة"), BotCommand("leave", "مغادرة اللعبة"), BotCommand("startgame", "بدء اللعبة"), BotCommand("endgame", "إنهاء اللعبة"), BotCommand("players", "قائمة اللاعبين"), BotCommand("myrole", "دوري"), BotCommand("stats", "الإحصائيات"), BotCommand("help", "المساعدة والقواعد"), BotCommand("language", "تغيير اللغة"), BotCommand("grouplanguage", "لغة المجموعة"), BotCommand("admin", "أوامر المشرف")],
        "de": [BotCommand("start", "Bot starten"), BotCommand("newgame", "Neue Lobby erstellen"), BotCommand("join", "Spiel beitreten"), BotCommand("leave", "Lobby verlassen"), BotCommand("startgame", "Spiel starten"), BotCommand("endgame", "Spiel beenden"), BotCommand("players", "Spielerliste"), BotCommand("myrole", "Meine Rolle"), BotCommand("stats", "Statistik"), BotCommand("help", "Hilfe und Regeln"), BotCommand("language", "Sprache ändern"), BotCommand("grouplanguage", "Gruppensprache"), BotCommand("admin", "Admin-Befehle")],
    }
    for code, commands in command_sets.items():
        await app.bot.set_my_commands(commands, language_code=code)

    async def _cleanup():
        while True:
            await asyncio.sleep(300)
            n = reg.cleanup()
            if n:
                log.info("Tozalandi: %d o'yin", n)

    asyncio.create_task(_cleanup())
    log.info("🎭 KVP GAME tayyor!")


async def post_shutdown(app: Application) -> None:
    await db.close()
    log.info("Bot to'xtatildi.")


# ══════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════
def main() -> None:
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        log.error("BOT_TOKEN topilmadi! Environment variable o'rnating.")
        sys.exit(1)

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("language",  cmd_language))
    app.add_handler(CommandHandler("grouplanguage", cmd_grouplanguage))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("panel", cmd_panel))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("newgame",   cmd_newgame))
    app.add_handler(CommandHandler("join",      cmd_join))
    app.add_handler(CommandHandler("leave",     cmd_leave))
    app.add_handler(CommandHandler("startgame", cmd_startgame))
    app.add_handler(CommandHandler("endgame",   cmd_endgame))
    app.add_handler(CommandHandler("players",   cmd_players))
    app.add_handler(CommandHandler("myrole",    cmd_myrole))
    app.add_handler(CommandHandler("stats",     cmd_stats))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_error_handler(on_error)

    if REQUIRED_CHANNEL:
        log.info("Majburiy kanal a'zoligi yoqilgan: %s", REQUIRED_CHANNEL)
        if not CHANNEL_JOIN_URL:
            log.warning("CHANNEL_JOIN_URL belgilanmagan; a'zo bo'lmaganlar uchun kanal havolasi ko'rsatilmaydi.")
    else:
        log.warning("REQUIRED_CHANNEL belgilanmagan; majburiy kanal a'zoligi o'chirilgan.")

    log.info("🎭 KVP GAME bot ishga tushmoqda...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
