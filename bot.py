"""Telegram orqali Claude Code ni boshqarish.

Kompyuterda ishlab turadi: Telegramdan matn yoki ovozli vazifa qabul qiladi,
mavjud Claude sessiyalarini ko'rsatadi va tanlanganini davom ettiradi,
natijani matn va (xohlasangiz) ovoz ko'rinishida qaytaradi.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import ai
import attachments
import config
import history
import hub as hub_mod
import machines
import single_instance
import tg_retry
import tgfmt
from tg_retry import RetryingHTTPXRequest

from logging.handlers import RotatingFileHandler

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        # Har bir fayl ~5 MB, 3 ta eskisi saqlanadi (~20 MB umumiy).
        RotatingFileHandler(
            config.LOG_FILE, maxBytes=5 * 1024 * 1024,
            backupCount=3, encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
# Gemini SDK har javobda "non-text parts" deb ogohlantiradi — logni ifloslantiradi.
logging.getLogger("google_genai.types").setLevel(logging.ERROR)
logging.getLogger("google_genai").setLevel(logging.WARNING)
log = logging.getLogger("claude-tg")

MODELS = ["opus", "sonnet", "haiku"]


# --------------------------------------------------------------------------
# Holat — (chat_id, thread_id) bo'yicha ajratilgan.
# Shaxsiy chat va guruhning umumiy oqimida thread_id = 0.
# Forum Topic ichida esa Telegramning message_thread_id ishlatiladi.
# --------------------------------------------------------------------------
Scope = tuple[int, int]  # (chat_id, thread_id)


def scope_of(update: Update) -> Scope:
    chat = update.effective_chat
    msg = update.effective_message
    thread_id = 0
    if msg is not None and getattr(msg, "is_topic_message", False):
        thread_id = msg.message_thread_id or 0
    # callback query holatida ham xuddi shu logika kerak
    if update.callback_query and update.callback_query.message:
        m = update.callback_query.message
        if getattr(m, "is_topic_message", False):
            thread_id = m.message_thread_id or thread_id
    return (chat.id, thread_id)


@dataclass
class ChatState:
    # Qaysi kompyuterda ishlayapmiz. Sessiyalar shu kompyuterda yashaydi,
    # shuning uchun bu maydon loyiha/sessiyadan oldin keladi.
    agent_id: str = ""
    cwd: str = ""
    project_key: str = ""
    session_id: str = ""
    model: str = config.DEFAULT_MODEL
    voice_reply: bool = False
    last_report: str = ""
    # /newtopic yoki /bind bilan mavzu loyihaga bog'langanini eslab qolamiz.
    bound_project: str = ""

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "cwd": self.cwd,
            "project_key": self.project_key,
            "session_id": self.session_id,
            "model": self.model,
            "voice_reply": self.voice_reply,
            "bound_project": self.bound_project,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChatState":
        return cls(
            agent_id=data.get("agent_id", ""),
            cwd=data.get("cwd", ""),
            project_key=data.get("project_key", ""),
            session_id=data.get("session_id", ""),
            model=data.get("model", config.DEFAULT_MODEL),
            voice_reply=bool(data.get("voice_reply", False)),
            bound_project=data.get("bound_project", ""),
        )


_states: dict[Scope, ChatState] = {}


def _key_str(scope: Scope) -> str:
    return f"{scope[0]}:{scope[1]}"


def _parse_key(raw: str) -> Scope | None:
    # Eski format (faqat chat_id) ham qo'llab-quvvatlanadi — migratsiya.
    try:
        if ":" in raw:
            a, b = raw.split(":", 1)
            return (int(a), int(b))
        return (int(raw), 0)
    except (TypeError, ValueError):
        return None


def load_states() -> None:
    if not config.STATE_FILE.exists():
        return
    try:
        raw = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    for key, value in raw.items():
        scope = _parse_key(key)
        if scope is None:
            continue
        try:
            _states[scope] = ChatState.from_dict(value)
        except (TypeError, ValueError):
            continue


def save_states() -> None:
    try:
        config.STATE_FILE.write_text(
            json.dumps(
                {_key_str(k): v.to_dict() for k, v in _states.items()}, indent=2
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("Holatni saqlab bo'lmadi: %s", exc)


def state_for(scope: Scope) -> ChatState:
    st = _states.get(scope)
    if st is None:
        st = ChatState()
        _states[scope] = st
    return st


# --------------------------------------------------------------------------
# Ishlayotgan vazifalar
# --------------------------------------------------------------------------
@dataclass
class FollowUp:
    """Vazifa ishlab turganda kelgan qo'shimcha — navbatda kutadi."""

    prompt: str
    files: list[attachments.Attachment] = field(default_factory=list)


@dataclass
class RunningTask:
    agent_id: str          # qaysi kompyuterda ketyapti
    task_id: str           # hub orqali bekor qilish uchun
    cwd: str
    prompt: str
    voice_input: bool = False  # ovoz orqali kelgan bo'lsa — javob ham ovozda
    # Rasm/fayllar — hub'ga uzatilgach bo'shatiladi (xotira).
    files: list[attachments.Attachment] = field(default_factory=list)
    task: asyncio.Task | None = None  # start_task ichida to'ldiriladi
    started_at: float = field(default_factory=time.time)
    follow_ups: list[FollowUp] = field(default_factory=list)  # ishlab turganda kelgan qo'shimchalar

    async def cancel(self) -> None:
        await machines.cancel(self.agent_id, self.task_id)


_running: dict[Scope, RunningTask] = {}


