#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量生成「評估範圍」小測驗要用嘅題目錄音（同詞語、課文錄音用同一個粵語聲音）。

會讀 assessments.json（同 data.json），為每一條題目生成：
  • 題目朗讀：題目入面嘅空格（＿＿）唔讀字，網站會喺嗰度播一下「嘟」聲效，
    所以錄音係將題目喺空格前後切開，兩段分開錄；
  • 答題後嘅完整句子（只有填充題先有）；
  • 每個答案（撳答案旁邊嘅 🔊 就會讀）；
  • 「睇提示」入面嘅短句、例句、小卡（撳 🔊 或者小卡就會讀）。
錄音放喺 audio/quiz/ （每段一個 .mp3），同埋 audio/quiz/manifest.json（對照表，
入面包括每個字讀到嘅時間，網站用嚟做「讀到邊個字就著色邊個字」）。
網站會自動用呢啲錄音；冇錄音嘅題目就用裝置內置語音朗讀，所以先後次序唔緊要。

【使用方法】
1. 呢個script要喺你自己部電腦度跑（要連Microsoft語音服務，Claude 呢邊連唔到）。
2. 裝好 Python 3 之後行（只需要裝一次）：
       pip install edge-tts
   （權限錯誤就改用：pip install --user edge-tts）
3. 將呢個script放喺網站資料夾（同 assessments.json、data.json 一齊嗰個），cd 入去，行：
       python3 generate_quiz_audio.py
   （Windows 可能要打 python 代替 python3）
4. 完成後網站資料夾入面會有 audio/quiz/ 。用 GitHub Desktop Commit → Push 就得。
5. 之後加咗新題目、或者改咗 assessments.json，再行一次就得：做過嘅錄音唔會重做，只會補新嘅。
   quiz_audio_cache/ 係暫存（逐字時間標記），唔使上傳。

