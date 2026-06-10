# 狮选 LionPick — 开发日志

> 滚动式、倒序排列的开发日志。最新条目置顶。
> 每个条目 = 一次交付时刻(一次合入 main、一个已验证的功能、
> 一次事后复盘)。取代了历史上的 `docs/WECHAT_UPDATE_*.md` 文件
> (已于 2026-05-25 删除;见该轮的 commit)。微信状态草稿现在
> **只留在本地** `docs/cluely/` 下(已 gitignore)— 不提交到仓库。
>
> 另见:
> - `docs/commits/YYYYMMDD-NNN-<topic>.md` — 各重大 commit 的深度记录。
> - `docs/PROPOSAL_YYYY-MM-DD.md` — 面向未来的提案。
> - `docs/POLICY.md` §"Team status updates" — 本文件所落实的
>   策略条款。

---

## 2026-06-09 — v0.2.0:应用内中英语言切换、签名 JWT、演示加固

**作者**: 管图杰 (JackGuan99) · CI 已验证(iOS Simulator Build + RAG Eval 全绿) · 后端已自动部署至 `main`

应用版本 **0.1.0 → 0.2.0**(build **1 → 6**)— 即
`client/AAALionApp/project.yml` 中的 `MARKETING_VERSION` /
`CURRENT_PROJECT_VERSION`。这是项目定名以来的首次版本号提升;后端改动
向后兼容,已安装的 0.1.0 版本可继续正常使用。

- **build 3 修复 — 运行时 UI 重新本地化。** build 2 切换了助手的
  回复语言,但 *UI* 仍是双语:SwiftUI 的 `Text("中文")`
  自动本地化是基于启动时的 bundle 解析的,因此应用内切换语言
  不会触发重新本地化(且在开发语言下会原样显示双语 key)。
  修复:`L(_:)` 现在直接读取所选语言的 `.lproj` bundle,
  并把约 170 个 UI 字符串字面量包进 `L(...)`;根视图的
  `.id(lang)` 重建会在切换时重新执行它们。即时生效,无需重启。**build 4** 随后
  扫清了剩余约 110 个动态字符串(三元分支、计算属性、
  辅助标签、toast 提示)以及双语页脚,使英文模式下除后端返回的
  商品数据外全部为英文。**build 5** 本地化了
  最后几个双语插值字符串(缓存统计的 `含 N 过期 / expired`
  一行、订单总额、"View on X" 商店链接、附件上限 toast)。
  **build 6** 去掉了根视图的 `.id(lang)` 重建 — 切换语言现在通过
  @Observable 原地重新本地化(`L()` 读取被观察的 `bundle`),用户
  得以保留导航/滚动位置,而不会被弹回首页。

- **应用内语言切换(中文 / English)** — 设置 → 语言选择器
  可在运行时切换整个 UI *以及助手的回复语言*,无需
  重启(运行时 `Bundle` 覆盖 + 根视图 `.id(lang)` 重建)。313/356
  个面向用户的字符串已通过由源码字面量生成的 `en.lproj` /
  `zh-Hans.lproj` 完成本地化;`Lf(...)` 处理插值/动态字符串。后端:
  `/chat/stream` 接受可选的 `language` 参数(默认 `zh`)→ 以
  该语言回复。**旧版本不传该参数,继续收到中文回复 — 不破坏兼容。**
- **真正的签名会话令牌** — 登录现在会额外签发一个带签名、有过期时间的
  **HS256 JWT**(`jwt` 字段),可通过 `POST /auth/verify` 验证;原有的不透明
  `token == user_id` 演示路径保持不动,客户端会忽略这个额外字段。
  纯标准库实现,零新增依赖(`server/app/services/jwt_session.py`)。
- **演示可靠性** — 抗网络抖动的聊天流(显式超时 +
  自动重试 + 友好的错误状态);`tools/warm-demo.py`(实测冷启动→预热
  首 token 提速 **≈16×**:15-18s → ~1s);新增 `docs/DEMO_RUNBOOK.md` +
  `docs/DEFENSE_DECK_OUTLINE.md`。