# --------------------------------------------------------------------------
# Ruxsat
# --------------------------------------------------------------------------
def _write_env_var(var: str, value: int) -> bool:
    """`.env` faylidagi ro'yxatga qiymat qo'shadi (link.py bilan bir xil mantiq)."""
    import re
    path = config.BASE_DIR / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    match = re.search(rf"^{var}=(.*)$", text, re.M)
    if not match:
        text = text.rstrip() + f"\n{var}={value}\n"
    else:
        current = [p.strip() for p in match.group(1).split(",") if p.strip()]
        if str(value) in current:
            return False
        current.append(str(value))
        text = text[: match.start()] + f"{var}={','.join(current)}" + text[match.end():]
    try:
        path.write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def authorized(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return False
    # Shaxsiy chatda — foydalanuvchi ro'yxatda bo'lishi kerak.
    if chat.type == "private":
        return bool(config.ALLOWED_USER_IDS) and user.id in config.ALLOWED_USER_IDS

    # Guruhda avto-bind: allaqachon ruxsatli foydalanuvchi guruhda komanda
    # yozganda, guruh ID avtomatik ALLOWED_CHAT_IDS ga qo'shiladi. Bu
    # qulaylik uchun — foydalanuvchi qo'lda .env tahrirlashi shart emas.
    if user.id in config.ALLOWED_USER_IDS and chat.id not in config.ALLOWED_CHAT_IDS:
        if _write_env_var("ALLOWED_CHAT_IDS", chat.id):
            config.ALLOWED_CHAT_IDS.add(chat.id)
            log.info(
                "Guruh avtomatik ro'yxatga qo'shildi: %s (%s) — %s tomonidan",
                chat.title or "?", chat.id, user.full_name or user.id,
            )

    if chat.id not in config.ALLOWED_CHAT_IDS:
        return False
    # Ro'yxat bo'sh bo'lsa guruhdagi HAMMA kira olardi — bu bypassPermissions
    # rejimida kompyuterga to'liq kirish demak. Shuning uchun bo'sh ro'yxat
    # "hammaga ruxsat" emas, "hech kimga ruxsat" degani.
    if not config.ALLOWED_USER_IDS or user.id not in config.ALLOWED_USER_IDS:
        return False
    return True


def is_conflicting(scope: Scope, agent_id: str, cwd: str) -> Scope | None:
    """Boshqa mavzu ayni damda XUDDI SHU kompyuterning shu papkasida
    ishlayotgan bo'lsa, uning kalitini qaytaradi. Turli kompyuterdagi bir xil
    yo'l — boshqa-boshqa papka, ular to'qnashmaydi."""
    for other_scope, task in _running.items():
        if other_scope == scope:
            continue
        if task.agent_id == agent_id and task.cwd == cwd:
            return other_scope
    return None


# --------------------------------------------------------------------------
# Xabar yuborish yordamchilari — Forum Topic ichidagi mavzuga to'g'ri
# yo'llash uchun har bir chaqiruvga `message_thread_id` qo'shiladi.
# --------------------------------------------------------------------------
async def send_to(bot, scope: Scope, text: str, *, html_mode: bool = True,
                  reply_markup=None, **kwargs):
    chat_id, thread_id = scope
    return await bot.send_message(
        chat_id, text,
        message_thread_id=thread_id or None,
        parse_mode=ParseMode.HTML if html_mode else None,
        reply_markup=reply_markup,
        **kwargs,
    )


async def send_voice_to(bot, scope: Scope, voice: bytes, **kwargs):
    chat_id, thread_id = scope
    return await bot.send_voice(
        chat_id, voice=voice, message_thread_id=thread_id or None, **kwargs
    )


async def send_audio_to(bot, scope: Scope, audio: bytes, **kwargs):
    chat_id, thread_id = scope
    return await bot.send_audio(
        chat_id, audio=audio, message_thread_id=thread_id or None, **kwargs
    )


async def send_chat_action_to(bot, scope: Scope, action) -> None:
    chat_id, thread_id = scope
    try:
        await bot.send_chat_action(
            chat_id, action, message_thread_id=thread_id or None
        )
    except Exception:
        pass


async def deny(update: Update) -> None:
    user = update.effective_user
    uid = user.id if user else "?"
    text = (
        "⛔️ Sizga ruxsat yo'q.\n\n"
        f"Sizning Telegram ID: <code>{uid}</code>\n\n"
        "Kompyuterdagi <code>.env</code> faylida <code>ALLOWED_USER_IDS</code> "
        "ga shu raqamni yozing va botni qayta ishga tushiring."
    )
    if update.callback_query:
        await update.callback_query.answer("Ruxsat yo'q", show_alert=True)
    if update.effective_message:
        await update.effective_message.reply_html(text)


# --------------------------------------------------------------------------
# Klaviaturalar
# --------------------------------------------------------------------------
async def projects_keyboard(agent_id: str) -> InlineKeyboardMarkup:
    rows = []
    for proj in await machines.list_projects(agent_id):
        rows.append([
            InlineKeyboardButton(
                f"📁 {proj.name}  ({proj.session_count})",
                callback_data=f"proj:{proj.key}",
            )
        ])
    if not rows:
        rows = [[InlineKeyboardButton("Loyiha topilmadi", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


def machines_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for m in machines.connected():
        i = m.info
        busy = f" · {m.busy_tasks} vazifa" if m.busy_tasks else ""
        rows.append([
            InlineKeyboardButton(
                f"💻 {i.name}  ({i.project_count} loyiha{busy})",
                callback_data=f"pc:{i.agent_id}",
            )
        ])
    if not rows:
        rows = [[InlineKeyboardButton("Kompyuter ulanmagan", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


def sessions_keyboard(items: list, back: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    for s in items[:10]:
        label = tgfmt.trim(s.label, 44)
        rows.append([
            InlineKeyboardButton(f"💬 {label}", callback_data=f"sess:{s.session_id}")
        ])
    if back:
        rows.append([InlineKeyboardButton("⬅️ Loyihalar", callback_data=back)])
    if not rows:
        rows = [[InlineKeyboardButton("Sessiya topilmadi", callback_data="noop")]]
    return InlineKeyboardMarkup(rows)


def result_keyboard(has_report: bool, failed: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if failed:
        rows.append([
            InlineKeyboardButton("🔧 Xatoni tuzat", callback_data="quick:fix"),
            InlineKeyboardButton("🔁 Qayta urin", callback_data="quick:retry"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("▶️ Davom ettir", callback_data="quick:continue"),
            InlineKeyboardButton("🧪 Test qil", callback_data="quick:test"),
        ])
        rows.append([
            InlineKeyboardButton("📝 Kommit qil", callback_data="quick:commit"),
            InlineKeyboardButton("🔍 Tekshir", callback_data="quick:review"),
        ])
    if has_report:
        rows.append([InlineKeyboardButton("🔊 Ovozda eshitish", callback_data="say")])
    rows.append([
        InlineKeyboardButton("💬 Sessiyalar", callback_data="list:recent"),
        InlineKeyboardButton("🆕 Yangi", callback_data="new"),
    ])
    return InlineKeyboardMarkup(rows)


QUICK_PROMPTS = {
    "continue": "Davom ettir.",
    "test": "Testlarni ishga tushirib, natijasini ko'r. Bo'lmasa yozib qo'y.",
    "commit": (
        "O'zgarishlarni ko'rib, mazmunli commit xabari bilan bitta commit qil "
        "(agar loyiha git ostida bo'lsa). Push qilma."
    ),
    "review": (
        "Hozirgi ishning natijasini o'zing bir bor tekshir: mantiqiy xatolar, "
        "yo'q qilingan holatlar, xavfsizlik muammolari bormi? Topsang tuzat."
    ),
    "fix": (
        "Yuqoridagi xatoning sababini toping va tuzating. Zarur bo'lsa qo'shimcha "
        "diagnostika komandalari ishga tushiring."
    ),
    "retry": "Xuddi shu ishni qaytadan urin.",
}


async def _quick_action(query, ctx: ContextTypes.DEFAULT_TYPE,
                        st: ChatState, scope: Scope, action: str) -> None:
    prompt = QUICK_PROMPTS.get(action)
    if not prompt:
        return await query.answer("Noma'lum amal", show_alert=True)
    if scope in _running:
        _running[scope].follow_ups.append(FollowUp(prompt))
        return await query.answer("Navbatga qo'shildi")
    await query.answer("Boshlanyapti…")
    await _launch(ctx, scope, prompt, voice_input=False)


# --------------------------------------------------------------------------
# Komandalar
# --------------------------------------------------------------------------
HELP = """<b>Claude Code — Telegram boshqaruvi</b>

Shunchaki <b>matn</b> yoki <b>ovozli xabar</b> yuboring — u tanlangan sessiyada vazifa sifatida bajariladi.
Ovozda so'rasangiz, javob ham avtomatik ovozda keladi.
<b>Rasm yoki fayl</b> ham yuborsa bo'ladi: caption bilan — darhol vazifa;
caption'siz — keyingi matn yoki ovoz bilan birga ketadi (15 daqiqa kutadi).
Albom ham bo'ladi. Claude fayllarni o'zi ko'radi.

<b>Kompyuterlar</b>
/pc — ulangan kompyuterlar; birini tanlash
Bittasi yoniq bo'lsa avtomatik o'sha tanlanadi. Sessiyalar har kompyuterda
o'zida qoladi — kompyuter oldiga borganingizda o'sha yozishmalar joyida.

<b>Guruhda parallel ishlash</b>
/newtopic &lt;loyiha&gt;[@kompyuter] [nomi] — yangi mavzu ochib bog'lash
/bind &lt;loyiha&gt;[@kompyuter] — hozirgi mavzuni bog'lash
Har mavzu — alohida sessiya, alohida vazifa. Turli mavzular parallel ishlaydi.

<b>Sessiyalar</b>
/sessions — tanlangan kompyuterdagi oxirgi sessiyalar
/projects — loyihalar ro'yxati
/new — shu loyihada yangi sessiya
/here — hozir qaysi sessiyadaman
/cleanup — eski test sessiyalarni tozalash

<b>Boshqaruv</b>
/status — ishlayotgan vazifa holati (vazifa ishlab turganda yangi xabar navbatga tushadi)
/stop — vazifani to'xtatish
/voice — har doim ovozli javob yoqish/o'chirish
/say — oxirgi hisobotni ovozda yuborish
/model — model tanlash
/xarajat — so'nggi 30 kundagi vazifalar va xarajat
/id — Telegram ID
"""


async def _callback_agent(query, st: ChatState) -> str | None:
    """Tugma bosilganda kompyuterni aniqlaydi; topilmasa ogohlantiradi."""
    try:
        agent_id = machines.resolve(st.agent_id)
    except machines.NoMachine as exc:
        await query.answer(str(exc), show_alert=True)
        return None
    if agent_id != st.agent_id:
        st.agent_id = agent_id
        save_states()
    return agent_id


async def _agent_or_reply(update: Update, st: ChatState) -> str | None:
    """Ishlatiladigan kompyuterni aniqlaydi; topilmasa foydalanuvchiga aytadi."""
    try:
        agent_id = machines.resolve(st.agent_id)
    except machines.NoMachine as exc:
        await update.effective_message.reply_text(
            f"💻 {exc}\n\nUlangan kompyuterlar: /pc"
        )
        return None
    if agent_id != st.agent_id:
        st.agent_id = agent_id
        save_states()
    return agent_id


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))
    # Faqat shaxsiy chatda avtomatik loyiha tanlaymiz. Guruhda foydalanuvchi
    # ataylab mavzu ochib /bind yoki /newtopic bilan bog'lashi kerak.
    if not st.cwd and update.effective_chat.type == "private":
        try:
            agent_id = machines.resolve(st.agent_id)
            recent = await machines.list_sessions(agent_id, limit=1)
            if recent:
                _apply_session(st, recent[0])
                save_states()
        except (machines.NoMachine, ConnectionError):
            pass  # kompyuter ulanmagan bo'lsa ham /start javob bersin
    await update.effective_message.reply_html(HELP + "\n" + _where_text(st))


async def cmd_pc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Kompyuterlar ro'yxati va tanlash."""
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))

    items = machines.connected()
    if not items:
        return await update.effective_message.reply_html(
            "💻 <b>Hech qaysi kompyuter ulanmagan.</b>\n\n"
            "Kompyuterda <code>agent.bat</code> ni ishga tushiring."
        )

    # Argument bilan to'g'ridan-to'g'ri tanlash: /pc notebook
    if ctx.args:
        machine = machines.hub().find(" ".join(ctx.args))
        if machine is None:
            return await update.effective_message.reply_text(
                "Bunday kompyuter yo'q: " + " ".join(ctx.args)
            )
        st.agent_id = machine.agent_id
        st.cwd = st.project_key = st.session_id = ""
        save_states()
        return await update.effective_message.reply_html(
            f"💻 <b>{html.escape(machine.name)}</b> tanlandi.\n"
            "Loyihani tanlang: /projects"
        )

    lines = ["<b>Ulangan kompyuterlar</b>", ""]
    for m in items:
        i = m.info
        mark = " ← hozir" if i.agent_id == st.agent_id else ""
        busy = f" · {m.busy_tasks} vazifa ketyapti" if m.busy_tasks else ""
        lines.append(
            f"💻 <b>{html.escape(i.name)}</b>{mark}\n"
            f"    {html.escape(i.platform)} · {i.project_count} loyiha, "
            f"{i.session_count} sessiya{busy}\n"
            f"    rejim: {html.escape(i.permission_mode)}"
        )
    await update.effective_message.reply_html(
        "\n".join(lines), reply_markup=machines_keyboard()
    )


async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_html(
        f"Telegram ID: <code>{user.id if user else '?'}</code>"
    )


async def _project_names(agent_id: str, limit: int = 10) -> str:
    try:
        projects = await machines.list_projects(agent_id, limit)
    except Exception:
        return "(ro'yxat olinmadi)"
    return ", ".join(p.name for p in projects) or "(loyiha yo'q)"


async def _resolve_project(agent_id: str, arg: str):
    """Loyihani kalit yoki nom bo'yicha topadi (qisman moslik ham)."""
    proj = await machines.find_project(agent_id, arg)
    if proj is not None:
        return proj
    low = arg.lower()
    for p in await machines.list_projects(agent_id, limit=100):
        if p.name.lower() == low or p.name.lower().endswith("-" + low):
            return p
    return None


def _split_target(arg: str, st: ChatState) -> tuple[str, str | None]:
    """`loyiha@kompyuter` shaklini ajratadi. Kompyuter ko'rsatilmasa None."""
    if "@" in arg:
        project, _, pc = arg.partition("@")
        machine = machines.hub().find(pc.strip())
        if machine is not None:
            return project.strip(), machine.agent_id
        return project.strip(), ""  # topilmadi — chaqiruvchi xato beradi
    return arg, None


async def cmd_newtopic(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/newtopic <loyiha>[@kompyuter] [nomi]` — yangi mavzu ochib bog'laydi."""
    if not authorized(update):
        return await deny(update)

    chat = update.effective_chat
    if chat.type == "private":
        return await update.effective_message.reply_text(
            "Bu komanda faqat guruhda ishlaydi. Shaxsiy chatda /projects ishlating."
        )
    if not getattr(chat, "is_forum", False):
        return await update.effective_message.reply_text(
            "Bu guruhda Topics (Mavzular) yoqilmagan.\n"
            "Guruh sozlamalari → Topics ni yoqing."
        )

    st_general = state_for((chat.id, 0))
    agent_id = await _agent_or_reply(update, st_general)
    if agent_id is None:
        return

    if not ctx.args:
        return await update.effective_message.reply_html(
            "Ishlatish: <code>/newtopic &lt;loyiha&gt;[@kompyuter] [mavzu nomi]</code>\n"
            "Misol: <code>/newtopic oson-ish rezyume xatosi</code>\n"
            "Boshqa kompyuterda: <code>/newtopic oson-ish@ishxona</code>\n\n"
            f"💻 {html.escape(machines.display_name(agent_id))} dagi loyihalar: "
            + html.escape(await _project_names(agent_id))
        )

    project_arg, target = _split_target(ctx.args[0], st_general)
    if target == "":
        return await update.effective_message.reply_text(
            "Bunday kompyuter ulanmagan. Ro'yxat: /pc"
        )
    if target:
        agent_id = target
    title_rest = " ".join(ctx.args[1:]).strip()

    proj = await _resolve_project(agent_id, project_arg)
    if proj is None:
        return await update.effective_message.reply_html(
            f"Loyiha topilmadi: <code>{html.escape(project_arg)}</code>\n"
            f"💻 {html.escape(machines.display_name(agent_id))}: "
            + html.escape(await _project_names(agent_id))
        )

    pc_name = machines.display_name(agent_id)
    display = f"📁 {proj.name} · {pc_name}"
    if title_rest:
        display += f" · {tgfmt.trim(title_rest, 40)}"

    try:
        topic = await ctx.bot.create_forum_topic(chat.id, name=display[:128])
    except Exception as exc:
        return await update.effective_message.reply_html(
            f"❌ Mavzu yaratilmadi: {html.escape(str(exc))}\n\n"
            "Bot admin bo'lganini va \"Manage Topics\" ruxsati borligini tekshiring."
        )

    new_scope: Scope = (chat.id, topic.message_thread_id)
    st = state_for(new_scope)
    st.agent_id = agent_id
    st.cwd = proj.cwd
    st.project_key = proj.key
    st.bound_project = proj.key
    st.session_id = ""  # yangi mavzu — yangi sessiya
    save_states()

    await ctx.bot.send_message(
        chat.id,
        f"✅ <b>{html.escape(proj.name)}</b> ga bog'landi.\n"
        f"💻 {html.escape(pc_name)}\n"
        f"<code>{html.escape(proj.cwd)}</code>\n\n"
        "Vazifani yozing yoki ovozda ayting — <b>yangi sessiya</b> boshlanadi.\n"
        "Mavjud sessiyani davom ettirish uchun: /sessions",
        parse_mode=ParseMode.HTML,
        message_thread_id=topic.message_thread_id,
    )
    await update.effective_message.reply_html(
        f"🆕 Mavzu ochildi: <b>{html.escape(display)}</b>"
    )


async def cmd_bind(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/bind <loyiha>` — hozirgi mavzuni loyihaga bog'laydi."""
    if not authorized(update):
        return await deny(update)

    scope = scope_of(update)
    if scope[1] == 0 and update.effective_chat.type != "private":
        return await update.effective_message.reply_text(
            "Bu komanda mavzu ichida ishlaydi (guruhning umumiy oqimida emas)."
        )

    st = state_for(scope)
    agent_id = await _agent_or_reply(update, st)
    if agent_id is None:
        return

    if not ctx.args:
        return await update.effective_message.reply_html(
            "Ishlatish: <code>/bind &lt;loyiha&gt;[@kompyuter]</code>\n"
            f"💻 {html.escape(machines.display_name(agent_id))}: "
            + html.escape(await _project_names(agent_id))
        )

    project_arg, target = _split_target(ctx.args[0], st)
    if target == "":
        return await update.effective_message.reply_text(
            "Bunday kompyuter ulanmagan. Ro'yxat: /pc"
        )
    if target:
        agent_id = target

    proj = await _resolve_project(agent_id, project_arg)
    if proj is None:
        return await update.effective_message.reply_html(
            f"Loyiha topilmadi: <code>{html.escape(project_arg)}</code>\n"
            f"💻 {html.escape(machines.display_name(agent_id))}: "
            + html.escape(await _project_names(agent_id))
        )

    st.agent_id = agent_id
    st.cwd = proj.cwd
    st.project_key = proj.key
    st.bound_project = proj.key
    save_states()
    await update.effective_message.reply_html(
        f"✅ Bu mavzu <b>{html.escape(proj.name)}</b> ga bog'landi.\n"
        f"<code>{html.escape(proj.cwd)}</code>\n\n"
        + _where_text(st)
    )


async def cmd_cost(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Oxirgi 30 kun ichidagi vazifalar va xarajat."""
    if not authorized(update):
        return await deny(update)

    days = 30
    if ctx.args:
        try:
            days = max(1, min(365, int(ctx.args[0])))
        except ValueError:
            pass
    await update.effective_message.reply_html(
        history.summary(chat_id=update.effective_user.id, days=days)
    )


def _apply_session(st: ChatState, info) -> None:
    st.session_id = info.session_id
    st.cwd = info.cwd
    st.project_key = info.project_key
    if getattr(info, "agent_id", ""):
        st.agent_id = info.agent_id


def _basename(path: str) -> str:
    """Yo'lning oxirgi qismi. Masofadagi kompyuter Linux ham, Windows ham
    bo'lishi mumkin — shuning uchun `Path` emas, ikkala ajratgichni ham
    hisobga oladigan qo'lda kesish."""
    cleaned = (path or "").replace("\\", "/").rstrip("/")
    return cleaned.rsplit("/", 1)[-1] if cleaned else ""


def _where_text(st: ChatState) -> str:
    pc = machines.display_name(st.agent_id) if st.agent_id else "tanlanmagan"
    head = f"💻 <b>{html.escape(pc)}</b>"
    if not st.cwd:
        return head + "\n📂 Loyiha tanlanmagan — /sessions yoki /projects"
    session = (
        f"sessiya <code>{st.session_id[:8]}</code>"
        if st.session_id
        else "yangi sessiya"
    )
    voice = "yoqilgan" if st.voice_reply else "o'chirilgan"
    return (
        f"{head}\n"
        f"📂 <b>{html.escape(_basename(st.cwd))}</b>\n"
        f"<code>{html.escape(st.cwd)}</code>\n"
        f"🧵 {session} · 🤖 {st.model} · 🔊 {voice}"
    )


async def cmd_here(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))
    await update.effective_message.reply_html(_where_text(st))


async def cmd_sessions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))
    agent_id = await _agent_or_reply(update, st)
    if agent_id is None:
        return
    # Mavzu loyihaga bog'langan bo'lsa — faqat o'sha loyihaning sessiyalari.
    # Aralash ro'yxatdan keraklisini topish qiyin.
    want_all = bool(ctx.args) and ctx.args[0].lower() in ("all", "hammasi", "*")
    project = None if want_all else (st.project_key or None)

    try:
        items = await machines.list_sessions(agent_id, project=project, limit=10)
        if not items and project:
            # Bu loyihada hali sessiya yo'q — boshqalarini ko'rsatamiz.
            items = await machines.list_sessions(agent_id, limit=10)
            project = None
    except ConnectionError as exc:
        return await update.effective_message.reply_text(f"💻 {exc}")

    if not items:
        return await update.effective_message.reply_text(
            "Sessiya topilmadi. Yangi sessiya boshlash uchun shunchaki "
            "vazifani yozing."
        )

    scope_label = (f"📁 {html.escape(_basename(st.cwd) or project)}"
                   if project else "barcha loyihalar")
    lines = [f"<b>Sessiyalar</b> · 💻 "
             f"{html.escape(machines.display_name(agent_id))} · {scope_label}", ""]
    now = time.time()
    any_open = False
    for s in items:
        mark = "🟢" if getattr(s, "is_open", False) else "💬"
        any_open = any_open or getattr(s, "is_open", False)
        lines.append(
            f"{mark} <b>{html.escape(tgfmt.trim(s.label, 60))}</b>\n"
            f"    <code>{html.escape(s.project_name)}</code> · "
            f"{s.user_turns} xabar · {tgfmt.human_age(now - s.mtime)}"
        )
    if any_open:
        lines.append("")
        lines.append("🟢 — hozir kompyuterda ochiq. Unga yozsangiz "
                     "yozishmalar aralashib ketishi mumkin.")
    if st.session_id:
        lines.append("")
        lines.append(f"Hozir bog'langan: <code>{st.session_id[:8]}</code>")
    lines.append("")
    lines.append("Birini bosing — shu mavzu o'sha sessiyada davom etadi.")
    if project:
        lines.append("Barcha loyihalar uchun: <code>/sessions all</code>")

    await update.effective_message.reply_html(
        "\n".join(lines), reply_markup=sessions_keyboard(items)
    )


async def cmd_projects(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))
    agent_id = await _agent_or_reply(update, st)
    if agent_id is None:
        return
    try:
        keyboard = await projects_keyboard(agent_id)
    except ConnectionError as exc:
        return await update.effective_message.reply_text(f"💻 {exc}")
    await update.effective_message.reply_html(
        f"<b>Loyihalar</b> · 💻 {html.escape(machines.display_name(agent_id))}\n"
        "Birini tanlang:", reply_markup=keyboard
    )


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    scope = scope_of(update)
    st = state_for(scope)
    if not st.cwd:
        return await update.effective_message.reply_text(
            "Avval loyihani tanlang: /projects"
        )
    st.session_id = ""
    save_states()
    dropped = attachments.clear(scope)
    if dropped is not None and dropped.timer is not None:
        dropped.timer.cancel()
    extra = ""
    if dropped is not None and dropped.items:
        extra = f"\n📎 Kutayotgan biriktirmalar ({dropped.summary()}) tashlab yuborildi."
    await update.effective_message.reply_html(
        "🆕 Yangi sessiya. Keyingi xabaringiz yangi suhbatni boshlaydi."
        f"{extra}\n\n" + _where_text(st)
    )


async def cmd_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))
    st.voice_reply = not st.voice_reply
    save_states()
    await update.effective_message.reply_text(
        "🔊 Ovozli javob yoqildi — har bir hisobot ovozda ham keladi."
        if st.voice_reply
        else "🔇 Ovozli javob o'chirildi. Kerak bo'lsa /say yoki tugma orqali."
    )


async def cmd_model(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))
    arg = (ctx.args[0].lower() if ctx.args else "")
    if arg in MODELS:
        st.model = arg
        save_states()
        return await update.effective_message.reply_text(f"🤖 Model: {arg}")
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(m, callback_data=f"model:{m}") for m in MODELS]]
    )
    await update.effective_message.reply_text(
        f"Hozirgi model: {st.model}", reply_markup=keyboard
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    scope = scope_of(update)
    task = _running.get(scope)
    if not task:
        return await update.effective_message.reply_html(
            "Hozir vazifa yo'q.\n\n" + _where_text(state_for(scope))
        )
    elapsed = int(time.time() - task.started_at)
    await update.effective_message.reply_html(
        f"⏳ Ishlayapti — {tgfmt.human_duration(elapsed * 1000)}\n"
        f"<i>{html.escape(tgfmt.trim(task.prompt, 200))}</i>\n\n"
        "To'xtatish: /stop"
    )


async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    task = _running.get(scope_of(update))
    if not task:
        return await update.effective_message.reply_text("To'xtatadigan vazifa yo'q.")
    await task.cancel()
    await update.effective_message.reply_text("🛑 To'xtatilyapti…")


async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Test/shovqin sessiyalarni ro'yxatlab, tugma bilan o'chirishga taklif qiladi."""
    if not authorized(update):
        return await deny(update)

    st = state_for(scope_of(update))
    agent_id = await _agent_or_reply(update, st)
    if agent_id is None:
        return
    try:
        noisy = await machines.noisy_sessions(agent_id)
    except ConnectionError as exc:
        return await update.effective_message.reply_text(f"💻 {exc}")
    if not noisy:
        return await update.effective_message.reply_text(
            "🧹 Tozalash uchun test sessiyalar topilmadi."
        )

    lines = [f"<b>Test/shovqin sessiyalar</b> · 💻 "
             f"{html.escape(machines.display_name(agent_id))}", ""]
    total_size = 0
    for s in noisy[:30]:
        size = int(s.get("size") or 0)
        total_size += size
        lines.append(
            f"• <i>{html.escape(tgfmt.trim(s.get('label') or '', 60))}</i>  "
            f"<code>{html.escape(s.get('project_name') or '')}</code>  ({size // 1024} KB)"
        )
    if len(noisy) > 30:
        lines.append(f"… va yana {len(noisy) - 30} ta")
    lines.append("")
    lines.append(f"Jami: <b>{len(noisy)}</b> ta · {total_size // 1024} KB")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🗑 Barchasini o'chirish ({len(noisy)})",
                              callback_data="cleanup:yes")],
        [InlineKeyboardButton("❌ Bekor qilish", callback_data="cleanup:no")],
    ])
    await update.effective_message.reply_html("\n".join(lines), reply_markup=keyboard)


async def cmd_say(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    st = state_for(scope_of(update))
    if not st.last_report:
        return await update.effective_message.reply_text(
            "Ovozga aylantiradigan hisobot yo'q."
        )
    await send_voice_report(ctx, scope_of(update), st.last_report)


# --------------------------------------------------------------------------
# Tugmalar
# --------------------------------------------------------------------------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    query = update.callback_query
    data = query.data or ""
    scope = scope_of(update)
    st = state_for(scope)

    if data == "noop":
        return await query.answer()

    if data.startswith("model:"):
        st.model = data.split(":", 1)[1]
        save_states()
        await query.answer(f"Model: {st.model}")
        return await query.edit_message_text(f"🤖 Model: {st.model}")

    if data.startswith("pc:"):
        machine = machines.hub().get(data.split(":", 1)[1])
        if machine is None:
            return await query.answer("Kompyuter ulanmagan", show_alert=True)
        st.agent_id = machine.agent_id
        st.cwd = st.project_key = st.session_id = ""
        save_states()
        await query.answer(machine.name)
        return await query.edit_message_text(
            f"💻 <b>{html.escape(machine.name)}</b> tanlandi.\n"
            "Loyihani tanlang:",
            parse_mode=ParseMode.HTML,
            reply_markup=await projects_keyboard(machine.agent_id),
        )

    if data.startswith("proj:"):
        agent_id = await _callback_agent(query, st)
        if agent_id is None:
            return
        proj = await machines.find_project(agent_id, data.split(":", 1)[1])
        if not proj:
            return await query.answer("Loyiha topilmadi", show_alert=True)
        st.cwd = proj.cwd
        st.project_key = proj.key
        save_states()
        await query.answer(proj.name)
        items = await machines.list_sessions(agent_id, project=proj.key, limit=10)
        return await query.edit_message_text(
            f"📁 <b>{html.escape(proj.name)}</b> · 💻 "
            f"{html.escape(machines.display_name(agent_id))}\n"
            "Sessiyani tanlang yoki /new:",
            parse_mode=ParseMode.HTML,
            reply_markup=sessions_keyboard(items, back="list:projects"),
        )

    if data.startswith("sess:"):
        agent_id = await _callback_agent(query, st)
        if agent_id is None:
            return
        info = await machines.find_session(agent_id, data.split(":", 1)[1])
        if not info:
            return await query.answer("Sessiya topilmadi", show_alert=True)
        _apply_session(st, info)
        save_states()
        await query.answer("Tanlandi")
        tail = await machines.session_tail(agent_id, info.session_id, turns=2)
        preview = ""
        if tail:
            role, text = tail[-1]
            who = "Siz" if role == "user" else "Claude"
            preview = f"\n\n<i>Oxirgi ({who}):</i>\n{html.escape(tgfmt.trim(text, 400))}"
        return await query.edit_message_text(
            f"✅ Sessiya tanlandi\n\n{_where_text(st)}"
            f"\n\n<b>{html.escape(tgfmt.trim(info.label, 80))}</b>{preview}"
            "\n\nEndi vazifani yozing yoki ovozda ayting.",
            parse_mode=ParseMode.HTML,
        )

    if data == "list:machines":
        await query.answer()
        return await query.edit_message_text(
            "<b>Kompyuterlar</b>\nBirini tanlang:",
            parse_mode=ParseMode.HTML,
            reply_markup=machines_keyboard(),
        )

    if data == "list:projects":
        agent_id = await _callback_agent(query, st)
        if agent_id is None:
            return
        await query.answer()
        return await query.edit_message_text(
            f"<b>Loyihalar</b> · 💻 {html.escape(machines.display_name(agent_id))}\n"
            "Birini tanlang:",
            parse_mode=ParseMode.HTML,
            reply_markup=await projects_keyboard(agent_id),
        )

    if data == "list:recent":
        agent_id = await _callback_agent(query, st)
        if agent_id is None:
            return
        await query.answer()
        return await query.edit_message_text(
            f"<b>Oxirgi sessiyalar</b> · 💻 "
            f"{html.escape(machines.display_name(agent_id))}",
            parse_mode=ParseMode.HTML,
            reply_markup=sessions_keyboard(
                await machines.list_sessions(agent_id, limit=10)
            ),
        )

    if data == "new":
        st.session_id = ""
        save_states()
        await query.answer("Yangi sessiya")
        return await query.edit_message_text(
            "🆕 Yangi sessiya tayyor. Vazifani yuboring.\n\n" + _where_text(st),
            parse_mode=ParseMode.HTML,
        )

    if data == "say":
        await query.answer("Ovoz tayyorlanyapti…")
        if st.last_report:
            await send_voice_report(ctx, scope, st.last_report)
        return

    if data == "stop":
        task = _running.get(scope)
        if task:
            await task.cancel()
            return await query.answer("To'xtatilyapti")
        return await query.answer("Vazifa yo'q")

    if data == "cleanup:yes":
        agent_id = await _callback_agent(query, st)
        if agent_id is None:
            return
        await query.answer("Tozalanyapti…")
        try:
            stats = await machines.cleanup(agent_id)
        except ConnectionError as exc:
            return await query.edit_message_text(f"💻 {exc}")
        deleted = int(stats.get("deleted") or 0)
        freed = int(stats.get("freed") or 0)
        return await query.edit_message_text(
            f"🧹 <b>Tozalandi.</b>  {deleted} ta sessiya, {freed // 1024} KB "
            f"bo'shatildi — 💻 {html.escape(machines.display_name(agent_id))}.",
            parse_mode=ParseMode.HTML,
        )

    if data == "cleanup:no":
        await query.answer("Bekor qilindi")
        return await query.edit_message_text("Bekor qilindi.")

    if data.startswith("quick:"):
        return await _quick_action(query, ctx, st, scope, data.split(":", 1)[1])

    await query.answer()


# --------------------------------------------------------------------------
# Xabarlar
# --------------------------------------------------------------------------
async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    text = (update.effective_message.text or "").strip()
    if text:
        await start_task(update, ctx, text, voice_input=False)


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    message = update.effective_message
    if not config.GEMINI_API_KEY:
        return await message.reply_text(
            "Ovozli xabar uchun GEMINI_API_KEY kerak (.env fayliga qo'shing)."
        )

    media = message.voice or message.audio
    mime = getattr(media, "mime_type", None) or "audio/ogg"
    notice = await message.reply_text("🎤 Tinglayapman…")
    try:
        tg_file = await media.get_file()
        audio = bytes(await tg_file.download_as_bytearray())
        text = await ai.transcribe(audio, mime)
    except ai.GeminiBusy:
        log.warning("Gemini band — ovoz o'girilmadi")
        return await notice.edit_text(
            "🕐 Gemini hozir band. Bir daqiqadan keyin ovozni qayta yuboring, "
            "yoki vazifani matn qilib yozing."
        )
    except Exception as exc:
        log.exception("Ovozni matnga o'girish xatosi")
        return await notice.edit_text(
            f"Ovozni tushunolmadim: {tgfmt.trim(str(exc), 200)}\n\n"
            "Qaytadan yuborib ko'ring yoki matn qilib yozing."
        )

    if not text:
        return await notice.edit_text("Ovozdan matn chiqmadi. Qaytadan urinib ko'ring.")

    await notice.edit_text(
        f"🎤 <i>{html.escape(tgfmt.trim(text, 900))}</i>", parse_mode=ParseMode.HTML
    )
    await start_task(update, ctx, text, voice_input=True)


# --------------------------------------------------------------------------
# Rasm va fayllar
#
# Biriktirma darhol vazifa bo'lmaydi: caption bo'lsa qisqa kutib (albomning
# qolgan bo'laklari kelsin) boshlaymiz; bo'lmasa keyingi matn/ovoz xabari
# ularni oladi (attachments.py). Har scope uchun bitta taymer.
# --------------------------------------------------------------------------
async def _download_attachment(message) -> attachments.Attachment | None:
    """Xabardagi rasm/faylni yuklaydi. Bo'lmasa yoki xato bo'lsa None
    (foydalanuvchiga sabab yozib)."""
    if message.photo:
        media = message.photo[-1]  # eng katta o'lcham
        name, mime = f"photo_{message.message_id}.jpg", "image/jpeg"
    elif message.document:
        media = message.document
        name = attachments.safe_name(media.file_name or "", f"file_{message.message_id}")
        mime = media.mime_type or "application/octet-stream"
    else:
        return None

    limit_mb = attachments.MAX_TOTAL_BYTES // (1024 * 1024)
    if (media.file_size or 0) > attachments.MAX_TOTAL_BYTES:
        await message.reply_text(
            f"📎 {name} juda katta — {limit_mb} MB gacha qabul qilinadi."
        )
        return None
    try:
        tg_file = await media.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:
        log.warning("Biriktirma yuklanmadi: %s", exc)
        await message.reply_text("📎 Faylni yuklab bo'lmadi, qayta yuboring.")
        return None
    return attachments.Attachment(name=name, mime=mime, data=data)


async def _box_timer(ctx: ContextTypes.DEFAULT_TYPE, scope: Scope,
                     box: attachments.PendingBox, reply_to) -> None:
    """Caption bo'lsa — debounce'dan keyin vazifa; bo'lmasa ogohlantirish va eskirish."""
    try:
        # Albom bo'laklari deyarli bir vaqtda keladi — har biri taymerni qayta
        # boshlaydi. Ogohlantirishni ham shu kutishdan keyin yuboramiz, aks
        # holda yuborilayotgan paytda bekor qilinib, dublikat chiqib qoladi.
        await asyncio.sleep(attachments.DEBOUNCE_SEC)
        if box.caption:
            if not attachments.drop(scope, box):
                return  # allaqachon olingan (masalan matn kelib qoldi)
            box.timer = None  # o'z vazifamizni bekor qilib qo'ymaslik uchun
            await _start_or_queue(ctx, scope, box.caption, voice_input=False,
                                  reply_to=reply_to, box=box)
            return

        text = f"📎 {box.summary()} kutmoqda — vazifani yozing yoki ovoz yuboring."
        if box.notice is None:
            box.notice = await reply_to.reply_text(text)
        else:
            try:
                await box.notice.edit_text(text)
            except BadRequest:
                pass
        await asyncio.sleep(attachments.TTL_SEC)
        if attachments.drop(scope, box) and box.notice is not None:
            try:
                await box.notice.edit_text("⌛ Biriktirmalar eskirdi — qayta yuboring.")
            except BadRequest:
                pass
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Biriktirma taymeri xatosi")


def _schedule_box(ctx: ContextTypes.DEFAULT_TYPE, scope: Scope,
                  box: attachments.PendingBox, reply_to) -> None:
    """Har yangi biriktirmada taymer qaytadan boshlanadi."""
    if box.timer is not None:
        box.timer.cancel()
    box.timer = asyncio.create_task(_box_timer(ctx, scope, box, reply_to))


async def on_attachment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update):
        return await deny(update)
    message = update.effective_message
    scope = scope_of(update)
    att = await _download_attachment(message)
    if att is None:
        return
    try:
        box = attachments.add(scope, att, message.caption or "")
    except attachments.TooLarge as exc:
        return await message.reply_text(f"📎 {exc}")
    _schedule_box(ctx, scope, box, message)


