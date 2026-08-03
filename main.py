"""
奕阳教育 - 客户资料数据库机器人 (V6)
新增：政府数据源（教育厅/政采网/公共资源交易网/教育局）
"""
import os, json, urllib.request, urllib.parse, time, re, html
from fastapi import FastAPI, Request
from typing import List, Dict

app = FastAPI()

APP_ID = os.getenv("APP_ID", "")
APP_SECRET = os.getenv("APP_SECRET", "")
BOT_NAME = os.getenv("BOT_NAME", "客户数据库")

_processed_msgs = set()
_MAX_CACHE = 500


def get_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read().decode())
    if result.get("code") == 0:
        return result["tenant_access_token"]
    raise Exception("Token失败: %s" % result)


def send_text(chat_id: str, text: str, token: str = None):
    """发送纯文本消息"""
    if not token:
        token = get_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"
    # 飞书API: content 必须是 JSON 字符串
    # content_str 是 '{"text": "..."}' 这样的字符串
    content_str = json.dumps({"text": text}, ensure_ascii=False)
    # body 是 '{"receive_id": "xxx", "msg_type": "text", "content": "{\"text\": \"...\"}"}'
    body = json.dumps({"receive_id": chat_id, "msg_type": "text", "content": content_str}, ensure_ascii=False)
    data = body.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode())
        if result.get("code") != 0:
            print("[发送失败] code=%d msg=%s" % (result.get("code"), result.get("msg", "")))
        else:
            print("[发送成功]")
        return result
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print("[发送HTTP错误] %d %s" % (e.code, err_body[:300]))
    except Exception as e:
        print("[发送异常] %s" % e)


def is_chinese_content(text):
    """判断是否为中文内容，过滤英文垃圾结果"""
    if not text:
        return False
    # 计算中文字符比例
    cn_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return cn_count >= 3  # 至少3个中文字符才算中文内容

def search_bing(query: str, max_results=10) -> List[dict]:
    """Bing搜索"""
    results = []
    try:
        encoded = urllib.parse.quote(query)
        url = "https://www.bing.com/search?q=%s&count=%d&cc=cn" % (encoded, max_results)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        })
        resp = urllib.request.urlopen(req, timeout=8)
        page_html = resp.read().decode("utf-8", errors="ignore")
        for m in re.finditer(r'<li class="b_algo"[^>]*>.*?<h2>.*?<a href="([^"]*)"[^>]*>(.*?)</a>.*?<p>(.*?)</p>', page_html, re.DOTALL):
            raw_title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            raw_desc = re.sub(r'<[^>]+>', '', m.group(3)).strip()
            title = html.unescape(raw_title)[:100]
            desc = html.unescape(raw_desc)[:150]
            # 过滤：必须是中文内容
            if len(title) > 3 and is_chinese_content(title):
                results.append({"title": title, "url": m.group(1), "desc": desc})
                if len(results) >= max_results:
                    break
        if not results:
            for m in re.finditer(r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', page_html, re.DOTALL):
                raw_title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                title = html.unescape(raw_title)[:100]
                if len(title) > 3 and is_chinese_content(title):
                    results.append({"title": title, "url": m.group(1), "desc": ""})
    except Exception as e:
        print("[Bing错误] %s: %s" % (query, e))
    return results


def search_wechat(query: str, max_results=6) -> List[dict]:
    """搜狗微信搜索"""
    results = []
    try:
        encoded = urllib.parse.quote(query)
        url = "https://weixin.sogou.com/weixin?type=2&query=%s&ie=utf8" % encoded
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        })
        resp = urllib.request.urlopen(req, timeout=8)
        page = resp.read().decode("utf-8", errors="ignore")
        for m in re.finditer(r'<div class="txt-box".*?<h3.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<p class="txt-info"[^>]*>(.*?)</p>', page, re.DOTALL):
            raw_title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            raw_desc = re.sub(r'<[^>]+>', '', m.group(3)).strip()
            title = html.unescape(raw_title)[:100]
            desc = html.unescape(raw_desc)[:150]
            if len(title) > 3:
                results.append({"title": title, "url": m.group(1), "desc": desc})
                if len(results) >= max_results:
                    break
    except Exception as e:
        print("[微信搜索错误] %s: %s" % (query, e))
    return results


