# 狮选 LionPick — 文档索引

请先阅读 [`../CLAUDE.md`](../CLAUDE.md) 完成 60 秒快速上手, 然后再使用
本索引。文件按用途分组; 此处列出的均为已提交的当前文档集
(历史产物存放于 `commits/` 与 `PLAN_ARCHIVE.md`)。

## 参考资料
| 文档 | 内容 |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 端到端系统设计及设计依据 |
| [API.md](API.md) | 后端接口 + SSE 事件分类体系 |
| [PIPELINE.md](PIPELINE.md) | 开发标准流程(SOP)— 一次改动从编辑到部署的完整流转 |
| [DATA.md](DATA.md) | 商品目录 + 种子数据结构 |
| [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md) | 在队友的 Mac + iPhone 上搭建整套技术栈 |
| [IOS_SETUP.md](IOS_SETUP.md) | Xcode、签名、每周重签节奏 |
| [HARDWARE.md](HARDWARE.md) | 设备清单 + A100 SSH 使用边界 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 每个反复出现的坑及其修复方案 |
| [POLICY.md](POLICY.md) | 团队规则、提交格式、状态更新政策 |

## 质量与评测
| 文档 | 内容 |
|---|---|
| [QUALITY_REVIEW.md](QUALITY_REVIEW.md) | 评审视角的自评(按评分细则加权) |
| [EVAL_RESULTS.md](EVAL_RESULTS.md) | RAG 检索指标 + 如何重新生成仪表盘 |
| [RUBRIC_MAPPING.md](RUBRIC_MAPPING.md) | PDF §4 → 代码/产物映射, 用于答辩 |
| [eval_report.html](eval_report.html) · [eval_report.json](eval_report.json) | 生成的评测仪表盘(按场景划分) |

## 状态与规划
| 文档 | 内容 |
|---|---|
| [DEV_LOG.md](DEV_LOG.md) | 滚动更新、按时间倒序的交付日志 |
| [ROADMAP.md](ROADMAP.md) | 截至代码冻结的当前前瞻计划 + 分工归属 |
| [COMPETITIVE_ANALYSIS.md](COMPETITIVE_ANALYSIS.md) | 狮选 vs 市场(基于网络调研, 实事求是) |

## 功能设计
| 文档 | 内容 |
|---|---|
| [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) | 单页实现讲解 |
| [REPURCHASE_PLAN.md](REPURCHASE_PLAN.md) | 复购提醒功能设计 |
| [ACCOUNT_SYSTEM_PLAN.md](ACCOUNT_SYSTEM_PLAN.md) | 账户/鉴权完善 — 已交付部分 + 待办事项(JWT、限流) |

## 答辩材料
| 文档 | 内容 |
|---|---|
| [DEFENSE_DECK_PROMPT.md](DEFENSE_DECK_PROMPT.md) | 答辩幻灯片生成提示词 |
| [explainers/](explainers/) | 面向非工程背景受众的通俗讲解 |
| [demos/](demos/) | 已录制的演示截图 + 结论判定 |

## 历史与来源
| 文档 | 内容 |
|---|---|
| [commits/](commits/) | 按轮次的变更记录(最新: R9–R10 纪实) |
| [PLAN_ARCHIVE.md](PLAN_ARCHIVE.md) | 早期规划归档 |
| [research/](research/) | 市场 + 数据可得性调研(附来源) |

---

*仅限本地的笔记(已 gitignore): `POLICY_LOCAL.md`、`cluely/` — 不属于
已提交的文件树。*