- **Bug 修复(检索)** — 英文/品牌产品线名称(iPhone/iPad/
  AirPods…)现在会锁定到 数码电子 类目,因此多轮开场 "推荐 iPhone" → "再便宜
  点的" 能保持在同一货架(`_category`/`_sub_categories` 大小写不敏感匹配)。43
  个服务端测试全部通过。
- **单独跟踪** — "不要X的Y" 句式的否定("不要苹果的耳机")返回
  0 张卡片;已拆为后台修复任务。演示使用可正常工作的逗号
  形式 "推荐降噪耳机,不要苹果"。

## 2026-06-01 — R10.2:主动反问、评测加固、Markdown、图片修复、CD 修复、真机部署

**作者**: Yusheng · 全部在云端 + 真机上实测验证

R10 轮次的延续;全部已合入 `main` 并自动部署。

- **#5 主动反问 (proactive clarification)** — 模糊的首轮请求
  ("推荐个礼物" / "随便看看" / "送女友什么礼物")现在会让 agent **先问一个
  澄清问题**而不是直接倾倒商品:一个保守的后端
  检测器(`_needs_clarification`)跳过检索/卡片,换用
  `_CLARIFY_PROMPT`。后端发出 `clarify` SSE 事件,带**可点击的
  快捷回复 chips**(礼物类 → 对象/预算/场合;泛型 → 品类/预算);点击
  其中一个即作为下一轮发送。已实测验证:模糊 → 反问 + chips,所有
  具体/否定/对比查询不受影响(无误触发)。
- **P2 — 评测加固** — 新增 6 个对抗性的否定/多轮 golden
  用例(golden.jsonl 76→82),每个用例在加入前都实测确认排除了被禁止的
  商品。亮点:持续性否定(防晒→不要日系→再便宜
  点 仍然能排除日系的 安热沙)。**否定准确率在 20 个用例上保持 1.000
  (用例数翻倍)**;82 用例集上 recall@5 为 0.932。
- **P1 — RUBRIC_MAPPING 刷新** — 该映射文档已经过时(把 4.1
  购物车列为 "deferred"、4.4 缓存列为 "partial",而两者均已交付)。已重写
  为当前实际状态并附验证标记。
- **Markdown 渲染** — 助手回复曾显示原始 markdown(竖线加
  横线的表格、字面的 `##`/`**`)。新的 `MarkdownMessageView` 把
  标题/表格/加粗/列表渲染为真正的 SwiftUI 视图,同时保留
  `[目录✓]`/`[推断?]` 溯源着色;不引入 markdown 依赖。
- **图片路径修复** — 种子数据的图片路径含中文类目文件夹
  (`/static/1_美妆护肤/…`);未经编码直接下发,iOS 的 `URL(string:)` 会间歇性
  加载失败。后端现在对路径做 `quote()` → 所有图片经隧道
  均返回 200。纯后端修复;无需重装应用。
- **`/cache/stats` 现在能展示检索缓存了**(提案 P3)— 此前只有
  响应缓存;已合入 `retrieval_cache_stats()`(向后兼容)+
  iOS 设置页新增一行显示检索命中率(即那个 8s→0.3s 的收益)。
- **CD 修复** — 自动部署的就绪检查只等 40 s,但负载下的预热
  要 ~60 s,导致它**把一个正常的 commit 误回滚并标记
  为坏提交**。已放宽到 150 s;端到端重新验证(push → 自动部署 →
  `deploy OK`,无回滚)。
- **真机部署** — 狮选 已安装到 iPhone 12 Pro Max + iPad Air(第 5
  代)用于实机测试(免费个人团队 (Personal Team),team `C3Y9PC45F8`)。

---

## 2026-05-30(晚些时候)— R10.perf:首屏提速 + 对话式购物车 + UX 打磨

**作者**: Yusheng(性能 + 客户端) · Shufeng 在云端验证

Yusheng 落地了一整轮性能 + 客户端体验改进,已自动部署
到云端(`123ef1b`)。2026-05-30 实测验证:

