"""Time one surah of the Archive Qatami recording: basmala end + start/end of every ayah (seconds).
Method: faster-whisper word times, aligned to the AlQuran.cloud quran-simple text, cuts at the midpoint between
ayahs, basmala/ayah-1 junction and last-ayah end snapped to real silences, each cut re-transcribed and scored.
Output is PRELIMINARY: validated is always false. A person must listen before anything is validated.
Usage: python3 align_surah.py N [N ...]   (writes out/NNN.json)"""
import sys, json, re, difflib, urllib.request, urllib.parse, unicodedata, subprocess, os, time, tempfile
from faster_whisper import WhisperModel

BASE = "https://archive.org/download/nasser-al-qatami_202404/"
DATA = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")))
VERIFY = os.environ.get("VERIFY", "1") == "1"
MODEL = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu", compute_type="int8",
                     cpu_threads=os.cpu_count() or 1)


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub("[ٱأإآ]", "ا", s).replace("ى", "ي").replace("ة", "ه").replace("ـ", "")
    return re.sub("[^\u0621-\u064A]", "", s)


def ayah_texts(n):
    d = json.load(urllib.request.urlopen(f"https://api.alquran.cloud/v1/surah/{n}/quran-simple", timeout=60))
    az = [a["text"] for a in d["data"]["ayahs"]]
    if n not in (1, 9) and norm(az[0]).startswith("بسماللهالرحمنالرحيم"):
        az[0] = " ".join(az[0].split()[4:])
    return az


def transcribe(path):
    segs, _ = MODEL.transcribe(path, language="ar", word_timestamps=True, condition_on_previous_text=False,
                               temperature=0.0, beam_size=5)
    return [w for s in segs for w in s.words if norm(w.word)]


def silences(f):
    o = subprocess.run(["ffmpeg", "-hide_banner", "-i", f, "-af", "silencedetect=noise=-35dB:d=0.04", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", o)]
    en = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", o)]
    dm = re.search(r"Duration: (\d+):(\d+):([\d.]+)", o)
    dur = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
    return [(s, en[i] if i < len(en) else dur) for i, s in enumerate(st)], dur


def run(n, work):
    t0 = time.time()
    f = os.path.join(work, f"{n:03d}.mp3")
    url = BASE + urllib.parse.quote(DATA["files"][str(n)])
    subprocess.run(["curl", "-sfL", "--retry", "3", "-m", "1800", "-o", f, url], check=True)
    iv, dur = silences(f)
    off, offsrc = 0.0, "none"
    if n not in (1, 9):
        head = os.path.join(work, "head.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", "8", "-i", f, "-ac", "1", "-ar", "16000", head], check=True)
        ws = transcribe(head)
        t = [norm(w.word) for w in ws[:4]]
        if len(ws) >= 4 and t[0].startswith("بسم") and t[3].startswith("الرحيم"):
            off, offsrc = round(ws[3].end, 3), "asr"
        else:
            off, offsrc = 3.0, "fallback_3.0"
    az = ayah_texts(n)
    wav = os.path.join(work, "body.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(off), "-i", f, "-ac", "1", "-ar", "16000", wav], check=True)
    words = [(norm(w.word), w.start + off, w.end + off) for w in transcribe(wav)]
    asr = "".join(w[0] for w in words)
    idx = [i for i, w in enumerate(words) for _ in w[0]]
    nz = [norm(a) for a in az]
    bounds, c = [], 0
    for a in nz:
        c += len(a)
        bounds.append(c)
    sm = difflib.SequenceMatcher(None, "".join(nz), asr, autojunk=False)
    mp = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
             mp[i1 + k] = j1 + k

    def first(a, b):
        for k in range(a, b):
            if k in mp:
                return words[idx[mp[k]]]

    def last(a, b):
        for k in range(b - 1, a - 1, -1):
            if k in mp:
                return words[idx[mp[k]]]
    segs, s0 = [], 0
    for b in bounds:
        fw, lw = first(s0, b), last(s0, b)
        segs.append((fw[1] if fw else None, lw[2] if lw else None))
        s0 = b
    ok = all(s is not None and e is not None for s, e in segs)
    res = {"surah": n, "ayahs": len(az), "align_ratio": round(sm.ratio(), 3), "ok": ok, "status": "preliminary_unreviewed",
           "validated": False, "basmala_end_asr": off, "basmala_src": offsrc, "audio_seconds": round(dur, 1)}
    if ok:
        st = [segs[0][0] - 0.05] + [(segs[i - 1][1] + segs[i][0]) / 2 for i in range(1, len(segs))]
        en = st[1:] + [min(dur, segs[-1][1] + 0.15)]
        bas_end = off
        if n not in (1, 9):  # snap basmala/ayah-1 junction to the silence nearest the ASR basmala end
            cand = [(abs(s - off), s, e) for s, e in iv if abs(s - off) < 0.8 and e - s >= 0.1 and s > 2]
            if cand:
                _, s, e = min(cand)
                bas_end = round(min(s + 0.12, (s + e) / 2), 2)
                st[0] = round(max(e - 0.12, (s + e) / 2), 2)
        tail = [(s, e) for s, e in iv if s >= en[-1] - 0.3 and e >= dur - 0.05]
        if tail:
            en[-1] = round(min(tail[0][0] + 0.12, dur), 2)
        cuts = [[round(a, 2), round(b, 2)] for a, b in zip(st, en)]
        res["basmala"] = [0.0, round(bas_end, 2)] if n not in (1, 9) else None
        res["ayahs_timing"] = cuts
        res["asr_check_scores"] = []
        if VERIFY:
            for i, (a, b) in enumerate(cuts, 1):
                p = os.path.join(work, "cut.mp3")
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", f,
                                "-ac", "1", "-b:a", "128k", p], check=True)
                tt = "".join(norm(w.word) for w in transcribe(p))
                res["asr_check_scores"].append(round(difflib.SequenceMatcher(None, nz[i - 1], tt, autojunk=False).ratio(), 2))
    res["compute_seconds"] = round(time.time() - t0, 1)
    res["compute_per_audio_second"] = round(res["compute_seconds"] / max(dur, 1), 3)
    res["cpus"] = os.cpu_count()
    return res


if __name__ == "__main__":
    os.makedirs("out", exist_ok=True)
    for n in map(int, sys.argv[1:]):
        with tempfile.TemporaryDirectory() as w:
            r = run(n, w)
        json.dump(r, open(f"out/{n:03d}.json", "w"), ensure_ascii=False, indent=1)
        print(n, "ok", r["ok"], "ratio", r["align_ratio"], "min score", min(r.get("asr_check_scores") or [None]),
              "compute/audio", r["compute_per_audio_second"], flush=True) 
