# 20-Day Roadmap

Living document. Update Sunday nights after the weekly sync.

| Date window | Shufeng (iOS) | Sam (backend) | Tujie (RAG) | Joint milestone |
|---|---|---|---|---|
| **05-22 → 05-24** | Xcode project; ChatView skeleton; mock SSE locally consumed | FastAPI skeleton; `/chat/stream` returning fixture tokens | Unzip data; Qdrant up via docker compose; ingest text chunks | Repo scaffolded; all three roles can hit `make dev` and see something running |
| **05-25 → 05-28** | Real SSE wired; message bubbles; ProductCard view | Doubao integration; real streaming; error/timeout handling | Top-k retrieval working; golden eval seeded (20 queries) | First end-to-end demo (text-only) |
| **05-29 → 06-01** | Image picker (PhotosPicker); multi-turn UI polish | Multi-turn context window in prompt; per-message intent classification | Negation / exclusion ("不要日系") handling; comparison prompt template | Multi-turn working end-to-end |
| **06-02 → 06-05** | 拍照找货 client side; upload to `/chat/multimodal` | Image upload endpoint; CLIP-call helper | CLIP image index on A100; vision retrieval; reranker | Photo-search end-to-end |
| **06-06 → 06-08** | UI polish, animations, demo script, skeleton screens | Caching layer for hot queries; rate-limit; Dockerfile hardened | Eval rerun; prompt tuning; document the prompt | Demo video recorded |
| **06-09 → 06-10** | Buffer: bug fixes; defense prep slides | Buffer; private-deploy verification | Buffer; final eval numbers in README | **Submit** |
| **06-11 → 06-19** | Defense rehearsals; respond to judge prompts | Same | Same | Defense |

## Demos planned

| Date | What | Who runs it |
|---|---|---|
| 05-28 (Wed) | Text-only end-to-end smoke | All three |
| 06-01 (Sun) | Multi-turn + negation | All three |
| 06-05 (Thu) | Photo-search | All three |
| 06-08 (Sun) | Full demo dress rehearsal | All three |
| 06-10 (Tue) | Submission cut | All three |

## Risk register

| Risk | Owner | Mitigation |
|---|---|---|
| Doubao rate limit hits during demo | Sam | Cache last N responses; have a recorded backup video. |
| SSE flaky on poor LAN | Shufeng | Add reconnect logic; fall back to non-streaming POST if needed. |
| CLIP index quality poor on Chinese products | Tujie | Use both vision and text retrieval; let LLM combine. |
| Real product data not sourced by 06-01 | All | Hard fallback: manual curation of 50 entries (4 hours, 3-way split). |
| Mac mini M4 doesn't arrive in time | Team | Stick to MacBook for backend; iOS demos directly on iPhone 13. |
