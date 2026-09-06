"""Bot sozlamalari — .env faylidan o'qiladi."""
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int_set(raw: str) -> set[int]:
    out = set()
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.add(int(part))
    return out


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_IDS = _int_set(os.getenv("ALLOWED_USER_IDS", ""))
# Guruh chat ID lari (Forum guruhlar uchun). Bo'sh — faqat shaxsiy chatlar.
# Guruh ID lari manfiy: -1001234567890 ko'rinishida.
ALLOWED_CHAT_IDS = _int_set(os.getenv("ALLOWED_CHAT_IDS", ""))

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest").strip()

# Asosiy model 503 ("high demand") qaytarsa shu ro'yxat bo'yicha navbat bilan
# urinib ko'riladi. Gemini bandligi tez-tez uchraydi, shuning uchun zaxira shart.
_FALLBACKS = os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-3.8-flash,gemini-3.6-flash,gemini-3.1-flash-lite"
)
GEMINI_MODELS = [GEMINI_MODEL] + [
    m.strip() for m in _FALLBACKS.split(",") if m.strip() and m.strip() != GEMINI_MODEL
]

# --- Hub (server) va agent (kompyuter) ---
# Hub faqat localhost'da tinglaydi; tashqi dunyoga nginx TLS bilan chiqaradi.
HUB_HOST = os.getenv("HUB_HOST", "127.0.0.1").strip()
HUB_PORT = int(os.getenv("HUB_PORT", "8787"))
# Agentlar shu maxfiy so'z bilan tanitiladi. Hub va har bir agentda bir xil.
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "").strip()
AGENT_HUB_URL = os.getenv("AGENT_HUB_URL", "").strip()
AGENT_NAME = os.getenv("AGENT_NAME", "").strip()

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "opus").strip()
PERMISSION_MODE = os.getenv("PERMISSION_MODE", "bypassPermissions").strip()
TASK_TIMEOUT_SEC = int(os.getenv("TASK_TIMEOUT_SEC", "3600"))

TTS_VOICE = os.getenv("TTS_VOICE", "uz-UZ-SardorNeural").strip()
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview").strip()
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore").strip()

PROJECTS_DIR = Path(
    os.getenv("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects"))
)
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "bot.log"

# Claude Code CLI uchun subprocess'ga uzatiladigan autentifikatsiya.
CLAUDE_CODE_OAUTH_TOKEN = os.getenv("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()


def resolve_claude_bin() -> str:
    """Claude CLI ning ishga tushirsa bo'ladigan yo'lini topadi.

    Windows'da `claude` odatda .cmd/.ps1 shim bo'ladi; .ps1 ni subprocess
    to'g'ridan-to'g'ri chaqira olmaydi, shuning uchun .cmd ga o'tkazamiz.
    """
    explicit = os.getenv("CLAUDE_BIN", "").strip()
    if explicit and explicit.lower() != "claude":
        return explicit

    found = shutil.which("claude")
    if not found:
        return "claude"

    p = Path(found)

    # Haqiqiy bajariladigan faylni afzal ko'ramiz. .cmd/.ps1 shim ni
    # subprocess to'g'ridan-to'g'ri ishga tushira olmaydi — cmd.exe orqali
    # o'rash kerak bo'ladi, bu esa vazifani to'xtatishni ishonchsiz qiladi:
    # cmd.exe o'ladi, lekin claude.exe tirik qolishi mumkin.
    if p.suffix.lower() in (".cmd", ".bat", ".ps1", ""):
        candidates = [
            p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
            p.with_suffix(".exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    if p.suffix.lower() == ".ps1":
        cmd = p.with_suffix(".cmd")
        if cmd.exists():
            return str(cmd)
    return str(p)


CLAUDE_BIN = resolve_claude_bin()


def missing_settings() -> list[str]:
    problems = []
    if not TELEGRAM_BOT_TOKEN:
        problems.append("TELEGRAM_BOT_TOKEN kiritilmagan (.env)")
    if not GEMINI_API_KEY:
        problems.append("GEMINI_API_KEY kiritilmagan — ovozli xabar ishlamaydi")
    if not PROJECTS_DIR.exists():
        problems.append(f"Claude sessiyalar papkasi topilmadi: {PROJECTS_DIR}")
    return problems
