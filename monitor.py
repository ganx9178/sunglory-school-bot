#!/usr/bin/env python3
"""
奕阳教育·学校动态监控 - GitHub Actions 云端版
触发: 每天 01:30 UTC (北京 09:30) + 07:00 UTC (北京 15:00)
搜索: Bing 网页（免费，无需 API Key）
推送: 飞书 Webhook (卡片格式)
"""
import json, os, sys, hashlib, base64, hmac, time, re, urllib.request, urllib.parse, html
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))
now_bj = datetime.now(BJ)
yesterday_bj = now_bj - timedelta(hours=24)
report_date = now_bj.strftime("%Y-%m-%d")

FEISHU_SECRET = os.environ.get("FEISHU_SECRET", "ogHzPD1BBWQfPDF26DBxLh")
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/1d2b3035-a565-4af2-bb16-55e7ec088d0f")

batch_label = "上午" if now_bj.hour < 14 else "下午"
MAX_SCHOOLS = 232 if batch_label == "上午" else None

EN_SPAM = {"gmail","roblox","pizza","minecraft","fortnite","steam","youtube","facebook",
           "twitter","instagram","tiktok","spotify","netflix","amazon","ebay","walmart",
           "uber","airbnb","google","apple","microsoft","samsung","nike","adidas"}

def gen_variants(name):
    variants = set()
    variants.add(name)
    clean = re.sub(r"\([^)]*\)", "", name).strip()
    if clean: variants.add(clean)
    clean2 = re.sub(r"（[^）]*）", "", name).strip()
    if clean2: variants.add(clean2)
    for prefix in ["杭州市", "宁波市", "浙江省"]:
        if name.startswith(prefix):
            variants.add(name[len(prefix):].strip())
    return list(variants)[:3]

def is_chinese(text, ratio=0.3):
    if not text: return False
    return len(re.findall(r"[\u4e00-\u9fff]", text)) / max(len(text), 1) >= ratio

def has_spam(text):
    return any(w in text.lower() for w in EN_SPAM)

def passes_time(text):
    if any(k in text for k in ["今天","今日","刚刚","分钟前","小时前","今晨","昨天","昨日"]):
        return True
    if any(k in text for k in ["回顾","总结","盘点","去年的","上学期的"]):
        return False
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return yesterday_bj <= dt <= now_bj
        except: pass
    return False

