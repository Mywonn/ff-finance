from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import numpy as np
import math

app = FastAPI(title="Future Flow Finance API")

# 配置 CORS，允许前端 Vue 跨域调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 实际部署后可以改成你的前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 移除你本地的代理配置，因为部署到云端不需要代理
# os.environ["HTTP_PROXY"] = ... 

def clean_nan(val):
    """处理 yfinance 返回的 NaN 值，防止 JSON 序列化报错"""
    if isinstance(val, float) and math.isnan(val):
        return None
    return val

# ==========================================
# 🌍 接口 1：获取宏观大盘数据
# ==========================================
@app.get("/api/macro")
def get_macro(period: str = "1mo"):
    # 支持的时间维度（yfinance原生支持）：'1d', '5d', '1mo', '3mo', '6mo', '1y', 'ytd', 'max'
    # 前端只需改变传参即可，例如：/api/macro?period=3mo 或 /api/macro?period=1y
    
    # 按照你要求的顺序排列字典（Python 3.7+ 会保留字典的插入顺序）
    tickers = {
        "us10y": "^TNX",      # 10年期美债
        "dxy": "DX-Y.NYB",    # 美元指数
        "vix": "^VIX",        # VIX恐慌指数 (新增)
        "btc": "BTC-USD",     # 比特币
        "spx": "^GSPC",       # 标普500
        "ndx": "^NDX",        # 纳斯达克
        "gold": "GC=F",       # 黄金
        "copper": "HG=F"      # 铜价 (新增)
    }
    
    # 将 mentor_insight 放在最前，assets 在后，方便前端直观解析
    result = {"mentor_insight": {}, "assets": {}}
    
    # 获取资产数据
    for key, ticker in tickers.items():
        try:
            hist = yf.Ticker(ticker).history(period=period)
            if not hist.empty:
                curr = float(hist['Close'].iloc[-1])
                prev = float(hist['Close'].iloc[0])
                chg = ((curr - prev) / prev) * 100
                result["assets"][key] = {
                    "current": round(curr, 2),
                    "change_pct": round(chg, 2),
                    # 返回最近 10 天的收盘价用于前端画迷你折线图
                    "sparkline": hist['Close'].tail(10).round(2).tolist() 
                }
        except Exception as e:
            continue
            
    # 计算导师点评逻辑 (针对最近 5 天的美债变化)
    try:
        us10y_hist = yf.Ticker("^TNX").history(period="5d")
        if len(us10y_hist) > 1:
            curr_rate = us10y_hist['Close'].iloc[-1]
            prev_rate = us10y_hist['Close'].iloc[-2]
            tnx_c = ((curr_rate - prev_rate) / prev_rate) * 100
            
            if tnx_c > 0.8:
                result["mentor_insight"] = {
                    "type": "risk",
                    "title": "紧缩风暴",
                    "content": f"10年期美债收益率大涨 {tnx_c:.2f}%。资金成本上升，风险资产承压。建议：多看少动。"
                }
            elif tnx_c < -0.8:
                result["mentor_insight"] = {
                    "type": "opportunity",
                    "title": "宽松暖风",
                    "content": f"美债收益率回落 {tnx_c:.2f}%。流动性边际改善。建议：关注科技股与加密货币低吸机会。"
                }
            else:
                result["mentor_insight"] = {
                    "type": "neutral",
                    "title": "震荡整理",
                    "content": "宏观因子波动不大，市场缺乏明确方向指引。建议：轻指数，重个股逻辑。"
                }
    except:
        pass

    return result

# ==========================================
# 🎯 接口 2：获取个股深度透视数据 (终极防弹版)
# ==========================================
@app.get("/api/stock/{ticker}")
def get_stock(ticker: str):
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="1y")
        
        if hist.empty or len(hist) < 14:
            raise HTTPException(status_code=404, detail="Stock data insufficient")
            
        info = stock.info or {}
        
        # 1. 评分 - 赋予默认值防崩溃
        scores = {'Value': 50, 'Growth': 50, 'Quality': 50, 'Financial': 50, 'Momentum': 50}
        
        peg = info.get('pegRatio')
        pe = info.get('trailingPE', 30)
        if peg is not None and peg > 0:
            scores['Value'] = max(0, min(100, (3 - peg) * 40))
        elif pe is not None:
            scores['Value'] = max(0, min(100, (60 - pe) * 2))
            
        g = info.get('revenueGrowth')
        if g is not None: scores['Growth'] = max(0, min(100, 30 + g * 100 * 2.3))
        
        pm = info.get('profitMargins')
        roe = info.get('returnOnEquity')
        if pm is not None and roe is not None:
            scores['Quality'] = max(0, min(100, (pm * 150 + roe * 150)))
            
        cr = info.get('currentRatio')
        if cr is not None: scores['Financial'] = max(0, min(100, cr * 45))
        
        high = info.get('fiftyTwoWeekHigh')
        curr = info.get('currentPrice')
        if high is not None and curr is not None and high > 0:
            scores['Momentum'] = (curr / high) * 100

        # 2. RSI & Alpha 防崩溃计算
        rsi = 50.0
        alpha = 0.0
        try:
            delta = hist['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            rsi_series = 100 - (100 / (1 + rs))
            # 过滤掉 NaN 的值
            if not rsi_series.dropna().empty:
                rsi = float(rsi_series.dropna().iloc[-1])
        except:
            pass
            
        try:
            spy = yf.Ticker("^GSPC").history(period="1y")
            if not spy.empty and len(spy) > 0 and len(hist) > 0:
                s_ret = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0]
                m_ret = (spy['Close'].iloc[-1] - spy['Close'].iloc[0]) / spy['Close'].iloc[0]
                alpha = float((s_ret - m_ret) * 100)
        except:
            pass
            
        return {
            "ticker": ticker.upper(),
            "price": round(float(hist['Close'].iloc[-1]), 2),
            "scores": {k: round(float(v), 1) for k, v in scores.items()},
            "indicators": {
                "rsi": round(rsi, 2),
                "alpha": round(alpha, 2),
                "peg": peg if peg is not None else 0
            },
            "history": hist['Close'].tail(30).round(2).tolist()
        }
    except Exception as e:
        print(f"Error fetching {ticker}: {e}") # 打印到日志
        raise HTTPException(status_code=500, detail=str(e))
