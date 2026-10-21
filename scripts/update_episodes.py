"""自動補上《未來轉運站》新集數到 index.html。

流程：讀 SoundOn RSS → 找出比 index.html 最新集數更新的集 → 下載音檔 →
faster-whisper 轉錄 → Claude 依既有格式逐段整理 → 寫回 index.html。

  python scripts/update_episodes.py                 # 全自動（需 ANTHROPIC_API_KEY）
  python scripts/update_episodes.py --from-json DIR # 只把 DIR/<n>.json 已整理好的集數併入
"""
import argparse, json, os, re, sys, urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
WORK = ROOT / ".work"
PODCAST = "6640e440-ba6e-4be0-8f07-04a45e2524c7"
RSS = f"https://feeds.soundon.fm/podcasts/{PODCAST}.xml"
YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=UC87QK4OK6qe6hsDT08VF30g"
UA = {"User-Agent": "Mozilla/5.0"}
EP_RE = re.compile(r"const EPISODES=(\[.*?\]);\n", re.S)
CAT_RE = re.compile(r"const CATEGORIES=(\[.*?\]);")


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120).read()


def load_index():
    html = INDEX.read_text(encoding="utf-8")
    return html, json.loads(EP_RE.search(html).group(1)), json.loads(CAT_RE.search(html).group(1))


def save_index(html, episodes):
    episodes.sort(key=lambda e: -e["n"])
    total = len(episodes)
    html = EP_RE.sub(lambda m: "const EPISODES=" + json.dumps(episodes, ensure_ascii=False) + ";\n", html, count=1)
    html = re.sub(r'(class="index-number">)\d+(<span>集已完成音檔整理／共 )\d+( 集)', rf"\g<1>{total}\g<2>{total}\g<3>", html)
    html = re.sub(r'(data-mode="all"[^>]*>所有集數 <small>)\d+(</small>)', rf"\g<1>{total}\g<2>", html)
    html = re.sub(r"未來轉運站_\d+集摘要", f"未來轉運站_{total}集摘要", html)
    INDEX.write_text(html, encoding="utf-8")
    readme = ROOT / "README.md"
    readme.write_text(re.sub(r"Podcast \d+ 集", f"Podcast {total} 集", readme.read_text(encoding="utf-8")), encoding="utf-8")


def feed_items():
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    yt = {}
    try:
        ans = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
        for e in ET.fromstring(fetch(YT_FEED)).findall("a:entry", ans):
            m = re.match(r"EP(\d+)", e.find("a:title", ans).text or "")
            if m:
                yt[int(m.group(1))] = e.find("yt:videoId", ans).text
    except Exception as exc:  # YouTube 連結抓不到不影響其他欄位
        print("YouTube feed 讀取失敗：", exc, file=sys.stderr)
    for it in ET.fromstring(fetch(RSS)).iter("item"):
        title = it.findtext("title") or ""
        m = re.match(r"EP(\d+)_《(.+?)》", title)
        if not m:
            continue
        n = int(m.group(1))
        from email.utils import parsedate_to_datetime
        yield {
            "n": n,
            "title": m.group(2).strip(),
            "feedTitle": title,
            "date": parsedate_to_datetime(it.findtext("pubDate")).strftime("%Y-%m-%d"),
            "minutes": round(int(it.findtext("itunes:duration", namespaces=ns) or 0) / 60),
            "audio": it.find("enclosure").get("url"),
            "originalUrl": it.findtext("link"),
            "desc": re.sub(r"<[^>]+>", "", it.findtext("description") or ""),
            "youtube": yt.get(n, ""),
        }


def transcribe(item):
    import av, numpy as np, opencc
    from faster_whisper import WhisperModel

    WORK.mkdir(exist_ok=True)
    out = WORK / f"{item['n']}.txt"
    if out.exists():
        return out.read_text(encoding="utf-8")
    mp3 = WORK / f"{item['n']}.mp3"
    mp3.write_bytes(fetch(item["audio"]))
    # 自行解碼成 16kHz mono，避開 faster-whisper 與新版 PyAV 的參數不相容
    c, rs, chunks = av.open(str(mp3)), av.AudioResampler(format="flt", layout="mono", rate=16000), []
    for fr in c.decode(audio=0):
        chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(fr)]
    chunks += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    audio = np.concatenate(chunks).astype(np.float32)
    model = WhisperModel(os.environ.get("WHISPER_MODEL", "medium"), device="cpu", compute_type="int8")
    cc = opencc.OpenCC("s2twp")
    segs, _ = model.transcribe(audio, language="zh", vad_filter=True,
                               initial_prompt="以下是臺大科技政策與產業發展研究中心未來轉運站節目的繁體中文逐字稿。")
    text = "".join(f"[{int(s.start)}] {cc.convert(s.text.strip())}\n" for s in segs)
    out.write_text(text, encoding="utf-8")
    return text


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "role", "host", "lead", "sections", "strategy", "cautions", "tags", "basis"],
    "properties": {
        "name": {"type": "string"},
        "role": {"type": "string"},
        "host": {"type": "string"},
        "lead": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["start", "end", "title", "text"],
            "properties": {"start": {"type": "integer"}, "end": {"type": "integer"},
                           "title": {"type": "string"}, "text": {"type": "string"}}}},
        "strategy": {"type": "string"},
        "cautions": {"type": "array", "items": {"type": "string"}},
        "tags": {"type": "array", "items": {"type": "string"}},
        "basis": {"type": "string"},
    },
}