# --------------------------------------------------------------------------
# Vazifani bajarish
# --------------------------------------------------------------------------
# Tiker — hodisa bo'lmasa ham vaqt va "hozir" qatori yangilanib tursin.
TICK_SEC = 4.0
# Ikki tahrir orasidagi eng kam vaqt — Telegram flood (429) bo'lmasin.
EDIT_MIN_INTERVAL = 3.0


class Progress:
    """Bitta xabarni davriy yangilab, jarayonni ko'rsatib turadi.

    Ro'yxatda oxirgi 6 hodisa (🔧 tool, ✅ tugagan, ⚠️ xato, 💬 matn), ostida
    bitta "hozir" qatori: qaysi tool ketyapti / javob yozilyapti / o'ylayapti
    va qachondan beri. Tiker har TICK_SEC da tahrirlaydi — Claude uzoq jim
    ishlasa ham xabar muzlamaydi.
    """

    def __init__(self, message, prompt: str) -> None:
        self.message = message
        self.prompt = prompt
        self.lines: list[str] = []
        self.tool_count = 0
        self.started = time.time()
        self.last_edit = 0.0
        self.last_text = ""
        self.lock = asyncio.Lock()
        # "Hozir" qatori: turi (tool | typing | thinking | ""), matni, qachondan.
        self.now_kind = ""
        self.now_text = ""
        self.now_since = 0.0
        self._ticker: asyncio.Task | None = None

    # -- tiker ------------------------------------------------------------
    def start_ticker(self) -> None:
        if self._ticker is None:
            self._ticker = asyncio.create_task(self._tick())

    async def stop(self) -> None:
        """Tikerni to'xtatib, tugashini kutadi — yakuniy tahrir bilan to'qnashmasin."""
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            ticker.cancel()
            try:
                await ticker
            except asyncio.CancelledError:
                pass

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_SEC)
            await self.flush()

    # -- hodisalar --------------------------------------------------------
    def _set_now(self, kind: str, text: str) -> None:
        if self.now_kind != kind:
            self.now_since = time.time()
        self.now_kind, self.now_text = kind, text

    def _clear_now(self) -> None:
        self.now_kind, self.now_text = "", ""

    def _mark_done(self, desc: str) -> None:
        for i in range(len(self.lines) - 1, -1, -1):
            if self.lines[i] == f"🔧 {desc}":
                self.lines[i] = f"✅ {desc}"
                return

    async def __call__(self, kind: str, text: str) -> None:
        if kind == "tool_start":
            # Nomi ma'lum, tavsifi (fayl, komanda) hali kelmadi.
            self.now_since = time.time()
            self.now_kind, self.now_text = "tool", f"{text} …"
        elif kind == "tool":
            self.tool_count += 1
            self.lines.append(f"🔧 {text}")
            if self.now_kind == "tool":
                self.now_text = text  # tool_start'dagi vaqt saqlanadi
            else:
                self.now_since = time.time()
                self.now_kind, self.now_text = "tool", text
        elif kind == "tool_done":
            self._mark_done(text)
            self._clear_now()
        elif kind == "tool_error":
            self.lines.append(f"⚠️ {text}")
            self._clear_now()
        elif kind == "typing":
            try:
                chars = int(json.loads(text).get("chars") or 0)
            except (ValueError, AttributeError):
                chars = 0
            self._set_now("typing", f"Javob yozilmoqda… {chars} belgi")
        elif kind == "thinking":
            self._set_now("thinking", "O'ylayapti…")
        elif kind == "retry":
            self.lines.append(f"🔁 {text}")
        elif kind == "wait":
            # Kutish holati bitta qatorda yangilanib tursin, ro'yxatni
            # to'ldirmasin.
            self.lines = [ln for ln in self.lines if not ln.startswith("⏸")]
            self.lines.append(f"⏸ {text}")
            return await self.flush(force=True)
        elif kind == "start":
            self.lines.append(f"▶️ {text}")
        elif kind == "text":
            self.lines.append(f"💬 {tgfmt.trim(text, 120)}")
            self._clear_now()  # matn keldi — oldingi tool/yozish tugagan
        else:
            return
        self.lines = self.lines[-6:]
        await self.flush()

    # -- ko'rinish --------------------------------------------------------
    def _now_line(self) -> str:
        if not self.now_kind:
            return ""
        icon = {"tool": "▶", "typing": "✍️", "thinking": "🤔"}[self.now_kind]
        secs = int(time.time() - self.now_since)
        return f"{icon} {self.now_text} ({secs} s)"

    def render(self) -> str:
        now = time.time()
        # Telegram HTML uchun faqat < > & majburiy — qo'shtirnoq va apostrof
        # o'z holicha qolsin (`O'ylayapti`, `echo "x"` o'qishga oson).
        body = "\n".join(html.escape(line, quote=False) for line in self.lines)
        now_line = self._now_line()
        if now_line:
            now_line = html.escape(now_line, quote=False)
            body = f"{body}\n{now_line}" if body else now_line
        elapsed = tgfmt.human_duration(int((now - self.started) * 1000))
        text = (
            f"⏳ <b>Ishlayapti</b> · {elapsed} · {self.tool_count} amal\n"
            f"<i>{html.escape(tgfmt.trim(self.prompt, 120))}</i>\n\n{body}"
        )
        return tgfmt.trim(text, 3800)

    async def flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_edit < EDIT_MIN_INTERVAL:
            return
        async with self.lock:
            text = self.render()
            if text == self.last_text:
                return
            try:
                await self.message.edit_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🛑 To'xtatish", callback_data="stop")]]
                    ),
                )
                self.last_text = text
                self.last_edit = now
            except BadRequest:
                pass
            except Exception as exc:
                log.debug("Progress yangilanmadi: %s", exc)


