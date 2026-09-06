"""Windows'ga kirganda agentni (yoki botni) avtomatik ishga tushiradi.

Ikki rol bor:
  agent — kompyuter serverdagi hub'ga ulanadi va vazifalarni bajaradi.
          Ko'p kompyuterli sozlamada har bir kompyuterga SHU kerak.
  bot   — Telegramni o'zi tinglaydi. Faqat bitta joyda ishlashi mumkin
          (aks holda getUpdates to'qnashadi) — odatda bu server.

Startup papkasiga yorliq qo'yiladi, admin huquqi kerak emas.

Ishlatish:
    .venv\\Scripts\\python.exe install_autostart.py            # agent (odatiy)
    .venv\\Scripts\\python.exe install_autostart.py on bot     # bot
    .venv\\Scripts\\python.exe install_autostart.py off        # ikkalasi ham
    .venv\\Scripts\\python.exe install_autostart.py status     # holat
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import config

STARTUP_DIR = Path(os.environ["APPDATA"]) / (
    "Microsoft/Windows/Start Menu/Programs/Startup"
)

ROLES: dict[str, tuple[Path, Path, str]] = {
    "agent": (
        STARTUP_DIR / "Claude Agent.lnk",
        config.BASE_DIR / "agent.bat",
        "Claude agent — serverdagi hub'ga ulanadi",
    ),
    "bot": (
        STARTUP_DIR / "Claude Telegram Bot.lnk",
        config.BASE_DIR / "start.bat",
        "Claude Telegram bot",
    ),
}


def _ps_quote(value: object) -> str:
    """PowerShell bitta qo'shtirnoqli qatori uchun. Apostrof ikkilantiriladi —
    o'zbekcha matnda (hub'ga, o'chirish) bu tez-tez uchraydi va aks holda
    qator o'rtasida uzilib qoladi."""
    return str(value).replace("'", "''")


def _create_shortcut(link: Path, target: Path, description: str) -> None:
    # PowerShell'dan foydalanamiz — Windows'da har doim bor, pywin32 shart emas.
    ps = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{_ps_quote(link)}')
$Shortcut.TargetPath = '{_ps_quote(target)}'
$Shortcut.WorkingDirectory = '{_ps_quote(config.BASE_DIR)}'
$Shortcut.WindowStyle = 7
$Shortcut.Description = '{_ps_quote(description)}'
$Shortcut.Save()
"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "PowerShell xatosi")


def install(role: str) -> int:
    if role not in ROLES:
        print(f"[XATO] Noma'lum rol: {role}. Mavjud: {', '.join(ROLES)}")
        return 2
    link, target, description = ROLES[role]
    if not target.exists():
        print(f"[XATO] {target} topilmadi.")
        return 1

    # Bitta kompyuterda ikkala rol bir vaqtda kerak emas — ikkinchisini
    # o'chirib qo'yamiz, aks holda bot server bilan to'qnashadi.
    for other, (other_link, _, _) in ROLES.items():
        if other != role and other_link.exists():
            other_link.unlink()
            print(f"[i] '{other}' avtomatik ishga tushishi o'chirildi "
                  "(bitta kompyuterda bitta rol).")

    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _create_shortcut(link, target, description)
    except Exception as exc:
        print(f"[XATO] Yorliq yaratilmadi: {exc}")
        return 1

    print(f"[OK] '{role}' avtomatik ishga tushishi yoqildi.")
    print(f"     Yorliq: {link.name}")
    print(f"     -> {target}")
    return 0


def uninstall() -> int:
    removed = False
    for role, (link, _, _) in ROLES.items():
        if link.exists():
            link.unlink()
            print(f"[OK] O'chirildi: {role} ({link.name})")
            removed = True
    if not removed:
        print("[i] Yorliq allaqachon yo'q.")
    return 0


def status() -> int:
    found = False
    for role, (link, target, _) in ROLES.items():
        if link.exists():
            print(f"[YOQILGAN] {role}: {link.name}")
            print(f"           -> {target}")
            found = True
    if not found:
        print("[O'CHIQ] Avtomatik ishga tushish sozlanmagan.")
        print("        Yoqish: .venv\\Scripts\\python.exe install_autostart.py")
    return 0


def main() -> int:
    args = [a.lower() for a in sys.argv[1:]]
    action = args[0] if args else "on"
    role = args[1] if len(args) > 1 else "agent"

    # `install_autostart.py bot` shakli ham ishlasin.
    if action in ROLES:
        role, action = action, "on"

    if action in ("on", "install", "add"):
        return install(role)
    if action in ("off", "uninstall", "remove"):
        return uninstall()
    if action == "status":
        return status()
    print(f"Noma'lum amal: {action}. Ishlatilishi: on [rol] | off | status")
    return 2


if __name__ == "__main__":
    sys.exit(main())
