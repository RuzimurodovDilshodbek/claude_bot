# Claude Code — Telegram boshqaruvi

Telefondan **matn yoki ovozli xabar** yuborasiz — u sizning kompyuteringizda
Claude Code sessiyasi sifatida bajariladi, natija matn va o'zbekcha ovozda
qaytadi. Bir nechta kompyuter va parallel sessiyalar qo'llab-quvvatlanadi.

```
                    Telegram
                        │
              ┌─────────┴──────────┐
              │   server (bot)     │  ← Telegramni tinglaydi
              │   + hub            │  ← agentlarni qabul qiladi
              │   + o'z agenti     │  ← serverdagi loyihalar
              └─────────┬──────────┘
                        │  wss://<domen>/agent
              ┌─────────┴──────────┐
              ▼                    ▼
        notebook (agent)     ishxona (agent)
        o'z sessiyalari      o'z sessiyalari
```

**Sessiyalar har kompyuterda o'zida qoladi.** Telegramda yozganingiz o'sha
mashinaning `~/.claude/projects/.../<id>.jsonl` fayliga qo'shiladi — kompyuter
oldiga borib `claude --resume` qilsangiz, butun yozishma joyida turadi.

---

## Nega shunday tuzilgan

| Qaror | Sabab |
|---|---|
| Bot serverda, kompyuterlarda agent | Kompyuterlar o'chiq bo'lsa ham bot javob beradi; bitta Telegram token faqat bitta joyda ishlashi mumkin |
| Agent server'ga **o'zi** ulanadi | Kompyuterlarda port ochish, NAT sozlash shart emas |
| Claude tokeni har mashinada qoladi | Server hech qachon boshqa kompyuterning hisobiga kirmaydi |
| Sessiya bajarilgan mashinada saqlanadi | "Telegramda boshlab, kompyuterda davom ettirish" shu tufayli ishlaydi |

---

## O'rnatish

### 1. Server (bot + hub)

```bash
git clone https://github.com/RuzimurodovDilshodbek/claude_bot.git
cd claude_bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # to'ldiring — 1,2,3,4,5-bo'limlar
```

Claude CLI kerak (`claude setup-token` bilan token oling):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

nginx orqali agentlar uchun manzil oching (`deploy/claude-tg.nginx` namunasi),
so'ng systemd:

```bash
sudo bash deploy/install.sh
```

### 2. Har bir kompyuter (agent)

```bash
git clone https://github.com/RuzimurodovDilshodbek/claude_bot.git
cd claude_bot
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env    # 4 va 5-bo'limlar yetarli
.venv\Scripts\python.exe install_autostart.py on agent
agent.bat
```

`AGENT_TOKEN` hamma joyda **bir xil** bo'lishi shart. `AGENT_NAME` esa har
xil: `notebook`, `ishxona`, `server`.

### 3. Tekshirish

```bash
.venv\Scripts\python.exe selftest.py
```

Har bir qatorda `[OK]` bo'lishi kerak.

---

## Foydalanish

| Komanda | Nima qiladi |
|---|---|
| *(matn yoki ovoz)* | Vazifa sifatida bajariladi |
| `/pc` | Kompyuter tanlash; bittasi yoniq bo'lsa avtomatik |
| `/sessions` | Shu loyihaning sessiyalari — bosib davom ettirasiz |
| `/sessions all` | Barcha loyihalar bo'yicha |
| `/projects` | Loyihalar ro'yxati |
| `/new` | Yangi sessiya |
| `/here` | Qaysi kompyuter / loyiha / sessiyadaman |
| `/status`, `/stop` | Ishlayotgan vazifa |
| `/voice`, `/say` | Ovozli javob |
| `/model` | opus / sonnet / haiku |
| `/xarajat` | So'nggi 30 kun xarajati |
| `/cleanup` | Test sessiyalarni tozalash |

### Guruhda parallel sessiyalar

Forum guruh yarating (Topics yoqing), botni **admin** qiling
("Manage Topics" ruxsati bilan). Keyin:

```
/newtopic oson-ish rezyume xatosi
/newtopic mm@ishxona dizayn audit
/bind wastix@server
```

