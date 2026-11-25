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
import html
import re
import os
import httpx
import random
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from languages import LANGUAGES, get_text, get_role_text as _get_role_text, get_achievement_text

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
GOOGLE_TRANSLATE_API_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "").strip()
_TRANSLATION_CACHE: dict[tuple[str, str], str] = {}

async def google_translate(text: str, target_lang: str) -> str:
    if not text or target_lang not in ("az", "fa") or not GOOGLE_TRANSLATE_API_KEY:
        return text
    key = (target_lang, text)
    if key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[key]
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                "https://translation.googleapis.com/language/translate/v2",
                params={"key": GOOGLE_TRANSLATE_API_KEY},
                json={"q": text, "source": "tr", "target": target_lang, "format": "html"},
            )
            response.raise_for_status()
            translated = response.json()["data"]["translations"][0]["translatedText"]
            _TRANSLATION_CACHE[key] = translated
            return translated
    except Exception as exc:
        log.warning("Google Translate error (%s): %s", target_lang, exc)
        return text

async def translate_markup(markup, target_lang: str):
    if markup is None or target_lang not in ("az", "fa") or not GOOGLE_TRANSLATE_API_KEY:
        return markup
    rows = []
    for row in markup.inline_keyboard:
        out = []
        for button in row:
            label = await google_translate(button.text, target_lang) if button.text else button.text
            out.append(InlineKeyboardButton(
                text=label, url=button.url, callback_data=button.callback_data,
                web_app=button.web_app, login_url=button.login_url,
                switch_inline_query=button.switch_inline_query,
                switch_inline_query_current_chat=button.switch_inline_query_current_chat,
                callback_game=button.callback_game, pay=button.pay,
            ))
        rows.append(out)
    return InlineKeyboardMarkup(rows)



def get_role_text(lang: str, role: str) -> tuple[str, str]:
    """Return localized role text, including the KVP-only Süleyman Çakır role."""
    cakir = {
        "tr": ("Süleyman Çakır", "Halk takımındasınız. Özel bir gece yeteneğiniz yok; Konsey'i bulup gündüz oylamasında eleyin."),
        "en": ("Suleyman Cakir", "You are on the town team. You have no night ability; find and vote out the Council."),
        "ru": ("Сулейман Чакыр", "Вы в команде горожан. У вас нет ночной способности; найдите Совет и исключите его голосованием."),
        "uz": ("Süleyman Çakır", "Siz xalq jamoasidasiz. Tungi qobiliyatingiz yo‘q; Kengashni topib, kunduzgi ovozda chiqaring."),
        "ar": ("سليمان جاكير", "أنت ضمن فريق المواطنين. ليست لديك قدرة ليلية؛ اكتشف أعضاء المجلس وأقصهم بالتصويت."),
        "de": ("Süleyman Çakır", "Du gehörst zum Bürgerteam. Du hast keine Nachtfähigkeit; finde den Rat und wähle ihn tagsüber ab."),
    }
    if role == "cakir":
        return cakir.get(lang, cakir["tr"])
    return _get_role_text(lang, role)


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
    "cakir": "https://media.cumhuriyet.com.tr/Archive/2021/4/16/1828754/kapak_224317.jpg",
    "suicid": "https://cdn.karar.com/news/1389096.jpg",
    "elif_eylul": "https://4.bp.blogspot.com/-7lSExFqNf-c/T_7Tfw40wMI/AAAAAAAAGuE/N2Y2YAEsXSE/s1600/741.jpg",
    "omer_baba": "https://www.yurtgazetesi.com.tr/d/other/2024/07/11/4e960baf-728xauto-elqf.jpg",
    "deli_hikmet": "https://imgrosetta.mynet.com.tr/file/13368780/728xauto.jpg",
}

# Ölüm görselleri: seçimle infaz ve gece öldürülmesi ayrı ele alınır.
# Görsel URL'leri IMAGE_SOURCES.md içinde belgelenmiştir.
TEMPLAR_VOTE_IMAGE = (
    "https://seyler.ekstat.com/img/max/800/8/8pidqdHBsBRlKneV-637316228192728472.jpg"
)
BARON_TEMPLAR_DEATH_IMAGE = "https://img.youtube.com/vi/eziAMoyvX64/hqdefault.jpg"
KIZ_KULESI_DEATH_IMAGE = "https://images.unsplash.com/photo-1665850420721-ccdfac5d7c23?auto=format&fit=crop&fm=jpg&q=85&w=1600"
COUNCIL_DEATH_IMAGES = [
    "https://galeri13.uludagsozluk.com/726/iplikci-nedim_1281287.jpg",
    "https://galeri13.uludagsozluk.com/678/nizamettin-guvenc_1291207_m.jpg",
    "https://img.youtube.com/vi/W7XsOKR6cag/hqdefault.jpg",
    "https://img.youtube.com/vi/hqf5QdXYyd8/hqdefault.jpg",
]
DEATH_SCENE_IMAGES = [
    "https://img.memurlar.net/galeri/3980/d801bd9a-8f47-e311-a39c-14feb5cc1801.jpg?width=800",
    "https://iv.okcdn.ru/i?fn=external_8&r=BEH7dJIjJJO0pjyIf46vcZ9AfED7ECzB4qghX2xk2M0qvfFUHBkc-axEloUKxdMNjrS5CqdNleAEysdLnA60zUgQ",
    "https://image.kanald.com.tr/i/kanald/100/1200x0/56b44c756ce43628c0a1b2f2.jpg",
    "https://pbs.twimg.com/media/G96A-4SagAAsTeQ.jpg",
    "https://img.internethaber.com/rcman/Cw1280h720q95gc/files/2016/4/1/1580431/1580431.jpg",
    "https://s1.dmcdn.net/v/OujRi1VcLUMkiAUyf/x1080",
    "https://iv.okcdn.ru/videoPreview?fn=external_8&i=1&id=218952566234&idx=30&tkn=sfpQ1fGjUhcoLFnMWuKJzMv_DCU&type=47",
    "https://image.kanald.com.tr/i/kanald/100/1200x630/54ca54f4f493b80b48a1525f.jpg",
]

