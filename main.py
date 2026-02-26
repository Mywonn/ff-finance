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
    tickers = {
        "us10y": "^TNX", "dxy": "DX-Y.NYB", "gold": "GC=F",
        "btc": "BTC-USD", "spx": "^GSPC", "ndx": "^NDX"
    }
    
    result = {"assets": {}, "mentor_insight": {}}
    
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
            
    # 计算导师点评逻辑 (针对最近 5 天的变化)
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
# 🎯 接口 2：获取个股深度透视数据
# ==========================================
@app.get("/api/stock/{ticker}")
def get_stock(ticker: str):
    try:
        stock = yf.Ticker(ticker.upper())
        hist = stock.history(period="1y")
        spy = yf.Ticker("^GSPC").history(period="1y")
        
        if hist.empty:
            raise HTTPException(status_code=404, detail="Stock not found")
            
        info = stock.info
        
        # 1. 计算 V7 五维评分
        scores = {}
        # 价值
        peg = clean_nan(info.get('pegRatio'))
        pe = clean_nan(info.get('trailingPE', 30))
        if peg and peg > 0: scores['Value'] = max(0, min(100, (3 - peg) * 40))
        else: scores['Value'] = max(0, min(100, (60 - (pe or 30)) * 2))
        
        # 成长
        g = clean_nan(info.get('revenueGrowth', 0)) * 100
        scores['Growth'] = max(0, min(100, 30 + g * 2.3))
        
        # 盈利
        pm = clean_nan(info.get('profitMargins', 0)) * 100
        roe = clean_nan(info.get('returnOnEquity', 0)) * 100
        scores['Quality'] = max(0, min(100, (pm * 1.5 + roe * 1.5)))
        
        # 财务
        cr = clean_nan(info.get('currentRatio', 1))
        scores['Financial'] = max(0, min(100, cr * 45))
        
        # 动量
        high = clean_nan(info.get('fiftyTwoWeekHigh', 100))
        curr = clean_nan(info.get('currentPrice', 50))
        scores['Momentum'] = (curr / high) * 100 if high else 50
        
        # 2. 计算 RSI & Alpha
        delta = hist['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = float(100 - (100 / (1 + rs)).iloc[-1])
        
        s_ret = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0]
        m_ret = (spy['Close'].iloc[-1] - spy['Close'].iloc[0]) / spy['Close'].iloc[0]
        alpha = float((s_ret - m_ret) * 100)
        
        return {
            "ticker": ticker.upper(),
            "price": round(float(hist['Close'].iloc[-1]), 2),
            "scores": {k: round(float(v), 1) for k, v in scores.items()},
            "indicators": {
                "rsi": round(rsi, 2),
                "alpha": round(alpha, 2),
                "peg": peg
            },
            # 返回最近 30 天收盘价供前端画图
            "history": hist['Close'].tail(30).round(2).tolist()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))