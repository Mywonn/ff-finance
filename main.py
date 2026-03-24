from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import asyncio
import math
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Future Flow Finance API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 🔑 API Keys
# ==========================================
AV_KEY = "JHQ7GEG4UI6WYC6L"  # Alpha Vantage
AV_BASE = "https://www.alphavantage.co/query"

# ==========================================
# 🗄️ 内存缓存
# ==========================================
_cache = {}

def get_cache(key: str, ttl: int):
    if key in _cache:
        if time.time() - _cache[key]["ts"] < ttl:
            return _cache[key]["data"]
    return None

def set_cache(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}

# ==========================================
# 🌍 接口 1：宏观大盘
# ==========================================

async def fetch_av(client: httpx.AsyncClient, params: dict) -> dict:
    """通用 Alpha Vantage 请求，带错误处理"""
    try:
        params["apikey"] = AV_KEY
        r = await client.get(AV_BASE, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"AV request failed: {params} | {e}")
        return {}

async def fetch_coingecko_btc(client: httpx.AsyncClient) -> dict:
    """BTC 用 CoinGecko，完全免费无需 key"""
    try:
        r = await client.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": "30", "interval": "daily"},
            timeout=10
        )
        r.raise_for_status()
        data = r.json()
        prices = [p[1] for p in data.get("prices", [])]
        if len(prices) < 2:
            return {}
        curr = prices[-1]
        prev = prices[0]
        sparkline = prices[-10:] if len(prices) >= 10 else prices
        return {
            "current": round(curr, 2),
            "change_pct": round(((curr - prev) / prev) * 100, 2),
            "sparkline": [round(p, 2) for p in sparkline]
        }
    except Exception as e:
        logger.warning(f"CoinGecko BTC failed: {e}")
        return {}

def parse_av_daily(data: dict, key_field: str = "4. close") -> dict:
    """解析 Alpha Vantage TIME_SERIES_DAILY 或 FX_DAILY"""
    # 兼容多种返回格式的 key
    ts = (data.get("Time Series (Daily)")
          or data.get("Time Series FX (Daily)")
          or data.get("Weekly Time Series")
          or {})
    if not ts:
        return {}
    dates = sorted(ts.keys(), reverse=True)
    if len(dates) < 2:
        return {}
    # 取最近 30 天
    recent = dates[:30]
    closes = []
    for d in reversed(recent):
        try:
            closes.append(float(ts[d][key_field]))
        except:
            pass
    if len(closes) < 2:
        return {}
    curr = closes[-1]
    prev = closes[0]
    sparkline = closes[-10:] if len(closes) >= 10 else closes
    return {
        "current": round(curr, 2),
        "change_pct": round(((curr - prev) / prev) * 100, 2),
        "sparkline": [round(p, 2) for p in sparkline]
    }

@app.get("/api/macro")
async def get_macro():
    cached = get_cache("macro", 600)  # 10分钟缓存
    if cached:
        logger.info("macro: cache hit")
        return cached

    logger.info("macro: fetching fresh data")

    async with httpx.AsyncClient() as client:
        # 并发拉取所有数据，大幅缩短等待时间
        tasks = {
            "spx":   fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "SPY",    "outputsize": "compact"}),
            "ndx":   fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "QQQ",    "outputsize": "compact"}),
            "vix":   fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "VIXY",   "outputsize": "compact"}),
            "gold":  fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "GLD",    "outputsize": "compact"}),
            "copper":fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "CPER",   "outputsize": "compact"}),
            "us10y": fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "IEF",    "outputsize": "compact"}),
            "dxy":   fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "UUP",    "outputsize": "compact"}),
            "btc":   fetch_coingecko_btc(client),
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        raw = dict(zip(tasks.keys(), results))

    assets = {}
    for key, data in raw.items():
        if isinstance(data, Exception) or not data:
            continue
        if key == "btc":
            if data:
                assets[key] = data
        else:
            parsed = parse_av_daily(data)
            if parsed:
                assets[key] = parsed

    # 导师点评（基于 IEF 债券 ETF 变化判断利率方向）
    mentor_insight = {}
    if "us10y" in assets:
        chg = assets["us10y"]["change_pct"]
        if chg > 0.5:
            mentor_insight = {"type": "risk", "title": "紧缩风暴",
                "content": f"债券价格下跌 {chg:.2f}%，意味着市场利率预期上升。风险资产承压，建议多看少动。"}
        elif chg < -0.5:
            mentor_insight = {"type": "opportunity", "title": "宽松暖风",
                "content": f"债券价格上涨 {abs(chg):.2f}%，利率预期回落，流动性边际改善。关注科技股与加密货币低吸机会。"}
        else:
            mentor_insight = {"type": "neutral", "title": "震荡整理",
                "content": "宏观因子波动不大，市场缺乏明确方向指引。建议轻指数，重个股逻辑。"}

    result = {"mentor_insight": mentor_insight, "assets": assets}
    set_cache("macro", result)
    return result