def death_image_for(role: "Role", cause: str) -> str:
    """Select a scene by elimination cause and character faction."""
    # Baron'un ölümü her durumda kendine ait Templar sahnesiyle duyurulur.
    if role == Role.DON:
        return BARON_TEMPLAR_DEATH_IMAGE
    if role == Role.ELIF_EYLUL:
        return KIZ_KULESI_DEATH_IMAGE
    if cause == "vote":
        return TEMPLAR_VOTE_IMAGE
    if role == Role.MAFIA:
        return random.choice(COUNCIL_DEATH_IMAGES)
    return random.choice(DEATH_SCENE_IMAGES)


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
    CAKIR    = "cakir"
    SUICID   = "suicid"
    ELIF_EYLUL = "elif_eylul"
    OMER_BABA = "omer_baba"
    DELI_HIKMET = "deli_hikmet"

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
            Role.CAKIR:    "🥃",
            Role.SUICID:   "🎭",
            Role.ELIF_EYLUL: "🚢",
            Role.OMER_BABA: "📖",
            Role.DELI_HIKMET: "🤪",
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
            Role.CAKIR:    "Süleyman Çakır",
            Role.SUICID:   "Hain",
            Role.ELIF_EYLUL: "Elif Eylül",
            Role.OMER_BABA: "Ömer Baba",
            Role.DELI_HIKMET: "Deli Hikmet",
        }[self]

    @property
    def is_mafia_team(self) -> bool:
        return self in (Role.MAFIA, Role.DON)

    @property
    def has_night_action(self) -> bool:
        return self in (
            Role.MAFIA, Role.DON, Role.KOMISSAR,
            Role.SHIFOKOR, Role.GUVOH, Role.ELIF_EYLUL, Role.CAKIR
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
            Role.CAKIR:    "Süleyman Çakır olarak halk takımındasın. Özel gece yeteneğin yok; Konsey'i bulup oylamada ele.",
            Role.SUICID:   "Hain olarak gündüz oylamasıyla elenirsen kendi zaferini kazanırsın.",
            Role.ELIF_EYLUL: "Gece DM üzerinden gemi arkadaşını seç. Sen ölürsen gemi arkadaşın da ölür.",
            Role.OMER_BABA: "İki gecede bir hikâye anlatarak tüm gece yeteneklerini devre dışı bırakır.",
            Role.DELI_HIKMET: "Oylamada hangi isme basarsan bas, oyun senin yerine rastgele bir hedef seçer.",
        }[self]


