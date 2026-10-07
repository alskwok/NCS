# -*- coding: utf-8 -*-
"""
生成「默書結束」語音檔：
  audio/dictation_end.mp3     （中文介面用，粵語）
  audio/dictation_end_en.mp3  （英文介面用，英文）
用同 generate_audio.py 一模一樣嘅聲音同語速（-5%），所以聽落同其他詞語讀音係同一把聲。
你已經有 dictation_end.mp3 嘅話，只想補英文版：加參數 en
    python3 make_dictation_end_audio.py en

用法（喺有網絡嘅電腦行）：
    pip install edge-tts
    python3 make_dictation_end_audio.py
"""
import asyncio, os, sys
try:
    import edge_tts
except ImportError:
    print("未搵到 edge-tts，請先行 `pip install edge-tts` 再重新執行。")
    sys.exit(1)

RATE = "-5%"
HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = {
    "zh": ("默書結束喇，請自己檢查寫得啱唔啱。",
           ["zh-HK-HiuGaaiNeural", "zh-HK-WanLungNeural", "zh-HK-HiuMaanNeural"], "zh-HK",
           os.path.join(HERE, "audio", "dictation_end.mp3")),
    "en": ("Dictation finished. Please check your answers.",
           ["en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural"], "en-US",
           os.path.join(HERE, "audio", "dictation_end_en.mp3")),
}

async def main():
    which = [a for a in sys.argv[1:] if a in JOBS] or list(JOBS)
    voices = {v["ShortName"] for v in await edge_tts.list_voices()}
    for key in which:
        text, preferred, locale, out = JOBS[key]
        voice = next((v for v in preferred if v in voices), None) or \
                next((v for v in sorted(voices) if v.startswith(locale)), None)
        if not voice:
            print("搵唔到聲音：", locale); continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        await edge_tts.Communicate(text, voice, rate=RATE).save(out)
        print("完成：", out, "（聲音：" + voice + "）")

asyncio.run(main())
