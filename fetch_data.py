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

# 1. 三大法人買賣超 (近10日, 全市場加總)
try:
    try:
        data = fetch_json("https://openapi.twse.com.tw/v1/fund/T86")
    except Exception:
        # Fallback: 用 www.twse.com.tw 逐日查詢最近10個交易日
        data = []
        today = datetime.date.today()
        days_checked = 0
        d = today
        while len(set(r["日期"] for r in data)) < 10 and days_checked < 20:
            date_str = d.strftime("%Y%m%d")
            try:
                day_data = fetch_json(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALLBUT0999&response=json")
                if day_data.get("stat") == "OK" and day_data.get("data"):
                    fields = day_data["fields"]
                    for row in day_data["data"]:
                        rowdict = dict(zip(fields, row))
                        rowdict["日期"] = date_str

                        def num(key_options, rd=rowdict):
                            for k in key_options:
                                if k in rd:
                                    v = str(rd[k]).replace(",", "").strip()
                                    return float(v) if v else 0
                            return 0

                        rowdict["外陸資買賣超股數(不含外資自營商)"] = num(["外陸資買賣超股數(不含外資自營商)", "外資及陸資(不含外資自營商)-買賣超股數"])
                        rowdict["外資自營商買賣超股數"] = num(["外資自營商買賣超股數", "外資自營商-買賣超股數"])
                        rowdict["投信買賣超股數"] = num(["投信買賣超股數", "投信-買賣超股數"])
                        rowdict["自營商買賣超股數(自行買賣)"] = num(["自營商買賣超股數(自行買賣)", "自營商(自行買賣)-買賣超股數"])
                        rowdict["自營商買賣超股數(避險)"] = num(["自營商買賣超股數(避險)", "自營商(避險)-買賣超股數"])
                        data.append(rowdict)
            except Exception:
                pass
            d -= datetime.timedelta(days=1)
            days_checked += 1

    by_date = {}
    for row in data:
        d = row.get("日期")
        if not d:
            continue
        if d not in by_date:
            by_date[d] = {"foreign": 0, "trust": 0, "dealer": 0}
        # 外資及陸資 + 外資自營商
        foreign = float(row.get("外陸資買賣超股數(不含外資自營商)", 0) or 0) + \
                  float(row.get("外資自營商買賣超股數", 0) or 0)
        trust = float(row.get("投信買賣超股數", 0) or 0)
        dealer = float(row.get("自營商買賣超股數(自行買賣)", 0) or 0) + \
                 float(row.get("自營商買賣超股數(避險)", 0) or 0)
        by_date[d]["foreign"] += foreign
        by_date[d]["trust"] += trust
        by_date[d]["dealer"] += dealer

    dates = sorted(by_date.keys(), reverse=True)[:10]
    result["institutional"] = [
        {
            "date": d,
            "foreign_lots": round(by_date[d]["foreign"] / 1000),
            "trust_lots": round(by_date[d]["trust"] / 1000),
            "dealer_lots": round(by_date[d]["dealer"] / 1000),
        }
        for d in dates
    ]
except Exception as e:
    result["institutional_error"] = str(e)

# 2. 台指期未平倉量 (三大法人，依日期，區分各期貨契約)
try:
    import csv
    import io

    today = datetime.date.today()
    parsed = None
    raw_text_sample = None

    for back in range(0, 10):
        d = today - datetime.timedelta(days=back)
        date_str = d.strftime("%Y/%m/%d")
        post_data = f"queryType=2&marketCode=0&dateaddcnt=&commodity_id=TXF&queryDate={date_str}".encode()
        req = urllib.request.Request(
            "https://www.taifex.com.tw/cht/3/futContractsDateDown",
            data=post_data,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
                'Content-Type': 'application/x-www-form-urlencoded',
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=20, context=_CTX) as res:
                raw = res.read().decode('utf-8-sig')
            if not raw.strip():
                continue
            raw_text_sample = raw[:1000]
            reader = csv.reader(io.StringIO(raw))
            rows = [r for r in reader if r and len(r) > 1]
            if len(rows) < 2:
                continue

            header = rows[0]
            parsed = []
            for row in rows[1:]:
                if len(row) < len(header):
                    continue
                rowdict = dict(zip(header, row))
                product = rowdict.get("商品名稱", "")
                if "臺股期貨" not in product and "TXF" not in product and "臺指期貨" not in product:
                    continue
                identity = rowdict.get("身份別", rowdict.get("身份", ""))

                def numval(key, rd=rowdict):
                    v = str(rd.get(key, "0")).replace(",", "").strip()
                    try:
                        return int(v)
                    except ValueError:
                        return 0

                parsed.append({
                    "identity": identity,
                    "long_oi": numval("未平倉契約數多方") or numval("未平倉多方") or numval("多空淨額未平倉契約數多方"),
                    "short_oi": numval("未平倉契約數空方") or numval("未平倉空方") or numval("多空淨額未平倉契約數空方"),
                    "raw": rowdict,
                })
            if parsed:
                break
        except Exception:
            continue

    if parsed:
        result["taifex"] = parsed
    else:
        result["taifex_error"] = "查無台股期貨資料"
        if raw_text_sample:
            result["taifex_raw_sample"] = raw_text_sample
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
