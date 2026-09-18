# Biriktirmalar va jarayon ko'rsatkichi — dizayn

Sana: 2026-09-18. Holat: tasdiqlangan.

Ikki mustaqil qism, shu tartibda bajariladi:

1. **Biriktirmalar** — Telegramdan kelgan rasm/fayl/albomni Claude ko'radigan qilish.
2. **Jarayon ko'rsatkichi va natija** — ish davomida xabar muzlamasin, natija
   tezroq va ishonchli kelsin.

---

## 1-qism. Biriktirmalar (rasm, fayl, albom)

### Muammo

`bot.py` da faqat `TEXT` va `VOICE | AUDIO` handlerlari bor. Rasm (photo) yoki
fayl (document) kelsa hech qaysi handler mos kelmaydi — xabar jimgina tashlab
yuboriladi, foydalanuvchi hech qanday javob olmaydi.

### Yondashuv

Faylni agentga uzatamiz, Claude uni **o'zi** `Read` bilan ko'radi (Claude Code
rasmlarni tushunadi). Gemini orqali tavsiflash rad etildi — skrinshotdagi kod
va xato matni buziladi.

### Oqim

```
Telegram photo/document
   │  bot: get_file() → bytes (Telegram cheklovi: fayl ≤ 20 MB)
   ▼
kutish ro'yxati  (scope bo'yicha: chat_id + thread_id)
   │  caption bor      → 1.5 s debounce (albom bo'laklari kelsin) → vazifa
   │  caption yo'q     → "📎 N ta fayl kutmoqda — vazifani yozing yoki ovoz yuboring"
   │                      keyingi matn/ovoz xabari ularni oladi
   │  vazifa ishlayapti → follow_ups navbatiga (matn + biriktirmalar)
   ▼
hub → agent: OP_RUN args.attachments = [{name, mime, data_b64}]
   │  agent: inbox/<task_id>/<n>_<name> ga yozadi
   │  prompt boshiga ro'yxat qo'shadi, claude ga --add-dir inbox/<task_id>
   ▼
Claude: Read <yo'l> → rasmni ko'radi
```

### Bot tomoni (`bot.py`, yangi `attachments.py`)

- Yangi handler: `MessageHandler(filters.PHOTO | filters.Document.ALL, on_attachment)`.
  Ruxsat tekshiruvi boshqa handlerlar bilan bir xil (`authorized`).
- `attachments.py` — kutish ro'yxati (bot xotirasida, diskka yozilmaydi):
  - `Attachment(name, mime, data: bytes)` (`size` = `len(data)`)
  - `PendingBox(scope)`: `items`, `caption`, `created_at`, `notice` (Telegram xabari),
    `timer` (debounce task).
  - `add(scope, att, caption)`, `take(scope) -> (caption, items)`, `expire()`.
- Photo: eng katta o'lcham (`message.photo[-1]`), nom `photo_<message_id>.jpg`,
  mime `image/jpeg`. Document: `file_name` tozalanadi — faqat basename, `\ / : * ? " < > |`
  olib tashlanadi, 80 belgigacha; bo'sh qolsa `file_<message_id>`.