def search_gov_site(domain: str, keywords: str, max_results=6) -> List[dict]:
    """通过Bing的site:操作符搜索政府网站"""
    results = []
    try:
        query = "site:%s %s" % (domain, keywords)
        encoded = urllib.parse.quote(query)
        url = "https://www.bing.com/search?q=%s&count=%d&cc=cn" % (encoded, max_results)
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        })
        resp = urllib.request.urlopen(req, timeout=6)
        page = resp.read().decode("utf-8", errors="ignore")
        for m in re.finditer(r'<li class="b_algo"[^>]*>.*?<h2>.*?<a href="([^"]*)"[^>]*>(.*?)</a>.*?<p>(.*?)</p>', page, re.DOTALL):
            raw_title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            raw_desc = re.sub(r'<[^>]+>', '', m.group(3)).strip()
            title = html.unescape(raw_title)[:100]
            desc = html.unescape(raw_desc)[:150]
            if len(title) > 3:
                results.append({"title": title, "url": m.group(1), "desc": desc, "source": domain})
                if len(results) >= max_results:
                    break
        if not results:
            for m in re.finditer(r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', page, re.DOTALL):
                raw_title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                title = html.unescape(raw_title)[:100]
                if len(title) > 3:
                    results.append({"title": title, "url": m.group(1), "desc": "", "source": domain})
    except Exception as e:
        print("[Gov搜索错误] %s %s: %s" % (domain, keywords, e))
    return results


def name_variants(name: str) -> List[str]:
    v = [name]
    for pfx in ["杭州市", "宁波市", "丽水市", "台州市", "温州市", "嘉兴市", "湖州市",
                 "绍兴市", "金华市", "衢州市", "舟山市", "浙江省",
                 "滨江区", "西湖区", "拱墅区", "上城区", "临平区", "钱塘区", "萧山区",
                 "余杭区", "富阳区", "鄞州区", "海曙区", "江北区", "北仑区", "镇海区",
                 "奉化区", "象山县", "宁海县", "慈溪市", "余姚市"]:
        if name.startswith(pfx):
            short = name[len(pfx):].strip()
            if short and short not in v:
                v.append(short)
    if len(name) < 6:
        for pfx in ["杭州市", "宁波市", "浙江省"]:
            full = pfx + name
            if full not in v:
                v.append(full)
    return v[:3]


def detect_city(school_name: str) -> str:
    """检测学校所在城市"""
    for city in ["杭州", "宁波", "丽水", "台州", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山"]:
        if city in school_name:
            return city
    return ""


def classify_item(title: str, desc: str) -> str:
    text = (title + " " + desc).lower()
    if any(k in text for k in ["招聘", "编制", "教师招考", "事业编", "招聘事业", "自主考核"]):
        return "job"
    if any(k in text for k in ["校长", "副校长", "书记", "任命", "履历", "新任校长", "现任校长"]):
        return "leader"
    if any(k in text for k in ["机器人", "竞赛", "编程", "创客", "获奖", "奥赛", "信息学", "科技创新", "创客教育"]):
        return "tech"
    if any(k in text for k in ["招标", "采购", "中标", "意向", "设备", "信息化"]):
        return "bid"
    if any(k in text for k in ["课后服务", "托管", "晚托", "四点半"]):
        return "course"
    if any(k in text for k in ["特色", "科技", "stem", "人工智能", "信息化", "科创实验室", "创客室"]):
        return "feature"
    if "微信" in text or "公众号" in text:
        return "wechat"
    return "other"


def search_government_sources(school_name: str, seen_urls: set, data: dict) -> None:
    """搜索政府网站数据源（教育厅、政采网、公共资源交易网、各地教育局）"""
    city = detect_city(school_name)
    
    # 城市 -> 教育局域名映射
    city_edu_domains = {
        "杭州": ["jyj.hangzhou.gov.cn"],
        "宁波": ["jyj.ningbo.gov.cn", "jyb.ningbo.gov.cn"],
        "丽水": ["jyj.lishui.gov.cn"],
        "台州": ["jyj.taizhou.gov.cn"],
        "温州": ["jyj.wenzhou.gov.cn"],
        "嘉兴": ["jyj.jiaxing.gov.cn"],
        "湖州": ["jyj.huzhou.gov.cn"],
        "绍兴": ["jyj.shaoxing.gov.cn"],
        "金华": ["jyj.jinhua.gov.cn"],
        "衢州": ["jyj.quzhou.gov.cn"],
        "舟山": ["jyj.zhoushan.gov.cn"],
    }
    
    # 省级采购/交易数据源
    provincial_sources = [
        ("zfcg.czt.zj.gov.cn", "学校 科技 机器人 创客"),
        ("zfcg.czt.zj.gov.cn", "学校 采购 中标"),
        ("ggzy.zj.gov.cn", "学校 科技 科创"),
        ("ggzy.zj.gov.cn", "学校 采购 招标"),
        ("ccgp.gov.cn", "浙江 学校 科技"),
        ("ccgp.gov.cn", "浙江 学校 采购"),
        ("jyt.zj.gov.cn", "科技教育 人工智能 机器人"),
        ("zjjyb.cn", "科技 科创 机器人"),
    ]
    
    # 1. 省级搜索
    for domain, kw in provincial_sources:
        items = search_gov_site(domain, kw, max_results=4)
        for item in items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                # 过滤：标题/描述必须和学校名相关
                title = item.get("title", "") + " " + item.get("desc", "")
                # 放宽匹配：只要和学校或科技相关都保留
                cat = classify_item(item["title"], item.get("desc", ""))
                if cat == "job":
                    continue
                if cat in data:
                    data[cat].append(item)
                elif "采购" in item["title"] or "招标" in item["title"] or "中标" in item["title"]:
                    data["bid"].append(item)
                else:
                    data["tech"].append(item)
    
    # 2. 地方教育局搜索
    if city and city in city_edu_domains:
        for domain in city_edu_domains[city]:
            edu_items = search_gov_site(domain, "%s 科技 机器人 竞赛" % school_name, max_results=5)
            for item in edu_items:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    cat = classify_item(item["title"], item.get("desc", ""))
                    if cat == "job":
                        continue
                    if cat in data:
                        data[cat].append(item)
                    else:
                        data["tech"].append(item)
            
            edu_bid = search_gov_site(domain, "%s 采购" % school_name, max_results=3)
            for item in edu_bid:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    cat = classify_item(item["title"], item.get("desc", ""))
                    if cat == "job":
                        continue
                    data["bid"].append(item)


def quick_search(school_name: str) -> Dict:
    variants = name_variants(school_name)
    seen_urls = set()
    data = {"leader": [], "tech": [], "bid": [], "course": [], "feature": [], "wechat": [], "general": []}

    queries = []
    for v in variants[:3]:
        queries.append("%s 校长 副校长" % v)
        queries.append("%s 机器人 竞赛" % v)
        queries.append("%s 科技创新大赛" % v)
        queries.append("%s 信息学奥赛" % v)
        queries.append("%s 创客 编程 获奖" % v)
        queries.append("%s 电子制作 锦标赛" % v)
        queries.append("%s 科技课程" % v)
        queries.append("%s 创客教育" % v)
        queries.append("%s 人工智能教育" % v)
        queries.append("%s STEM课程" % v)
        queries.append("%s 科创实验室" % v)
        queries.append("%s 采购 招标" % v)
        queries.append("%s 信息化 设备" % v)
        queries.append("%s 课后服务 科技" % v)
        queries.append("%s 社团活动" % v)
        queries.append("%s 新闻 活动" % v)

    wechat_queries = []
    for v in variants[:2]:
        wechat_queries.append("%s 科技" % v)
        wechat_queries.append("%s 机器人" % v)
        wechat_queries.append("%s 竞赛" % v)
        wechat_queries.append("%s 科创" % v)

    start = time.time()

    # 1. Bing搜索
    for q in queries:
        if time.time() - start > 12:
            break
        items = search_bing(q, max_results=8)
        for item in items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                cat = classify_item(item["title"], item["desc"])
                if cat == "job":
                    continue
                if cat in data:
                    data[cat].append(item)
                else:
                    data["general"].append(item)

    # 2. 政府网站搜索（教育厅/政采网/公共资源交易网/地方教育局）
    if time.time() - start < 22:
        try:
            search_government_sources(school_name, seen_urls, data)
            print("[政府数据源] 搜索完成")
        except Exception as e:
            print("[政府数据源错误] %s" % e)

    # 3. 微信搜索
    for q in wechat_queries:
        if time.time() - start > 28:
            break
        items = search_wechat(q, max_results=5)
        for item in items:
            if item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                cat = classify_item(item["title"], item["desc"])
                if cat == "job":
                    continue
                if cat == "wechat" or cat == "other":
                    data["wechat"].append(item)
                elif cat in data:
                    data[cat].append(item)

    # 4. 科技类补充搜索
    if len(data["tech"]) < 3:
        for v in variants[:2]:
            if time.time() - start > 30:
                break
            q = "%s 科技" % v
            items = search_bing(q, max_results=10)
            for item in items:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    cat = classify_item(item["title"], item["desc"])
                    if cat == "job":
                        continue
                    if cat == "tech" or cat == "feature":
                        data[cat].append(item)

    total = sum(len(v) for v in data.values())
    print("[搜索完成] %s: %d条, 耗时%.1fs" % (school_name, total, time.time() - start))

    analysis = build_analysis(school_name, data)
    data["analysis"] = analysis
    data["total"] = total
    return data


def clean_url(url):
    """清理URL：解码HTML实体，返回完整URL（飞书纯文本自动识别http/https开头的URL）"""
    if not url:
        return ""
    try:
        url = html.unescape(url)  # 解码 &amp; 等HTML实体
        # 确保以 http 开头
        if url.startswith("http"):
            return url
        return ""
    except Exception:
        return ""

def fmt_item(item: dict) -> str:
    """格式化单条信息，标题一行，链接独立一行"""
    url = item.get("url", "")
    title = item["title"]
    clean = clean_url(url)
    if clean:
        # 飞书纯文本消息：链接独立一行才会被识别为可点击
        return "- %s\n  %s" % (title, clean)
    return "- %s" % title


def build_analysis(name, data):
    lines = []
    tags = []
    if data["tech"]: tags.append("机器人/竞赛活跃")
    if data["bid"]: tags.append("近期有采购需求")
    if data["feature"]: tags.append("科技特色明显")
    if data["course"]: tags.append("课后服务丰富")
    if data["wechat"]: tags.append("公众号运营活跃")
    tag_str = "、".join(tags) if tags else "公开信息有限"
    lines.append("学校特色: %s" % tag_str)
    lines.append("")

    if data["leader"]:
        lt = " ".join(x['title'] + " " + x.get('desc','') for x in data["leader"])
        if any(k in lt for k in ["科技", "信息", "AI", "编程", "创新"]):
            lines.append("领导班子: 有科技/信息化背景")
        else:
            lines.append("领导班子: 公开信息有限，建议以提升学校品牌角度切入")
    else:
        lines.append("领导班子: 未检索到公开信息")
    lines.append("")

    needs = []
    if data["bid"]: needs.append("硬件采购/信息化升级")
    if data["tech"]: needs.append("竞赛成绩/社团活动")
    if data["feature"]: needs.append("特色课程建设")
    if data["course"]: needs.append("课后服务内容")
    need_str = "、".join(needs) if needs else "暂未明确"
    lines.append("可能需求: %s" % need_str)
    lines.append("")

    lines.append("聊天建议:")
    if data["tech"]:
        lines.append("- 聊竞赛成绩和机器人项目，对方有荣誉感")
    if data["bid"]:
        lines.append("- 聊采购参数对标，直接给配置清单")
    if data["feature"]:
        lines.append("- 聊特色课程建设，提供完整方案")
    if data["course"]:
        lines.append("- 聊课后服务科技课程，减轻老师负担")
    if not any([data["tech"], data["bid"], data["feature"], data["course"]]):
        lines.append("- 先聊教育理念，提供免费公开课建立信任")

    return "\n".join(lines)


@app.get("/")
def root():
    return {"msg": "Bot V6 running"}


@app.post("/webhook")
async def handle_event(request: Request):
    try:
        body = await request.json()
        if "challenge" in body:
            return {"challenge": body["challenge"]}

        event = body.get("event", {})
        msg = event.get("message", {})
        msg_id = msg.get("message_id", "")
        chat_id = msg.get("chat_id")
        content = json.loads(msg.get("content", "{}"))
        text = content.get("text", "")
        mentions = msg.get("mentions", [])

        if msg_id in _processed_msgs:
            print("[去重] 忽略: %s" % msg_id)
            return {"code": 0}
        _processed_msgs.add(msg_id)
        if len(_processed_msgs) > _MAX_CACHE:
            _processed_msgs.clear()

        is_mention = False
        school_name = text
        for m in mentions:
            m_name = m.get("name", "")
            if m_name == BOT_NAME or "客户" in m_name or "数据库" in m_name:
                is_mention = True
                school_name = text.replace(m.get("key", ""), "").strip()
                break

        if not is_mention:
            return {"code": 0}
        if not school_name or len(school_name) < 2:
            send_text(chat_id, "请告诉我学校名称，例如：@%s 浦沿小学" % BOT_NAME)
            return {"code": 0}

        print("[查询] %s" % school_name)
        send_text(chat_id, "正在查询 %s ，请稍候..." % school_name)

        token = get_token()
        data = quick_search(school_name)

        parts = []
        parts.append("【%s】资料查询" % school_name)
        parts.append("共检索 %d 条信息" % data['total'])
        parts.append("")

        if data["leader"]:
            parts.append("=== 领导班子 ===")
            for x in data["leader"][:5]:
                parts.append(fmt_item(x))
            parts.append("")

        if data["tech"]:
            parts.append("=== 科技赛事/竞赛 ===")
            for x in data["tech"][:6]:
                parts.append(fmt_item(x))
            parts.append("")

        if data["feature"]:
            parts.append("=== 科技课程/学校特色 ===")
            for x in data["feature"][:6]:
                parts.append(fmt_item(x))
            parts.append("")

        if data["bid"]:
            parts.append("=== 采购招标 ===")
            for x in data["bid"][:5]:
                parts.append(fmt_item(x))
            parts.append("")

        if data["course"]:
            parts.append("=== 课后服务 ===")
            for x in data["course"][:5]:
                parts.append(fmt_item(x))
            parts.append("")

        if data["wechat"]:
            parts.append("=== 微信公众号 (%d条) ===" % len(data['wechat']))
            for x in data["wechat"][:8]:
                parts.append(fmt_item(x))
            parts.append("")

        parts.append("=== AI商业洞察 ===")
        parts.append(data["analysis"])

        full_text = "\n".join(parts)
        if len(full_text) > 18000:
            full_text = full_text[:18000] + "\n\n...（内容过长已截断）"

        send_text(chat_id, full_text, token)
        print("[完成] %s" % school_name)
        return {"code": 0}

    except Exception as e:
        print("[Error] %s" % e)
        import traceback; traceback.print_exc()
        return {"code": 1}
