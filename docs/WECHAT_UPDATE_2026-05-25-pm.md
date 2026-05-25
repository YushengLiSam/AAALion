# WeChat 更新 — 2026-05-25 晚（Round 7）

> 粘贴到团队群，简体中文。Round 6.5 上午已发过一次，这是当天的第二轮收尾。

---

各位辛苦了 🦁 今天后半天 Round 7 收尾，重点几件事先汇报：

## 一、感谢两位队友的 PR 都已上 main 🎉

- **@李雨晟（Sam）**：评测看板 `601abb6` 已 merge。56 + 3 = 59 cases、6 个场景 tag、3 个策略对比、HTML 看板 `docs/eval_report.html` 逐 case 可点。这是真正把"我们的 RAG 好不好"从感觉变成数字的一刀。👏
- **@管图杰（Tujie）**：同义词扩展 + 多轮上下文 + 价格 intent 三件套 (`b317081`, `4c2fe51`) 现在跑在生产路径上。R6.5 测出来给我们 **+19% recall@5**，"再便宜点的呢" 这种短回复现在能正确继承上文 anchor。👏

## 二、我今晚补的几件事（都在 `shufeng → main`）

1. **品牌产地反选修复** `dc13f32`
   - R6 我们的 "不要日系防晒霜" 会漏 安热沙（Anessa）— 因为它标题里没"日"字。
   - 新 `rag/retrieve/brand_origin.py` 维护 70 个品牌的产地（ISO-2 国家码）+ 国别关键词字典；`apply_negation` 现在也会按产地剔除。
   - 现场 SSE 验证：`推荐防晒霜不要日系品牌` → 巴黎欧莱雅 / 理肤泉 / 科颜氏 / La Roche-Posay（零日系）✅
   - 截图：`docs/demos/2026-05-25/03-negation.png`

2. **检索快路径** `server/app/services/rag_client.py`
   - 当 query 里直接点名了目录里的品牌 + 没有反选信号 → 跳过 rerank。
   - 同评测集上 recall@5 0.723 → **0.746**，中位延迟 305 → **266 ms**，反选准确率 0.733 保持不变。

3. **iOS 自动朗读首段**（默认关）
   - 设置页面新加 "自动朗读首段 / Auto-read first paragraph" 开关。
   - 第一段（句号/感叹号/问号 / `\n\n` / 200 字 三选一为边界）触发 TTS，每条消息只读一次。

4. **压力测试** `tools/stress_test.py`（之前 R5 计划过没写）
   - 20 并发 × 45 秒，**100% 成功率**（92/92），p50 first-delta 2.3 秒。
   - 报告：`docs/stress_test_2026-05-25.md`

5. **重录 6 个 demo** `docs/demos/2026-05-25/`
   - 6 个场景对应 Sam 评测的 6 个 tag：basic / filter / negation / multiturn / compare / no-match。
   - 5 张模拟器截图 + 6 篇 sidecar `.md` + index README，每个都列了 SSE 原文 + 商品 ID + verdict。

## 三、当前估分 90 / 100 ✨

R5 → R6 → R6.5 → R7 自评：86.0 → 88.0 → 89.5 → **90.0**
- 详见 `docs/QUALITY_REVIEW.md` 顶部的 R7 row + 增量表。

| 维度 | 分 | 备注 |
|---|---:|---|
| 基础功能完整性 | 94 | 不变 |
| 工程质量 | **90** | +1 from Sam 的评测看板 |
| 效果与可靠性 | **82** | +2 from 品牌产地反选修复 |
| 加分项 | 84 | 不变 |

## 四、距离 6 月 10 日代码冻结还有 16 天，待办分工

| # | 事项 | Owner | 工时 |
|---|---|---|---|
| 1 | **演示视频** 3-5 分钟（QuickTime 屏录 + 配音） | @陈澍枫 (我) | 2 hrs |
| 2 | **答辩 slide 草稿**（Gamma prompt 已写在 `docs/defense/gamma-prompt.md`） | @陈澍枫 起稿 → 全员 review | 4 hrs |
| 3 | **真实热门商品图替换**（10 件主打商品，从品牌官网/Wiki 取） | 三人各拿 3-4 件 | 3 hrs |
| 4 | **JD SKU 复核**（华为 GT4 / 凯乐石 MT5-3 / 牧高笛 冷山2 三个 URL 可能掉链子） | @管图杰 (or @李雨晟) 浏览器实测一遍 | 30 min |
| 5 | **brand_origin 字典扩充** 新加 brand 时补一条 | @管图杰 | 持续 |
| 6 | **观测面板**（延迟、缓存命中率、recall 趋势） | @李雨晟（可选） | 4 hrs |

## 五、Cluely 答辩支持已就绪

我本地准备了一份 Cluely prompt + meeting context + 15 个常见评委问题预演答案（30 秒口语版），到时候答辩前可以集中过一遍。这部分**保留在本地不提交远端**（personal prep），有需要单独同步给你们。

---

仓库当前两个分支都同步在 `3ab5a7d`：

```
git pull origin main          # 拿最新
python -m rag.eval.report     # 重跑评测看板
aaalion ios-sim               # 跑模拟器看 R7 demo
```

任何问题群里 @ 我 🦁
