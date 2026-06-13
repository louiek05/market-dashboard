"""
每日盤後資料抓取腳本
資料來源: TWSE OpenAPI, TAIFEX OpenAPI, Yahoo Finance
輸出: data/latest.json
"""
import json
import urllib.request
import datetime

import ssl

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as res:
        raw = res.read().decode('utf-8')
        if not raw.strip():
            raise ValueError(f"Empty response body from {url}")
        return json.loads(raw)

result = {
    "updated_at": datetime.datetime.now().isoformat(),
    "institutional": None,
    "institutional_error": None,
    "taifex": None,
    "taifex_raw_sample": None,
    "taifex_error": None,
    "top10": None,
    "top10_error": None,
    "top10_sell": None,
    "top10_sell_error": None,
    "vix": None,
    "vix_error": None,
    "us_indices": None,
    "us_indices_error": None,
}

# 1. 三大法人買賣金額統計 (近10日, 單位:億元)
try:
    by_date = {}
    today = datetime.date.today()
    days_checked = 0
    d = today
    while len(by_date) < 10 and days_checked < 20:
        date_str = d.strftime("%Y%m%d")
        try:
            day_data = fetch_json(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={date_str}&type=day&response=json")
            if day_data.get("stat") == "OK" and day_data.get("data"):
                fields = day_data["fields"]
                entry = {"foreign": 0, "trust": 0, "dealer": 0}
                for row in day_data["data"]:
                    rd = dict(zip(fields, row))
                    name = rd.get("單位名稱", "")
                    net_str = str(rd.get("買賣差額", "0")).replace(",", "").strip()
                    try:
                        net = float(net_str) if net_str else 0
                    except ValueError:
                        net = 0
                    # 億元 = 元 / 1e8
                    net_yi = net / 1e8
                    if "自營商" in name and "合計" not in name:
                        entry["dealer"] += net_yi
                    elif "投信" in name:
                        entry["trust"] += net_yi
                    elif "外資" in name or "陸資" in name:
                        entry["foreign"] += net_yi
                if any(entry.values()):
                    by_date[date_str] = entry
        except Exception:
            pass
        d -= datetime.timedelta(days=1)
        days_checked += 1

    dates = sorted(by_date.keys(), reverse=True)[:10]
    result["institutional"] = [
        {
            "date": dt,
            "foreign_amt": round(by_date[dt]["foreign"], 1),
            "trust_amt": round(by_date[dt]["trust"], 1),
            "dealer_amt": round(by_date[dt]["dealer"], 1),
        }
        for dt in dates
    ]
except Exception as e:
    result["institutional_error"] = str(e)

# 2. 台指期未平倉量淨額 (外資/投信/自營商, 最近一日)
try:
    import re

    req = urllib.request.Request(
        "https://www.wantgoo.com/futures/institutional-investors/net-open-interest",
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.wantgoo.com/futures',
            'Cache-Control': 'no-cache',
        }
    )
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as res:
        page = res.read().decode('utf-8')

    # 找出表格內容 (使用正則抓出第一個資料列：日期 + 8組 數字/增減)
    # 格式範例: 2026/06/04 -69,476 -2,704 -67,975 -1,885 51,858 554 3,219 745 ...
    pattern = re.compile(
        r'(\d{4}/\d{2}/\d{2})\s*</td>\s*'
        r'<td[^>]*>\s*(-?[\d,]+)\s*</td>\s*<td[^>]*>\s*(-?[\d,]+)\s*</td>\s*'  # 外資
        r'<td[^>]*>\s*(-?[\d,]+)\s*</td>\s*<td[^>]*>\s*(-?[\d,]+)\s*</td>\s*'  # 小外資
        r'<td[^>]*>\s*(-?[\d,]+)\s*</td>\s*<td[^>]*>\s*(-?[\d,]+)\s*</td>\s*'  # 投信
        r'<td[^>]*>\s*(-?[\d,]+)\s*</td>\s*<td[^>]*>\s*(-?[\d,]+)\s*</td>'     # 自營商
    )
    m = pattern.search(page)

    if m:
        def to_int(s):
            return int(s.replace(",", ""))

        date_str, foreign, foreign_chg, sfor, sfor_chg, trust, trust_chg, dealer, dealer_chg = m.groups()
        result["taifex"] = {
            "date": date_str,
            "foreign_net": to_int(foreign),
            "foreign_chg": to_int(foreign_chg),
            "trust_net": to_int(trust),
            "trust_chg": to_int(trust_chg),
            "dealer_net": to_int(dealer),
            "dealer_chg": to_int(dealer_chg),
        }
    else:
        result["taifex_error"] = "找不到資料表格"
        # 存一段樣本方便除錯
        idx = page.find("2026/")
        if idx == -1:
            idx = page.find("法人未平倉")
        result["taifex_raw_sample"] = page[max(0, idx-200):idx+800] if idx != -1 else page[:800]
