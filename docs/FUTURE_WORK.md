# Future Work

Things we deliberately deferred during the sprint. Roughly ordered by ROI.

## 陈澍枫 (Shufeng) / iOS

- **Voice input** — `Speech` framework for ASR, transcribed text feeds the chat. ~1.5 days.
- **TTS playback** — `AVSpeechSynthesizer` reads AI replies aloud. ~0.5 day.
- **Shopping cart UI** — `@Observable` cart store + drawer view + CRUD via natural language ("把第二个删掉"). Pairs with bonus 4.1. ~2 days.
- **On-device CoreML rerank** — embed a tiny rerank model (e.g. distilled cross-encoder) to rerank top-20 on device before display. Cuts perceived latency for the cards. ~3 days.
- **Widget extension** — "今日推荐" homescreen widget pulling from the same backend. Demo flair. ~1 day.
- **Skeleton screens + haptics** — polish for the demo video. ~0.5 day.

## 李雨晟 (Sam) / backend

- **Rate limiting with Redis** — per-IP token bucket. Needed if we open the demo to judges to poke at. ~0.5 day.
- **Observability** — Prometheus metrics on retrieval latency, Doubao latency, token usage. Grafana dashboard. ~1 day.
- **Retry/backoff for Doubao** — exponential backoff with jitter; circuit-breaker after N failures. ~0.5 day.
- **Cache layer** — Redis-backed cache for `(filter, top-k query)` → product ids. Hit ratio target 30%+ in demo. ~0.5 day.
- **gRPC for client** (post-competition) — protobuf schema, swift-grpc client; possibly better streaming. Speculative.
- **Tracing** — OpenTelemetry from iOS → backend → Doubao. ~1 day. Useful for the engineering-quality bonus.

## 管图杰 (Tujie) / RAG

- **Hybrid sparse+dense retrieval** — BM25 (Tantivy/Whoosh) + Qdrant, combined via RRF. Usually +5-10% recall. ~1 day.
- **Learned reranker** — train a small cross-encoder on the golden eval. ~3 days.
- **Query rewriting** — LLM rewrites the user query into a retrieval-friendly form before embedding. ~0.5 day.
- **Recall@k benchmarking dashboard** — track recall over time as we change prompts/filters. ~1 day.
- **Multimodal late-fusion** — combine text and image vectors with learned weights. ~2 days.
- **Negation as filter, not prompt-only** — explicitly extract negative constraints and apply at Qdrant filter level. ~1 day.

## Joint

- **A/B testing infra** — flag-controlled prompt variants; collect win-rate. Useful for the demo to show iterations.
- **User-session telemetry** — anonymized per-session retrieval logs for analysis (requires user consent in the App).
- **One-click deploy** — a `make deploy` that brings up the full stack on a judge's laptop. The PDF asks for private-deployment support; we should make it easy.
- **Bilingual prompt** — let the system reply in English too, for international judges if any.
- **Stress test** — `locust` or `k6` against `/chat/stream`. Bonus per PDF 4.4. ~1 day.

## Things NOT to do (clear out the temptation list)

- Implementing a custom embedding model from scratch.
- Hosting on Kubernetes.
- Adding GraphQL.
- Replacing FastAPI with something "faster".
- Building an admin web dashboard.

Each of those takes ≥ 1 week and adds zero judge-visible value before 06-10.
