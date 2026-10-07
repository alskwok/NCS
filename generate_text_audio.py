#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量生成「課文朗讀」同「課文默書」要用嘅錄音（同詞語錄音用同一個粵語聲音）。

每一課課文會生成：
  • 逐句錄音（s0, s1, ...）：課文朗讀用，帶逐字時間標記，等網站可以跟住讀到邊個字著色；
  • 逐短語錄音（p0, p1, ...）：課文默書用，標點會讀出標點名（例如「開引號，今天天氣很好，感嘆號」）。
然後將同一課所有錄音合併做一個檔案 audio/text/<課文id>.mp3，同埋
audio/text/manifest.json（每段錄音喺合併檔入面嘅開始時間、長度、逐字時間標記）。

【使用方法】
1. 呢個script要喺你自己部電腦度跑（要連Microsoft語音服務，Claude 呢邊連唔到）。
2. 裝好 Python 3 之後，Terminal／命令提示字元行（只需要裝一次）：
       pip install edge-tts mutagen
   （權限錯誤就改用：pip install --user edge-tts mutagen）
3. 將呢個script放喺網站資料夾（同 texts.json 一齊嗰個；Excel 填好「課文」工作表之後
   行 excel_to_json.py 就會產生 texts.json），cd 入去，行：
       python3 generate_text_audio.py
   （Windows 可能要打 python 代替 python3）
4. 完成後會喺網站資料夾入面有 audio/text/ （一課一個 .mp3 ＋ manifest.json）。
   將成個 audio/text 資料夾放入網站、上傳到 GitHub 就得（用 GitHub Desktop：
   整個資料夾已經喺網站資料夾入面，Commit → Push 就得）。
5. 中途斷咗、或者之後加咗新課文／改咗課文，直接再行一次：錄音會暫存喺
   text_audio_cache/（內容一樣嘅句子唔會重新生成），所以只會補返新嘅／改過嘅部分。
   text_audio_cache 係暫存，唔使上傳。

想只做某啲年級（例如先做小一、小二）：
    python3 generate_text_audio.py --grade 小一,小二
（之後再行其他年級，之前做好嘅錄音同 manifest 會保留。）

想只重新打包（唔再生成）：python3 generate_text_audio.py --bundle-only