def search_bing_web(query, max_results=5):
    results = []
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://www.bing.com/search?q={encoded}&count={max_results}&cc=cn&setlang=zh-Hans"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        resp = urllib.request.urlopen(req, timeout=10)
        page_html = resp.read().decode("utf-8", errors="ignore")
        for m in re.finditer(r'<li class="b_algo"[^>]*>.*?<h2>.*?<a href="([^"]*)"[^>]*>(.*?)</a>.*?<p>(.*?)</p>', page_html, re.DOTALL):
            raw_title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            raw_desc = re.sub(r"<[^>]+>", "", m.group(3)).strip()
            title = html.unescape(raw_title)[:100]
            desc = html.unescape(raw_desc)[:200]
            if len(title) > 3 and is_chinese(title) and not has_spam(title):
                results.append({"title": title, "url": m.group(1), "snippet": desc})
                if len(results) >= max_results:
                    break
        if not results:
            for m in re.finditer(r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', page_html, re.DOTALL):
                raw_title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                title = html.unescape(raw_title)[:100]
                if len(title) > 3 and is_chinese(title) and not has_spam(title):
                    results.append({"title": title, "url": m.group(1), "snippet": ""})
                    if len(results) >= max_results:
                        break
    except Exception as e:
        print(f"[Bing] {query}: {e}")
    return results

def search_school(school):
    name = school["name"]
    core = name.split("(")[0].split("（")[0].strip()
    variants = gen_variants(name)
    school_results = {"招标": [], "科技赛事": [], "科技新闻": [], "人事变动": [], "公众号推文": []}
    new_count = 0

    queries = []
    for v in variants:
        queries.extend([
            (f"{v} 采购 招标", "招标"),
            (f"{v} 设备 采购", "招标"),
            (f"{v} 机器人 竞赛", "科技赛事"),
            (f"{v} 编程 比赛", "科技赛事"),
            (f"{v} 科技 活动", "科技新闻"),
            (f"{v} 人工智能 教育", "科技新闻"),
            (f"{v} 校长 任命", "人事变动"),
            (f"{v} 公众号 科技", "公众号推文"),
        ])

    seen = set()
    for query, cat in queries:
        items = search_bing_web(query, 5)
        for item in items:
            key = f"{core}:{item['title'][:30]}"
            if key in seen: continue
            seen.add(key)
            combined = item["title"] + " " + item.get("snippet", "")
            if not passes_time(combined): continue
            school_results[cat].append({**item, "pub_date": report_date, "school": core})
            new_count += 1
        time.sleep(0.3)

    return core, school_results, new_count

# ========== 主流程 ==========
print(f"[{now_bj.strftime('%Y-%m-%d %H:%M')}] {batch_label}批次启动")

schools_file = os.path.join(os.path.dirname(__file__), "监控学校清单_全量.json")
with open(schools_file, "r", encoding="utf-8") as f:
    all_schools = json.load(f)

if batch_label == "上午":
    batch_schools = all_schools[:MAX_SCHOOLS]
else:
    batch_schools = all_schools[MAX_SCHOOLS:]

print(f"[加载] {len(batch_schools)} 所学校")

results = {}
total_items = 0
schools_with_news = 0

for i, school in enumerate(batch_schools):
    try:
        core, data, count = search_school(school)
        if count > 0:
            results[core] = data
            total_items += count
            schools_with_news += 1
            print(f"  [{i+1}/{len(batch_schools)}] {core}: {count}条")
        elif (i+1) % 50 == 0:
            print(f"  进度: {i+1}/{len(batch_schools)}, {schools_with_news}所, {total_items}条")
    except Exception as e:
        print(f"  [错误] {school['name']}: {e}")

print(f"\n[完成] {schools_with_news}所学校有动态, 共{total_items}条")

# ========== 统计 ==========
cat_counts = {"招标": 0, "科技赛事": 0, "科技新闻": 0, "人事变动": 0, "公众号推文": 0}
for sd in results.values():
    for cat in cat_counts:
        cat_counts[cat] += len(sd.get(cat, []))

# ========== 飞书推送（卡片格式，与原始 run_morning_batch.py 一致） ==========
timestamp = str(int(time.time()))
string_to_sign = timestamp + "\n" + FEISHU_SECRET
hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
sign = base64.b64encode(hmac_code).decode("utf-8")

card_elements = []

# 顶部统计
summary = "**日期**: " + report_date + "（" + batch_label + "）"
summary += "\n\n**本批监控**: " + str(len(batch_schools)) + "所 | **24h更新**: " + str(schools_with_news) + "条 | **3个月更新**: " + str(total_items) + "条"
card_elements.append({"tag": "markdown", "content": summary})

# 近24h区块
if total_items > 0:
    card_elements.append({"tag": "markdown", "content": "**[24h] 近24小时更新（" + str(total_items) + "条）**"})
    for cat in ["招标", "科技赛事", "科技新闻", "人事变动", "公众号推文"]:
        cat_items = []
        for sn, sd in results.items():
            for item in sd.get(cat, []):
                school = sn
                title = item["title"]
                date = item.get("pub_date", "")
                url = item.get("url", "")
                tag = "(" + date + ")" if date else ""
                link = " [原文](" + url + ")" if url else ""
                cat_items.append("**" + school + "**" + tag + " " + title + link)
        if cat_items:
            detail_text = "\n".join(cat_items[:8])
            card_elements.append({"tag": "markdown", "content": "### " + cat + "（" + str(len(cat_items)) + "条）\n\n" + detail_text})
else:
    card_elements.append({"tag": "markdown", "content": "**[24h] 近24小时无学校更新信息**"})

msg = {
    "timestamp": timestamp,
    "sign": sign,
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": "奕阳教育·学校动态监控（" + batch_label + "批次）"},
            "template": "blue"
        },
        "elements": [
            *card_elements,
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看完整报告"},
                        "type": "primary",
                        "url": ""
                    }
                ]
            }
        ]
    }
}

data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
req = urllib.request.Request(FEISHU_WEBHOOK, data=data, headers={"Content-Type": "application/json"})
try:
    resp = urllib.request.urlopen(req, timeout=30)
    print(f"\n[飞书] {resp.read().decode()}")
except Exception as e:
    print(f"\n[飞书] 推送失败: {e}")

print(f"\n[完成] {batch_label}批次执行完毕！")