- **4.1 对话式改数量** — "把第二个改成3个" → 云端返回
  `{"type":"cart_intent","action":"set_quantity","index":2,"quantity":3}`
  ✅;iOS 无需点 +/− 即可更新购物车。后端正则 + iOS 联动。
- **4.4 首屏极速 (sub-second first screen)** — 商品卡片现在在
  LLM 文本**之前**流式下发(验证:5 个 `product_card` 事件先于
  任何 `delta` 到达 ✅;纯顺序调整,召回不变)。外加 LLM 长连接
  keep-alive + 重排候选裁剪。Yusheng 实测:缓存命中
  0.3 s,跳过重排 0.14 s,冷启动 0.14–2.2 s(此前 4–14 s,慢于整个
  AI 环节)。
- **4.4 客户端打磨** — 发送时显示骨架屏闪烁卡片;收藏 ❤️
  (弹簧动画 + 触感反馈,本地持久化,`FavoritesStore`);购物车滑动手势
  (左滑删除 / 右滑收藏)。新文件 `SkeletonCardView.swift`、
  `FavoritesStore.swift`。
- **重排成本旋钮**(`RERANK_INPUT_CAP` / `RERANK_MAX_LENGTH`)— 云端
  提速 3.8×(7884→2059 ms);golden recall@5 0.964→0.941,MRR 持平,
  否定准确率 1.000 — 质量保持,改一个环境变量即可回滚。
- **自动部署 CD** — `lionpick-autodeploy.timer` 每 ~2 分钟 git fetch
  一次,然后 `reset --hard` + 重启 + `/ready` 检查,失败回滚。合入
  main 后 ~2 分钟内即在云端生效,全程免人工。(修正此前
  "tarball" 的说法 — VM 实际是一个 git clone。)

演示技巧(Yusheng):提前把演示话术跑一遍灌进缓存 → 上台时首屏
0.3 s。冷查询 LLM ~1.5 s 是纯 CPU 云端的网络下限(GPU 只能
加速重排,而缓存已经跳过了重排)。

待办:`retrieval_cache_stats()` 仍未接入 `/cache/stats`
(外观问题);稳定的云端域名仍未落实(隧道 URL 是临时的);
Tujie 的 Docker 文档分支仍未合入 main。

---

## 2026-05-30 — R10 账号体系 + 后端上云 ☁️

**作者**: Shufeng(账号) + Yusheng(云基础设施、RAG 缓存)

### 后端现已云端托管(Yusheng)

FastAPI 后端现在跑在一台 **GCP VM**(4 vCPU / 15 GB)上,
由 `systemd` 管理(开机自启、崩溃自动重启),通过 **Cloudflare 隧道
(tunnel)** 暴露为公网 HTTPS。再也不用 "谁的 Mac 在同一个
WiFi 上" — 任何有网络的人都能访问。

- 公网基础 URL(⚠️ **临时的** — 隧道重启就会变;
  变更后 Yusheng 会重新广播):
  `https://actions-funeral-treating-trigger.trycloudflare.com`
- 自动生成的 Swagger UI:**`/docs`**(可点击,实时)。
- iOS 自动连接 — `Config.swift` 的 `defaultBackendURL` 已
  指向隧道(`c2eb98e`)。`git pull main` → 构建 → 完成。
  (如果你曾在设置里手输过 URL,长按齿轮 1.5 s
  → 开发者模式 → 清掉它。)
- 接口清单:`GET /health` `GET /ready` `POST /chat/stream`
  `GET /products` `GET /cache/stats` `GET /currency/rate`
  `POST /repurchase/purchase` `GET /repurchase/reminders`
  + R10 新增:`/auth/*` `/groupbuy/*` `/preferences/*` `/price_watch/*`。
- 性能说明:新查询首次命中约 ~10 s(检索冷启动),重复查询
  ~2 s(缓存)。中文查询快;英文会走
  多语言重排器,可能 30 s+ — **演示请用中文**。

### R10 账号体系(Shufeng — `9679d65`、`1117195`)