async def start_task(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    *,
    voice_input: bool = False,
) -> None:
    await _start_or_queue(ctx, scope_of(update), prompt, voice_input=voice_input,
                          reply_to=update.effective_message)


async def _start_or_queue(
    ctx: ContextTypes.DEFAULT_TYPE,
    scope: Scope,
    prompt: str,
    *,
    voice_input: bool,
    reply_to,
    box: attachments.PendingBox | None = None,
) -> None:
    """Vazifani boshlaydi yoki (ishlab turgan bo'lsa) navbatga qo'yadi.

    Kutayotgan biriktirmalar (rasm, fayl) shu yerda vazifaga qo'shiladi —
    matn, ovoz va caption yo'llari hammasi shu orqali o'tadi. `box` caption
    yo'lidan keladi (allaqachon olingan); qolganlar uchun o'zimiz olamiz.
    """
    if box is None:
        box = attachments.take(scope)
    files = list(box.items) if box is not None else []
    if box is not None:
        if box.timer is not None:
            box.timer.cancel()
            box.timer = None
        if box.notice is not None:
            try:
                await box.notice.edit_text(f"📎 {box.summary()} vazifaga qo'shildi.")
            except Exception:
                pass
    clip = f"\n📎 {attachments.summary(files)}" if files else ""

    active = _running.get(scope)
    if active is not None:
        # Ishlab turgan vazifaga qo'shimcha ko'rsatma sifatida navbatga qo'yamiz.
        active.follow_ups.append(FollowUp(prompt, files))
        elapsed = tgfmt.human_duration(int((time.time() - active.started_at) * 1000))
        return await reply_to.reply_html(
            f"📝 <b>Navbatga qo'shildi</b> (avvalgi ish {elapsed} dan beri ishlayapti)\n"
            f"<i>{html.escape(tgfmt.trim(prompt, 200))}</i>{clip}\n\n"
            "Avvalgi tugagach o'zi boshlanadi. Zudlik bilan: /stop"
        )
    await _launch(ctx, scope, prompt, voice_input=voice_input, reply_to=reply_to, files=files)


