"""التحقق من المقطع: تفريغ آلي ومقارنته بنص الآيات المتوقع."""
import difflib
import re

MIN_RATIO = 0.85

_DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")


def normalize(s):
    s = _DIACRITICS.sub("", s)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"), ("ى", "ي"),
                 ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        s = s.replace(a, b)
    s = re.sub(r"[^\u0621-\u064A ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a, b):
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def transcribe(path):
    """تفريغ بـ faster-whisper (مثبّت في بيئة التشغيل فقط)."""
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(path, language="ar", beam_size=5)
    return " ".join(s.text for s in segments)


EDGE_WORDS = 3
EDGE_RATIO = 0.70


def edge_ok(heard, expected):
    """يتأكد أن أول وآخر كلمات النص المتوقع موجودة في طرفي التفريغ، لأن القص الخاطئ
    يقطع غالبًا الطرف فقط وتبقى النسبة الكلية عالية في المقاطع الطويلة."""
    h, e = normalize(heard).split(), normalize(expected).split()
    if len(e) < 2 or not h:
        return False
    k = min(EDGE_WORDS, len(e))
    head = difflib.SequenceMatcher(None, " ".join(h[:k]), " ".join(e[:k])).ratio()
    tail = difflib.SequenceMatcher(None, " ".join(h[-k:]), " ".join(e[-k:])).ratio()
    return head >= EDGE_RATIO and tail >= EDGE_RATIO


def verify_clip(path, expected_text, transcriber=transcribe, min_ratio=MIN_RATIO):
    """ترجع (نجح؟، نسبة التطابق، النص المفرَّغ).
    النجاح يشترط نسبة كلية كافية وسلامة الطرفين."""
    heard = transcriber(path)
    ratio = similarity(heard, expected_text)
    return (ratio >= min_ratio and edge_ok(heard, expected_text)), ratio, heard