两种登录方式 + 一个**可插拔的云端接缝**,以便 Yusheng 之后接管
真正的用户服务:`UserStore` 协议 + `get_user_store()`
工厂,由 `USER_STORE_BACKEND=local|cloud` 切换。本地演示后端
= SQLite + 模拟短信(验证码直接显示在屏幕上,不真实发送)。iOS:
`LoginView`、`AuthService`、`AuthState`;`DeviceIdentity.userId` 在登录后返回
**账号 id**,因此所有功能(偏好 /
拼单 / 降价提醒 / 复购 / 聊天偏好先验)都重新以账号为
键,并对匿名设备数据做一次性迁移。

### R10 拼单打磨(Shufeng — `1117195`)

拼单 成功状态现在是一个真正的 **去支付** 按钮 → 把商品加入购物车
→ 打开 CheckoutView。邀请分享改为干净的文本 + 一个 `LP-XXXXX` 加入码
(而非旧的后端 JSON URL)+ 一个 复制邀请 的复制兜底。

### R10.bugfix(Shufeng — `46e1e6b`)— 在 iPhone 上发现并修复

1. **已登录用户在 拼单 / 偏好 / 降价提醒 /
   复购上 400。** 路由的 `user_id` 正则 `^[A-Za-z0-9_\-]{8,64}$`
   拒绝了 `phone:…` / `apple:…` 中的 `:`。已在全部四条路由放宽为
   `^[A-Za-z0-9_:.@\-]{8,64}$`。
2. **👍/👎 高亮在离开商品页后重置。** `prefSignal` 之前是
   临时的 `@State`;现在按 `(userId, productId)` 持久化到
   UserDefaults(服务端的分数一直是持久的)。
3. **"验证码直接显示在屏幕上感觉很假。"** 新增 **邮箱/手机号 + 密码**
   认证(`POST /auth/register`、`POST /auth/password/login`;
   `pbkdf2_hmac sha256` 10 万次迭代、16 字节盐)。LoginView 新增
   密码 / 短信 / Apple 的分段选择器(默认密码)。

> **⚠️ 待 YUSHENG 处理**:云端 VM 仍停留在 `c2eb98e`(旧
> 代码)。在它 `git pull && systemctl restart` 到 **`46e1e6b`** 之前,
> 已登录用户在那四个功能上会 400,密码认证会 404。
> 2026-05-30 实测验证:云端 `/auth/register` → 404,已登录
> `/groupbuy/create` → 400。

### Yusheng 的 RAG 检索缓存 — 在 `origin/Yusheng` 上,尚未合入

`008238b`:对昂贵的、与偏好无关的检索
流水线(混合检索 + 交叉编码器重排)做记忆化;他的测量中重复查询
提速 5287×;保留实时的 👍/👎 重排(缓存
键中不含 `user_id`)。待本地抽查 + 评测后再 FF 到 main。也还没
上云。

---

## 2026-05-25 夜 — R8 + R8.D + R8.E(Tujie 的 `2f9b6c4` 之后)

**作者**: Shufeng

在 Tujie 最后一次合入 main 的 `2f9b6c4`(有状态约束 + Docker
预热)之后,Shufeng 在三个子轮次中落地了 7 个 commit。

### R8 核心(`672c6fc`、`bcfb8ab`)

- iOS 设置页:实时 "缓存命中率" 面板,每 10 s 轮询一次 `/cache/stats`
  (把 Sam 的 R7e 端点用了起来,此前客户端一直没用上)。
- 多轮否定持久化:`Filter.exclude_keywords` 现在通过
  `constraint_state.py` 的合并跨轮携带 — `"推荐防晒霜不要日系"` →
  `"再便宜点的呢"` 在第 2 轮仍然排除日系品牌。
- 品牌产地覆盖扩展到 KR / DE / GB。`"不要韩系 / 英系 /
  德系"` 现在是硬过滤。
- Golden 审计抽查:确认 Sam/Tujie 的 `negation_accuracy=1.000`
  来自真实的标注修正(而非挑选样本)。
- 9 张演示截图 + 配套 `.md` 放在 `docs/demos/2026-05-25-evening/` 下。