async def _launch(
    ctx: ContextTypes.DEFAULT_TYPE,
    scope: Scope,
    prompt: str,
    *,
    voice_input: bool = False,
    reply_to=None,
    files: list[attachments.Attachment] | None = None,
) -> None:
    """Vazifani boshlaydi. `_start_or_queue` va follow-up halqasi shundan foydalanadi."""
    st = state_for(scope)
    files = list(files or [])

    async def send(text: str, *, html_mode: bool = True):
        if reply_to is not None:
            if html_mode:
                return await reply_to.reply_html(text)
            return await reply_to.reply_text(text)
        return await send_to(ctx.bot, scope, text, html_mode=html_mode)

    # Kompyuterni aniqlaymiz: tanlangani ulangan bo'lsa o'sha, aks holda
    # bittasi ulangan bo'lsa avtomatik o'sha.
    try:
        agent_id = machines.resolve(st.agent_id)
    except machines.NoMachine as exc:
        # Tanlangan kompyuter vaqtincha uzilgan bo'lishi mumkin — tarmoq bu
        # yerda tez-tez uziladi. Uni tashlab yubormaymiz, vazifani navbatga
        # qo'yib qayta ulanishini kutamiz.
        if st.agent_id and machines.hub().known_name(st.agent_id):
            agent_id = st.agent_id
        else:
            return await send(
                f"💻 {exc}\n\nUlangan kompyuterlar: /pc", html_mode=False
            )
    if agent_id != st.agent_id:
        st.agent_id = agent_id
        save_states()

    if not st.cwd:
        # Guruh mavzusida — hech qachon avtomatik tanlamaymiz. Bu mavzuga
        # ataylab /bind yoki /newtopic bilan loyiha biriktirilishi kerak.
        if scope[1] != 0:
            return await send(
                "Bu mavzu hali loyihaga bog'lanmagan.\n"
                "Bog'lash: <code>/bind &lt;loyiha&gt;</code>",
            )
        recent = await machines.list_sessions(agent_id, limit=1)
        if not recent:
            return await send(
                "Loyiha tanlanmagan va sessiya topilmadi. /projects",
                html_mode=False,
            )
        _apply_session(st, recent[0])
        save_states()
        await send("Loyiha avtomatik tanlandi:\n" + _where_text(st))

    # Boshqa mavzu shu kompyuterning shu loyihasida ishlayotgan bo'lsa,
    # ogohlantiraman — foydalanuvchi to'liq parallel rejimni tanlagan.
    conflict = is_conflicting(scope, agent_id, st.cwd)
    if conflict is not None:
        other = _running[conflict]
        await send(
            "⚠️ <b>Diqqat: parallel yozish</b>\n"
            f"Shu loyihada ({html.escape(_basename(st.cwd))}) boshqa mavzu "
            f"allaqachon ishlayapti (<i>{html.escape(tgfmt.trim(other.prompt, 100))}</i>).\n"
            "Fayllar bir-birini bosishi mumkin — ogohsizlik oxirida kod yo'qolishi mumkin."
        )

    # Sessiya kompyuterda Claude Code ilovasida ochiq bo'lsa ogohlantiramiz.
    # Ikkalasi bitta .jsonl ga yozadi — biri ikkinchisining qatorlarini
    # bosib ketishi mumkin.
    if st.session_id and machines.is_online(agent_id):
        state = await machines.session_state(agent_id, st.session_id)
        if state.get("open"):
            where = state.get("entrypoint") or "Claude Code"
            label = {"claude-desktop": "Claude Code ilovasida",
                     "cli": "terminalda"}.get(where, f"({where})")
            await send(
                "⚠️ <b>Bu sessiya hozir kompyuterda ochiq</b>\n"
                f"💻 {html.escape(machines.display_name(agent_id))} — "
                f"{html.escape(label)}\n\n"
                "Ikkalasi bitta faylga yozadi va yozishmangiz buzilishi "
                "mumkin. Kompyuterda o'sha sessiyani yopib qo'ying, "
                "yoki bu yerda /new bilan yangi sessiya boshlang.\n\n"
                "<i>Vazifa baribir yuborildi.</i>"
            )

    clip = f" · 📎 {attachments.summary(files)}" if files else ""
    status = await send(
        f"⏳ <b>Boshlandi</b> · 💻 {html.escape(machines.display_name(agent_id))}{clip}\n"
        f"<i>{html.escape(tgfmt.trim(prompt, 150))}</i>"
    )
    progress = Progress(status, prompt)
    progress.start_ticker()

    running = RunningTask(
        agent_id=agent_id, task_id=machines.new_task_id(),
        cwd=st.cwd, prompt=prompt, voice_input=voice_input, files=files,
    )
    _running[scope] = running
    running.task = asyncio.create_task(_execute(ctx, scope, progress, st, running))