【檔名點樣嚟】檔名 = 題目文字嘅雜湊值（FNV-1a），網站用同一個算法砌返檔名，
所以題目文字一改，舊錄音就自動唔再用，要重新行呢個script先會有新錄音。
"""
import asyncio
import json
import os
import re
import struct
import sys

try:
    import edge_tts
except ImportError:
    print("未搵到 edge-tts，請先行 `pip install edge-tts`（或 `pip install --user edge-tts`）再重新執行。")
    sys.exit(1)

ASSESS_PATH = "assessments.json"
DATA_PATH = "data.json"
OUT_DIR = os.path.join("audio", "quiz")
CACHE_DIR = "quiz_audio_cache"   # 逐字時間標記嘅暫存，唔使上傳
MANIFEST_PATH = os.path.join(OUT_DIR, "manifest.json")

PREFERRED_ZH = ["zh-HK-HiuGaaiNeural", "zh-HK-WanLungNeural", "zh-HK-HiuMaanNeural"]
RATE = "-10%"   # 讀慢少少，細路仔聽得清楚啲（同課文默書一樣）

# 有啲罕用字 Microsoft 語音讀唔出，錄音時用讀音一樣嘅字代替（只影響錄音，網站顯示唔變）。
TTS_SUBST = {
    "嗵": "通",
    "戔": "煎",   # 「戔」電腦語音讀唔到，讀音同「煎」一樣
}

MAX_CONCURRENT = 4
MAX_RETRIES = 3


def fnv(text):
    """同 index.html 入面嘅 fnv() 一模一樣：FNV-1a，逐個 UTF-16 單位。"""
    h = 0x811C9DC5
    data = text.encode("utf-16-le")
    for (unit,) in struct.iter_unpack("<H", data):
        h ^= unit
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def for_tts(text):
    return "".join(TTS_SUBST.get(c, c) for c in text)


_SPEECH = re.compile("[\u3400-\u9fffA-Za-z0-9\U00020000-\U0003ffff]")


def opt_say(txt):
    """答案如果係「氵（三點水）」呢種寫法，就讀括號入面嗰part（同 index.html 入面 optSay 一致）。"""
    m = re.match(r"^[^（]+（(.+)）$", txt)
    return m.group(1) if m else txt


def question_segments(p):
    """未答嘅題目要讀嘅文字段：喺空格（＿＿）前後切開，淨係標點嘅一段唔讀。
    （要同 index.html 入面 speakQuestion / hasSpeech 一致。）"""
    if "＿＿" not in p:
        return [p]
    return [seg for seg in p.split("＿＿") if _SPEECH.search(seg)]


def chip_say(c):
    """提示入面細卡（部首、嘆詞等）撳落去讀嘅字（要同 index.html 入面 chipSay 一致）。"""
    return c[1].replace("・", "，") if "・" in c[1] else c[0] + "，" + c[1]


def fig_say(f):
    """字型結構小卡撳落去讀嘅字（要同 index.html 入面 figSay 一致）。"""
    return f[0].replace("：", "，") + "，例如" + f[2]


def tip_texts(it):
    """「睇提示」入面可以撳 🔊 聽嘅文字（要同 index.html 入面 renderAssess 一致）。"""
    out = []
    for bl in it.get("blocks", []) or []:
        if bl.get("t"):
            out.append(bl["t"])
        if bl.get("big"):
            out.append(bl["big"])
        out.extend(bl.get("ex") or [])
        out.extend(chip_say(c) for c in bl.get("chips") or [])
        out.extend(fig_say(f) for f in bl.get("figs") or [])
    return [x for x in out if _SPEECH.search(x)]


def collect_texts():
    """列出所有要朗讀嘅字串（要同 index.html 入面 speakQuestion / sayFullText 一致）。"""
    assess = json.load(open(ASSESS_PATH, encoding="utf-8"))
    data = json.load(open(DATA_PATH, encoding="utf-8"))
    texts = set()
    for grade, a in assess.items():
        for it in a.get("items", []):
            if it.get("type") == "cloze":
                key = "|||".join([grade, it["book"], it["lesson"]])
                for w in data["words"].get(key, []):
                    texts.add(w["word"])   # 揀答案旁邊嘅 🔊 讀呢個詞語
                    sents = []
                    if w.get("example"):
                        sents.append(w["example"])
                    sents += (it.get("extra") or {}).get(w["word"], [])
                    for s in sents:
                        if w["word"] not in s:
                            continue
                        texts.update(question_segments(s.replace(w["word"], "＿＿", 1)))
                        texts.add(s)
            texts.update(tip_texts(it))
            for q in it.get("quiz", []) or []:
                texts.update(question_segments(q["p"]))
                for opt in q["o"]:          # 每個答案旁邊嘅 🔊
                    spoken = opt_say(opt)
                    if _SPEECH.search(spoken):
                        texts.add(spoken)
                if "＿＿" in q["p"]:
                    texts.add(q["p"].replace("＿＿", q["o"][q["a"]], 1))
    return sorted(texts)


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
    """WordBoundary → [[字元位置, 秒], ...]（同 generate_text_audio.py 一樣）。"""
    marks, cursor = [], 0
    for offset_100ns, frag in events:
        frag = frag or ""
        idx = text.find(frag, cursor) if frag else -1
        if idx == -1:
            idx = cursor
        cursor = idx + len(frag) if frag else cursor
        marks.append([idx, round(offset_100ns / 1e7, 3)])
    return marks


async def synth(text, voice, out_mp3, out_marks, sem, failures):
    # mp3 同逐字時間標記都有，先當做完咗
    if (os.path.exists(out_mp3) and os.path.getsize(out_mp3) > 0 and os.path.exists(out_marks)):
        return
    spoken = for_tts(text)
    async with sem:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                try:
                    comm = edge_tts.Communicate(spoken, voice, rate=RATE, boundary="WordBoundary")
                except TypeError:
                    comm = edge_tts.Communicate(spoken, voice, rate=RATE)
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
                with open(out_marks, "w", encoding="utf-8") as f:
                    json.dump(compute_marks(spoken, events), f)
                return
            except Exception as e:
                if attempt == MAX_RETRIES:
                    failures.append((text[:24], str(e)))
                else:
                    await asyncio.sleep(1.5 * attempt)


async def main():
    if not os.path.exists(ASSESS_PATH) or not os.path.exists(DATA_PATH):
        print(f"搵唔到 {ASSESS_PATH} 或者 {DATA_PATH}，請喺網站資料夾入面行呢個script。")
        sys.exit(1)
    texts = collect_texts()
    print(f"一共 {len(texts)} 段要朗讀嘅文字。")
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    voice = await resolve_voice()
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    failures = []
    jobs, manifest = [], {}
    for text in texts:
        h = fnv(text)
        if h in manifest:
            print(f"  （雜湊撞咗：{text[:20]}，請話俾 Claude 知）")
            continue
        manifest[h] = h + ".mp3"
        jobs.append(synth(text, voice, os.path.join(OUT_DIR, h + ".mp3"),
                          os.path.join(CACHE_DIR, h + ".json"), sem, failures))
    done = 0
    for coro in asyncio.as_completed(jobs):
        await coro
        done += 1
        if done % 20 == 0 or done == len(jobs):
            print(f"  進度 {done}/{len(jobs)}")
    # 只將真係生成咗嘅錄音寫入對照表
    out = {}
    for h, fn in manifest.items():
        mp3 = os.path.join(OUT_DIR, fn)
        if not (os.path.exists(mp3) and os.path.getsize(mp3) > 0):
            continue
        entry = {"f": fn}
        mk = os.path.join(CACHE_DIR, h + ".json")
        if os.path.exists(mk):
            marks = json.load(open(mk, encoding="utf-8"))
            if len(marks) > 1:
                entry["m"] = marks
        out[h] = entry
    manifest = out
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
    print(f"完成：{len(manifest)} 段錄音，對照表 {MANIFEST_PATH}")
    print(f"錄音放咗喺呢個資料夾：{os.path.abspath(OUT_DIR)}")
    if failures:
        print(f"有 {len(failures)} 段生成失敗（再行一次通常就得）：")
        for t, e in failures[:5]:
            print("  ", t, "→", e)
        if len(manifest) == 0:
            print()
            print("【一段都未成功】常見原因：")
            print("  1. edge-tts 太舊：行 pip install -U edge-tts 更新，再重新行呢個 script；")
            print("  2. 部電腦連唔到網絡（學校網絡／VPN／防火牆可能會擋住）：轉用屋企或手機熱點試下；")
            print("  請將上面嘅錯誤訊息複製俾 Claude 睇。")


if __name__ == "__main__":
    asyncio.run(main())