- Chegara: bitta vazifa uchun jami **20 MB**. Oshsa bot faylni qabul qilmaydi va
  sabab yozadi (avval yig'ilganlar saqlanib qoladi).
- Caption bor (albomda odatda birinchi elementda): 1.5 s debounce, so'ng
  `start_task(prompt=caption, attachments=items)`. Debounce vaqtida kelgan
  albom bo'laklari ham kiradi.
- Caption yo'q: bitta ogohlantirish xabari yuboriladi va har yangi fayl kelganda
  **tahrirlanadi** ("📎 2 ta rasm, 1 ta fayl kutmoqda…"). `on_text` va `on_voice`
  (transkripsiyadan keyin) `take(scope)` qilib biriktirmalar bilan boshlaydi;
  ogohlantirish xabari "📎 vazifaga qo'shildi" ga o'zgaradi.
- Eskirish: **15 daqiqa**. Muddati o'tganda ogohlantirish xabari
  "⌛ Biriktirmalar eskirdi — qayta yuboring" ga tahrirlanadi, ro'yxat bo'shaydi.
  `/new` ham ro'yxatni tozalaydi.
- Vazifa ishlab turganda: `RunningTask.follow_ups` elementi `(prompt, attachments)`
  bo'ladi (hozir faqat `str`). Follow-up boshlanganda biriktirmalar u bilan ketadi.
- `_launch(..., attachments=...)` → `machines.run(..., attachments=...)`.
  Bot biriktirmalarni hub'ga uzatgach xotiradan bo'shatadi.

### Protokol va agent (`protocol.py`, `machines.py`, `agent.py`, `runner.py`)

- `OP_RUN` args ga ixtiyoriy `attachments: [{name, mime, data_b64}]` qo'shiladi.
  Base64 bilan 20 MB → ~27 MB, hub/agent `max_size` 32 MB — sig'adi.
- Agent `do_run`:
  - `INBOX = BASE_DIR / "inbox"`; vazifa papkasi `inbox/<rid>/`.
  - Fayllar `01_<name>`, `02_<name>` … tartibda yoziladi (albom tartibi saqlanadi).
  - Prompt boshiga blok qo'shiladi:
    ```
    [Foydalanuvchi 2 ta fayl biriktirdi. Avval har birini Read bilan ko'rib chiq:
    1. D:\...\inbox\ab12cd\01_photo_512.jpg  (image/jpeg, 118 KB)
    2. D:\...\inbox\ab12cd\02_error.log  (text/plain, 4 KB)]

    <foydalanuvchi matni>
    ```
    Matn bo'sh bo'lsa (bot caption'siz ishga tushirmaydi, lekin himoya uchun) —
    "Biriktirilgan fayllarni ko'rib chiq va nima kerakligini ayt."
  - `runner.ClaudeRun(..., add_dirs=[inbox/<rid>])` → `--add-dir <papka>`;
    shunda `acceptEdits` rejimida (server) ham `Read` ruxsat so'ramaydi.
- Agent ishga tushganda `inbox/` ichidagi **7 kundan eski** papkalar o'chiriladi.
- `.gitignore` ga `inbox/` qo'shiladi.

### Xatolar

- Telegramdan yuklab bo'lmasa — foydalanuvchiga "Faylni yuklab bo'lmadi, qayta yuboring".
- Agent faylni yoza olmasa — vazifa `done(ok=False, error=...)` bilan tugaydi,
  hozirgi "Papka topilmadi" yo'li kabi.
- Biriktirma bilan kelgan vazifa navbatga tushib, kompyuter 10 daqiqa ichida
  ulanmasa — mavjud xato matni; biriktirmalar shu vazifa bilan yo'qoladi
  (foydalanuvchi qayta yuboradi).

### Test

- `attachments.py`: caption bilan debounce; caption'siz kutish → matn kelganda
  olish; albom (3 ta xabar, caption birinchisida); eskirish; 20 MB chegarasi;
  nom tozalash.
- `agent.py` `do_run`: base64 → fayl, prompt bloki, `--add-dir` argumenti
  (runner `_argv` testi).
- Jonli: Telegramdan rasm + caption; rasm, keyin ovoz; albom; fayl (.log).

---

## 2-qism. Jarayon ko'rsatkichi va natija

### Muammo

`Progress` faqat hodisa kelganda (`tool`, `text`, …) va 3 s throttle bilan
yangilanadi. Claude uzoq o'ylasa, uzun Bash komandasi ketsa yoki **oxirgi javobni
yozayotgan bo'lsa** (opus'da 30–60 s) — hodisa yo'q, xabar muzlaydi, hatto vaqt
ham yangilanmaydi. Foydalanuvchiga "tugadi, natija kelmayapti" bo'lib ko'rinadi.

Natijani yuborishda `RetryAfter` (Telegram flood, 429) ushlanmaydi — `_execute`
ichida istisno chiqsa natija **umuman yo'qoladi**, navbatdagi follow-up'lar ham
bajarilmaydi.

O'lchovlar (2026-09-18): `result` → jarayon tugashi 0.6 s; server → Telegram
API 72 ms; trivial savol CLI: server 1.8 s, Windows 5.6 s. Ya'ni tarmoq va
jarayon tugashi sabab emas.

### Yondashuv

Tiker + "hozir" qatori + oqimli matn. Har deltada Telegramni tahrirlash rad
etildi (flood); faqat tiker rad etildi ("javob yozilmoqda"ni ko'rsatolmaydi).

### Runner (`runner.py`)

- `--include-partial-messages` qo'shiladi. `stream_event` hodisalari:
  - `content_block_start` (`tool_use`) → `tool` hodisasi shu paytdan (hozirgidan
    ertaroq — to'liq xabar kelishini kutmaydi). To'liq `assistant` xabari kelganda
    o'sha tool ikkinchi marta e'lon qilinmaydi (id bo'yicha).
  - `content_block_delta` `text_delta` → matn yig'iladi; **2 s** da bir marta
    `typing` hodisasi: `{"chars": N, "tail": oxirgi 80 belgi}`.
  - `content_block_delta` `thinking_delta` → 2 s da bir marta `thinking` hodisasi
    (davomiylik sekundlarda).
- `user` hodisasidagi `tool_result` (xatosiz) → `tool_done` hodisasi.
- `result` kelishi bilan `run()` **darhol** qaytadi; `proc.wait()` fon vazifasida
  (`asyncio.create_task`) — 0.6 s tejaladi, zombi qolmaydi.
- Hodisa matni `protocol.ev` orqali o'zgarishsiz o'tadi (`kind`, `text`); `typing`
  uchun `text` = JSON qator.

### Progress (`bot.py`)

- **Tiker**: `_launch` da `asyncio.create_task(progress.tick())` — har **4 s**
  `flush()`; vazifa tugaganda bekor qilinadi. Matn o'zgarmagan bo'lsa tahrir
  qilinmaydi (Telegram xatosi bermasin) — lekin vaqt qatori o'zgargani uchun
  amalda har 4 s da yangilanadi.
- **"Hozir" qatori** (ro'yxat ostida bitta qator):
  - tool boshlandi → `▶ Bash: npm test … (35 s)` — sekund tiker bilan o'sadi;
  - `tool_done` → ro'yxatdagi qator `✅` bilan belgilanadi, "hozir" bo'shaydi;
  - `typing` → `✍️ Javob yozilmoqda… 850 belgi`;
  - `thinking` → `🤔 O'ylayapti… 12 s`;
  - `wait` → hozirgidek `⏸`.
- Throttle 3 s saqlanadi (hodisalar ketma-ket yog'ilsa flood bo'lmasin).
- Ro'yxatda oxirgi 6 qator — hozirgidek.

### Natija (`bot.py` `_execute`)

- Yuborish bloki `try/except` bilan o'raladi: `RetryAfter` → `retry_after + 0.5`
  s kutib qayta; boshqa istisno → log + foydalanuvchiga qisqa xato; follow-up
  halqasi har holda ishlaydi.
- Pastki qatorga model qo'shiladi: `⏱ 33s · 🔧 1 amal · 🧵 ab12cd34 · 🤖 claude-opus-5 · 💵 $0.041`.
- `Progress` yakuniy tahriri (`✅ Bajarildi`) hozirgidek natija matnidan oldin
  qoladi — lekin tiker avval to'xtatiladi, ikkalasi bir xabarni bir vaqtda
  tahrirlamasin.

### Test

- `runner`: soxta stdout oqimi bilan `stream_event` → `tool`/`typing`/`thinking`/
  `tool_done` hodisalari tartibi va throttle; `result` dan keyin darhol qaytishi.
- `Progress`: tiker bilan vaqt o'zgarishi; "hozir" qatorining har holati;
  `tool_done` belgilash; 3 s throttle.
- `_execute` yuborish: `RetryAfter` da kutib qayta yuborish (soxta bot).
- Jonli: uzun Bash komandali vazifa; uzun javobli savol (opus); flood — ikki
  mavzuda parallel.

### O'zgarmaydi

`DEFAULT_MODEL=opus` taxallusi qoladi (hozir `claude-opus-5` ga ochiladi);
xohlasa foydalanuvchi `.env` da `claude-opus-5` deb qattiq bog'laydi.
