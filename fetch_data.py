"""
每日盤後資料抓取腳本
資料來源: TWSE OpenAPI, TAIFEX OpenAPI, Yahoo Finance
輸出: data/latest.json
"""
import json
import urllib.request
import datetime

def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode('utf-8'))

result = {
    "updated_at": datetime.datetime.now().isoformat(),
    "institutional": None,
    "institutional_error": None,
    "taifex": None,
    "taifex_raw_sample": None,
    "taifex_error": None,
    "top10": None,
    "top10_error": None,
    "vix": None,
    "vix_error": None,
    "us_indices": None,
    "us_indices_error": None,
}

# 1. 三大法人買賣超 (近10日, 全市場加總)
try:
    data = fetch_json("https://openapi.twse.com.tw/v1/fund/T86")
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

# 2. 台指期未平倉量 (三大法人)
try:
    data = fetch_json("https://openapi.taifex.com.tw/v1/OpenInterestOfTaifexFuturesAndOptionsFinancialIndicators")
    # 存一筆原始樣本方便除錯（之後可移除）
    if isinstance(data, list) and data:
        result["taifex_raw_sample"] = data[0]

    # 嘗試篩選台股期貨相關資料
    tx_rows = []
    for row in data:
        product = str(row.get("商品名稱", "") or row.get("契約名稱", "") or row.get("商品", ""))
        if "臺股期貨" in product or "台股期貨" in product or product.strip() == "TX":
            tx_rows.append(row)

    parsed = []
    for row in tx_rows:
        parsed.append({
            "identity": row.get("身份") or row.get("身分") or row.get("投資人類別") or row.get("資料日期"),
            "long_oi": row.get("多方未平倉口數") or row.get("多方未平倉契約數"),
            "short_oi": row.get("空方未平倉口數") or row.get("空方未平倉契約數"),
            "raw": row,
        })
    result["taifex"] = parsed
except Exception as e:
    result["taifex_error"] = str(e)

# 3. 個股買賣超前十大
try:
    data = fetch_json("https://openapi.twse.com.tw/v1/fund/TWT38U")
    top10 = []
    for row in data[:10]:
        code = row.get("證券代號") or row.get("股票代號") or ""
        name = row.get("證券名稱") or row.get("股票名稱") or ""
        amt = row.get("買賣超股數") or row.get("三大法人買賣超股數") or 0
        top10.append({
            "code": code,
            "name": name,
            "net_lots": round(float(amt) / 1000),
        })
    result["top10"] = top10
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