async def _execute(
    ctx: ContextTypes.DEFAULT_TYPE,
    scope: Scope,
    progress: Progress,
    st: ChatState,
    running: RunningTask,
) -> None:
    try:
        # Kompyuter uzilgan bo'lsa — darhol rad etmaymiz, qaytishini kutamiz.
        if not machines.is_online(running.agent_id):
            name = machines.hub().known_name(running.agent_id) or "kompyuter"

            async def waiting(remaining: float) -> None:
                await progress("wait", f"{name} qayta ulanishini kutyapmiz "
                                       f"({int(remaining)} s qoldi)")

            await waiting(OFFLINE_WAIT)
            if not await machines.wait_for(running.agent_id, OFFLINE_WAIT, waiting):
                raise ConnectionError(
                    f"{name} {int(OFFLINE_WAIT // 60)} daqiqa ichida ulanmadi"
                )

        payload = await machines.run(
            running.agent_id, running.prompt, running.cwd,
            st.session_id or None, st.model,
            task_id=running.task_id, on_progress=progress,
            attachments=running.files,
        )
        result = machines.RunOutcome.from_dict(payload)
    except ConnectionError as exc:
        log.warning("Kompyuter bilan aloqa uzildi: %s", exc)
        result = machines.RunOutcome(
            ok=False,
            error=(f"💻 {exc}\n\n"
                   "Vazifa yuborilmadi. Kompyuter yonganini tekshiring "
                   "va qaytadan yuboring."),
        )
    except asyncio.TimeoutError:
        result = machines.RunOutcome(ok=False, error="Vazifa vaqti tugadi.")
    except Exception as exc:
        log.exception("Vazifa xatosi")
        result = machines.RunOutcome(ok=False, error=str(exc))
    finally:
        _running.pop(scope, None)
        running.files = []  # baytlar hub'ga ketdi — xotirani bo'shatamiz
        await progress.stop()

    if result.session_id:
        st.session_id = result.session_id
        save_states()

    history.record(
        chat_id=scope[0], project=st.project_key or _basename(st.cwd),
        session_id=result.session_id, model=result.model,
        prompt=running.prompt, ok=result.ok, cancelled=result.cancelled,
        duration_ms=result.duration_ms, cost_usd=result.cost_usd,
        tools=len(result.tools_used),
    )

    elapsed = tgfmt.human_duration(result.duration_ms)
    footer = (
        f"⏱ {elapsed} · 🔧 {len(result.tools_used)} amal · "
        f"🧵 <code>{html.escape(result.session_id[:8])}</code>"
    )
    if result.cost_usd:
        footer += f" · 💵 ${result.cost_usd:.3f}"

    if result.cancelled:
        head = "🛑 <b>To'xtatildi</b>"
    elif result.ok:
        head = "✅ <b>Bajarildi</b>"
    else:
        head = "❌ <b>Xato</b>"

    try:
        await progress.message.edit_text(f"{head}\n{footer}", parse_mode=ParseMode.HTML)
    except Exception:
        pass

    body = result.text or result.error or "(javob bo'sh)"
    st.last_report = body
    save_states()

    # Ovozli javob talab qilingan bo'lsa — matn yuborilayotganda parallel tayyorlaymiz.
    # Foydalanuvchi uzun hisobotni o'qib borishi bilan ovoz tayyor bo'lib qoladi.
    voice_needed = result.text and (st.voice_reply or running.voice_input)
    voice_task = (
        asyncio.create_task(_prepare_voice(result.text))
        if voice_needed else None
    )

    kb = result_keyboard(bool(result.text), failed=not result.ok and not result.cancelled)
    chunks = tgfmt.html_chunks(body)
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        try:
            await send_to(
                ctx.bot, scope, chunk,
                reply_markup=kb if is_last else None,
                disable_web_page_preview=True,
            )
        except BadRequest:
            # HTML noto'g'ri chiqsa — oddiy matn bilan yuboramiz.
            await send_to(
                ctx.bot, scope, tgfmt.split_plain(body)[index],
                html_mode=False,
                reply_markup=kb if is_last else None,
            )
        await asyncio.sleep(0.1)

    if voice_task is not None:
        try:
            audio, ext = await voice_task
            await send_voice_to(ctx.bot, scope, voice=audio, filename=f"hisobot.{ext}")
        except Exception as exc:
            log.warning("Ovoz yuborilmadi: %s", exc)
            await send_to(
                ctx.bot, scope,
                f"🔇 Ovoz tayyorlanmadi: {tgfmt.trim(str(exc), 200)}",
                html_mode=False,
            )

    # Ishlab turganda kelgan qo'shimchalarni ketma-ket bajaramiz.
    for follow in running.follow_ups:
        clip = f"\n📎 {attachments.summary(follow.files)}" if follow.files else ""
        await send_to(
            ctx.bot, scope,
            f"▶️ Navbatdagi vazifa:\n<i>{html.escape(tgfmt.trim(follow.prompt, 300))}</i>{clip}",
        )
        await _launch(ctx, scope, follow.prompt, voice_input=running.voice_input,
                      files=follow.files)


