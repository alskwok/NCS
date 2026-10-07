# -*- coding: utf-8 -*-
"""
生成「默書結束」語音檔 audio/dictation_end.mp3
用同 generate_audio.py 一模一樣嘅粵語聲音（zh-HK-HiuGaaiNeural，搵唔到就依次退返
WanLung／HiuMaan）同語速（-5%），所以聽落同其他詞語讀音係同一把聲。

用法（同你之前行 generate_audio.py 一樣，喺有網絡嘅電腦行）：
    pip install edge-tts
    python3 make_dictation_end_audio.py
完成後會出現 audio/dictation_end.mp3，將佢放上GitHub嘅 audio/ 資料夾就得。
"""
import asyncio, os, sys
try:
    import edge_tts
except ImportError:
    print("未搵到 edge-tts，請先行 `pip install edge-tts` 再重新執行。")
    sys.exit(1)

TEXT = "默書結束喇，請自己檢查寫得啱唔啱。"
PREFERRED = ["zh-HK-HiuGaaiNeural", "zh-HK-WanLungNeural", "zh-HK-HiuMaanNeural"]
RATE = "-5%"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audio", "dictation_end.mp3")

async def main():
    voices = {v["ShortName"] for v in await edge_tts.list_voices()}
    voice = next((v for v in PREFERRED if v in voices), None)
    if not voice:
        voice = next((v for v in sorted(voices) if v.startswith("zh-HK")), None)
    if not voice:
        print("搵唔到粵語聲音"); sys.exit(1)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    await edge_tts.Communicate(TEXT, voice, rate=RATE).save(OUT)
    print("完成：", OUT, "（聲音：" + voice + "）")

asyncio.run(main())
