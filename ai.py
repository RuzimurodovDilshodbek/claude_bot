"""Ovoz bilan bog'liq yordamchilar: Gemini (matnga o'girish, xulosa) va edge-tts."""
from __future__ import annotations

import asyncio
import io
import re

import config

_client = None


def _gemini():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY kiritilmagan (.env)")
        from google import genai

        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


# Gemini vaqtincha band bo'lishi odatiy hol — shu kodlarda qayta urinamiz.
RETRYABLE = {408, 429, 500, 502, 503, 504}


def _status_of(exc: BaseException) -> int | None:
    """Istisnolar zanjiridan HTTP kodini topadi.

    SDK o'z qayta urinishlarini `RetryError` ichiga o'raydi, shuning uchun
    sababni ochib ko'rish kerak.
    """
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "code", None)
        if isinstance(code, int):
            return code
        for token in ("503", "429", "500", "502", "504", "408"):
            if token in str(current)[:400]:
                return int(token)
        nxt = getattr(current, "last_attempt", None)
        if nxt is not None and hasattr(nxt, "exception"):
            try:
                current = nxt.exception()
                continue
            except Exception:
                pass
        current = current.__cause__ or current.__context__
    return None


class GeminiBusy(RuntimeError):
    """Barcha modellar band bo'lganda ko'tariladi."""


async def _generate(contents, gen_config=None, models: list[str] | None = None,
                    attempts: int = 3):
    """Modellar ro'yxati bo'yicha qayta urinish bilan so'rov yuboradi."""
    client = _gemini()
    candidates = models or config.GEMINI_MODELS
    last: BaseException | None = None
    busy = False

    for model in candidates:
        for attempt in range(attempts):
            try:
                return await client.aio.models.generate_content(
                    model=model, contents=contents, config=gen_config
                )
            except Exception as exc:
                last = exc
                code = _status_of(exc)
                if code in RETRYABLE:
                    busy = True
                    if attempt < attempts - 1:
                        await asyncio.sleep(1.5 * (2 ** attempt))
                        continue
                break  # boshqa xato — keyingi modelga o'tamiz

    if busy:
        raise GeminiBusy(
            "Gemini hozir band (503). Bir necha model sinaldi, hammasi javob bermadi."
        ) from last
    raise last if last else RuntimeError("Gemini javob bermadi.")


TRANSCRIBE_PROMPT = (
    "Bu foydalanuvchining ovozli xabari. Uni so'zma-so'z matnga o'gir.\n"
    "- Foydalanuvchi asosan o'zbek tilida gapiradi, orasida ruscha yoki "
    "inglizcha texnik atamalar bo'lishi mumkin — ularni asl holida yoz.\n"
    "- Hech narsa qo'shma, izohlama, tarjima qilma.\n"
    "- Faqat matnning o'zini qaytar."
)

SUMMARY_PROMPT = (
    "Quyida dasturchiga bajarilgan ish bo'yicha hisobot. Uni ovozda tinglash "
    "uchun o'zbek tilida qisqacha aytib ber.\n"
    "- 2-6 gap, jonli og'zaki uslubda.\n"
    "- Nima qilinganini va natija nima bo'lganini ayt.\n"
    "- Fayl yo'llari, kod bo'laklari va maxsus belgilarni o'qima; ularni "
    "sodda so'z bilan tushuntir (masalan 'konfiguratsiya fayli').\n"
    "- Faqat xulosa matnini qaytar, sarlavha yozma.\n\n"
    "HISOBOT:\n"
)


async def transcribe(audio: bytes, mime_type: str = "audio/ogg") -> str:
    """Ovozli xabarni matnga o'giradi.

    Telegram ovozni Ogg Opus qilib yuboradi. Ikkala mime nomi ham qabul
    qilinadi, lekin `audio/opus` ishonchliroq ishlagani uchun avval shuni
    sinaymiz.
    """
    from google.genai import types

    mimes = ["audio/opus", "audio/ogg"] if "ogg" in mime_type or "opus" in mime_type \
        else [mime_type, "audio/mpeg"]

    last: BaseException | None = None
    for mime in dict.fromkeys(mimes):
        try:
            response = await _generate([
                types.Part.from_bytes(data=audio, mime_type=mime),
                TRANSCRIBE_PROMPT,
            ])
            text = (response.text or "").strip()
            if text:
                return text
        except GeminiBusy:
            raise
        except Exception as exc:
            last = exc

    if last is not None:
        raise last
    return ""


