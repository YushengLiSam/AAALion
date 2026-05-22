# WeChat 更新 — 2026-05-24

> 直接粘贴到群里。内容比较长，分两段也可以。结尾的 **PROPOSAL** 链接是重点：希望雨晟、图杰看完发表意见后再继续推进。

---

@雨晟 @图杰 跟一下昨晚到今早的进度。Round 3 已经全部完成 + push 了，物理 iPhone 13 Pro 端到端测过。下面分三块：✅ 已完成、⚠️ 已知问题、📋 接下来希望你们 review 的 proposal。

## ✅ Round 3 ship 清单

**iOS 客户端**
- 真实商品图片现在会加载了（Round 2 那个 bug 修了，`ProductCard` 把 `/static/...` 相对 URL 在 Codable 里 resolve 成绝对 URL）
- **Settings 设置页**：右上齿轮 → 改后端 URL + 测试连接 + 存到 UserDefaults。换 Wi-Fi 不用重新编译了
- **长按消息菜单**：用户消息可以「编辑」「复制」，助手消息可以「朗读」（TTS, 中文）
- **三种上传方式**："+" 菜单 → 照片库 / 相机 / 文件（iCloud Drive 也支持，今天又修了一个 file picker 的 bug 用 `NSFileCoordinator`）
- **语音输入**：麦克风按钮 → Speech.framework 中文识别 → 实时打到输入框
- **TTS 朗读**：长按助手消息 → 朗读 → AVSpeechSynthesizer 中文播音

**视觉**
- Claude opus 设计的色板和字体（暖象牙背景 + 琥珀金强调色 + 深咖文字 + SF Pro Rounded），落在 `Views/Theme.swift`
- App 图标用 TokenRouter 的 `openai/gpt-5.4-image-2` 生成的，一个友好的狮子头 mascot + 购物 tag 鬃毛
- 空状态、TypingDots、骨架占位都加了

**A100 终于用上了**
- 装了 `torch==2.4.1+cu124`，**CUDA 正常工作**（驱动 580 + cu124 torch 兼容；没碰系统驱动，cuda-fuzzing 完全没动）
- OpenCLIP ViT-B/32 给 100 张商品图打了 embedding，10 秒搞定，存进 Chroma `products_image` 集合
- 后端 `routes/chat.py` 现在带图查询时**优先用 CLIP 视觉检索**而不是文字检索，更准

**iPhone 13 Pro 端到端验证通过**
- 物理设备签名 + 安装 + 信任 cert + 启动 + 真实 LLM 流式返回 + 商品卡片渲染 + 编辑/复制/朗读 / 语音输入 / 相机 / 文件 都跑过了
- **唯一注意**：免费 Apple ID 个人 team 的 cert 7 天会过期，每周日跑一次 `aaalion resign` 就好。我加了 Makefile target。

**仓库整理**
- README 顶部右上加了狮子图标，状态表更新到 Round 3
- 新加 `docs/IMPLEMENTATION_GUIDE.md`：一页索引，给新人/答辩快速了解全栈每一块的 owner / 关键文件 / 深度文档
- 9 个 demo 截图（6 个 Round 2 + 3 个 Round 3）都在 `docs/demos/`
- 之前调研得到的 3 个 Perplexity 文件已经整理到 `docs/research/`

## ⚠️ 已知问题 / 已修

| 问题 | 状态 |
|---|---|
| 物理设备 file picker 报错（你昨晚测出的）| ✅ 今天用 `NSFileCoordinator` + 错误显示修了，新 build 已经装上 |
| 候选商品太少（"助孕用品"返回化妆品）| ⏳ 数据扩展是 PROPOSAL 里 open decision，等你们意见 |
| 多模态视觉 LLM 有时认错商品（candle → beer 那次）| ✅ CLIP 视觉检索接上后，先用相似度匹商品，再让 LLM 在候选里推荐，准多了 |

## 📋 接下来 — Proposal 等你们 review

新文件：[`docs/PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md)（已 push 到 main）。**我希望你们先看完再决定下一轮怎么做，我设的截止是 5/26 周一晚上 10 点；那之前没回我就默认按 proposal 里的优先级开始单干。**

里头主要有这些事要决定：

| # | 事 | 工时 | 建议 owner |
|---|---|---|---|
| 1 | 答辩 demo 视频（3-5 分钟） | 2h | 陈澍枫 录，1 人 review |
| 2 | 答辩 PPT 15 页 | 4h | 陈澍枫 起草，三人各贡献 |
| 3 | 接上后端 `services/cache.py` 缓存 | 30min | 李雨晟（低风险，4.4 ⭐ 加分项） |
| 4 | 手工整理 10-15 条真实商品做侧验证集 | 4h 三人分 | 全员 |
| 5 | golden eval 扩到 30+ 条 + 实测 recall@5 | 3h | 管图杰 |
| 6 | 压力测试 locust（100 RPS × 60s） | 2h | 李雨晟（4.4 ⭐⭐⭐） |

**几个我想听你们意见的决定**（详见 proposal）：

- **A. 数据扩展**：手工整理 10-15 条真实商品做"侧验证集"？还是再用 AI 生成扩 200-300 条覆盖更多类目？
- **B. 缓存 TTL**：10 分钟（我的默认）够不够，要不要更长？
- **C. Demo 视频**：脚本化分镜剪辑（我的偏好）还是单次实录？
- **D. PPT 分工**：我起草各 owner 加 2 页？还是各自端到端负责一段？
- **E. Doubao key**：雨晟有从组织方拿到新 Key 的消息没？

## 雨晟今天能不能问一下组织方 Doubao Key 状态

PDF 里那个 Key 是确认作废了（被人 push 到公共仓库被滥用了），TokenRouter 这边 1000 次额度暂时顶着没问题。但如果能拿到新 Doubao Key，把 `LLM_PROVIDER` 切回 doubao 是一行 env var 的事，对答辩故事更完整。

## 故障排查 / FAQ

不变 — 一站式 [`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。今天 file picker 的修复也加进去了。

辛苦，等你们的回复。

---

## 附：仓库重要路径速查（更新版）

- 主仓库：`https://github.com/YushengLiSam/AAALion-`
- ⭐ **下一轮 proposal**：[`docs/PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md)
- 单页实施索引（推荐先看）：[`docs/IMPLEMENTATION_GUIDE.md`](IMPLEMENTATION_GUIDE.md)
- 评分对照：[`docs/RUBRIC_MAPPING.md`](RUBRIC_MAPPING.md)
- 9 个 demo 截图：[`docs/demos/`](demos/)
- 数据调研：[`docs/research/`](research/)
- 部署指南：[`docs/DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)
- 故障排查：[`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- 兜底执行计划：[`docs/SOLO_DEV_PLAN.md`](SOLO_DEV_PLAN.md)
- 历次重大提交记录：[`docs/commits/`](commits/)
