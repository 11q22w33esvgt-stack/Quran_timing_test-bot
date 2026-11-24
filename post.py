#!/usr/bin/env python3
"""بوت نشر القرآن: يُشغَّل كل 15 دقيقة، وينشر مرة واحدة يوميًا بعد الفجر بساعة.

متغيرات البيئة (أسرار): TELEGRAM_BOT_TOKEN, GROUP_CHAT_ID, OWNER_CHAT_ID
الاستخدام:
    python post.py                  # التشغيل الدوري العادي
    python post.py --preview        # يرسل المجموعة التالية لك أنت فقط ولا يغيّر الحالة
    python post.py --preview --index 5
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import cut_audio
import format as fmt
import schedule as sch
import verify

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
MANIFEST_FILE = os.path.join(HERE, "audio_manifest.json")
ARCHIVE_BASE = "https://archive.org/download"
ALADHAN = "https://api.aladhan.com/v1/timings"
LATE_NOTICE_MIN = 90


# ---------------- الحالة ----------------
def load_state(path=STATE_FILE):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"next_index": 0, "last_posted_date": None, "pending": None, "alerts": {}}


def save_state(state, path=STATE_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------- الشبكة (قابلة للاستبدال في الاختبار) ----------------
class Net:
    def __init__(self, token):
        self.token = token
        self.api = f"https://api.telegram.org/bot{token}"

    def get_json(self, url):
        for attempt in range(4):
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)

    def download(self, url, dest):
        for attempt in range(3):
            try:
                with requests.get(url, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                return dest
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))

    def _call(self, method, data, files=None):
        for attempt in range(4):
            r = requests.post(f"{self.api}/{method}", data=data, files=files, timeout=120)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and body.get("ok"):
                return body
            if r.status_code == 429:
                time.sleep(body.get("parameters", {}).get("retry_after", 5) + 1)
                continue
            if r.status_code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Telegram {method} فشل: {r.status_code} {body.get('description', r.text[:200])}")
        raise RuntimeError(f"Telegram {method} فشل بعد محاولات متعددة")

    def send_message(self, chat, text, html=True):
        data = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
        if html:
            data["parse_mode"] = "HTML"
        return self._call("sendMessage", data)

    def send_audio(self, chat, path, caption, title, performer, duration):
        with open(path, "rb") as f:
            return self._call("sendAudio", {
                "chat_id": chat, "caption": caption, "parse_mode": "HTML",
                "title": title, "performer": performer, "duration": int(duration),
            }, files={"audio": f})


# ---------------- الوقت والفجر ----------------
def fajr_time(config, day, net):
    """ترجع (datetime بتوقيت القاهرة، هل استُخدم الاحتياطي)."""
    tz = ZoneInfo(config["timezone"])
    url = (f"{ALADHAN}/{day.strftime('%d-%m-%Y')}?latitude={config['latitude']}"
           f"&longitude={config['longitude']}&method={config['fajr_method']}")
    try:
        hhmm = net.get_json(url)["data"]["timings"]["Fajr"].split(" ")[0]
        fallback = False
        h, m = map(int, hhmm.split(":"))
    except Exception:
        h, m = map(int, config["fallback_fajr"].split(":"))
        fallback = True
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz), fallback


# ---------------- التنبيهات ----------------
def alert(net, owner, state, key, text, save=None):
    """ينبّه المالكة مرة واحدة لكل (key) في اليوم لتجنب الإزعاج عند التكرار كل 15 دقيقة."""
    today = datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d")
    if state["alerts"].get(key) == today:
        return
    state["alerts"][key] = today
    if save:
        save(state)
    try:
        net.send_message(owner, f"⚠️ تنبيه من بوت القرآن\n{text}", html=False)
    except Exception as e:  # التنبيه نفسه لا يجب أن يكسر التشغيل
        print("تعذر إرسال التنبيه:", e, file=sys.stderr)


# ---------------- تجهيز المقطع ----------------
def sha_ok(path, md5):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == md5


def prepare(group, ayahs_all, n_ayahs, net, workdir, manifest, transcriber):
    """يحمّل ملف السورة، يقص، يتحقق. ترجع (مسار_mp3، المدة). ترفع CutError عند أي شك."""
    entry = manifest["files"][str(group["surah"])]
    src = os.path.join(workdir, f"{group['surah']:03d}.mp3")
    url = f"{ARCHIVE_BASE}/{manifest['identifier']}/{urllib.parse.quote(entry['name'])}"
    net.download(url, src)
    if not sha_ok(src, entry["md5"]):
        raise cut_audio.CutError("بصمة ملف السورة المحمّل لا تطابق المتوقع (ملف تالف أو تغيّر)")
    duration = cut_audio.probe_duration(src)
    sil = cut_audio.detect_silences(src, duration)
    texts = {n: fmt.strip_basmala(group["surah"], n, t) for n, (t, _) in ayahs_all.items()}
    start, end = cut_audio.plan_group(sil, duration, group["surah"], group["from"], group["to"], texts, n_ayahs)
    out = os.path.join(workdir, f"clip_{group['index']}.mp3")
    cut_audio.cut(src, out, start, end)
    expected = " ".join(texts[n] for n in range(group["from"], group["to"] + 1))
    if group["basmala"]:
        expected = cut_audio.BASMALA_TEXT + " " + expected
    ok, ratio, heard = verify.verify_clip(out, expected, transcriber=transcriber)
    if not ok:
        raise cut_audio.CutError(
            f"فشل التحقق من المقطع (تطابق {ratio:.2f}).\nالمتوقع: {expected[:150]}\nالمسموع: {heard[:150]}")
    return out, cut_audio.probe_duration(out)


# ---------------- الإرسال ----------------
def deliver(group, chat, net, state, save, clip, clip_dur, surah_name, items, track):
    """يرسل الصوت ثم التفسير مباشرة. لو انقطع في المنتصف يكمل من حيث توقف."""
    pend = state.get("pending") if track else None
    if not pend or pend.get("index") != group["index"]:
        pend = {"index": group["index"], "audio_sent": False, "tafsir_sent": 0}
    caption = fmt.build_caption(surah_name, group["from"], group["to"], group["basmala"])
    messages = fmt.build_tafsir_messages(surah_name, group["from"], group["to"], group["basmala"], items)
    if not pend["audio_sent"]:
        title = f"{surah_name} {fmt.range_label(group['from'], group['to'])}"
        net.send_audio(chat, clip, caption, title, "ناصر القطامي", clip_dur)
        pend["audio_sent"] = True
        if track:
            state["pending"] = pend
            save(state)
    while pend["tafsir_sent"] < len(messages):
        net.send_message(chat, messages[pend["tafsir_sent"]])
        pend["tafsir_sent"] += 1
        if track:
            state["pending"] = pend
            save(state)


# ---------------- التشغيل ----------------
def run(config, env, net, now=None, state_path=STATE_FILE, transcriber=verify.transcribe,
        manifest=None, preview=False, index=None, workdir=None):
    tz = ZoneInfo(config["timezone"])
    now = now or datetime.now(tz)
    state = load_state(state_path)
    save = lambda s: save_state(s, state_path)
    owner = env["OWNER_CHAT_ID"]
    schedule = sch.build_schedule(config)

    if not preview:
        if not config.get("enabled", False):
            print("البوت متوقف (enabled=false في config.json).")
            return "disabled"
        today = now.strftime("%Y-%m-%d")
        if state.get("last_posted_date") == today:
            return "already_posted"
        fajr, fallback = fajr_time(config, now.date(), net)
        target = fajr + timedelta(minutes=config["fajr_offset_minutes"])
        if now < target:
            return "too_early"
        if fallback:
            alert(net, owner, state, "fajr_fallback",
                  f"تعذر جلب موعد الفجر؛ استخدمت {config['fallback_fajr']} الاحتياطي.", save)

    idx = index if (preview and index is not None) else state["next_index"]
    if idx >= len(schedule):
        alert(net, owner, state, "finished", "🎉 اكتملت كل المجموعات. لا يوجد المزيد للنشر.", save)
        return "finished"
    group = schedule[idx]
    chat = owner if preview else env["GROUP_CHAT_ID"]
    manifest = manifest or json.load(open(MANIFEST_FILE, encoding="utf-8"))
    label = f"#{idx} سورة {group['surah']} آيات {group['from']}-{group['to']}"
    try:
        surah_name, ayahs = fmt.fetch_surah(group["surah"], net.get_json)
        items = fmt.group_ayahs(group["surah"], group["from"], group["to"], ayahs)
        with (tempfile.TemporaryDirectory() if workdir is None else _Keep(workdir)) as wd:
            clip, dur = prepare(group, ayahs, len(ayahs), net, wd, manifest, transcriber)
            if preview:
                net.send_message(owner, f"🧪 معاينة (لم تُنشر): {label}", html=False)
            deliver(group, chat, net, state, save, clip, dur, surah_name, items, track=not preview)
    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        alert(net, owner, state, f"fail_{idx}", f"فشل نشر {label}\n{type(e).__name__}: {e}", save)
        return "failed"

    if not preview:
        state["next_index"] = idx + 1
        state["last_posted_date"] = now.strftime("%Y-%m-%d")
        state["pending"] = None
        save(state)
        late = (now - target).total_seconds() / 60
        if late > LATE_NOTICE_MIN:
            alert(net, owner, state, "late", f"تم النشر متأخرًا {int(late)} دقيقة عن الموعد ({label}).", save)
    return "posted"


class _Keep:
    def __init__(self, path):
        self.path = path
        os.makedirs(path, exist_ok=True)

    def __enter__(self):
        return self.path

    def __exit__(self, *a):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--index", type=int)
    args = ap.parse_args()
    env = {k: os.environ.get(k, "") for k in ("TELEGRAM_BOT_TOKEN", "GROUP_CHAT_ID", "OWNER_CHAT_ID")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit(f"أسرار ناقصة: {', '.join(missing)}")
    config = sch.load_config()
    net = Net(env["TELEGRAM_BOT_TOKEN"])
    result = run(config, env, net, preview=args.preview, index=args.index)
    print("النتيجة:", result)
    if result == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()
