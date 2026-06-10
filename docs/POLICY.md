# 项目政策(Project Policy)

团队的长期规则与偏好。本文件中的所有内容都会与全体队友**共享**(提交并推送到远程)。私密条目请写入 `docs/POLICY_LOCAL.md`(已被 gitignore)。

当 Shufeng 在与项目助手的对话中说"store X in policy"(把 X 存入政策)时,该条目会落在本文件(若标记为私密,则落在 `POLICY_LOCAL.md`)。

## 比赛与课题(Scope and Identity)

- **团队名(Team)**: AAALion (3 人,编队名)。
- **产品名(Product)**: 狮选 **LionPick** — 基于 RAG 的多模态电商智能导购 (Lion's Pick of the right product)。
- **比赛(Competition)**: ByteDance 2026 AI 全栈挑战赛。即 AI 全栈挑战赛(AI Full-Stack Challenge)— **不是** 工程训练营。
- **课题(Topic)**: 基于 RAG 的多模态电商智能导购 AI Agent。
- **截止(Deadline)**: 2026-06-10(代码冻结 code-freeze)。答辩窗口: 2026-06-11 至 2026-06-19。

## 分工(Ownership)

| 中文名 | 英文名 / 昵称 | 模块 | 主要分支 |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | iOS 客户端 `client/`(+ 全模块兜底) | `shufeng` |
| 李雨晟 | Yusheng Li | 后端 `server/` | `sam` |
| 管图杰 | Tujie Guan | RAG 检索 `rag/` | `tujie` |

跨模块改动在合并前须获得受影响模块负责人的批准。

> **重点(Important)**: Shufeng 担任项目负责人,同时是**所有模块的兜底负责人**。如果某位队友的交付物延期,缺口由 Shufeng 补上。请据此规划 — 见 [SOLO_DEV_PLAN.md](SOLO_DEV_PLAN.md)。

## 密钥(Secrets)

- Doubao API key 仅通过团队私有频道分享,本地放入 `.env`。它**绝不**提交、绝不推送、绝不发送给 iOS 客户端。
- `.env` 已列入 `.gitignore`;示例模板位于 `.env.example`。

### 2026-05-22: Doubao key 泄露事件(主办方公告)

比赛 PDF 中印发的原始 Doubao API key 被另一支队伍通过开源的 GitHub 提交泄露。泄露的 key 被非参赛者滥用,导致正常使用被阻塞,主办方已**将其停用**。新 key 将重新分发。

在另行通知前的操作规则:

- PDF 提供的 key 会返回 HTTP 401 — 不必再尝试使用。
- **绝不提交任何 API key**,任何时候都不行,哪怕只是临时提交。只使用 `.env`。pre-commit 钩子(见 [`tools/check-secrets.sh`](../tools/check-secrets.sh))可以帮上忙。
- 如发现任何团队成员的仓库或分支存在泄露,**立即轮换(rotate)密钥**并通知频道。
- 在新的 Doubao key 到位之前,使用 Anthropic Claude 提供方(在 `.env` 中设置 `LLM_PROVIDER=anthropic`)— 见 [docs/API.md](API.md) 与 [server/README.md](../server/README.md)。

## 分支模型(Branch Model)

- `main` 为稳定分支。
- 每位开发者拥有个人分支: `shufeng`、`sam`、`tujie`。
- Shufeng 的日常开发大多在 `shufeng` 分支进行,以保护 `main` 的稳定性。
- PR 以 squash 方式合并进 `main`。

### 自 2026-05-24 起(Round 5+)

- Shufeng 的所有提交先落在 `shufeng` 分支。
- 仅在每轮迭代结束时执行 fast-forward 合并 `shufeng → main`,
  且须在评分自评(`docs/QUALITY_REVIEW.md`)写完、
  用户审阅过 diff 之后。
- `main` 始终保持随时可部署;`shufeng` 为进行中的工作。

## 提交信息格式(Commit Message Format)

使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范:

```
<type>(<scope>): <one-sentence summary in present tense>

<optional body — why, not what>
```

**类型(Types)**:
- `feat` — 新功能
- `fix` — 缺陷修复
- `docs` — 仅文档改动
- `chore` — 工具、基础设施、依赖、仓库整理
- `refactor` — 不改变功能的代码调整
- `test` — 新增/更新测试
- `style` — 仅格式调整

**作用域(Scopes)**(与仓库目录结构对应):
- `client`(iOS)· `server`(后端)· `rag`(检索)
- `docs` · `meetings` · `data` · `tools` · `repo`(仓库级)

**示例**:
```
feat(client): wire ChatService to real backend SSE
fix(server): handle Doubao 429 with exponential backoff
docs(policy): add commit message format
chore(tools): add Makefile and xcodegen project.yml
```

摘要行尽可能保持在 70 字符以内。列表项写在正文里,不要写进主题行。

## A100(SSH UC)— 边界

- 所有项目工作均位于 `~/shufeng/AAALion-/` 之下。
- 已有的 `~/shufeng/cuda-fuzzing/` 属于另一项进行中的任务,严禁触碰。
- 不得在 `~/shufeng/` 之外做任何改动(不装系统软件包,不改 shell rc 文件)。

## 幻觉防控规则

- Agent 绝不编造价格、SKU、优惠券或商品名称。
- 客户端渲染的商品卡片只能来自已索引的 JSON。
- 若检索未命中,系统提示词(system prompt)会指示模型如实承认。

## 各开发者本地配置(机器特定的值不要提交)

- 仓库中 `client/AAALionApp/AAALionApp/Config.swift` 的 `defaultBackendURL`
  **保持为 `http://localhost:8000`**。这样每位开发者的模拟器
  零配置即可使用。
- 若用真机 iPhone 走局域网,每位开发者通过应用内的
  **⚙ 设置面板(Settings sheet)** 填入自己 Mac 的 IP(持久化在 `UserDefaults` 中)。
  **不要**把自己的局域网 IP 推进 `Config.swift` — 会和其他开发者冲突。
- 其他个人配置只放在已 gitignore 的文件中: `server/.env`、
  `~/.config/lionpick/credentials.env`、`docs/POLICY_LOCAL.md`。
- 完整的后端 URL 解析矩阵见 [`README.md`](../README.md)
  §"Backend URL: how each developer points the app at their own Mac"。

## 加分项承诺

- 我们承诺做**两条**加分项赛道(依据 PDF 中"做精一项胜过浅尝三项"的指引):
  1. **4.3 对话深度** — 多轮、否定、比较。
  2. **4.2 多模态 — 拍照找货** — 借助 A100 上的 CLIP 实现以图找货。
- 语音 / TTS / 购物车 / 下单 **不在 v1 范围内**。

## 数据

- 随仓库附带的 `data/seed/` 为 AI 生成(已得到招募方确认),用作冒烟测试集。
- 演示与评测必须使用**真实**商品数据。数据来源见 `docs/DATA.md`。

### 自 2026-05-24 起(Round 6)— 真实商品扩充

- Round 6 新增 45 个人工精选的真实商品(20 个国际商品来自 Amazon US/JP +
  25 个来自京东/天猫)。每个商品都提交在 `data/seed/<cat>/data/p_*_*.json` 下,
  其 `provenance.external_url` 指向真实商品详情页。
- **图片版权注意事项**: 真实商品图片是*外链引用*,而非转载。
  目录 JSON 存储来源 `image_url_external`;iOS `AsyncImage`
  直接从平台的 CDN 拉取。我们不二次分发图片。
  这属于学术研究 / 私有演示用途;商业转载
  需取得明确授权。
- AI 生成的商品保持 `provenance.source_platform = "AI-gen (demo)"`,
  并在 UI 中带 `演示` 角标展示,以免评委误判
  哪些商品是真实的。

## 数据溯源与货币

- 每个商品 JSON 都带有 `provenance` 区块: `origin_country`、
  `source_platform`、`currency`、`external_url`、`shipping_note`。
- 目录的 `base_price` 与 SKU 的 `price` 保持源货币不变;它们
  是证据字段,绝不被覆盖。
- 后端在响应时为海外商品补充 `price_cny` 与 `exchange_rate`。
  它查询最新可用的 Frankfurter 参考汇率,
  缓存一小时,并对外暴露汇率日期/提供方。
- iOS 应用渲染:
  - 根据 `origin_country` 显示国旗 emoji(🇨🇳/🇺🇸/🇯🇵/🇩🇪/🇫🇷),
  - 以人民币为主展示价,跨境商品同时可见原始外币金额及
    带日期的参考汇率,
  - 带来源平台前缀的品牌行("Amazon US · Sony"),
  - 在有参考报价可用时,购物车/结算给出统一的人民币(CNY)合计。
- 这只是信息性的展示换算,并非支付或结算
  报价。若没有实时或缓存的外汇报价,应用将显示
  原始货币,不会把它悄悄计入 CNY 合计,也不会
  将其视作满足人民币预算筛选。

## 文档纪律

- `docs/` 中的每篇文档都假定读者是冷启动的新队友。
- 会议纪要放在 `meetings/` 中,文件名格式为 `YYYY-MM-DD-topic.md`。

## 团队状态更新(R8 起)

自 R8(2026-05-25)起,历史上的 `docs/WECHAT_UPDATE_*.md`
节奏正式退役。那些文件是定格在某个时间点的消息,
写完几小时内就过时了。新的拆分方式:

- **微信状态草稿**: **仅限本地**。放在 `docs/cluely/`
  (已 gitignore)或临时目录中。绝不对它们执行 `git add`。`.gitignore`
  中已有 `docs/WECHAT_*.md` 与 `docs/wechat/` 模式,即使草稿
  被误放到 `docs/` 下也能强制拦截。
- **持久的团队可见记录**: [`docs/DEV_LOG.md`](DEV_LOG.md)
  (滚动更新、倒序排列)。每个交付时刻各加一条记录
  (合并进 `main`、验证通过的功能、复盘)。每条记录
  带一行 `by:`,注明工作完成者。
- **逐提交深度记录**: 重大提交继续写在
  `docs/commits/YYYYMMDD-NNN-<topic>.md` 下。这是
  SHA 级别的记录;`DEV_LOG.md` 是轮次级别的摘要。
- **前瞻性提案**: 继续写在
  `docs/PROPOSAL_YYYY-MM-DD.md` 下。

## 决策日志

本节用于记录改变架构或范围的决策。

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-05-22 | 客户端从 Android(Sam 的微信提议)切换为 **iOS**。 | Shufeng 负责客户端且偏好 iOS;有 iPhone 13 可用于测试。 |
| 2026-05-22 | **Qdrant** 作为主向量数据库,Chroma 作为备选。 | 多向量与过滤支持更好;仍是单容器 Docker,便于私有化部署。 |
| 2026-05-25 | 使用带日期的参考汇率将海外商品展示价换算为人民币(CNY),同时保留源金额。 | 中国消费者需要可比的价格和人民币预算筛选;保留源价格/汇率可避免虚假精确。 |
| 2026-05-22 | A100 仅用于**索引构建**,不用于请求路径上的服务。 | 保持后端可移植;用 A100 做在线服务过于浪费。 |
| 2026-05-22 | 产品名 = **狮选 LionPick**(与团队名 AAALion 区分)。 | 中英双语、易于品牌化、呼应狮子标识,贴合"AI 选中对的商品"之意。 |
| 2026-05-22 | 采用 Conventional Commits 作为提交格式。 | 历史更易浏览;支持后续工具化(生成 changelog)。 |
| 2026-05-22 | Shufeng 是**所有模块的兜底负责人**(单人开发姿态)。 | 缓解 06-10 之前队友产出延期的风险。 |
