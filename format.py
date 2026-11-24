"""جلب النص والتفسير الميسر وتنسيق الرسائل (HTML لتيليجرام)."""
import html

API = "https://api.alquran.cloud/v1"
TG_LIMIT = 4096
CAPTION_LIMIT = 1024
BASMALA = "﷽"
LINE = "─ ─ ─ ─ ─ ─"
_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def ar_num(n):
    return str(n).translate(_DIGITS)


def esc(s):
    return html.escape(s, quote=False)


def fetch_surah(surah, get):
    """يجلب سورة كاملة: النص العثماني + التفسير الميسر. `get(url)` ترجع JSON.
    ترجع (اسم_السورة، {رقم_الآية: (نص، تفسير)})."""
    data = get(f"{API}/surah/{surah}/editions/quran-uthmani,ar.muyassar")["data"]
    text_ed, tafsir_ed = data[0], data[1]
    if len(text_ed["ayahs"]) != len(tafsir_ed["ayahs"]):
        raise ValueError(f"سورة {surah}: عدد آيات النص والتفسير مختلف")
    ayahs = {}
    for t, f in zip(text_ed["ayahs"], tafsir_ed["ayahs"]):
        n = t["numberInSurah"]
        if f["numberInSurah"] != n:
            raise ValueError(f"سورة {surah}: ترقيم غير متطابق عند الآية {n}")
        ayahs[n] = (t["text"].strip(), f["text"].strip())
    return text_ed["name"], ayahs


def strip_basmala(surah, n, text):
    """الـAPI يضيف البسملة (4 كلمات) قبل الآية 1 في كل السور إلا الفاتحة والتوبة."""
    if n == 1 and surah not in (1, 9):
        words = text.split(" ")
        if len(words) <= 4:
            raise ValueError(f"سورة {surah}: الآية 1 أقصر من المتوقع بعد حذف البسملة")
        return " ".join(words[4:])
    return text


def group_ayahs(surah, a, b, ayahs):
    out = []
    for n in range(a, b + 1):
        if n not in ayahs:
            raise ValueError(f"الآية {surah}:{n} غير موجودة في البيانات")
        text, tafsir = ayahs[n]
        text = strip_basmala(surah, n, text)
        if not text or not tafsir:
            raise ValueError(f"نص أو تفسير فارغ للآية {surah}:{n}")
        out.append((n, text, tafsir))
    return out


def range_label(a, b):
    return f"الآية {ar_num(a)}" if a == b else f"الآيات {ar_num(a)}–{ar_num(b)}"


def build_caption(surah_name, a, b, basmala):
    cap = f"🎧 {esc(surah_name)} · {range_label(a, b)}\nبصوت الشيخ ناصر القطامي"
    if len(cap) > CAPTION_LIMIT:
        raise ValueError("التعليق أطول من حد تيليجرام")
    return cap


def _ayah_block(n, text, tafsir):
    return (f"<b>﴿ {esc(text)} ﴾</b> ‏﴿{ar_num(n)}﴾\n"
            f"<blockquote>{esc(tafsir)}</blockquote>")


def _split_long(s, limit):
    """يقسم نصًا طويلًا عند آخر مسافة قبل الحد."""
    parts = []
    while len(s) > limit:
        cut = s.rfind(" ", 0, limit)
        cut = cut if cut > 0 else limit
        parts.append(s[:cut].rstrip())
        s = s[cut:].lstrip()
    parts.append(s)
    return parts


def build_tafsir_messages(surah_name, a, b, basmala, items):
    """items = [(رقم، نص، تفسير)]. ترجع قائمة رسائل HTML كل منها <= 4096 حرفًا.
    يُقسَّم عند حدود الآيات؛ وآية طويلة جدًا تُقسم عند مسافة."""
    header = f"📖 <b>التفسير الميسر</b>\n{esc(surah_name)} · {range_label(a, b)}\n{LINE}"
    if basmala:
        header = f"{BASMALA}\n{header}"
    blocks = []
    for n, text, tafsir in items:
        block = _ayah_block(n, text, tafsir)
        if len(block) > TG_LIMIT - len(header) - 4:
            # آية طويلة: نقسم نص التفسير ونعيد بناء كتل صالحة
            head = f"<b>﴿ {esc(text)} ﴾</b> ‏﴿{ar_num(n)}﴾\n"
            room = TG_LIMIT - len(head) - len("<blockquote></blockquote>") - 400
            for piece in _split_long(esc(tafsir), room):
                blocks.append(head + f"<blockquote>{piece}</blockquote>")
                head = "<i>(تابع)</i>\n"
        else:
            blocks.append(block)
    messages, current = [], header
    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) <= TG_LIMIT:
            current = candidate
        else:
            messages.append(current)
            current = block
    messages.append(current)
    for m in messages:
        assert len(m) <= TG_LIMIT, "رسالة أطول من حد تيليجرام"
    return messages
