"""جدول النشر: ترتيب السور وتقسيم الآيات إلى مجموعات يومية.

القواعد:
  * 3 آيات يوميًا.
  * لو بقيت آية واحدة بعد المجموعة تُضاف إليها فتصبح 4.
  * لو بقيت آيتان تُنشران وحدهما.
  * بسملة في أول كل سورة إلا التوبة (9).
  * الفاتحة (1): البسملة هي الآية الأولى نفسها، فلا تُكرَّر.
"""
import json
import os

# عدد آيات كل سورة (رواية حفص)، من 1 إلى 114
AYAH_COUNTS = [
    7, 286, 200, 176, 120, 165, 206, 75, 129, 109, 123, 111, 43, 52, 99, 128,
    111, 110, 98, 135, 112, 78, 118, 64, 77, 227, 93, 88, 69, 60, 34, 30, 73,
    54, 45, 83, 182, 88, 75, 85, 54, 53, 89, 59, 37, 35, 38, 29, 18, 45, 60,
    49, 62, 55, 78, 96, 29, 22, 24, 13, 14, 11, 11, 18, 12, 12, 30, 52, 52,
    44, 28, 28, 20, 56, 40, 31, 50, 40, 46, 42, 29, 19, 36, 25, 22, 17, 19,
    26, 30, 20, 15, 21, 11, 8, 8, 19, 5, 8, 8, 11, 11, 8, 3, 9, 5, 4, 7, 3,
    6, 3, 5, 4, 5, 6,
]
assert len(AYAH_COUNTS) == 114 and sum(AYAH_COUNTS) == 6236

HERE = os.path.dirname(os.path.abspath(__file__))


def load_config(path=None):
    with open(path or os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def split_surah(n_ayahs, size=3):
    """يرجع قائمة (من_آية، إلى_آية) لسورة عدد آياتها n_ayahs."""
    groups, i = [], 1
    while i <= n_ayahs:
        rem = n_ayahs - i + 1
        # لو سيبقى بعد مجموعة عادية آية واحدة فقط نضمها إليها
        take = rem if rem == size + 1 else min(size, rem)
        groups.append((i, i + take - 1))
        i += take
    return groups


def has_basmala(surah, first_ayah):
    """هل نذكر بسملة قبل هذه المجموعة؟ (أول مجموعة في السورة، غير التوبة والفاتحة)"""
    return first_ayah == 1 and surah not in (1, 9)


def build_schedule(config=None):
    """الجدول الكامل: قائمة مجموعات مرتبة، كل مجموعة قاموس."""
    config = config or load_config()
    order = config["surah_order"]
    assert sorted(order) == list(range(1, 115)), "ترتيب السور لازم يحتوي 1..114 مرة واحدة"
    schedule = []
    for s in order:
        for a, b in split_surah(AYAH_COUNTS[s - 1], config.get("group_size", 3)):
            schedule.append({
                "index": len(schedule),
                "surah": s,
                "from": a,
                "to": b,
                "basmala": has_basmala(s, a),
            })
    return schedule


if __name__ == "__main__":
    sch = build_schedule()
    print(f"عدد المجموعات (أيام النشر): {len(sch)}")
    for g in sch[:8]:
        print(g)
