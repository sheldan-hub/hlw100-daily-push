# -*- coding: utf-8 -*-
"""
红利低波动100指数 (930955) 每日 MA250 偏离度推送  ·  GitHub Actions 版

- 每日拉取最新行情，计算 MA250偏离度 / KDJ(9,3,3) / 滚动PE
- 偏离度 <= BUY_THRESH(-8%) 时，在简报中附加【建议购买】标记
- 通过 Server酱(ServerChan) 推送到微信
- SendKey 从环境变量 SCT_SENDKEY 读取（不硬编码，适配 GitHub Secrets）

【v2 修复说明】
- 数据获取改为「三源容错」：中证官网直连 → akshare → 东方财富，任一成功即可
- 强化重试：指数退避（2/4/8 秒），应对 GitHub 海外 runner 访问国内站点的抖动
- 失败时推送「真实错误详情」而非笼统提示，便于从微信直接定位问题
"""
import os
import sys
import time
import random
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
INDEX_CODE = "930955"
MIN_ROWS = 300                                              # 至少需要的行数（>250 才能算 MA250）

# 中证官网返回的 16 个字段（按位置映射）
CSINDEX_COLS = [
    "日期", "指数代码", "指数中文全称", "指数中文简称", "指数英文全称", "指数英文简称",
    "开盘", "最高", "最低", "收盘", "涨跌", "涨跌幅", "成交量", "成交金额", "样本数量", "滚动市盈率",
]

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.csindex.com.cn/",
}


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


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


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名与数据类型，保证下游指标计算可用。"""
    # 对齐到目标列：缺失的数值列补 NaN，保证「滚动市盈率」等关键列一定存在
    for col in CSINDEX_COLS:
        if col not in df.columns:
            df[col] = np.nan
    df = df[CSINDEX_COLS].copy()
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce")
    for col in ["开盘", "最高", "最低", "收盘", "涨跌", "涨跌幅", "成交量", "成交金额", "滚动市盈率"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ---------------------- 三个数据源 ----------------------
def fetch_csindex_direct(start_date, end_date):
    """源1：中证官网 API 直连（带完整浏览器 headers，最贴近原数据源）"""
    url = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
    r = requests.get(
        url,
        params={"indexCode": INDEX_CODE, "startDate": start_date, "endDate": end_date},
        headers=BROWSER_HEADERS, timeout=25,
    )
    r.raise_for_status()
    data = r.json()["data"]
    df = pd.DataFrame(data).iloc[:, :len(CSINDEX_COLS)]
    df.columns = CSINDEX_COLS[:df.shape[1]]
    return _normalize(df)


def fetch_akshare(start_date, end_date):
    """源2：akshare 封装的中证接口（原方案）"""
    import akshare as ak
    df = ak.stock_zh_index_hist_csindex(
        symbol=INDEX_CODE, start_date=start_date, end_date=end_date
    )
    return _normalize(df)


def fetch_eastmoney(start_date, end_date):
    """源3：东方财富 K 线（对海外 IP 更友好；无 PE 字段，PE 分位自动降级）"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"2.{INDEX_CODE}",       # 2 = 中证指数
        "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56",  # 日期,开,收,高,低,量
        "klt": "101", "fqt": "0",
        "beg": start_date, "end": end_date,
    }
    r = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=25)
    r.raise_for_status()
    klines = (r.json().get("data") or {}).get("klines", [])
    rows = []
    for k in klines:
        p = k.split(",")
        rows.append([p[0], p[1], p[3], p[4], p[2], p[5]])  # 日期,开,高,低,收,量
    df = pd.DataFrame(rows, columns=["日期", "开盘", "最高", "最低", "收盘", "成交量"])
    return _normalize(df)


def fetch_data_with_fallback(start_date, end_date):
    """
    依次尝试三个数据源，每个源内部做 3 次指数退避重试。
    返回 (df, 诊断信息字符串)；全部失败时 df 为 None。
    """
    sources = [
        ("中证直连", fetch_csindex_direct),
        ("akshare", fetch_akshare),
        ("东方财富", fetch_eastmoney),
    ]
    errors = []
    for name, fn in sources:
        for attempt in range(1, 4):
            try:
                df = fn(start_date, end_date)
                if df is not None and len(df) >= MIN_ROWS:
                    log(f"✓ 数据源「{name}」成功，获取 {len(df)} 行（第 {attempt} 次尝试）")
                    return df, " | ".join(errors) if errors else ""
                errors.append(f"{name}#{attempt}: 数据不足({0 if df is None else len(df)}<{MIN_ROWS})")
            except Exception as e:
                errors.append(f"{name}#{attempt}: {type(e).__name__}: {str(e)[:100]}")
                if attempt < 3:
                    wait = 2 ** attempt + random.uniform(0, 1)
                    log(f"  「{name}」第{attempt}次失败，{wait:.1f}s 后重试…")
                    time.sleep(wait)
    return None, " | ".join(errors)