Har mavzu — alohida sessiya. Turli mavzular **bir vaqtda** ishlaydi
(o'lchangan: 2 vazifa 6.3 s, ketma-ket bo'lsa 11.6 s bo'lardi).

> ⚠️ Ikki mavzu **bir kompyuterning bir loyihasiga** bog'lansa, ikkita Claude
> bir xil fayllarni tahrirlaydi va biri ikkinchisini bosib ketishi mumkin.
> Bot ogohlantiradi, lekin to'xtatmaydi.

---

## Xavfsizlik

`.env` da Telegram tokeni, Claude OAuth tokeni va agent maxfiy so'zi bor.
**Bu repo ochiq** — `.gitignore` ularni chetda ushlaydi va har commit oldidan
`check_secrets.py` tekshiradi (pre-commit ilgagi).

```bash
python check_secrets.py        # qo'lda tekshirish
```

Agar token tasodifan tushib ketsa: **darhol bekor qiling va yangisini oling.**
Tarixdan o'chirish yetarli emas — u allaqachon ko'chirib olingan bo'lishi mumkin.

Boshqa nuqtalar:

- `ALLOWED_USER_IDS` bo'sh bo'lsa bot **hech kimni** kiritmaydi (ataylab).
- Guruhda ham foydalanuvchi ID si alohida tekshiriladi — guruhdagi begonalar
  kira olmaydi.
- Serverda `PERMISSION_MODE=acceptEdits` tavsiya etiladi; shaxsiy
  kompyuterda `bypassPermissions` qulay, lekin Claude hech narsa so'ramaydi.
- Loyihalaringizni git ostiga oling — Telegramdan kelgan o'zgarishni
  qaytarish uchun yagona ishonchli yo'l shu.

---

## Ishonchlilik

Beqaror tarmoq uchun uch qatlam:

1. **Transport** — TCP/TLS uzilsa darhol qayta sinaydi (`httpx`, 3 marta)
2. **So'rov** — `tg_retry.py`, 5 marta, kechikish oshib boradi
3. **Qorovul** — har 45 s da oxirgi muvaffaqiyatli `getUpdates` tekshiriladi;
   150 s sukunat bo'lsa jarayondan chiqadi va qayta yoqiladi

Uchinchisi kerak, chunki polling halqasi **jimgina o'lishi** mumkin: jarayon
tirik ko'rinadi, `updater.running` ham `True`, lekin bot hech narsa qabul
qilmaydi. Shuning uchun qorovul jarayon holatiga emas, haqiqiy `getUpdates`
yurak urishiga qaraydi.

`single_instance.py` esa ikkinchi nusxa ko'tarilishini to'xtatadi — ikki bot
bir vaqtda `getUpdates` qilsa Telegram birini uzadi va xabarlar yo'qoladi.

Gemini bepul tierda tez-tez `503 high demand` qaytaradi, shuning uchun
`ai.py` bir necha modelni navbat bilan sinaydi.

---

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `bot.py` | Telegram bot — komandalar, tugmalar, jarayon ko'rsatkichi |
| `hub.py` | Agentlarni qabul qiluvchi WebSocket server |
| `agent.py` | Kompyuterda ishlaydi, Claude ni chaqiradi |
| `protocol.py` | Hub ↔ agent xabar formati |
| `machines.py` | Bot uchun kompyuterlar bilan ishlash qatlami |
| `runner.py` | Claude CLI va `stream-json` oqimi |
| `sessions.py` | `~/.claude/projects` dan sessiyalarni o'qish |
| `ai.py` | Gemini (ovoz→matn, xulosa) va ovoz sintezi |
| `tgfmt.py` | Markdown → Telegram HTML |
| `tg_retry.py` | Tarmoq uchun qayta urinish qatlami |
| `single_instance.py` | Ikkinchi nusxani to'xtatadi |
| `check_secrets.py` | Sirlar git ga tushmasligini tekshiradi |
| `selftest.py` | Ishga tushirishdan oldingi tekshiruv |
| `link.py` | Telegram hisobi/guruhini bog'lash |