async def _prepare_voice(report: str) -> tuple[bytes, str]:
    """Hisobotdan ovoz tayyorlaydi (xulosalash + sintez)."""
    summary = await ai.spoken_summary(report)
    return await ai.synthesize(summary)


async def send_voice_report(ctx: ContextTypes.DEFAULT_TYPE, scope: Scope, report: str) -> None:
    await send_chat_action_to(ctx.bot, scope, ChatAction.RECORD_VOICE)
    try:
        audio, ext = await _prepare_voice(report)
    except Exception as exc:
        log.warning("Ovoz tayyorlanmadi: %s", exc)
        return await send_to(
            ctx.bot, scope, f"🔇 Ovoz tayyorlanmadi: {exc}", html_mode=False
        )

    filename = f"hisobot.{ext}"
    try:
        await send_voice_to(ctx.bot, scope, voice=audio, filename=filename)
    except Exception:
        try:
            await send_audio_to(
                ctx.bot, scope, audio=audio, filename=filename, title="Hisobot"
            )
        except Exception as exc:
            log.warning("Ovoz yuborilmadi: %s", exc)
            await send_to(
                ctx.bot, scope, f"🔇 Ovoz yuborilmadi: {exc}", html_mode=False
            )


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # Conflict = shu token bilan boshqa joyda ham bot ishlayapti. Mahalliy
    # nusxalarni qulf to'xtatadi, lekin boshqa kompyuterdagisini emas —
    # bunda xabarlar jimgina yo'qoladi, shuning uchun alohida ogohlantiramiz.
    from telegram.error import Conflict

    if isinstance(ctx.error, Conflict):
        log.error(
            "CONFLICT: shu bot tokeni bilan boshqa nusxa ham getUpdates qilyapti. "
            "Xabarlar ikkisi orasida yo'qoladi — ikkinchisini to'xtating."
        )
        return
    log.error("Bot xatosi", exc_info=ctx.error)