### R8.D — 公网部署(`417f840`、`a22ce7d`)

- **Cloudflare Tunnel** 把 `localhost:8000` 暴露为
  `https://reader-missile-absolute-memphis.trycloudflare.com`。
  iPhone 可以从任何网络连接(蜂窝网络、公共 Wi-Fi、酒店 SSID),
  零局域网配置。`Config.swift` 内置隧道 URL。
- **开发者模式手势**:长按齿轮图标 1.5 s 翻转
  `@AppStorage("lionpick.devMode")`;设置里的后端 URL 编辑器
  默认隐藏,只在开发者模式下出现。视觉反馈:
  开启时 `gearshape` → `gearshape.fill`(琥珀色)。
- **语音跨会话修复**(`a22ce7d`):`SFSpeechRecognitionTask`
  在 `cancel()` 后会泄漏最后一次回调。`sessionID` 守卫 +
  `task.finish()`(替代 `cancel()`)+ `startListening` 中防御性清空
  草稿,共同关闭了 `"toy + cosmetic = 'toys and cosmetic'"`
  这个 bug。

### R8.E — iOS UX 对齐 ChatGPT / Claude(`1a32a79`、`c14972c`、`0eb143a`)

- **语音空闲计时自动停止**,主 RunLoop 上 1.8 s 阈值、
  `.common` 模式。相同文本的部分结果通过 `lastTranscript`
  守卫抑制,环境噪音不会延长窗口。计时器触发时 `onStop` 回调
  同步 ViewModel 上绑定 UI 的标志位。
  输入栏显示 `"正在听… / Listening — 停顿 ~2 秒自动结束"`。
- **多附件最多 10 个**(照片 + 文件 + 相机,可混合)。新增
  `Attachment` 结构体(`kind: .photo/.camera/.file`,MIME 通过
  魔数嗅探)。输入栏为 64×64 chips 的水平滚动行,带
  x 删除和 "N/10" 计数。消息气泡在文本上方渲染一个 2 行的
  `LazyVGrid`(每行 5 个,96×96)。
- **PhotosPicker 选择 bug**:复数形式的 `PhotosPicker` 内联在
  `Menu` 里存在 SwiftUI 绑定 bug。改为在 NavigationStack 上用
  `.photosPicker(isPresented:)` 修饰符;Menu
  现在只负责置一个标志位。
- **上传时图片降采样**:`Attachment.compressForUpload` 把最长边
  缩到 1280 px 并以 0.78 质量重新编码 JPEG。
  典型的 4032×3024 iPhone 照片:2.4 MB → ~120 KB(缩小 20×)。
- **后端异步卸载**:`top_k_image()` 和 `top_k()` 现在跑在
  `asyncio.to_thread()` 里,检索期间 FastAPI 事件循环保持
  响应。这正是多图聊天时 `/cache/stats` 超时的原因 —
  单 worker 的 uvicorn 事件循环被同步 torch 调用
  阻塞了。缓存统计的拉取超时也从 15 s → 60 s,
  作为双保险。
- **后端多图**:`_extract_image_bytes` →
  `_extract_image_bytes_list`(上限 10)。CLIP 检索器仍使用
  `imgs[0]`(单图视觉检索器);LLM 通过 content 数组看到
  全部图片。缓存键使用 `hash_image_bytes_list`(排序后的
  SHA 拼接),因此顺序无关。

### 质量指标

与 R7.3 合入时相比无变化(R8 的工作是 UX + 基础设施,不涉及检索):
- `recall@5 = 0.880`、`MRR = 0.828`(审计后的 59 用例集)
- `negation_accuracy = 1.000`
- 自评 **~91-92 / 100**

### 进入 R9 时的待办

- 演示视频(3-5 分钟 QuickTime 录屏)
- 答辩幻灯片(Gamma 提示词在 `docs/defense/gamma-prompt.md`)
- 第二阶段云端 VM 部署(Hetzner CX22,约 2026-06-05)
- Chroma 快照 zip 上传 Drive 供团队内部分发
