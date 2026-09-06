"""Telegram hisobingizni yoki guruhni botga bog'laydi.

Ishga tushiring, keyin shaxsiy chatda yoki guruhda botga xabar yozing
(guruhda bot admin bo'lishi shart). ID avtomatik topilib `.env` ga qo'shiladi:
 - shaxsiy chat -> ALLOWED_USER_IDS
 - guruh chat  -> ALLOWED_CHAT_IDS

Ishlatish:  .venv\\Scripts\\python.exe link.py
"""
from __future__ import annotations

import asyncio
import re
import sys

import config

WAIT_SECONDS = 180


def _add_to_env(var: str, value: int) -> bool:
    """`.env` faylidagi var ga qiymatni qo'shadi. Qaytaruvchi: qo'shildimi."""
    path = config.BASE_DIR / ".env"
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"^{var}=(.*)$", text, re.M)
    if not match:
        text = text.rstrip() + f"\n{var}={value}\n"
        path.write_text(text, encoding="utf-8")
        return True

    current = [p.strip() for p in match.group(1).split(",") if p.strip()]
    if str(value) in current:
        return False
    current.append(str(value))
    text = text[: match.start()] + f"{var}={','.join(current)}" + text[match.end():]
    path.write_text(text, encoding="utf-8")
    return True


async def main() -> int:
    if not config.TELEGRAM_BOT_TOKEN:
        print("[XATO] TELEGRAM_BOT_TOKEN kiritilmagan (.env)")
        return 1

    from telegram import Bot
    from tg_retry import RetryingHTTPXRequest

    # Beqaror tarmoq uchun bir xil retry qatlami — bot.py da bo'lgani kabi.
    req = RetryingHTTPXRequest(
        connect_timeout=30.0, read_timeout=30.0,
        write_timeout=30.0, pool_timeout=30.0,
    )
    bot = Bot(config.TELEGRAM_BOT_TOKEN, request=req,
              get_updates_request=RetryingHTTPXRequest(
                  connect_timeout=30.0, read_timeout=40.0,
                  write_timeout=30.0, pool_timeout=30.0,
              ))
    async with bot:
        me = await bot.get_me()
        print(f"Bot: @{me.username}")
        print(f"Shu botga xabar yozing (shaxsiy yoki guruhda) — "
              f"{WAIT_SECONDS} sekund kutaman.")
        print("Guruh uchun: botni admin qiling va guruhda istalgan xabar yozing.")
        print()

        offset = None
        deadline = asyncio.get_event_loop().time() + WAIT_SECONDS
        seen_users: set[int] = set()
        seen_chats: set[int] = set()
        found_any = False

        while asyncio.get_event_loop().time() < deadline:
            updates = await bot.get_updates(offset=offset, timeout=20)
            for update in updates:
                offset = update.update_id + 1
                chat = update.effective_chat
                user = update.effective_user

                # Shaxsiy chat — foydalanuvchini qo'shamiz
                if chat and chat.type == "private" and user and not user.is_bot:
                    if user.id not in seen_users:
                        seen_users.add(user.id)
                        name = user.full_name or user.username or "?"
                        added = _add_to_env("ALLOWED_USER_IDS", user.id)
                        status = "qo'shildi" if added else "allaqachon ro'yxatda"
                        print(f"[OK] Foydalanuvchi: {name} — ID {user.id} — {status}")
                        found_any = True

                # Guruh — chatni qo'shamiz
                elif chat and chat.type in ("group", "supergroup"):
                    if chat.id not in seen_chats:
                        seen_chats.add(chat.id)
                        title = chat.title or "?"
                        forum = " (Forum ✅)" if getattr(chat, "is_forum", False) else ""
                        added = _add_to_env("ALLOWED_CHAT_IDS", chat.id)
                        status = "qo'shildi" if added else "allaqachon ro'yxatda"
                        print(f"[OK] Guruh: {title}{forum} — ID {chat.id} — {status}")
                        # Guruh yozgan foydalanuvchini ham qo'shamiz
                        if user and not user.is_bot and user.id not in seen_users:
                            seen_users.add(user.id)
                            _add_to_env("ALLOWED_USER_IDS", user.id)
                            print(f"     va foydalanuvchi: {user.full_name} — {user.id}")
                        found_any = True

            if found_any and asyncio.get_event_loop().time() > deadline - WAIT_SECONDS + 8:
                # Bir necha xabar keldi va biroz kutdik — yakunlaymiz
                print()
                print("Tayyor. Endi botni qayta ishga tushiring: start.bat")
                return 0

        if not found_any:
            print("[!] Xabar kelmadi. Botga yozib, qaytadan urinib ko'ring.")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