# --------------------------------------------------------------------------
# Qorovul: tarmoq uzilganda polling halqasi jimgina o'lib qolishi mumkin —
# jarayon tirik qoladi, lekin bot javob bermaydi. Buni sezib, jarayondan
# chiqamiz; start.bat 10 sekunddan keyin toza holda qayta yoqadi.
# --------------------------------------------------------------------------
RESTART_FLAG = config.BASE_DIR / ".restarted"
_watchdog_task: asyncio.Task | None = None
WATCHDOG_INTERVAL = 45
MAX_FAILURES = 5          # ketma-ket muvaffaqiyatsiz aloqa tekshiruvi
MAX_DEFERRALS = 10        # vazifa ishlayotganda necha marta kechiktiramiz
# PTB uzun so'rov bilan har ~10 sekundda getUpdates qiladi. 150 sekundlik
# sukunat — polling haqiqatan to'xtaganini bildiradi.
POLL_SILENCE_LIMIT = 150
# Kompyuter uzilganda vazifani shu muddat ichida kutamiz. Bu yerda
# tarmoq bir necha daqiqaga uzilib turishi odatiy hol.
OFFLINE_WAIT = 600.0


def _restart_process(reason: str) -> None:
    log.error("Qayta yoqilyapti: %s", reason)
    try:
        RESTART_FLAG.write_text(reason, encoding="utf-8")
    except OSError:
        pass
    logging.shutdown()
    # sys.exit() bu yerda ishlamaydi — asyncio uni yutib yuboradi.
    os._exit(1)


async def _watchdog(app: Application) -> None:
    failures = 0
    deferrals = 0
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)

        updater = app.updater
        # `updater.running` yolg'on tinchlik beradi: ichki polling vazifasi
        # o'lsa ham True bo'lib qolaveradi. Shuning uchun asosiy o'lchov —
        # oxirgi muvaffaqiyatli getUpdates qachon bo'lgani.
        silence = tg_retry.seconds_since_last_poll()
        polling_dead = (
            updater is None
            or not updater.running
            or silence > POLL_SILENCE_LIMIT
        )
        if polling_dead and silence > POLL_SILENCE_LIMIT:
            log.error("getUpdates %.0f sekunddan beri javob bermayapti.", silence)

        if not polling_dead:
            try:
                await app.bot.get_me()
                failures = 0
                deferrals = 0
                continue
            except Exception as exc:
                failures += 1
                log.warning("Telegram bilan aloqa yo'q (%d/%d): %s",
                            failures, MAX_FAILURES, exc)
                if failures < MAX_FAILURES:
                    continue

        reason = "polling to'xtagan" if polling_dead else "aloqa tiklanmadi"

        # Vazifa ishlayotgan bo'lsa natijani yo'qotmaslik uchun biroz kutamiz.
        if _running and deferrals < MAX_DEFERRALS:
            deferrals += 1
            log.warning("%s, lekin vazifa ishlab turibdi — kechiktiramiz (%d/%d)",
                        reason, deferrals, MAX_DEFERRALS)
            continue

        _restart_process(reason)


async def _announce_restart(app: Application) -> None:
    """Qayta yoqilgandan keyin foydalanuvchini ogohlantiradi.

    Kutilayotgan yangilanishlar tashlab yuborilgani uchun uzilish paytida
    yozilgan xabar yetib bormagan bo'lishi mumkin.
    """
    if not RESTART_FLAG.exists():
        return
    try:
        reason = RESTART_FLAG.read_text(encoding="utf-8").strip()
        RESTART_FLAG.unlink()
    except OSError:
        return

    text = (
        "🔄 Bot tarmoq uzilishidan keyin qayta yoqildi "
        f"(<i>{html.escape(reason)}</i>).\n\n"
        "Uzilish paytida yozgan xabaringiz yetib bormagan bo'lishi mumkin — "
        "kerak bo'lsa qaytadan yuboring."
    )
    for chat_id in config.ALLOWED_USER_IDS:
        try:
            await app.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.debug("Qayta yoqilish xabari yuborilmadi (%s): %s", chat_id, exc)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("pc", "Kompyuter tanlash"),
        BotCommand("sessions", "Oxirgi sessiyalar"),
        BotCommand("projects", "Loyihalar"),
        BotCommand("newtopic", "Yangi mavzu (guruh) + loyiha"),
        BotCommand("bind", "Hozirgi mavzuni loyihaga bog'lash"),
        BotCommand("new", "Yangi sessiya"),
        BotCommand("here", "Qayerdaman"),
        BotCommand("status", "Vazifa holati"),
        BotCommand("stop", "Vazifani to'xtatish"),
        BotCommand("voice", "Ovozli javob"),
        BotCommand("say", "Oxirgi hisobotni ovozda"),
        BotCommand("model", "Model tanlash"),
        BotCommand("xarajat", "So'nggi vazifalar va xarajat"),
        BotCommand("cleanup", "Test sessiyalarni tozalash"),
        BotCommand("id", "Telegram ID"),
    ])
    me = await app.bot.get_me()
    log.info("Bot ishga tushdi: @%s", me.username)
    log.info("Claude CLI: %s", config.CLAUDE_BIN)
    log.info("Ruxsat berilgan ID: %s", config.ALLOWED_USER_IDS or "YO'Q — /id yuboring")

    await _announce_restart(app)
    # `app.create_task` bu yerda erta — ilova hali ishga tushmagan va PTB
    # ogohlantirish beradi. Oddiy asyncio vazifasi, havolasi saqlanadi
    # (aks holda axlat yig'uvchi uni yo'q qilishi mumkin).
    global _watchdog_task
    _watchdog_task = asyncio.create_task(_watchdog(app))
    log.info("Qorovul yoqildi — polling har %d sekundda tekshiriladi.",
             WATCHDOG_INTERVAL)

    # Agentlar uchun hub. Bot va hub bitta jarayonda — hub'ni to'xtatmasdan
    # botni qayta yoqib bo'lmaydi, lekin sozlash ancha sodda bo'ladi.
    hub = hub_mod.Hub(config.AGENT_TOKEN, name="claude-tg")
    machines.bind(hub)
    await hub.start(config.HUB_HOST, config.HUB_PORT)


async def post_shutdown(app: Application) -> None:
    # Qorovul asyncio vazifasi — to'xtatmasak, chiqishda
    # "Task was destroyed but it is pending" xatosi chiqadi.
    global _watchdog_task
    if _watchdog_task is not None:
        _watchdog_task.cancel()
        try:
            await _watchdog_task
        except (asyncio.CancelledError, Exception):
            pass
        _watchdog_task = None
    try:
        await machines.hub().stop()
    except Exception:
        pass


def main() -> None:
    problems = config.missing_settings()
    fatal = [p for p in problems if "TELEGRAM_BOT_TOKEN" in p]
    for problem in problems:
        log.warning(problem)
    if fatal:
        raise SystemExit("TELEGRAM_BOT_TOKEN kiritilmagan — .env faylini to'ldiring.")

    # Ikkinchi nusxa Telegramning getUpdates oqimini o'g'irlaydi va xabarlar
    # yo'qoladi — shuning uchun darhol, tinch chiqamiz.
    try:
        single_instance.acquire()
    except single_instance.AlreadyRunning as exc:
        log.warning("%s", exc)
        raise SystemExit(0) from exc

    load_states()

    # O'zbekistondan Telegram API ga ulanish beqaror — SSL handshake timeout
    # va host reset xatolari tez-tez uchraydi. RetryingHTTPXRequest har bir
    # so'rovni tarmoq xatosi uchun 5 marta qayta yuboradi.
    common_kwargs = dict(
        connect_timeout=30.0, read_timeout=30.0,
        write_timeout=30.0, pool_timeout=30.0,
    )
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .request(RetryingHTTPXRequest(**common_kwargs))
        .get_updates_request(RetryingHTTPXRequest(
            connect_timeout=30.0, read_timeout=40.0,
            write_timeout=30.0, pool_timeout=30.0,
            track_polling=True,  # qorovul uchun yurak urishi
        ))
        .build()
    )

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("newtopic", cmd_newtopic))
    app.add_handler(CommandHandler("bind", cmd_bind))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("here", cmd_here))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("say", cmd_say))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler(["pc", "kompyuter"], cmd_pc))
    app.add_handler(CommandHandler(["xarajat", "cost"], cmd_cost))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, on_attachment))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
