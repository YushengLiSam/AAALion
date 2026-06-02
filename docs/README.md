# 狮选 LionPick — Documentation

Start with [`../CLAUDE.md`](../CLAUDE.md) for the 60-second bootstrap, then
use this index. Files are grouped by purpose; everything here is the
committed, current set (historical artifacts live in `commits/` and
`PLAN_ARCHIVE.md`).

## Reference
| Doc | What |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | End-to-end system design + rationale |
| [API.md](API.md) | Backend endpoints + SSE event taxonomy |
| [PIPELINE.md](PIPELINE.md) | Dev SOP — how a change flows from edit to deploy |
| [DATA.md](DATA.md) | Catalog + seed-data shape |
| [DEPLOY_GUIDE.md](DEPLOY_GUIDE.md) | Stand up the stack on a teammate's Mac + iPhone |
| [IOS_SETUP.md](IOS_SETUP.md) | Xcode, signing, weekly resign cadence |
| [HARDWARE.md](HARDWARE.md) | Devices + A100 SSH boundaries |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Every recurring gotcha + its fix |
| [POLICY.md](POLICY.md) | Team rules, commit format, status-update policy |

## Quality & evaluation
| Doc | What |
|---|---|
| [QUALITY_REVIEW.md](QUALITY_REVIEW.md) | Grader-style self-assessment (rubric-weighted) |
| [EVAL_RESULTS.md](EVAL_RESULTS.md) | RAG retrieval metrics + how to regenerate the dashboard |
| [RUBRIC_MAPPING.md](RUBRIC_MAPPING.md) | PDF §4 → code/artifact mapping for defense |
| [eval_report.html](eval_report.html) · [eval_report.json](eval_report.json) | Generated eval dashboard (per-scenario) |

## Status & planning
| Doc | What |
|---|---|
| [DEV_LOG.md](DEV_LOG.md) | Rolling, reverse-chronological shipping log |
| [ROADMAP.md](ROADMAP.md) | Current forward plan to code-freeze + ownership |
| [COMPETITIVE_ANALYSIS.md](COMPETITIVE_ANALYSIS.md) | 狮选 vs the market (web-researched, honest) |

## Feature design
| Doc | What |
|---|---|
| [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) | Single-page implementation walkthrough |
| [REPURCHASE_PLAN.md](REPURCHASE_PLAN.md) | Repurchase-reminder feature design |
| [ACCOUNT_SYSTEM_PLAN.md](ACCOUNT_SYSTEM_PLAN.md) | Account/auth completion — shipped + what remains (JWT, rate-limit) |

## Presentation
| Doc | What |
|---|---|
| [DEFENSE_DECK_PROMPT.md](DEFENSE_DECK_PROMPT.md) | Slide-deck generation prompt |
| [explainers/](explainers/) | Plain-language explainers for a non-engineer audience |
| [demos/](demos/) | Recorded demo screenshots + verdicts |

## History & sources
| Doc | What |
|---|---|
| [commits/](commits/) | Per-round change records (newest: R9–R10 documentary) |
| [PLAN_ARCHIVE.md](PLAN_ARCHIVE.md) | Archived earlier planning |
| [research/](research/) | Market + data-availability research with sources |

---

*Local-only notes (gitignored): `POLICY_LOCAL.md`, `cluely/` — not part of
the committed tree.*