def summarize(item, transcript, example, categories):
    import anthropic

    ex = {k: example[k] for k in ("name", "role", "host")} | {k: example["audioNote"][k] for k in ("basis", "lead", "sections", "strategy", "cautions")} | {"tags": example["tags"]}
    prompt = f"""你在維護《未來轉運站》Podcast 的個人閱讀整理網站。請把下面這一集的機器轉錄逐字稿，整理成與範例完全相同風格與結構的 JSON。

要求：
- 全部使用繁體中文（臺灣用語），以第三人稱轉述講者內容，忠於逐字稿，不加入逐字稿沒有的事實。
- name／role：來賓站長的姓名與職稱；host：節目主持人「姓名（職稱）」。依官方節目介紹為準，逐字稿的人名誤辨要校正。
- lead：一段 150–250 字的核心結論。
- sections：依逐字稿時間軸切成 5–8 段，start／end 為秒數（取自逐字稿行首的 [秒數]），略過開場與結尾的節目介紹；每段 title 概括主張，text 300–600 字詳述講者的主張、方法與案例。
- strategy：以「整理者的課程連結：」開頭，連結策略管理概念，並明確區分這是整理者解讀而非講者原話。
- cautions：列出 ASR 誤辨、推定校正、無法確認之處，附約略時間點（mm:ss）。
- tags：從這些分類中選 1–4 個：{json.dumps(categories, ensure_ascii=False)}
- basis：說明整理依據（完整官方音檔機器轉錄後逐段閱讀整理，未人工逐秒核聽；講者與主持人依官方節目介紹）。

=== 範例（EP{example['n']} 的輸出） ===
{json.dumps(ex, ensure_ascii=False)}

=== 本集官方資訊 ===
標題：{item['feedTitle']}
節目介紹：{item['desc']}

=== 本集逐字稿（[秒數] 文字） ===
{transcript}"""
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
        max_tokens=64000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"EP{item['n']} 整理失敗：stop_reason={msg.stop_reason}")
    return json.loads(next(b.text for b in msg.content if b.type == "text"))


def build_episode(item, s, categories):
    tags = [t for t in s["tags"] if t in categories]
    return {
        "n": item["n"], "title": item["title"], "name": s["name"], "role": s["role"], "host": s["host"],
        "date": item["date"], "minutes": item["minutes"], "points": [s["lead"], s["strategy"]], "tags": tags,
        "url": f"https://www.youtube.com/watch?v={item['youtube']}" if item["youtube"] else item["originalUrl"],
        "note": "",
        "audioNote": {"n": item["n"], "basis": s["basis"], "lead": s["lead"], "sections": s["sections"],
                      "strategy": s["strategy"], "cautions": s["cautions"], "checks": []},
        "youtube": item["youtube"], "originalUrl": item["originalUrl"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-json", type=Path, help="併入已整理好的 <n>.json，不呼叫轉錄與 Claude")
    args = ap.parse_args()

    html, episodes, categories = load_index()
    have = {e["n"] for e in episodes}
    new = sorted((it for it in feed_items() if it["n"] not in have), key=lambda it: it["n"])
    if not new:
        print("沒有新集數，最新為 EP%d" % max(have))
        return
    added = []
    for item in new:
        if args.from_json:
            f = args.from_json / f"{item['n']}.json"
            if not f.exists():
                continue
            s = json.loads(f.read_text(encoding="utf-8"))
        else:
            print(f"EP{item['n']} 轉錄中…", flush=True)
            transcript = transcribe(item)
            print(f"EP{item['n']} 整理中…", flush=True)
            s = summarize(item, transcript, episodes[0], categories)
        episodes.append(build_episode(item, s, categories))
        added.append(item["n"])
        save_index(html, episodes)  # 每集完成就存檔，中途失敗也保留已完成的集數
        html = INDEX.read_text(encoding="utf-8")
    print("新增：", ", ".join(f"EP{n}" for n in added) or "無")


if __name__ == "__main__":
    main()