ROLE_POWER = {
    Role.DON: 100, Role.KOMISSAR: 90, Role.SHIFOKOR: 80,
    Role.GUVOH: 70, Role.KAMIKAZE: 60, Role.MAFIA: 50,
    Role.SUICID: 40, Role.CAKIR: 75, Role.ELIF_EYLUL: 65,
    Role.OMER_BABA: 35, Role.DELI_HIKMET: 10, Role.FUQARO: 10,
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
    ship_partner: Optional[int] = None
    last_special_round: int = -100

    def reset_night(self):
        self.night_target = None
        self.has_acted = False

    @property
    def tag(self) -> str:
        dead = "💀 " if not self.is_alive else ""
        return f"{dead}{html.escape(self.name)}"


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
        return [Role.DON, Role.KOMISSAR, Role.CAKIR, Role.FUQARO]
    elif n == 5:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.CAKIR, Role.DELI_HIKMET]
    elif n == 6:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.CAKIR, Role.ELIF_EYLUL]
    elif n == 7:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.CAKIR, Role.OMER_BABA]
    elif n == 8:
        return [Role.DON, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.CAKIR, Role.ELIF_EYLUL]
    elif n == 9:
        return [Role.DON, Role.MAFIA, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.CAKIR, Role.OMER_BABA]
    elif n == 10:
        return [Role.DON, Role.MAFIA, Role.MAFIA, Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.SUICID, Role.CAKIR, Role.ELIF_EYLUL]
    else:
        mafia_n = max(2, n // 4)
        roles = [Role.DON] + [Role.MAFIA] * mafia_n
        roles += [Role.KOMISSAR, Role.SHIFOKOR, Role.GUVOH, Role.KAMIKAZE, Role.SUICID, Role.CAKIR]
        roles += [Role.ELIF_EYLUL, Role.OMER_BABA, Role.DELI_HIKMET]
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
                if players[t].role.is_mafia_team:
                    continue
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

        # The living Baron's order takes priority. If the Baron did not choose
        # a valid target, use the Council vote; a tie means no kill.
        baron_targets = [
            p.night_target for p in alive.values()
            if p.role == Role.DON and p.night_target in alive
            and not alive[p.night_target].role.is_mafia_team
        ]
        kill_target = baron_targets[0] if baron_targets else None
        if kill_target is None and mafia_votes:
            highest_votes = max(mafia_votes.values())
            leaders = [uid for uid, votes in mafia_votes.items() if votes == highest_votes]
            if len(leaders) == 1:
                kill_target = leaders[0]

        if kill_target is not None:
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
                    info = get_text(lang, "witness_mafia", name=html.escape(target_p.name), victim=html.escape(victim_name))
                else:
                    info = get_text(lang, "witness_no_target", name=html.escape(target_p.name))
            elif target_p.role == Role.KOMISSAR and target_p.night_target:
                tname = players.get(target_p.night_target, Player(-1,"?")).name
                info = get_text(lang, "witness_check", name=html.escape(target_p.name), target=html.escape(tname))
            elif target_p.role == Role.SHIFOKOR and target_p.night_target:
                tname = players.get(target_p.night_target, Player(-1,"?")).name
                info = get_text(lang, "witness_heal", name=html.escape(target_p.name), target=html.escape(tname))
            else:
                info = get_text(lang, "witness_idle", name=html.escape(target_p.name))
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
    role_wins    TEXT DEFAULT '{}',
    mafia_found  INTEGER DEFAULT 0
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
        # Existing Heroku/local databases need a safe additive migration.
        async with self._db.execute("PRAGMA table_info(player_stats)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        if "mafia_found" not in columns:
            await self._db.execute(
                "ALTER TABLE player_stats ADD COLUMN mafia_found INTEGER DEFAULT 0"
            )
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
            (game_id, chat_id, datetime.now(timezone.utc).isoformat(), player_count)
        )
        await self._db.commit()

    async def end_game(self, game_id: str, winner: str, rounds: int):
        await self._db.execute(
            "UPDATE games SET ended_at=?,winner=?,rounds=? WHERE game_id=?",
            (datetime.now(timezone.utc).isoformat(), winner, rounds, game_id)
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
        actual_elo_change = new_elo - old_elo

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
        return actual_elo_change

    async def add_investigation_success(self, user_id: int, username: str) -> None:
        """Count a successful human Komissar check for the related achievement."""
        await self._db.execute(
            "INSERT OR IGNORE INTO player_stats(user_id, username) VALUES(?, ?)",
            (user_id, username),
        )
        await self._db.execute(
            "UPDATE player_stats SET username=?, mafia_found=mafia_found+1 WHERE user_id=?",
            (username, user_id),
        )
        await self._db.commit()

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
               SUM(CASE WHEN winner='suicid' THEN 1 ELSE 0 END) as suicid,
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
        if stats.get("best_streak", stats["streak"]) >= 3: earned.append("streak3")
        if stats.get("best_streak", stats["streak"]) >= 5: earned.append("streak5")
        if stats["survived"] >= 5:       earned.append("survivor")
        rw = stats["role_wins"]
        if rw.get("mafia",0)+rw.get("don",0) >= 5:   earned.append("mafia_boss")
        if rw.get("don",0) >= 3:         earned.append("don_master")
        if stats.get("mafia_found", 0) >= 3: earned.append("komissar_pro")
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
        self._resolving_night = False
        self._resolving_vote = False
        self._executing_vote = False
        self._vote_message_id: Optional[int] = None

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
        if self.host_id == uid:
            self.host_id = next(iter(self.players), None)
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
        self._lobby_remaining = LOBBY_TIMEOUT
        while self._lobby_remaining > 0:
            await asyncio.sleep(1)
            if self.phase != Phase.LOBBY:
                return
            self._lobby_remaining = max(0, self._lobby_remaining - 1)
            remaining = self._lobby_remaining
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
        if self.phase != Phase.LOBBY or self._lobby_remaining <= 0:
            return get_text(self.lang, "lobby_not_active")
        if self._extends >= MAX_EXTEND:
            return get_text(self.lang, "extend_limit", maximum=MAX_EXTEND)
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
                if self.phase != Phase.STARTING:
                    return
                if player.is_ai or player.user_id < 0:
                    continue
                player_lang = await db.get_language(player.user_id)
                role_text = self.role_info(player.user_id, player_lang)
                role_text = await google_translate(role_text, player_lang)
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
        if self.app is not None and self.phase == Phase.NIGHT:
            if missing_dm:
                try:
                    me = await self.app.bot.get_me()
                    start_link = f"https://t.me/{me.username}?start=play"
                    await self.send(
                        self.chat_id,
                        get_text(self.lang, "dm_missing", names=', '.join(html.escape(n) for n in missing_dm), link=start_link)
                    )
                except TelegramError:
                    log.exception("DM yetkazish ogohlantirishi yuborilmedi")

    # ── Night ─────────────────────────────────────────────────────
    async def _begin_night(self) -> None:
        if self.phase == Phase.ENDED:
            return
        self.phase = Phase.NIGHT
        self.round += 1
        for p in self.players.values():
            p.reset_night()

        self._omer_silence = (self.round % 2 == 0 and any(
            p.is_alive and p.role == Role.OMER_BABA for p in self.players.values()
        ))
        if self._omer_silence:
            for p in self.players.values():
                if p.is_alive and p.role.has_night_action:
                    p.has_acted = True
            for p in self.players.values():
                if p.is_alive and not p.is_ai and p.role.has_night_action and self.app:
                    try:
                        await self.app.bot.send_message(chat_id=p.user_id, text="📖 Ömer Baba bir hikâye anlatıyor bir gün bir sahbe... Bu gece yetenekler mühürlendi.")
                    except TelegramError:
                        pass
        else:
            for p in self.players.values():
                if p.is_alive and p.role == Role.OMER_BABA:
                    p.has_acted = True
        for p in self.players.values():
            if p.is_alive and p.role == Role.ELIF_EYLUL and p.ship_partner is not None:
                p.has_acted = True
            if p.is_alive and p.role == Role.CAKIR and self.round - p.last_special_round < 2:
                p.has_acted = True
        if self.app is not None and not self._omer_silence:
            for p in self.players.values():
                if p.is_alive and not p.is_ai and p.user_id > 0 and p.role.has_night_action and not p.has_acted:
                    await send_night_dm(self.app, self, p.user_id)

        await self.send(
            self.chat_id,
            get_text(self.lang, "night_started", round=self.round, seconds=NIGHT_TIMEOUT)
        )
        if self.phase != Phase.NIGHT:
            return

        # AI actions
        await self._ai_night()
        if self.phase != Phase.NIGHT:
            return

        self._cancel()
        if self._all_acted():
            self._task = asyncio.create_task(self._resolve_night())
        else:
            self._task = asyncio.create_task(self._night_timeout())

    async def _ai_night(self) -> None:
        if getattr(self, "_omer_silence", False):
            return
        alive_others_ids = [uid for uid, p in self.players.items() if p.is_alive]

        async def choose_target(uid: int, player: Player) -> None:
            await asyncio.sleep(random.uniform(1, 3))
            candidates = [
                target_id for target_id in alive_others_ids
                if target_id != uid
                and not (
                    player.role.is_mafia_team
                    and self.players[target_id].role.is_mafia_team
                )
            ]
            if candidates:
                chosen = random.choice(candidates)
                if player.role == Role.ELIF_EYLUL and player.ship_partner is None:
                    player.ship_partner = chosen
                elif player.role != Role.ELIF_EYLUL:
                    player.night_target = chosen
            player.has_acted = True

        tasks = [
            choose_target(uid, player)
            for uid, player in self.players.items()
            if player.is_alive and player.is_ai and player.role.has_night_action
            and not (player.role == Role.CAKIR and self.round - player.last_special_round < 2)
            and not (player.role == Role.ELIF_EYLUL and player.ship_partner is not None)
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _night_timeout(self) -> None:
        await asyncio.sleep(NIGHT_TIMEOUT)
        await self._resolve_night()

    async def record_action(self, uid: int, target_id: int, lang: Optional[str] = None) -> str:
        display_lang = lang if lang in LANGUAGES else self.lang
        if self.phase != Phase.NIGHT or self._resolving_night:
            return get_text(display_lang, "phase_night")
        p = self.players.get(uid)
        if not p or not p.is_alive:
            return get_text(display_lang, "not_alive")
        if not p.role.has_night_action:
            return get_text(display_lang, "no_night_action")
        if getattr(self, "_omer_silence", False):
            return "📖 Ömer Baba bu gece tüm yetenekleri susturdu."
        if p.role == Role.ELIF_EYLUL and p.ship_partner is not None:
            return "🚢 Gemi arkadaşını zaten seçtin."
        if p.role == Role.CAKIR and self.round - p.last_special_round < 2:
            return "⏳ Çakır'ın silahı henüz hazır değil; iki gece beklemelisin."
        t = self.players.get(target_id)
        if not t or not t.is_alive:
            return get_text(display_lang, "invalid_target")
        if target_id == uid and p.role != Role.SHIFOKOR:
            return get_text(display_lang, "self_target")
        if p.role.is_mafia_team and t.role.is_mafia_team:
            return get_text(display_lang, "invalid_target")
        if p.role == Role.ELIF_EYLUL:
            if target_id == uid:
                return "Kendini gemi arkadaşı seçemezsin."
            p.ship_partner = target_id
            p.has_acted = True
            return f"🚢 Gemi arkadaşın {html.escape(t.name)} olarak seçildi. Sen ölürsen o da ölecek."
        if p.role == Role.CAKIR:
            p.night_target = target_id
            p.has_acted = True
            p.last_special_round = self.round
            if self._all_acted():
                self._cancel()
                asyncio.create_task(self._resolve_night())
            return f"🥃 Hedefin {html.escape(t.name)}. Racon kesildi."
        p.night_target = target_id
        p.has_acted = True
        if self._all_acted():
            self._cancel()
            asyncio.create_task(self._resolve_night())
        return get_text(display_lang, "action_recorded", name=html.escape(t.name))

    async def skip_action(self, uid: int, lang: Optional[str] = None) -> str:
        display_lang = lang if lang in LANGUAGES else self.lang
        if self.phase != Phase.NIGHT or self._resolving_night:
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
        if self.phase != Phase.NIGHT or self._resolving_night:
            return
        self._resolving_night = True

        res = RoleEngine.resolve_night(self.players, self.lang)
        cakir_kills = []
        if not getattr(self, "_omer_silence", False):
            for uid, player in self.players.items():
                target_id = player.night_target
                if player.is_alive and player.role == Role.CAKIR and player.has_acted and target_id in self.players:
                    target = self.players[target_id]
                    if target.is_alive and target_id not in res.killed and target_id not in res.healed:
                        target.is_alive = False
                        res.killed.append(target_id)
                        cakir_kills.append(target_id)
        for player in self.players.values():
            if not player.is_alive and player.role == Role.ELIF_EYLUL and player.ship_partner in self.players:
                partner = self.players[player.ship_partner]
                if partner.is_alive:
                    partner.is_alive = False
                    if partner.user_id not in res.killed:
                        res.killed.append(partner.user_id)
        lines = [get_text(self.lang, "dawn") + "\n"]
        for victim_id in cakir_kills:
            victim = self.players[victim_id]
            lines.append(f"🥃 Süleyman Çakır Racon değil kafa kesti: <b>{html.escape(victim.name)}</b> — {html.escape(get_role_text(self.lang, victim.role.value)[0])}")

        if not res.killed:
            lines.append(get_text(self.lang, "nobody_died"))
        lines.extend(res.messages)
        await self.send(self.chat_id, "\n".join(lines))

        # KVP ölüm sahnesi: elenen her oyuncu grup içinde rol görseliyle duyurulur.
        for uid in res.killed:
            p = self.players[uid]
            role_name, _ = get_role_text(self.lang, p.role.value)
            death_text = get_text(
                self.lang, "player_killed", name=html.escape(p.name),
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
            is_ship_partner = any(
                owner.role == Role.ELIF_EYLUL and owner.ship_partner == uid
                for owner in self.players.values()
            )
            death_caption = await google_translate(death_caption, self.lang)
            image = KIZ_KULESI_DEATH_IMAGE if p.role == Role.ELIF_EYLUL or is_ship_partner else death_image_for(p.role, "night")
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

        if self.phase != Phase.NIGHT:
            return

        # Private: komissar results. AI oyuncuların Telegram hesabı yoktur;
        # negatif dahili ID'lere DM göndermeye çalışma.
        for k_uid, p in self.players.items():
            if (
                not p.is_ai and k_uid > 0
                and p.role == Role.KOMISSAR and p.is_alive
                and p.night_target in res.checked
            ):
                is_m = res.checked[p.night_target]
                t_name = self.players[p.night_target].name
                player_lang = await db.get_language(k_uid)
                result_text = get_text(player_lang, "mafia_result" if is_m else "town_result")
                if is_m:
                    try:
                        await db.add_investigation_success(k_uid, p.name)
                    except Exception:
                        log.exception("Komissar başarım sayacı güncellenemedi: %s", k_uid)
                await self.send(k_uid, get_text(player_lang, "check_result", name=html.escape(t_name), result=result_text))

        # Private: guvoh results. AI için özel mesaj gönderilmez.
        for g_uid, info in res.witnessed.items():
            player = self.players.get(g_uid)
            if not player or player.is_ai or not player.is_alive or g_uid <= 0:
                continue
            player_lang = await db.get_language(g_uid)
            await self.send(g_uid, get_text(player_lang, "witness_result", info=info))

        if self.phase != Phase.NIGHT:
            return

        # Baron herhangi bir gece etkisiyle elendiyse oyun anında biter.
        # Halk sonucu, rol açıklaması ve final duyurusu grup sohbetine gönderilir.
        baron = next((p for p in self.players.values()
                      if p.role == Role.DON and not p.is_alive), None)
        if baron is not None:
            log.info("Baron Mehmet Karahanlı gece elendi; Halk kazandı: game=%s", self.game_id)
            await self._end_game("town")
            return

        winner = RoleEngine.check_win(self.players)
        if winner:
            winner_id = RoleEngine.last_standing_winner_id(self.players)
            await self._end_game(winner, winner_id=winner_id)
        else:
            await self._begin_day()

    # ── Day ───────────────────────────────────────────────────────
    async def _begin_day(self) -> None:
        if self.phase == Phase.ENDED:
            return
        self._resolving_night = False
        self.phase = Phase.DAY

        alive_list = "\n".join(
            f"  ✅ {p.tag}" for p in self.players.values() if p.is_alive
        )
        await self.send(
            self.chat_id,
            get_text(self.lang, "day_started", round=self.round, count=len(self.alive()),
                     players=alive_list, seconds=DAY_TIMEOUT)
        )
        if self.phase != Phase.DAY:
            return

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
        if self.phase != Phase.DAY:
            return
        self.phase = Phase.VOTING
        self.votes.reset()

        alive = self.alive()
        # Simple display without role emoji (roles hidden).
        buttons = [
            [InlineKeyboardButton(f"🗳 {player.name[:55]}", callback_data=f"vote:{player.user_id}")]
            for player in alive
        ]
        buttons.append([InlineKeyboardButton(get_text(self.lang, "vote_skip"), callback_data="vote:skip")])

        if self.app is not None:
            try:
                vote_text = await google_translate(get_text(self.lang, "voting_started", seconds=VOTE_TIMEOUT), self.lang)
                vote_markup = await translate_markup(InlineKeyboardMarkup(buttons), self.lang)
                vote_msg = await self.app.bot.send_message(chat_id=self.chat_id, text=vote_text, reply_markup=vote_markup)
                self._vote_message_id = vote_msg.message_id
            except TelegramError as exc:
                log.warning("Oylama mesajı gönderilemedi: %s", exc)
                self._vote_message_id = None
        else:
            vote_text = await google_translate(get_text(self.lang, "voting_started", seconds=VOTE_TIMEOUT), self.lang)
            vote_markup = await translate_markup(InlineKeyboardMarkup(buttons), self.lang)
            await self.send(self.chat_id, vote_text, markup=vote_markup)
        if self.phase != Phase.VOTING:
            return

        # AI votes are collected concurrently so six AI players do not add up
        # to 6-18 seconds of serial waiting before the human vote timer starts.
        async def choose_vote(uid: int):
            await asyncio.sleep(random.uniform(1, 3))
            voter = self.players[uid]
            candidates = [player for player in self.alive() if player.user_id != uid]
            if voter.role.is_mafia_team:
                opponents = [player for player in candidates if not player.role.is_mafia_team]
            else:
                opponents = [player for player in candidates if player.role.is_mafia_team]
            if opponents:
                candidates = opponents
            return (uid, random.choice(candidates).user_id) if candidates else None

        ai_vote_tasks = [
            choose_vote(uid) for uid, player in self.players.items()
            if player.is_alive and player.is_ai
        ]
        if ai_vote_tasks:
            for choice in await asyncio.gather(*ai_vote_tasks):
                if choice is not None:
                    self.votes.cast(*choice)
        if self.phase != Phase.VOTING:
            return

        self._cancel()
        self._task = asyncio.create_task(self._vote_timeout())

    def cast_vote(self, voter: int, target: int, lang: Optional[str] = None) -> str:
        display_lang = lang if lang in LANGUAGES else self.lang
        if self.phase != Phase.VOTING or self._resolving_vote:
            return get_text(display_lang, "not_voting")
        vp = self.players.get(voter)
        tp = self.players.get(target)
        if not vp or not vp.is_alive:
            return get_text(display_lang, "cannot_vote")
        if not tp or not tp.is_alive:
            return get_text(display_lang, "invalid_target")
        if voter == target and vp.role != Role.DELI_HIKMET:
            return get_text(display_lang, "self_vote")
        if vp.role == Role.DELI_HIKMET:
            choices = [x.user_id for x in self.alive() if x.user_id != voter]
            if choices:
                target = random.choice(choices)
                tp = self.players[target]
        prev = self.votes.prev(voter)
        self.votes.cast(voter, target)
        if prev and prev != target and prev in self.players:
            return get_text(display_lang, "vote_changed", name=html.escape(tp.name))
        return get_text(display_lang, "vote_cast", name=html.escape(tp.name))

    async def _vote_timeout(self) -> None:
        await asyncio.sleep(VOTE_TIMEOUT)
        await self._resolve_vote()

    async def _resolve_vote(self) -> None:
        if self.phase != Phase.VOTING or self._resolving_vote:
            return
        self._resolving_vote = True
        if self._vote_message_id and self.app is not None:
            try:
                await self.app.bot.delete_message(chat_id=self.chat_id, message_id=self._vote_message_id)
            except TelegramError as exc:
                log.warning("Oylama mesajı silinemedi: %s", exc)
            self._vote_message_id = None
        log.info("Oylama çözülüyor: game=%s players_alive=%s", self.game_id, len(self.alive()))

        tally = self.votes.tally()
        if not tally:
            await self.send(self.chat_id, get_text(self.lang, "no_votes"))
            if self.phase == Phase.VOTING:
                await self._maybe_continue()
            return

        # Show results
        tally_lines = [
            get_text(self.lang, "vote_line", name=html.escape(self.players[uid].name), count=v)
            for uid, v in sorted(tally.items(), key=lambda x: -x[1])
            if uid in self.players
        ]
        max_v = max(tally.values())
        leaders = [uid for uid, v in tally.items() if v == max_v and uid in self.players]

        if len(leaders) > 1:
            names = ", ".join(html.escape(self.players[uid].name) for uid in leaders)
            await self.send(
                self.chat_id,
                get_text(self.lang, "vote_results") + "\n" + "\n".join(tally_lines) +
                "\n\n" + get_text(self.lang, "vote_tie", names=names)
            )
            if self.phase == Phase.VOTING:
                await self._maybe_continue()
            return

        condemned = leaders[0]
        p = self.players[condemned]
        log.info(
            "Oylama sonucu: game=%s condemned=%s role=%s votes=%s",
            self.game_id, condemned, p.role.value, max_v
        )

        # Suicid: wins immediately
        if p.role == Role.SUICID:
            p.is_alive = False
            traitor_msg = (
                get_text(self.lang, "vote_results") + "\n" + "\n".join(tally_lines) +
                "\n\n" + get_text(self.lang, "traitor_eliminated", name=html.escape(p.name))
            )
            await self.send(self.chat_id, traitor_msg)
            if self.phase != Phase.VOTING:
                return
            if self.app is not None:
                try:
                    await self.app.bot.send_photo(
                        chat_id=self.chat_id, photo=death_image_for(p.role, "vote"),
                        caption=f"⚖️ <b>Oylama kararı: {html.escape(p.name)}</b>\n🎭 Rol: {html.escape(get_role_text(self.lang, p.role.value)[0])}",
                        parse_mode=ParseMode.HTML
                    )
                except TelegramError as exc:
                    log.warning("Oylama ölüm görseli gönderilemedi: %s", exc)
            if self.phase == Phase.VOTING:
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
        self._resolving_vote = False
        self.phase = Phase.DEFENSE

        msg = get_text(
            self.lang, "defense_prompt", votes="\n".join(tally_lines),
            name=html.escape(p.name), seconds=DEFENSE_TIMEOUT
        )
        await self.send(self.chat_id, msg)
        if self.phase != Phase.DEFENSE:
            return

        self._cancel()
        self._task = asyncio.create_task(
            self._defense_timeout(condemned, kamikaze_extra)
        )
        log.info("Savunma süresi başladı: game=%s player=%s seconds=%s", self.game_id, condemned, DEFENSE_TIMEOUT)

    async def _defense_timeout(self, condemned_id: int, kamikaze_extra: Optional[int]) -> None:
        try:
            await asyncio.sleep(DEFENSE_TIMEOUT)
            log.info("Oylama infazı başlıyor: game=%s player=%s", self.game_id, condemned_id)
            await self._execute(condemned_id, kamikaze_extra)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "Oylama infazında beklenmeyen hata: game=%s player=%s phase=%s",
                self.game_id, condemned_id, self.phase.name
            )
            # İnfaz başladıktan sonra bir Telegram/görsel/çeviri hatası oyunu
            # savunma aşamasında kilitlemesin. Eleneni kesinleştir ve akışı sürdür.
            if self.phase == Phase.DEFENSE:
                condemned = self.players.get(condemned_id)
                if condemned is not None:
                    condemned.is_alive = False
                if kamikaze_extra and kamikaze_extra in self.players:
                    self.players[kamikaze_extra].is_alive = False
                self._executing_vote = False
                try:
                    if condemned is not None and condemned.role == Role.DON:
                        log.info("Baron oylamayla elendi; Halk galibiyeti zorlanıyor: game=%s", self.game_id)
                        await self._end_game("town")
                    else:
                        await self._maybe_continue()
                except Exception:
                    log.exception("Oylama sonrası kurtarma da başarısız: game=%s", self.game_id)

    async def _execute(self, condemned_id: int, kamikaze_extra: Optional[int]) -> None:
        if self.phase != Phase.DEFENSE or self._executing_vote:
            log.warning(
                "Oylama infazı atlandı: game=%s player=%s phase=%s executing=%s",
                self.game_id, condemned_id, self.phase.name, self._executing_vote
            )
            return
        self._executing_vote = True
        p = self.players.get(condemned_id)
        if not p or not p.is_alive:
            log.warning("Oylama hedefi bulunamadı/zaten ölü: game=%s player=%s", self.game_id, condemned_id)
            self._executing_vote = False
            return

        log.info(
            "Oylama hedefi kesinleşti: game=%s player=%s role=%s",
            self.game_id, condemned_id, p.role.value
        )
        p.is_alive = False
        role_name, _ = get_role_text(self.lang, p.role.value)
        msg = get_text(self.lang, "player_eliminated", name=html.escape(p.name), role=html.escape(role_name))
        if p.role == Role.DON:
            msg += "\n\n👑 Baron Mehmet Karahanlı oylamayla öldürüldü! 🏙 Halk kazandı."
        if p.role == Role.ELIF_EYLUL and p.ship_partner in self.players:
            partner = self.players[p.ship_partner]
            if partner.is_alive:
                partner.is_alive = False
                msg += f"\n\n🚢 Elif Eylül ile gemi arkadaşı <b>{html.escape(partner.name)}</b> birlikte Kız Kulesi'ne veda etti."

        if kamikaze_extra and self.players.get(kamikaze_extra):
            victim = self.players[kamikaze_extra]
            victim.is_alive = False
            msg += "\n\n" + get_text(self.lang, "kamikaze_hit", victim=html.escape(victim.name))

        await self.send(self.chat_id, msg)
        if self.phase != Phase.DEFENSE:
            self._executing_vote = False
            return
        if self.app is not None:
            eliminated_players = [p]
            if p.role == Role.ELIF_EYLUL and p.ship_partner in self.players:
                eliminated_players.append(self.players[p.ship_partner])
            if kamikaze_extra and self.players.get(kamikaze_extra):
                eliminated_players.append(self.players[kamikaze_extra])
            for eliminated in eliminated_players:
                try:
                    ship_partner_death = any(
                        owner.role == Role.ELIF_EYLUL and owner.ship_partner == eliminated.user_id
                        for owner in self.players.values()
                    )
                    scene = KIZ_KULESI_DEATH_IMAGE if ship_partner_death else death_image_for(eliminated.role, "vote" if eliminated is p else "night")
                    await self.app.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=scene,
                        caption=(f"⚰️ <b>{html.escape(eliminated.name)}</b> oyunun dışında kaldı.\n"
                                 f"🎭 {html.escape(get_role_text(self.lang, eliminated.role.value)[0])}"),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as exc:
                    log.warning("Oylama ölüm görseli gönderilemedi: %s", exc)

        if self.phase != Phase.DEFENSE:
            self._executing_vote = False
            return

        # Baron oylamayla elendiyse, fotoğraf gönderimi başarısız olsa bile
        # doğrudan Halk galibiyetiyle oyunu bitir.
        if p.role == Role.DON:
            log.info("Baron Mehmet Karahanlı oylamayla elendi; Halk kazandı: game=%s", self.game_id)
            await self._end_game("town")
            return

        self._executing_vote = False
        log.info("Oylama sonrası geceye geçiliyor: game=%s", self.game_id)
        await self._maybe_continue()

    async def _maybe_continue(self) -> None:
        self._resolving_night = False
        self._resolving_vote = False
        winner = RoleEngine.check_win(self.players)
        if winner:
            winner_id = RoleEngine.last_standing_winner_id(self.players)
            await self._end_game(winner, winner_id=winner_id)
        else:
            await self._begin_night()

    # ── End ───────────────────────────────────────────────────────
    async def _end_game(self, winner: str, winner_id: Optional[int] = None) -> None:
        self._resolving_night = False
        self._resolving_vote = False
        self._executing_vote = False
        self.phase = Phase.ENDED
        self._cancel()

        emoji = {"town": "🏙", "mafia": "🔫", "suicid": "☠️", "draw": "⚖️"}.get(winner, "🎭")
        reveal_rows = []
        for p in self.players.values():
            role_name, _ = get_role_text(self.lang, p.role.value)
            state = get_text(self.lang, "alive" if p.is_alive else "dead")
            reveal_rows.append(
                f"  {p.role.emoji} <b>{html.escape(p.name)}</b> — "
                f"{html.escape(role_name)} [{html.escape(state)}]"
            )
        reveal = "\n".join(reveal_rows)

        winner_text = get_text(self.lang, "winner_" + winner)
        # Dil dosyasında ilgili sonuç anahtarı bulunamazsa anahtar adını
        # oyunculara göstermek yerine anlaşılır bir sonuç metni kullan.
        if not winner_text or winner_text == "winner_" + winner:
            winner_text = {
                "town": "Halk kazandı!",
                "mafia": "Konsey kazandı!",
                "suicid": "Hain kazandı!",
                "draw": "Oyun berabere bitti.",
            }.get(winner, "Oyun sona erdi.")

        end_caption = (
            f"{emoji} <b>{html.escape(str(get_text(self.lang, 'game_over_title')))}</b>\n"
            f"<b>{html.escape(str(winner_text))}</b>\n\n"
            f"<b>{html.escape(str(get_text(self.lang, 'final_roles')))}</b>\n{reveal}\n\n"
            f"📊 {html.escape(str(get_text(self.lang, 'rounds_played', rounds=self.round)))}"
        )
        end_caption = await google_translate(end_caption, self.lang)
        # Final sonucunu genel safe_send katmanından bağımsız gönder.
        # safe_send bazı Telegram hatalarını yuttuğu için burada başarı/başarısızlık
        # açıkça loglanır; HTML reddedilirse düz metin yedeği denenir.
        log.info(
            "Final duyurusu gönderiliyor: game=%s chat=%s winner=%s chars=%s",
            self.game_id, self.chat_id, winner, len(end_caption)
        )
        if self.app is not None:
            # Telegram isteğinin sonsuza kadar bekleyip oyun bitiş akışını
            # kilitlemesini önle. Beklenmeyen istisnaları da mutlaka logla.
            plain_text = html.unescape(re.sub(r"</?[^>]+>", "", end_caption))
            try:
                await asyncio.wait_for(
                    self.app.bot.send_message(
                        chat_id=self.chat_id,
                        text=end_caption,
                        parse_mode=ParseMode.HTML,
                        connect_timeout=8,
                        read_timeout=12,
                        write_timeout=12,
                        pool_timeout=8,
                    ),
                    timeout=15,
                )
                log.info(
                    "Final duyurusu gönderildi: game=%s chat=%s",
                    self.game_id, self.chat_id
                )
            except Exception as send_error:
                log.exception(
                    "Final HTML duyurusu başarısız; düz metin deneniyor: game=%s chat=%s error_type=%s",
                    self.game_id, self.chat_id, type(send_error).__name__
                )
                try:
                    await asyncio.wait_for(
                        self.app.bot.send_message(
                            chat_id=self.chat_id,
                            text=plain_text,
                            connect_timeout=8,
                            read_timeout=12,
                            write_timeout=12,
                            pool_timeout=8,
                        ),
                        timeout=15,
                    )
                    log.info(
                        "Final düz metin duyurusu gönderildi: game=%s chat=%s",
                        self.game_id, self.chat_id
                    )
                except Exception:
                    log.exception(
                        "Final düz metin duyurusu da gönderilemedi: game=%s chat=%s",
                        self.game_id, self.chat_id
                    )
        else:
            try:
                await self.send(self.chat_id, end_caption)
                log.info("Final duyurusu send_fn ile gönderildi: game=%s", self.game_id)
            except Exception:
                log.exception("Final duyurusu send_fn hatası: game=%s", self.game_id)

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
        try:
            await db.end_game(self.game_id, winner, self.round)
        except Exception:
            log.exception("Oyun sonucu veritabanına yazılamadı: %s", self.game_id)

        player_elos = {}
        for player in self.players.values():
            if player.is_ai or player.user_id <= 0:
                continue
            try:
                stats = await db.get_stats(player.user_id)
                player_elos[player.user_id] = stats["elo"] if stats else 1000
            except Exception:
                log.exception("ELO bilgisi okunamadı: %s", player.user_id)
                player_elos[player.user_id] = 1000

        for p in self.players.values():
            if p.is_ai or p.user_id < 0:
                continue
            opponent_elos = [elo for other_uid, elo in player_elos.items() if other_uid != p.user_id]
            avg_opponent_elo = round(sum(opponent_elos) / len(opponent_elos)) if opponent_elos else 1000
            won = (
                (winner == "town"   and not p.role.is_mafia_team) or
                (winner == "mafia"  and p.role.is_mafia_team) or
                (winner == "suicid" and p.role == Role.SUICID and p.user_id == winner_id)
            )
            try:
                elo_ch = await db.update_player(
                    p.user_id, p.name, won, p.is_alive, p.role.value, avg_opponent_elo
                )
            except Exception:
                log.exception("Oyuncu istatistiği güncellenemedi: %s", p.user_id)
                continue
            sign = "+" if elo_ch >= 0 else ""
            try:
                player_lang = await db.get_language(p.user_id)
                await self.send(
                    p.user_id,
                    get_text(player_lang, "private_result",
                             result=get_text(player_lang, "win_label" if won else "loss_label"),
                             sign=sign, elo=elo_ch)
                )
            except Exception:
                log.exception("Oyuncuya sonuç DM'i gönderilemedi: %s", p.user_id)

    async def force_end(self) -> None:
        """Stop the active game without awarding a winner or changing player stats."""
        self._resolving_night = False
        self._resolving_vote = False
        self._executing_vote = False
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
            mates = [html.escape(q.name) for qid, q in self.players.items()
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

    def find_user(self, uid: int, game_id: Optional[str] = None) -> Optional[Game]:
        for g in self._g.values():
            if game_id is not None and g.game_id != game_id:
                continue
            if uid in g.players and g.phase != Phase.ENDED:
                return g
        return None


reg = Registry()

# ══════════════════════════════════════════════════════════════════
# SEND HELPER
# ══════════════════════════════════════════════════════════════════
async def safe_send(app: Application, chat_id: int, text: str,
                    markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        target_lang = await db.get_group_language(chat_id) if chat_id < 0 else await db.get_language(chat_id)
        if target_lang in ("az", "fa") and GOOGLE_TRANSLATE_API_KEY:
            text = await google_translate(text, target_lang)
            markup = await translate_markup(markup, target_lang)
        await app.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    except Forbidden:
        log.warning("Forbidden: %s", chat_id)
    except BadRequest as e:
        error_text = str(e).lower()
        if "chat not found" in error_text or "bot can't initiate conversation" in error_text:
            log.warning("Mesaj gönderilecek sohbet bulunamadı veya erişilemiyor (%s): %s", chat_id, e)
            return
        # Bozuk HTML biçimlendirmesi zafer/sonuç mesajını tamamen kaybettirmesin.
        log.warning("HTML mesajı gönderilemedi (%s): %s; düz metin deneniyor", chat_id, e)
        plain_text = html.unescape(re.sub(r"</?[^>]+>", "", text))
        try:
            await app.bot.send_message(
                chat_id=chat_id, text=plain_text, reply_markup=markup
            )
        except TelegramError as retry_error:
            log.error("Düz metin mesajı da gönderilemedi (%s): %s", chat_id, retry_error)
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
    if game.phase != Phase.NIGHT or not p or not p.is_alive or not p.role.has_night_action:
        return

    alive_targets = [
        (q.user_id, q.name[:45])
        for q in game.alive()
        if q.user_id != uid
        and not (p.role.is_mafia_team and q.role.is_mafia_team)
    ]
    if p.role == Role.SHIFOKOR:
        alive_targets.insert(0, (uid, f"{p.name[:36]} (kendin)"))
    if p.role == Role.CAKIR and game.round - p.last_special_round < 2:
        return
    if p.role == Role.ELIF_EYLUL and p.ship_partner is not None:
        return

    player_lang = await db.get_language(uid)
    special_verbs = {
        Role.ELIF_EYLUL: "Gemi arkadaşını seç",
        Role.CAKIR: "İki gecede bir hedef seç ve öldür",
    }
    verb = special_verbs.get(p.role, get_text(player_lang, "night_action_" + p.role.value))
    buttons = [
        [InlineKeyboardButton(get_text(player_lang, "select_target", name=name), callback_data=f"night:{game.game_id}:{uid2}")]
        for uid2, name in alive_targets
    ]
    buttons.append([InlineKeyboardButton(get_text(player_lang, "skip_action"), callback_data=f"night:{game.game_id}:skip")])

    try:
        await app.bot.send_message(
            chat_id=uid,
            text=get_text(player_lang, "night_action_title", action=verb),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except TelegramError as e:
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
    # Controls remain visible on the shared group message; callbacks enforce
    # host/admin permissions server-side, so one player's edit cannot hide them.
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
        prompt += "\n\n" + get_text(lang, "channel_required")
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
    help_text = get_text(lang, "help_message")
    cakir_help = {
        "tr": "🥃 Süleyman Çakır — Halk takımında, özel gece yeteneği yok.",
        "en": "🥃 Suleyman Cakir — Town team; no special night ability.",
        "ru": "🥃 Сулейман Чакыр — команда горожан; без ночной способности.",
        "uz": "🥃 Süleyman Çakır — Xalq jamoasi; tungi qobiliyati yo‘q.",
        "ar": "🥃 سليمان جاكير — ضمن فريق المواطنين؛ بلا قدرة ليلية خاصة.",
        "de": "🥃 Süleyman Çakır — Bürgerteam; keine besondere Nachtfähigkeit.",
    }
    help_text += "\n\n" + cakir_help.get(lang, cakir_help["tr"])
    try:
        await update.effective_message.reply_photo(
            photo=ROLE_IMAGES["mafia"], caption=help_text,
            parse_mode=ParseMode.HTML
        )
    except TelegramError:
        await update.effective_message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def cmd_newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_channel_membership(update, ctx):
        return
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text(
            get_text(await db.get_language(update.effective_user.id), "newgame_group")
        )
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
        g.lang, "lobby_created", host=html.escape(user.full_name), n=1,
        maximum=MAX_PLAYERS, players=f"  ✅ {html.escape(user.full_name)}",
        seconds=LOBBY_TIMEOUT
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
        plist = "\n".join(f"  ✅ {html.escape(p.name)}" for p in g.players.values())
        await update.message.reply_text(
            get_text(g.lang, "lobby_joined", name=html.escape(user.full_name), count=n, maximum=MAX_PLAYERS, players=plist),
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(n, is_host=True, lang=g.lang),
        )
    else:
        key = "already_joined" if user.id in g.players else ("lobby_full" if g.n() >= MAX_PLAYERS else "already_joined")
        await update.message.reply_text(get_text(g.lang, key))


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
        plist = "\n".join(f"  ✅ {html.escape(p.name)}" for p in g.players.values()) or "(boş)"
        await update.message.reply_text(
            get_text(g.lang, "lobby_left", name=html.escape(user.full_name), count=n, maximum=MAX_PLAYERS, players=plist),
            parse_mode=ParseMode.HTML,
            reply_markup=lobby_kb(n, is_host=True, lang=g.lang),
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
    if not g or g.phase == Phase.ENDED:
        await update.message.reply_text(get_text(await db.get_group_language(update.effective_chat.id), "no_game"))
        return
    lines = [get_text(g.lang, "players_title", game_id=g.game_id, phase=get_text(g.lang, "phase_" + g.phase.name.lower())) + "\n"]
    for p in g.players.values():
        icon = "✅" if p.is_alive else "💀"
        ai = " 🤖" if p.is_ai else ""
        lines.append(f"  {icon} {html.escape(p.name)}{ai}")
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
    if update.effective_chat.type in ("group", "supergroup"):
        g = reg.get(update.effective_chat.id)
        if g and (uid not in g.players or g.phase == Phase.ENDED):
            g = None
    else:
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
# CALLBACK MESSAGE EDITOR
# Text, photo-caption and media-without-caption messages are handled safely.
async def edit_callback_message(q, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    message = q.message
    if message is None:
        return await q.get_bot().send_message(
            chat_id=q.from_user.id, text=text, parse_mode=parse_mode,
            reply_markup=reply_markup
        )

    if message.text is not None:
        try:
            return await q.edit_message_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return None
            raise

    if message.caption is not None:
        try:
            return await q.edit_message_caption(
                caption=text, parse_mode=parse_mode, reply_markup=reply_markup
            )
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return None
            if "caption" not in str(exc).lower() and "too long" not in str(exc).lower():
                raise
            # Telegram captions are limited to 1024 characters. If the lobby
            # grows beyond that, move the updated lobby to a normal text post.
            await q.edit_message_reply_markup(reply_markup=None)
            return await q.get_bot().send_message(
                chat_id=message.chat_id, text=text, parse_mode=parse_mode,
                reply_markup=reply_markup
            )

    # Fotoğraf/video gibi metinsiz medya mesajlarında metin düzenlenemez.
    # Eski butonları kaldırıp güncel lobi bilgisini yeni mesaj olarak gönder.
    await q.edit_message_reply_markup(reply_markup=None)
    return await q.get_bot().send_message(
        chat_id=message.chat_id, text=text, parse_mode=parse_mode,
        reply_markup=reply_markup, reply_to_message_id=message.message_id
    )


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
        await edit_callback_message(
            q, get_text(lang, "choose_language"),
            parse_mode=ParseMode.HTML,
            reply_markup=language_kb(),
        )
        await q.answer()
        return

    if data == "channel_link_missing":
        await q.answer(
            get_text(await db.get_language(uid), "channel_url_missing"),
            show_alert=True,
        )
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
            await q.answer(get_text(await db.get_language(uid), "newgame_group"), show_alert=True)
            return

        elif data == "howto":
            if q.message and q.message.chat.type in ("group", "supergroup"):
                help_lang = await db.get_group_language(cid)
            else:
                help_lang = await db.get_language(uid)
            await q.answer(get_text(help_lang, "help_message")[:190], show_alert=True)
            return

        elif data == "stats_menu":
            if q.message and q.message.chat.type in ("group", "supergroup"):
                lang = await db.get_group_language(cid)
            else:
                lang = await db.get_language(uid)
            await q.answer()
            await edit_callback_message(
                q, get_text(lang, "stats_title"),
                parse_mode=ParseMode.HTML,
                reply_markup=stats_kb(lang),
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
                    f"{medals[i]} <b>{html.escape(r['username'])}</b> — " +
                    get_text(await db.get_language(uid), "leaderboard_row", elo=r['elo'], wins=r['wins'], games=r['games'], wr=r['wr'])
                )
            try:
                await ctx.application.bot.send_message(
                    uid, "\n".join(lines), parse_mode=ParseMode.HTML
                )
                await q.answer(get_text(await db.get_language(uid), "dm_leaderboard"))
            except TelegramError as exc:
                log.warning("Sıralama DM gönderilemedi (%s): %s", uid, exc)
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
            msg = get_text(lang, "personal_stats", username=html.escape(s['username']), games=s['games'],
                           wins=s['wins'], wr=wr, losses=s['losses'], survived=s['survived'],
                           elo=s['elo'], streak=s['best_streak'], roles=rw_lines)
            try:
                await ctx.application.bot.send_message(uid, msg, parse_mode=ParseMode.HTML)
                await q.answer(get_text(lang, "dm_stats"))
            except TelegramError as exc:
                log.warning("İstatistik DM gönderilemedi (%s): %s", uid, exc)
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
                plist = "\n".join(f"  ✅ {html.escape(p.name)}" for p in g.players.values())
                await edit_callback_message(
                    q, get_text(g.lang, "lobby_joined", name=html.escape(name), count=n, maximum=MAX_PLAYERS, players=plist),
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=True, lang=g.lang),
                )
            else:
                key = "already_joined" if uid in g.players else ("lobby_full" if g.n() >= MAX_PLAYERS else "already_joined")
                await q.answer(get_text(g.lang, key), show_alert=True)

        elif data == "leave":
            g = reg.get(cid)
            if g and g.leave(uid):
                await q.answer()
                n = g.n()
                plist = "\n".join(f"  ✅ {html.escape(p.name)}" for p in g.players.values()) or "(bo'sh)"
                await edit_callback_message(
                    q, get_text(g.lang, "lobby_joined", name=html.escape(name), count=n, maximum=MAX_PLAYERS, players=plist),
                    parse_mode=ParseMode.HTML,
                    reply_markup=lobby_kb(n, is_host=True, lang=g.lang),
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
                plist = "\n".join(f"  ✅ {html.escape(p.name)}" for p in g.players.values())
                await edit_callback_message(
                    q, get_text(g.lang, "lobby_joined", name=html.escape(name), count=n, maximum=MAX_PLAYERS, players=plist),
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
            await edit_callback_message(q, get_text(g.lang, "starting_game"), parse_mode=ParseMode.HTML)
            result = await g.start_game()
            if result != "ok":
                await safe_send(ctx.application, cid, result)

        # ── night action ───────────────────────────────────────
        elif data.startswith("night:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                _, game_id, ts = parts
                g = reg.find_user(uid, game_id=game_id)
            else:
                ts = parts[1]
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
