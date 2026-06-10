# 开发流水线(团队 SOP)

动手写任何代码之前先读本文。它告诉你操作的先后顺序、如何测试你正在构建的东西,以及如何在不影响另外两位开发者的前提下把改动落地。

> **写给非工程读者**:本文面向将要在项目里写代码的队友。
> 如果你想了解的是这个项目"做什么"而不是怎么开发它,
> 请从 [`docs/explainers/README.md`](explainers/README.md) 开始阅读。

## 如何开发

### 便于并行工作的顺序

1. **管图杰 / RAG** 先把向量索引拉起来并摄入 `data/seed/`。产出:一个已填充数据的 Chroma 集合(或 Qdrant `:6333`)。
2. **李雨晟 / 后端** 用 FastAPI 把它包起来,对外暴露 `/chat/stream`。初期他可以打桩(stub)检索,从本地 JSON 返回固定的 top-3。
3. **陈澍枫 / iOS** 对着 SSE 端点搭建聊天 UI。他可以在另一个终端运行 `python tools/mock_backend.py` 来打桩后端。

### 各角色为其他人提供的桩(stub)

- **RAG 桩**:`rag/retrieve/query.py` 接受 `--stub` 参数,返回 `data/seed/` 中的前 3 个商品。不需要 Qdrant。
- **后端桩**:提供固定的 token 流(从 `server/app/fixtures/sample_stream.txt` 读取),让 iOS 无需 Doubao 即可测试 SSE 解析。
- **iOS 桩**:其他人不需要;iOS 只做消费端。

### 分支模型

- `main` 保持稳定。只有脚手架 + 通过评审的 PR 才能落到这里。
- 每位开发者的个人分支:`shufeng`、`sam`、`tujie`。进行中的工作放在这里。
- 当你想要一个干净的 PR 时使用特性分支:`<owner>/<feature>`,例如 `tujie/negation-filter`。
- 每天把你的个人分支 rebase 到 `main` 上,保持分歧尽量小。

### 提交规范

- 主题行用祈使句("Add SSE delta type",而不是 "Added"/"Adding")。
- 正文写 *为什么*,而不是 *改了什么*。
- 一次提交只包含一个逻辑改动;小提交比完美提交落地更快。

### PR 规则

- PR 标题:与提交主题行同样的形式。
- 描述必须包含:
  - **改了什么**(1-2 句话)。
  - **为什么**(链接到需求或 issue)。
  - **如何测试**(命令 + 预期输出)。
- 评审人:
  - iOS PR → 陈澍枫自行合并(没有其他 iOS 开发)。
  - 后端 → 李雨晟自行合并。
  - RAG → 管图杰自行合并。
  - **跨领域** PR(例如修改 API 契约)→ 需要受影响领域负责人的批准。

## 如何测试

三个层次,成本依次递增:

### 1. RAG 评测(最便宜,每次 RAG 改动都要跑)

```bash
cd rag
python -m eval --golden eval/golden.jsonl
```

报告 recall@5 以及一份未命中(miss)的 CSV。目标:06-05 之前在黄金集上 recall@5 ≥ 80%。

### 2. 后端集成

```bash
cd server
pytest tests/
```

使用真实运行的 Qdrant(由测试 fixture 启动)和一个 **被 mock 的 Doubao 客户端** —— 测试中没有真实 API 调用,结果确定。成本:0¥。

### 3. iOS

- 用 `XCTest` 测 `ChatService` 的 SSE 解析(在 Xcode 中按 `Cmd+U`)。
- 在 iPhone 13 模拟器 + 真机上做手动 UI 冒烟测试(相机流程需要真机)。
- 每次演示前:在真实 iPhone 13 上对接真实后端跑一遍完整的端到端流程。

## 如何迭代

### 每日节奏

- 早上:拉取 `main`,rebase 你的分支。
- 在团队群里发站会文字:昨天 / 今天 / 阻塞点(各一行)。
- 晚上:即使是 WIP 也要推送你的分支 —— 防止笔记本丢失造成损失,也让其他人看到进展。

### 每周节奏(每周日)

- 30 分钟同步会。演示你的最新进展。更新 [`ROADMAP.md`](ROADMAP.md)。根据风险高低重新排定加分项功能的优先级。

### 卡住的时候

- 卡住超过 30 分钟:在团队群里发代码片段 + 你已经尝试过什么。
- 卡住超过 2 小时:打电话结对调试。时间比面子更贵。

### 针对本团队的代码评审建议

- 信任负责人在其领域内的判断;评审正确性,而不是风格偏好。
- 只要这个 diff 让系统变得更好就批准,即使你自己会写得不一样。
- 永远不要因为假设性的未来用例而阻塞 —— 代码冻结日是 2026-06-10。

## 本地运行快速上手(5 条命令)

```bash
cd server && docker compose up -d qdrant         # 1. vector DB
cd ../rag && python -m ingest.run                # 2. one-time index
cd ../server && uvicorn app.main:app --reload    # 3. backend
open ../client/AAALionApp/AAALionApp.xcodeproj   # 4. iOS — then Cmd+R
python ../tools/screenshot_watcher.py            # 5. (optional) for design loop
```
