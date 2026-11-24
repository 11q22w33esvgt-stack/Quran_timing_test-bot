"""Merge out/*.json (from align_surah.py) into data.json as PRELIMINARY timings. Never sets validated true.
Surahs where alignment failed, or the ayah count differs, are skipped and listed."""
import json, glob, sys
d = json.load(open("data.json"))
skipped = []
for p in sorted(glob.glob(sys.argv[1] if len(sys.argv) > 1 else "out/*.json")):
    r = json.load(open(p)); n = str(r["surah"])
    if not r["ok"] or len(r["ayahs_timing"]) != d["counts"][n]:
        skipped.append((n, "alignment failed or count mismatch")); continue
    if d["timings"].get(n, {}).get("validated") is True:
        skipped.append((n, "already validated, not overwritten")); continue
    d["timings"][n] = {"status": "preliminary_unreviewed", "validated": False,
                       "basmala": r["basmala"] if r["basmala"] else [0.0, 0.0], "ayahs": r["ayahs_timing"],
                       "asr_check_scores": r["asr_check_scores"],
                       "note": "ASR alignment, boundaries snapped to silences at surah start/end. Not listened to. Do not set validated true until a person has checked every cut."}
json.dump(d, open("data.merged.json", "w"), ensure_ascii=False, indent=1)
print("merged into data.merged.json; skipped:", skipped)