# ==========================================
# 🎯 接口 2：个股深度透视
# ==========================================

@app.get("/api/stock/{ticker}")
async def get_stock(ticker: str):
    t = ticker.upper()
    cached = get_cache(f"stock_{t}", 300)  # 5分钟缓存
    if cached:
        logger.info(f"stock {t}: cache hit")
        return cached

    logger.info(f"stock {t}: fetching fresh data")

    async with httpx.AsyncClient() as client:
        daily_task    = fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": t, "outputsize": "compact"})
        overview_task = fetch_av(client, {"function": "OVERVIEW", "symbol": t})
        rsi_task      = fetch_av(client, {"function": "RSI", "symbol": t, "interval": "daily", "time_period": 14, "series_type": "close"})
        spy_task      = fetch_av(client, {"function": "TIME_SERIES_DAILY", "symbol": "SPY", "outputsize": "compact"})

        daily, overview, rsi_data, spy = await asyncio.gather(
            daily_task, overview_task, rsi_task, spy_task, return_exceptions=True
        )

    # 价格历史
    ts = {}
    if isinstance(daily, dict):
        ts = daily.get("Time Series (Daily)", {})
    if not ts:
        raise HTTPException(status_code=404, detail=f"No data for {t}")

    dates = sorted(ts.keys(), reverse=True)
    closes = []
    for d in dates[:252]:
        try:
            closes.append(float(ts[d]["4. close"]))
        except:
            pass
    if len(closes) < 14:
        raise HTTPException(status_code=404, detail="Insufficient price data")

    closes.reverse()  # 时间正序
    curr_price = closes[-1]
    history_30 = closes[-30:]

    # 概览数据
    info = overview if isinstance(overview, dict) else {}

    # 评分
    scores = {'Value': 50, 'Growth': 50, 'Quality': 50, 'Financial': 50, 'Momentum': 50}
    try:
        pe = float(info.get('PERatio', 0) or 0)
        peg = float(info.get('PEGRatio', 0) or 0)
        if peg > 0:
            scores['Value'] = max(0, min(100, (3 - peg) * 40))
        elif pe > 0:
            scores['Value'] = max(0, min(100, (60 - pe) * 2))
    except: pass
    try:
        g = float(info.get('RevenueGrowthTTM') or info.get('QuarterlyRevenueGrowthYOY', 0) or 0)
        scores['Growth'] = max(0, min(100, 30 + g * 100 * 2.3))
    except: pass
    try:
        pm = float(info.get('ProfitMargin', 0) or 0)
        roe = float(info.get('ReturnOnEquityTTM', 0) or 0)
        scores['Quality'] = max(0, min(100, pm * 150 + roe * 150))
    except: pass
    try:
        cr = float(info.get('CurrentRatio', 0) or 0)
        if cr > 0:
            scores['Financial'] = max(0, min(100, cr * 45))
    except: pass
    try:
        high52 = float(info.get('52WeekHigh', 0) or 0)
        if high52 > 0:
            scores['Momentum'] = (curr_price / high52) * 100
    except: pass

    # RSI
    rsi = 50.0
    try:
        rsi_ts = rsi_data.get("Technical Analysis: RSI", {}) if isinstance(rsi_data, dict) else {}
        if rsi_ts:
            latest_rsi_date = sorted(rsi_ts.keys(), reverse=True)[0]
            rsi = float(rsi_ts[latest_rsi_date]["RSI"])
    except: pass

    # Alpha vs SPY
    alpha = 0.0
    try:
        spy_ts = spy.get("Time Series (Daily)", {}) if isinstance(spy, dict) else {}
        spy_dates = sorted(spy_ts.keys(), reverse=True)
        spy_closes = [float(spy_ts[d]["4. close"]) for d in spy_dates[:len(closes)]]
        spy_closes.reverse()
        if spy_closes and closes:
            s_ret = (closes[-1] - closes[0]) / closes[0]
            m_ret = (spy_closes[-1] - spy_closes[0]) / spy_closes[0]
            alpha = (s_ret - m_ret) * 100
    except: pass

    result = {
        "ticker": t,
        "price": round(curr_price, 2),
        "scores": {k: round(float(v), 1) for k, v in scores.items()},
        "indicators": {
            "rsi": round(rsi, 2),
            "alpha": round(alpha, 2),
            "peg": float(info.get('PEGRatio', 0) or 0)
        },
        "history": [round(p, 2) for p in history_30]
    }
    set_cache(f"stock_{t}", result)
    return result