except Exception as e:
    result["taifex_error"] = str(e)

# 3. 個股買賣超前十大 (買超前10 + 賣超前10)
try:
    top_data = None
    today = datetime.date.today()
    d = today
    for _ in range(10):
        date_str = d.strftime("%Y%m%d")
        try:
            day_data = fetch_json(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json")
            if day_data.get("stat") == "OK" and day_data.get("data"):
                fields = day_data["fields"]
                rows = []
                for row in day_data["data"]:
                    rd = dict(zip(fields, row))
                    code = rd.get("證券代號", "")
                    name = rd.get("證券名稱", "")
                    net_str = rd.get("三大法人買賣超股數", "0")
                    try:
                        net = float(str(net_str).replace(",", "").strip() or 0)
                    except ValueError:
                        net = 0
                    rows.append({"code": code, "name": name, "net": net})
                top_data = rows
                break
        except Exception:
            pass
        d -= datetime.timedelta(days=1)

    if top_data is None:
        raise ValueError("無法取得買賣超資料")

    sorted_rows = sorted(top_data, key=lambda r: r["net"], reverse=True)

    try:
        top10_buy = [
            {"code": r["code"], "name": r["name"], "net_lots": round(r["net"] / 1000)}
            for r in sorted_rows[:10]
        ]
        result["top10"] = top10_buy
    except Exception as e:
        result["top10_error"] = str(e) or "未知錯誤(買超)"

    try:
        top10_sell = [
            {"code": r["code"], "name": r["name"], "net_lots": round(r["net"] / 1000)}
            for r in sorted_rows[-10:][::-1]
        ]
        result["top10_sell"] = top10_sell
    except Exception as e:
        result["top10_sell_error"] = str(e) or "未知錯誤(賣超)"

except Exception as e:
    result["top10_error"] = str(e)

# 4. VIX
try:
    data = fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d")
    res0 = data["chart"]["result"][0]
    closes = [c for c in res0["indicators"]["quote"][0]["close"] if c is not None]
    last = closes[-1]
    prev = closes[-2]
    result["vix"] = {
        "close": round(last, 2),
        "change": round(last - prev, 2),
        "change_pct": round((last - prev) / prev * 100, 2),
    }
except Exception as e:
    result["vix_error"] = str(e)

# 5. 美股收盤
try:
    symbols = {"^IXIC": "那斯達克", "^SOX": "費半指數", "^GSPC": "S&P 500", "^DJI": "道瓊"}
    us_data = []
    for sym, label in symbols.items():
        try:
            data = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d")
            res0 = data["chart"]["result"][0]
            closes = [c for c in res0["indicators"]["quote"][0]["close"] if c is not None]
            last = closes[-1]
            prev = closes[-2]
            us_data.append({
                "label": label,
                "close": round(last, 2),
                "change_pct": round((last - prev) / prev * 100, 2),
            })
        except Exception as e:
            us_data.append({"label": label, "error": str(e)})
    result["us_indices"] = us_data
except Exception as e:
    result["us_indices_error"] = str(e)

with open("data/latest.json", "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("Done. Wrote data/latest.json")
print(json.dumps({k: v for k, v in result.items() if not k.endswith("_raw_sample")}, ensure_ascii=False, indent=2)[:3000])
