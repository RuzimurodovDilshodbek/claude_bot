"""Botni ishga tushirishdan oldingi tekshiruv.

Ishlatish:  .venv\\Scripts\\python.exe selftest.py
"""
from __future__ import annotations

import asyncio
import sys

OK = "[OK]  "
WARN = "[!]   "
BAD = "[XATO]"


def line(mark: str, text: str) -> None:
    print(f"{mark} {text}")


async def main() -> int:
    problems = 0

    import config
    line(OK, f"Claude CLI: {config.CLAUDE_BIN}")
    line(OK, f"Sessiyalar papkasi: {config.PROJECTS_DIR}")

    # 1. Telegram token
    if config.TELEGRAM_BOT_TOKEN:
        try:
            from telegram import Bot

            me = await Bot(config.TELEGRAM_BOT_TOKEN).get_me()
            line(OK, f"Telegram bot: @{me.username}")
        except Exception as exc:
            line(BAD, f"Telegram token ishlamadi: {exc}")
            problems += 1
    else:
        line(BAD, "TELEGRAM_BOT_TOKEN kiritilmagan (.env)")
        problems += 1

    # 2. Ruxsat berilgan foydalanuvchilar
    if config.ALLOWED_USER_IDS:
        line(OK, f"Ruxsat berilgan ID: {sorted(config.ALLOWED_USER_IDS)}")
    else:
        line(WARN, "ALLOWED_USER_IDS bo'sh — bot hech kimni kiritmaydi. "
                   "Botga /id yozing va raqamni .env ga qo'shing.")

    # 3. Sessiyalar
    import sessions

    projects = sessions.list_projects()
    total = sum(len(p.sessions) for p in projects)
    if projects:
        line(OK, f"{len(projects)} loyiha, {total} sessiya topildi "
                 f"(masalan: {', '.join(p.name for p in projects[:3])})")
    else:
        line(WARN, "Sessiya topilmadi — avval kompyuterda Claude Code bilan ishlang.")

    # 4. Gemini (ovoz -> matn) — qayta urinish va zaxira modellar bilan
    if config.GEMINI_API_KEY:
        try:
            import ai as ai_mod

            resp = await ai_mod._generate("Faqat 'ha' deb javob ber.")
            line(OK, f"Gemini ishlayapti: {(resp.text or '').strip()[:20]}")
        except ai_mod.GeminiBusy:
            line(WARN, "Gemini hozir band — bir necha daqiqada qayta urinib ko'ring.")
        except Exception as exc:
            line(BAD, f"Gemini xatosi: {exc}")
            problems += 1
    else:
        line(WARN, "GEMINI_API_KEY yo'q — ovozli xabar ishlamaydi.")

    # 5. Ovoz sintezi
    try:
        import ai

        audio, ext = await ai.synthesize("Sinov. Bot tayyor.")
        line(OK, f"Ovoz sintezi ishlayapti: {len(audio)} bayt (.{ext})")
    except Exception as exc:
        line(WARN, f"Ovoz sintezi ishlamadi: {exc}")

    # 6. Claude CLI autentifikatsiyasi — eng muhim tekshiruv
    import runner

    run = runner.ClaudeRun(
        prompt="Faqat 'TAYYOR' deb javob ber, boshqa hech narsa yozma.",
        cwd=str(config.BASE_DIR),
        model="sonnet",
        persist=False,  # sinov sessiyasi /sessions ro'yxatiga tushmasin
    )
    result = await run.run()
    if result.ok:
        line(OK, f"Claude CLI javob berdi: {result.text.strip()[:40]!r} "
                 f"({result.duration_ms} ms)")
    else:
        line(BAD, f"Claude CLI ishlamadi: {result.error}")
        if "authenticat" in (result.error or "").lower() or "401" in (result.error or ""):
            print()
            print("      Yechim: terminalda quyidagini bajaring —")
            print("        claude setup-token")
            print("      chiqqan tokenni .env fayliga yozing:")
            print("        CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...")
        problems += 1

    print()
    if problems:
        print(f"{problems} ta muammo bor — yuqoridagi [XATO] qatorlarni tuzating.")
    else:
        print("Hammasi joyida. Botni ishga tushiring:  start.bat")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