def main():
    if not SCT_SENDKEY:
        print("ERROR: 未设置环境变量 SCT_SENDKEY，无法推送。")
        sys.exit(1)

    today = datetime.date.today().strftime("%Y%m%d")
    log(f"开始拉取 {INDEX_CODE} 历史行情（{HIST_START} ~ {today}）")

    df, diag = fetch_data_with_fallback(HIST_START, today)

    if df is None or len(df) == 0:
        # 失败时推送「真实错误」，不再笼统说“网络问题”
        detail = (
            f"日期 {today}\n\n"
            f"⚠️ 三个数据源均获取失败，请按下方错误定位：\n\n"
            f"{diag[:900]}\n\n"
            f"> 提示：多为 GitHub 海外节点访问国内数据源受限所致，"
            f"可手动 Run workflow 重试或切换数据源。"
        )
        ok, msg = push_to_wechat("红利低波100 数据获取失败", detail)
        log(f"DATA_FAIL | 推送结果: {'成功' if ok else '失败'} | {msg}")
        return

    df = df.sort_values("日期").reset_index(drop=True)
    c, h, l = "收盘", "最高", "最低"

    # MA250 + 偏离度
    df["MA250"] = df[c].rolling(250, min_periods=250).mean()
    df["偏离度"] = (df[c] - df["MA250"]) / df["MA250"] * 100

    # KDJ(9,3,3)
    low_n = df[l].rolling(9, min_periods=1).min()
    high_n = df[h].rolling(9, min_periods=1).max()
    df["RSV"] = (df[c] - low_n) / high_n.replace(0, np.nan) * 100
    df["RSV"] = df["RSV"].fillna(50)
    K, D = [50.0], [50.0]
    for i in range(1, len(df)):
        K.append(2 / 3 * K[-1] + 1 / 3 * df["RSV"].iloc[i])
        D.append(2 / 3 * D[-1] + 1 / 3 * K[-1])
    df["K"], df["D"], df["J"] = K, D, (3 * np.array(K) - 2 * np.array(D))

    # PE 分位（东方财富源无 PE 时自动降级）
    has_pe = df["滚动市盈率"].notna().any()
    if has_pe:
        df["PE分位"] = df["滚动市盈率"].rank(pct=True)

    last = df.iloc[-1]
    dev = float(last["偏离度"])
    devpct = float(df.dropna(subset=["偏离度"])["偏离度"].rank(pct=True).iloc[-1] * 100)
    j = float(last["J"])
    dt = pd.to_datetime(last["日期"]).strftime("%Y-%m-%d")
    buy = dev <= BUY_THRESH

    # 组装简报
    title = f"红利低波100 每日偏离度 · {dt}"
    L = [
        "## 红利低波动100指数 (930955) 每日跟踪",
        f"**日期**：{dt}",
        f"**收盘**：{last[c]:.2f} ｜ MA250：{last['MA250']:.2f}",
        f"**MA250偏离度**：`{dev:+.2f}%`（历史分位 {devpct:.1f}%）",
        f"**KDJ(9,3,3)**：K={last['K']:.1f} D={last['D']:.1f} J={j:.1f}",
    ]
    if has_pe:
        pe = float(last["滚动市盈率"])
        pepct = float(last["PE分位"] * 100)
        L.append(f"**滚动PE**：{pe:.2f}（样本期分位 {pepct:.1f}%）")
    else:
        L.append("**滚动PE**：数据源暂无（本次使用备用源，PE 不可得）")

    if buy:
        L += ["", (f"> ⚠️ **【建议购买】**  偏离度 `{dev:+.2f}%` ≤ {BUY_THRESH:.0f}% 阈值，"
                    f"已进入深度超卖区，属历史强买点信号区间，可关注左侧布局。")]
    else:
        L += ["", f"> 偏离度未达 {BUY_THRESH:.0f}% 购买阈值，维持每日跟踪。"]
    L += ["", "> 数据来源：中证指数官网 ｜ 指标：MA250偏离度 / KDJ(9,3,3) / 滚动PE"]
    desp = "\n".join(L)

    ok, msg = push_to_wechat(title, desp)
    log(f"[{dt}] 偏离度={dev:+.2f}% 分位={devpct:.1f}% J={j:.1f} 购买建议={'是' if buy else '否'}")
    log(f"推送结果: {'成功' if ok else '失败'} | {msg}")


if __name__ == "__main__":
    main()