【份量提示】全部 271 課大約有 1.5 萬段錄音，要行成一兩個鐘；錄音檔總共大約 300 MB 上下，
所以建議分年級做，逐個年級上傳。
"""
import asyncio
import hashlib
import json
import os
import sys

try:
    import edge_tts
except ImportError:
    print("未搵到 edge-tts，請先行 `pip install edge-tts mutagen`（或 `pip install --user edge-tts mutagen`）再重新執行。")
    sys.exit(1)
try:
    from mutagen.mp3 import MP3
except ImportError:
    print("未搵到 mutagen，請先行 `pip install mutagen`（或 `pip install --user mutagen`）再重新執行。")
    sys.exit(1)

DATA_PATH = "texts.json"
CACHE_DIR = "text_audio_cache"
OUT_DIR = os.path.join("audio", "text")
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.json")

PREFERRED_ZH = ["zh-HK-HiuGaaiNeural", "zh-HK-WanLungNeural", "zh-HK-HiuMaanNeural"]
RATE_SENTENCE = "-5%"   # 課文朗讀：同詞語錄音一樣
RATE_PHRASE = "-10%"    # 課文默書：讀慢少少，俾學生有時間寫

# 有啲罕用字（尤其係擬聲詞）Microsoft 語音讀唔出，成句淨係得呢啲字就會「No audio was received」。
# 呢度俾佢「讀嗰陣」用一個讀音一樣、讀得出嘅字代替（只影響錄音，網站顯示嘅課文仍然係原字）。
# 代替嘅字一定要同原字一樣係單個字，咁逐字時間標記先會對得啱位。
# 之後如果見到其他字生成失敗，可以喺度加一行：「原字」: 「代替字」。
TTS_SUBST = {
    "嗵": "通",
}

MAX_CONCURRENT = 4
MAX_RETRIES = 3


async def resolve_voice():
    voices = await edge_tts.list_voices()
    names = {v["ShortName"] for v in voices}
    for n in PREFERRED_ZH:
        if n in names:
            print(f"  聲音：{n}")
            return n
    cands = [v["ShortName"] for v in voices if v["Locale"].startswith("zh-HK")] or \
            [v["ShortName"] for v in voices if v["Locale"].startswith("zh")]
    if not cands:
        print("搵唔到中文聲音。")
        sys.exit(1)
    print(f"  聲音：{cands[0]}")
    return cands[0]


def compute_marks(text, events):
    """WordBoundary → [[字元位置, 秒], ...]（同 generate_audio.py 一樣）。"""
    marks, cursor = [], 0
    for offset_100ns, frag in events:
        frag = frag or ""
        idx = text.find(frag, cursor) if frag else -1
        if idx == -1:
            idx = cursor
        cursor = idx + len(frag) if frag else cursor
        marks.append([idx, round(offset_100ns / 1e7, 3)])
    return marks


def for_tts(text):
    return "".join(TTS_SUBST.get(c, c) for c in text)


def clip_hash(voice, rate, text, with_marks):
    raw = f"{voice}|{rate}|{int(with_marks)}|{text}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


async def synth(text, voice, rate, with_marks, out_mp3, out_json, sem, failures):
    text = for_tts(text)  # 同長度替換，字元位置唔變
    if os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0 and os.path.exists(out_json):
        return
    async with sem:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                if with_marks:
                    try:
                        comm = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
                    except TypeError:
                        comm = edge_tts.Communicate(text, voice, rate=rate)
                else:
                    comm = edge_tts.Communicate(text, voice, rate=rate)
                audio, events = bytearray(), []
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        audio.extend(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        events.append((chunk["offset"], chunk.get("text")))
                if not audio:
                    raise RuntimeError("生成咗空音頻")
                with open(out_mp3, "wb") as f:
                    f.write(audio)
                marks = compute_marks(text, events) if with_marks else []
                with open(out_json, "w", encoding="utf-8") as f:
                    json.dump({"marks": marks}, f, ensure_ascii=False)
                return
            except Exception as e:
                if attempt == MAX_RETRIES:
                    failures.append((text[:20], str(e)))
                else:
                    await asyncio.sleep(1.5 * attempt)


def selected_texts(data):
    """--grade 小一,小二 → 只處理呢啲年級嘅課文；冇指定就全部。"""
    grades = None
    for i, a in enumerate(sys.argv):
        if a == "--grade" and i + 1 < len(sys.argv):
            grades = set(x.strip() for x in sys.argv[i + 1].split(",") if x.strip())
    items = data["texts"].items()
    if grades:
        items = [(k, v) for k, v in items if k.split("|||")[0] in grades]
    return dict(items)


def lesson_clip_list(text_entry):
    """課文 → [(clipKey, 讀出嘅文字, rate, 有冇時間標記)]，次序固定。"""
    out = []
    for par in text_entry["paragraphs"]:
        for s in par["sentences"]:
            out.append((s["id"], s["text"], RATE_SENTENCE, True))
            for ph in s["phrases"]:
                out.append((ph["id"], ph["say"], RATE_PHRASE, False))
    return out


async def generate(texts, voice):
    os.makedirs(CACHE_DIR, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    failures, tasks = [], []
    for entry in texts.values():
        for key, text, rate, with_marks in lesson_clip_list(entry):
            if not text.strip():
                continue
            h = clip_hash(voice, rate, text, with_marks)
            base = os.path.join(CACHE_DIR, h)
            tasks.append(synth(text, voice, rate, with_marks, base + ".mp3", base + ".json", sem, failures))
    total = len(tasks)
    print(f"總共 {total} 段錄音（已經有嘅會自動跳過）...")
    done = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        done += 1
        if done % 25 == 0 or done == total:
            print(f"  進度：{done}/{total}")
    return failures


def bundle(texts, voice):
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = {}
    if os.path.exists(MANIFEST_PATH):  # 保留之前做好嘅年級
        try:
            manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
        except Exception:
            manifest = {}
    n_lessons = n_missing = 0
    for lesson_key, entry in texts.items():
        tid = entry["id"]
        cursor = 0.0
        clips = {}
        out_path = os.path.join(OUT_DIR, tid + ".mp3")
        with open(out_path, "wb") as out:
            for key, text, rate, with_marks in lesson_clip_list(entry):
                if not text.strip():
                    continue
                base = os.path.join(CACHE_DIR, clip_hash(voice, rate, text, with_marks))
                if not (os.path.exists(base + ".mp3") and os.path.exists(base + ".json")):
                    n_missing += 1
                    continue
                with open(base + ".mp3", "rb") as f:
                    blob = f.read()
                dur = MP3(base + ".mp3").info.length
                out.write(blob)
                marks = json.load(open(base + ".json", encoding="utf-8")).get("marks") or []
                c = {"start": round(cursor, 3), "duration": round(dur, 3)}
                if marks:
                    c["marks"] = marks
                clips[key] = c
                cursor += dur
        if clips:
            manifest[tid] = {"bundle": tid, "clips": clips}
            n_lessons += 1
        else:
            os.remove(out_path)
            manifest.pop(tid, None)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    mb = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR) if f.endswith(".mp3")) / 1e6
    print(f"\n已打包 {n_lessons} 課課文，共 {mb:.1f} MB → {OUT_DIR}/")
    if n_missing:
        print(f"（有 {n_missing} 段錄音未生成，網站會對嗰幾段改用裝置朗讀；再行一次script可以補返。）")


async def main():
    if not os.path.exists(DATA_PATH):
        print(f"揾唔到 {DATA_PATH}，請將呢個script放喺網站資料夾入面再行（要先行過 excel_to_json.py 產生 texts.json）。")
        sys.exit(1)
    data = json.load(open(DATA_PATH, encoding="utf-8"))
    if not data.get("texts"):
        print("texts.json 入面未有課文。請先喺 Excel 「課文」工作表填好課文，再行 excel_to_json.py。")
        sys.exit(1)
    texts = selected_texts(data)
    if not texts:
        print("揀唔到任何課文（--grade 嘅年級名要同 Excel 一樣，例如 小一）。")
        sys.exit(1)
    print(f"今次處理 {len(texts)} 課課文（共 {len(data['texts'])} 課）。")
    print("揀緊聲音：")
    voice = await resolve_voice()
    if "--bundle-only" not in sys.argv:
        failures = await generate(texts, voice)
        if failures:
            print(f"\n有 {len(failures)} 段生成失敗（可以再行一次script補返）：")
            for t, err in failures[:10]:
                print(f"   - {t}…: {err}")
    bundle(texts, voice)
    print("\n完成！記得將 audio/text 資料夾一齊上傳到網站（GitHub）。")


if __name__ == "__main__":
    asyncio.run(main())
