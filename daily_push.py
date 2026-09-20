# -*- coding: utf-8 -*-
"""
红利低波动100指数 (930955) 每日 MA250 偏离度推送  ·  GitHub Actions 版

- 每日拉取最新行情，计算 MA250偏离度 / KDJ(9,3,3) / 滚动PE
- 偏离度 <= BUY_THRESH(-8%) 时，在简报中附加【建议购买】标记
- 通过 Server酱(ServerChan) 推送到微信
- SendKey 从环境变量 SCT_SENDKEY 读取（不硬编码，适配 GitHub Secrets）
"""
import os
import sys
import time
import warnings
import datetime
import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ===== 配置（均可经环境变量覆盖）=====
SCT_SENDKEY = os.environ.get("SCT_SENDKEY")
BUY_THRESH = float(os.environ.get("BUY_THRESH", "-8.0"))   # 偏离度 <= 此值 提示购买（深度超卖）
HIST_START = os.environ.get("HIST_START", "20230101")      # 历史起点，确保 MA250 有值


def push_to_wechat(title: str, desp: str):
    """推送消息到 Server酱 -> 微信。返回 (是否成功, 说明)。"""
    if not SCT_SENDKEY:
        return False, "未配置 SCT_SENDKEY 环境变量"
    url = f"https://sctapi.ftqq.com/{SCT_SENDKEY}.send"
    try:
        r = requests.post(
            url, data={"title": title, "desp": desp},
            timeout=25, headers={"User-Agent": "Mozilla/5.0"},
        )
        try:
            j = r.json()
            return j.get("code", -1) == 0, f"HTTP {r.status_code} | {r.text[:120]}"
        except Exception:
            return False, f"HTTP {r.status_code} | 非JSON返回"
    except Exception as e:
        return False, f"请求异常: {e}"


def main():
    if not SCT_SENDKEY:
        print("ERROR: 未设置环境变量 SCT_SENDKEY，无法推送。")
        sys.exit(1)

    # 1. 拉取行情（优先 akshare 中证官网源）
    try:
        import akshare as ak
    except Exception as e:
        ok, msg = push_to_wechat("红利低波100 依赖缺失", f"⚠️ 运行环境缺少 akshare：{e}")
        print("IMPORT_FAIL", e, "|", msg)
        return

    today = datetime.date.today().strftime("%Y%m%d")
    df = None
    for _ in range(3):
        try:
            df = ak.stock_zh_index_hist_csindex(symbol="930955", start_date=HIST_START, end_date=today)
            if df is not None and len(df) > 300:
                break
        except Exception:
            time.sleep(3)

    if df is None or len(df) == 0:
        ok, msg = push_to_wechat(
            "红利低波100 数据获取失败",
            f"日期 {today}\n\n⚠️ 行情数据获取失败，请检查网络或数据源后重试。",
        )
        print("DATA_FAIL", msg)
        return

    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期").reset_index(drop=True)
    c, h, l = "收盘", "最高", "最低"

    # 2. MA250 + 偏离度
    df["MA250"] = df[c].rolling(250, min_periods=250).mean()
    df["偏离度"] = (df[c] - df["MA250"]) / df["MA250"] * 100

    # 3. KDJ(9,3,3)
    low_n = df[l].rolling(9, min_periods=1).min()
    high_n = df[h].rolling(9, min_periods=1).max()
    df["RSV"] = (df[c] - low_n) / high_n.replace(0, np.nan) * 100
    df["RSV"] = df["RSV"].fillna(50)
    K, D = [50.0], [50.0]
    for i in range(1, len(df)):
        K.append(2 / 3 * K[-1] + 1 / 3 * df["RSV"].iloc[i])
        D.append(2 / 3 * D[-1] + 1 / 3 * K[-1])
    df["K"], df["D"], df["J"] = K, D, (3 * np.array(K) - 2 * np.array(D))
    df["PE分位"] = df["滚动市盈率"].rank(pct=True)

    last = df.iloc[-1]
    dev = float(last["偏离度"])
    devpct = float(df.dropna(subset=["偏离度"])["偏离度"].rank(pct=True).iloc[-1] * 100)
    j = float(last["J"])
    pe = float(last["滚动市盈率"])
    pepct = float(last["PE分位"] * 100)
    dt = pd.to_datetime(last["日期"]).strftime("%Y-%m-%d")

    buy = dev <= BUY_THRESH

    # 4. 组装简报
    title = f"红利低波100 每日偏离度 · {dt}"
    L = [
        "## 红利低波动100指数 (930955) 每日跟踪",
        f"**日期**：{dt}",
        f"**收盘**：{last[c]:.2f} ｜ MA250：{last['MA250']:.2f}",
        f"**MA250偏离度**：`{dev:+.2f}%`（历史分位 {devpct:.1f}%）",
        f"**KDJ(9,3,3)**：K={last['K']:.1f} D={last['D']:.1f} J={j:.1f}",
        f"**滚动PE**：{pe:.2f}（样本期分位 {pepct:.1f}%）",
    ]
    if buy:
        L += ["", (f"> ⚠️ **【建议购买】**  偏离度 `{dev:+.2f}%` ≤ {BUY_THRESH:.0f}% 阈值，"
                    f"已进入深度超卖区，属历史强买点信号区间，可关注左侧布局。")]
    else:
        L += ["", f"> 偏离度未达 {BUY_THRESH:.0f}% 购买阈值，维持每日跟踪。"]
    L += ["", "> 数据来源：中证指数官网 ｜ 指标：MA250偏离度 / KDJ(9,3,3) / 滚动PE"]
    desp = "\n".join(L)

    ok, msg = push_to_wechat(title, desp)
    print(f"[{dt}] 偏离度={dev:+.2f}% 分位={devpct:.1f}% J={j:.1f} 购买建议={'是' if buy else '否'}")
    print("推送结果:", "成功" if ok else "失败", "|", msg)


if __name__ == "__main__":
    main()
