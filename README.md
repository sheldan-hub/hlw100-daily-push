# 红利低波100 每日偏离度推送

每天自动计算 **中证红利低波动100指数 (930955)** 的 MA250 偏离度 / KDJ(9,3,3) / 滚动PE，并通过 [Server酱(ServerChan)](https://sct.ftqq.com/) 推送到你的微信。

由 GitHub Actions 在云端按时运行，**不依赖本地电脑开机**，也不会因沙箱回收而中断。

## 指标含义

| 指标 | 说明 |
|---|---|
| MA250 偏离度 | `(收盘价 − 250日均线) / 250日均线 × 100%`，衡量价格相对“年线”中枢的便宜/昂贵程度。负值越大越超卖。 |
| KDJ(9,3,3) | 短线动能指标，J 值 < 20 为超卖区。 |
| 滚动PE | 指数估值，结合历史分位判断贵贱。 |

**购买建议触发**：当 MA250 偏离度 ≤ **−8%**（深度超卖）时，微信简报会附加「⚠️ 建议购买」标记（近3年仅约19个交易日满足，属强买点预警）。其余时间照常推送偏离度信息。阈值可在仓库 Secrets 中用 `BUY_THRESH` 覆盖。

## ⚠️ 公开仓库必读：60 天自动暂停

GitHub 官方规则：**公开仓库若连续 60 天无任何仓库活动（commit/push 等），会自动静默禁用所有 scheduled 工作流**——你不会收到通知，往往等到第 61 天没收到推送才发现。

本仓库已内置 `keep-alive.yml` 应对此问题：它每隔半个月（每月 1 号、15 号）自动提交一次，持续产生仓库活动，重置 60 天计时器，确保主推送任务不被禁用。

- **如果你用私有仓库**：60 天规则不适用，`keep-alive.yml` 可留着（无害）也可删除。
- **如果你用公开仓库**：**必须保留 `keep-alive.yml`**，否则约两个月后任务会停。

## 部署步骤

1. 在 GitHub 新建仓库（公开/私有均可；本例按**公开仓库**说明，已含保活）。
2. 逐个新建以下文件并粘贴内容（共 6 个）：
   - `daily_push.py`（根目录）
   - `requirements.txt`（根目录）
   - `.github/workflows/daily-push.yml`（隐藏目录，新建文件名直接填完整路径）
   - `.github/workflows/keep-alive.yml`（保活，公开仓必留）
   - `.gitignore`（根目录，可选）
   - `README.md`（根目录，可选）
3. 在仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加：
   - `SCT_SENDKEY`：你的 Server酱 SendKey（形如 `SCTxxxxx...`，关注 Server酱后获取）
   - （可选）`BUY_THRESH`：购买阈值，默认 `-8.0`
4. 确保 **Settings → Actions → General → Workflow permissions** 设为 `Read and write permissions`（保活自动提交需要写权限），或至少 `Read`（仅主推送时）。
5. 到 **Actions** 标签，分别手动 **Run workflow** 触发 `每日偏离度推送` 与 `Keep Alive` 各一次，确认微信收到消息、且 `.keepalive` 被提交。
6. 之后：
   - 主推送：每天 UTC 12:00（北京时间 20:00）自动运行
   - 保活：每月 1 号、15 号 UTC 03:00 自动提交一次

## 本地运行

```bash
pip install -r requirements.txt
export SCT_SENDKEY="你的SendKey"
python daily_push.py