async def spoken_summary(report: str, max_chars: int = 20000) -> str:
    """Uzun hisobotdan ovozda o'qish uchun qisqa xulosa tayyorlaydi."""
    clean = strip_markup(report)
    if len(clean) <= 600:
        return clean
    if not config.GEMINI_API_KEY:
        return clean[:600].rsplit(" ", 1)[0] + "…"
    try:
        response = await _generate(SUMMARY_PROMPT + clean[:max_chars])
        text = (response.text or "").strip()
        return text or clean[:600]
    except Exception:
        # Xulosa chiqmasa ham ovoz kelsin — boshini o'qiymiz.
        return clean[:600].rsplit(" ", 1)[0] + "…"


_CODE_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HEADING = re.compile(r"^#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)")
_MULTI_NL = re.compile(r"\n{3,}")


def strip_markup(text: str) -> str:
    """Markdown bezaklarini olib tashlaydi — ovozda o'qish uchun."""
    out = _CODE_FENCE.sub(" (kod bo'lagi) ", text or "")
    out = _INLINE_CODE.sub(r"\1", out)
    out = _LINK.sub(r"\1", out)
    out = _HEADING.sub("", out)
    out = _BULLET.sub("", out)
    out = _EMPHASIS.sub("", out)
    out = _MULTI_NL.sub("\n\n", out)
    return out.strip()


async def _edge_tts(spoken: str, voice: str) -> bytes:
    import edge_tts

    buffer = io.BytesIO()
    comm = edge_tts.Communicate(spoken, voice)
    async for chunk in comm.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            buffer.write(chunk["data"])
    data = buffer.getvalue()
    if not data:
        raise RuntimeError("Ovoz fayli bo'sh chiqdi.")
    return data


def _wav_header(pcm: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    """Gemini xom PCM qaytaradi — ustiga WAV sarlavhasini qo'yamiz."""
    import struct

    block_align = channels * width
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(pcm))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block_align, block_align, width * 8)
        + b"data"
        + struct.pack("<I", len(pcm))
        + pcm
    )


async def _gemini_tts(spoken: str) -> bytes:
    """Zaxira yo'l: edge-tts ishlamasa Gemini ovozidan foydalanamiz."""
    from google.genai import types

    response = await _generate(
        f"O'zbek tilida, tabiiy ohangda o'qi:\n\n{spoken}",
        gen_config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=config.GEMINI_TTS_VOICE
                    )
                )
            ),
        ),
        models=[config.GEMINI_TTS_MODEL],
    )
    part = response.candidates[0].content.parts[0]
    pcm = part.inline_data.data
    if not pcm:
        raise RuntimeError("Gemini ovoz qaytarmadi.")
    return _wav_header(pcm)


async def synthesize(text: str, voice: str | None = None) -> tuple[bytes, str]:
    """Matnni ovozga aylantiradi. (baytlar, kengaytma) qaytaradi.

    Avval edge-tts (bepul, o'zbek ovozi), tarmoq uzilsa Gemini ovozi.
    """
    spoken = strip_markup(text)[:4000].strip()
    if not spoken:
        raise RuntimeError("Ovozga aylantirish uchun matn yo'q.")

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return await _edge_tts(spoken, voice or config.TTS_VOICE), "mp3"
        except Exception as exc:  # tarmoq uzilishi tez-tez uchraydi
            last_error = exc
            if attempt == 0:
                await asyncio.sleep(1.5)

    if config.GEMINI_API_KEY:
        try:
            return await _gemini_tts(spoken), "wav"
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Ovoz tayyorlab bo'lmadi: {last_error}")


async def available_uz_voices() -> list[str]:
    import edge_tts

    voices = await edge_tts.list_voices()
    return [v["ShortName"] for v in voices if v["ShortName"].startswith("uz-")]


if __name__ == "__main__":
    async def _demo() -> None:
        print("O'zbek ovozlari:", await available_uz_voices())
        data, ext = await synthesize("Salom. Vazifa bajarildi, hammasi joyida.")
        print(f"ovoz: {len(data)} bayt, .{ext}")

    asyncio.run(_demo())
