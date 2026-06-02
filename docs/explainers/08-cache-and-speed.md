# 08 — Making it fast: cache, prewarm, async-offload

## What is this?

Three different techniques work together to keep our app responsive:

1. **Caching** — if someone asks the same question twice, we don't pay
   to compute the answer twice. We replay the saved answer in 300 ms
   instead of taking 7 seconds.
2. **Prewarming** — the AI models (BGE-zh, the reranker, CLIP) take ~10
   seconds to load. We load them at startup so the FIRST user request
   doesn't pay that cost.
3. **Async-offload** — the AI models are written in PyTorch, which is
   synchronous code. Running them directly would block our web server
   from handling anything else. We push them onto a separate thread.

## Why does it matter?

The user feels two things acutely: how fast the FIRST word appears, and
whether the app feels stuck while waiting for something else.

- Without prewarm: the first chat after the server starts would take ~17
  seconds (10 s model load + 7 s normal). The user assumes the app is
  broken.
- Without caching: a judge demoing the same query twice would burn LLM
  budget and wait the full 7 s each time. Boring and expensive.
- Without async-offload: while one chat is doing retrieval, the cache
  panel poll times out, the next user can't get an answer, the whole
  server feels frozen.

Each one is invisible when working and obvious when broken.

## How we built it

### Caching — the LRU with TTL

Our cache lives in `server/app/services/cache.py`. It's a simple
in-memory ordered dictionary with:

- **Max size**: 200 entries (older ones get evicted when full).
- **TTL**: 600 seconds (10 minutes) — after that, the entry is stale
  and gets refetched.
- **Key**: a SHA-256 hash of `(system_prompt + user_messages_json +
  image_sha)`. Same query + same conversation history + same images =
  same key.

When the chat route gets a request:

1. Compute the key.
2. Look it up in the cache. If found and fresh: replay the saved
   events one at a time with a small artificial delay (15 ms per token)
   so the UX still feels streamed. Cache hit is ~300 ms total vs ~7000
   ms for a fresh LLM call. About **23× faster**.
3. If not found or stale: hit the LLM, save the event list, return.

Cache stats are exposed via `GET /cache/stats` so we can verify hit
rates during demos. The iOS Settings sheet has a panel that polls this
endpoint every 10 seconds.

### A subtle detail — what we DON'T cache

If the LLM call errors out (rate-limited, network blip), we DON'T save
that broken stream. Specifically, the chat route only calls
`cache.put(...)` when the event list contains at least one successful
`delta` event. Saving errors would mean the next user who asks the same
question replays the same error. We'd rather let them retry against
a fresh call.

### Prewarm — loading models at startup

When the FastAPI server starts, it runs a "lifespan" function (think:
boot-up hook). Ours, in `server/app/main.py`, does this:

1. Mark the app as "not ready" (`/ready` returns 503 with details).
2. Load the BGE-zh embedding model. ~3 s on Mac CPU.
3. Load the bge-reranker-base cross-encoder. ~3 s.
4. Load the BM25 corpus and tokenizer. ~1 s.
5. Optionally load CLIP if image retrieval is enabled. ~3 s.
6. Run one "warmup query" through each so PyTorch JITs its kernels.
7. Mark the app as "ready" (`/ready` returns 200).

Total: ~10-15 seconds. After that, every chat request finds the models
already loaded in RAM. We added a `/ready` endpoint that returns 503
("Service Unavailable") with a status block while models are warming
up, so a curious load-balancer or curl knows not to send traffic yet.

This was contributed by Tujie in Round 8.

### Async-offload — keeping the event loop free

FastAPI is async. Every endpoint is an `async def`. The web server can
handle hundreds of in-flight requests if each one mostly waits on I/O
(network calls to the LLM, database queries).

But PyTorch operations are synchronous CPU work — they don't release
the async event loop. If you `await retrieve(query)` and `retrieve` is
secretly a sync 1.5-second CPU computation, the event loop is BLOCKED.
No other request gets handled.

We discovered this the hard way in Round 8.E. The `/cache/stats` panel
on the iOS Settings sheet would time out whenever a chat with multi-image
was in flight — the chat's retrieval step was hogging the event loop for
2-3 seconds at a time.

The fix is one line per call site:

```python
# BEFORE (blocks event loop):
products = top_k(retrieval_query, k=5)

# AFTER (releases event loop):
products = await asyncio.to_thread(top_k, retrieval_query, k=5)
```

`asyncio.to_thread` pushes the function call onto a separate thread pool.
The event loop is free to handle other requests while the thread runs.
We applied this to `top_k`, `top_k_image`, and `normalize_product_prices`
in `server/app/routes/chat.py`.

After the fix: cache panel polls during a 7-second chat return in ~50
ms. No timeouts.

## The big picture — what each technique buys us

```
Without optimization       With our setup
──────────────────────────────────────────────
First request after boot   17 s            7 s   (prewarm saved 10s)
Same query, second time    7 s             0.3 s (cache saved 6.7s)
Cache panel during chat    times out       50 ms (to_thread freed loop)
```

These are independent — caching helps repeat queries, prewarm helps cold
starts, async-offload helps concurrent requests. We need all three.

## Honest limitations

- The cache is in-process memory. If we restart the server (which we do
  every code change), the cache empties. For demos this is fine; for
  production we'd want Redis.
- The 600-second TTL was picked by intuition. We haven't measured what
  hit rate it actually achieves on production-like traffic.
- `asyncio.to_thread` is limited to ~40 worker threads (Python's default
  pool). If we ever needed dozens of CONCURRENT sync calls, we'd need
  to tune the pool. We're nowhere near that.

## Where to dig deeper

- `server/app/services/cache.py` — the LRU + TTL + stats counters.
- `server/app/main.py` — the lifespan prewarm and `/ready` endpoint.
- `server/app/routes/chat.py` — the `asyncio.to_thread` wrappers around
  retrieval calls.
- `server/app/services/retrieval_readiness.py` — the prewarm
  orchestration that loads each model.
- [`07-streaming-replies.md`](07-streaming-replies.md) — how cache hits
  still stream events for UX consistency.
- [`13-what-numbers-mean.md`](13-what-numbers-mean.md) — the actual
  latency numbers we measure.
