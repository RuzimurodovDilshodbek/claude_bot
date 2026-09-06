"""Bot ni ota-jarayondan mustaqil ravishda ishga tushiradi.

Windows'da oddiy `Start-Process` yoki `subprocess.Popen` ba'zan ota-jarayon
yopilganda bola jarayonni ham yopib yuboradi. Bu skript to'g'ri
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` bayroqlari bilan ishga
tushiradi — bot butun sessiyadan mustaqil bo'ladi.

Yakuniy chiqish: pythonw.exe (konsol oynasi yo'q, ko'rinmas), start.bat ning
qayta yoqish halqasidan foydalanmaydi — buni `install_autostart.py` +
`bot.py` ichidagi qorovul + O'z-o'zidan qayta yoqish (`os._exit(1)`) bajaradi.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
PYW = BASE / ".venv" / "Scripts" / "pythonw.exe"
BOT = BASE / "bot.py"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def main() -> int:
    if not PYW.exists():
        print(f"[XATO] pythonw.exe topilmadi: {PYW}")
        return 1

    proc = subprocess.Popen(
        [str(PYW), str(BOT)],
        cwd=str(BASE),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
        close_fds=True,
    )
    print(f"[OK] Bot fon rejimida ishga tushirildi (PID {proc.pid})")
    print("     Ko'rinmas oyna — loglar: bot.log")
    print("     To'xtatish: taskkill /PID {} /F".format(proc.pid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
