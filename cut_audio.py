"""قص مقطع مجموعة آيات من ملف سورة كاملة.

الطريقة: كشف فراغات الصمت الحقيقية بـ ffmpeg، ثم اختيار أنسب فراغات لتكون حدود
الآيات (مطابقة تناسب عدد الحروف)، والقص دائمًا داخل الصمت لا داخل الكلام.
التحقق النهائي (تفريغ المقطع ومقارنته بالنص) في verify.py.
"""
import re
import subprocess

from verify import normalize

PAD = 0.25            # أقصى صمت يُترك قبل المقطع وبعده (ثانية)
MIN_SILENCE = 0.20    # أقصر صمت يُعتبر فاصلًا
NOISE_DB = "-35dB"
BASMALA_TEXT = "بسم الله الرحمن الرحيم"


class CutError(Exception):
    pass


def probe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def parse_silences(stderr, duration):
    """يستخرج [(بداية، نهاية)] من مخرجات silencedetect."""
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", stderr)]
    sil = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration  # صمت ممتد حتى نهاية الملف
        sil.append((max(0.0, s), e))
    return sil


def detect_silences(path, duration):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
         f"silencedetect=noise={NOISE_DB}:d={MIN_SILENCE}", "-f", "null", "-"],
        capture_output=True, text=True)
    return parse_silences(r.stderr, duration)


def speech_bounds(silences, duration):
    """بداية الكلام ونهايته بعد استبعاد الصمت في أول الملف وآخره."""
    start, end = 0.0, duration
    if silences and silences[0][0] <= 0.05:
        start = silences[0][1]
        silences = silences[1:]
    if silences and silences[-1][1] >= duration - 0.05:
        end = silences[-1][0]
        silences = silences[:-1]
    return start, end, silences


def weight(text):
    return max(1, len(normalize(text).replace(" ", "")))


def choose_boundaries(cands, speech_start, speech_end, weights):
    """يختار len(weights)-1 فاصلًا من cands (قائمة (بداية، نهاية)) بترتيب زمني،
    بحيث تقترب نسب الأزمنة من نسب عدد الحروف. برمجة ديناميكية."""
    k = len(weights) - 1
    if k == 0:
        return []
    m = len(cands)
    if m < k:
        raise CutError(f"فواصل الصمت ({m}) أقل من المطلوب ({k}): التلاوة متصلة")
    total_w = float(sum(weights))
    span = speech_end - speech_start
    expect, acc = [], 0.0
    for w in weights[:-1]:
        acc += w
        expect.append(acc / total_w)
    pos = [((s + e) / 2 - speech_start) / span for s, e in cands]
    bonus = [min(e - s, 1.0) * 0.02 for s, e in cands]   # تفضيل الصمت الأطول
    INF = float("inf")
    cost = [[INF] * m for _ in range(k)]
    back = [[-1] * m for _ in range(k)]
    for i in range(m):
        cost[0][i] = (pos[i] - expect[0]) ** 2 - bonus[i]
    for j in range(1, k):
        best, best_i = INF, -1
        for i in range(j, m):
            if cost[j - 1][i - 1] < best:
                best, best_i = cost[j - 1][i - 1], i - 1
            if best < INF:
                cost[j][i] = best + (pos[i] - expect[j]) ** 2 - bonus[i]
                back[j][i] = best_i
    last = min(range(m), key=lambda i: cost[k - 1][i])
    if cost[k - 1][last] == INF:
        raise CutError("تعذر اختيار الفواصل")
    chosen = [last]
    for j in range(k - 1, 0, -1):
        chosen.append(back[j][chosen[-1]])
    chosen.reverse()
    return [cands[i] for i in chosen]


def clip_window(boundaries, seg_first, seg_last, n_segs, speech_start, speech_end):
    """يحسب (بداية، نهاية) المقطع للقطاعات seg_first..seg_last (فهارس من 0).
    boundaries[i] هو الصمت بين القطاع i والقطاع i+1."""
    if seg_first == 0:
        start = speech_start
    else:
        s, e = boundaries[seg_first - 1]
        start = e - min(PAD, (e - s) / 2)
    if seg_last == n_segs - 1:
        end = speech_end
    else:
        s, e = boundaries[seg_last]
        end = s + min(PAD, (e - s) / 2)
    if end - start < 1.0:
        raise CutError("المقطع الناتج قصير جدًا")
    return start, end


def plan_group(silences, duration, surah, first, last, ayah_texts, n_ayahs):
    """يخطط القص لمجموعة آيات first..last من سورة عدد آياتها n_ayahs.
    ayah_texts: {رقم: نص} لكل آيات السورة (للأوزان). ترجع (بداية، نهاية)."""
    has_basmala_seg = surah not in (1, 9)
    texts = ([BASMALA_TEXT] if has_basmala_seg else []) + [ayah_texts[i] for i in range(1, n_ayahs + 1)]
    weights = [weight(t) for t in texts]
    speech_start, speech_end, cands = speech_bounds(silences, duration)
    cands = [c for c in cands if speech_start < c[0] and c[1] < speech_end]
    boundaries = choose_boundaries(cands, speech_start, speech_end, weights)
    offset = 1 if has_basmala_seg else 0
    # أول مجموعة في السورة تبدأ من بداية الملف (فتشمل البسملة المسجلة)
    seg_first = 0 if first == 1 else offset + first - 1
    seg_last = offset + last - 1
    return clip_window(boundaries, seg_first, seg_last, len(texts), speech_start, speech_end)


def cut(src, dst, start, end):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}",
         "-to", f"{end:.3f}", "-i", src, "-vn", "-c:a", "libmp3lame", "-b:a", "96k",
         "-ar", "44100", dst],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise CutError(f"فشل ffmpeg: {r.stderr[-300:]}")
    return dst
