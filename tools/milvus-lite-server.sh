#!/usr/bin/env bash
# Start Milvus Lite as a standalone gRPC server — a separate process that owns
# the vector index — and point LionPick at it:
#
#   tools/milvus-lite-server.sh                       # serves data/.milvus/lionpick.db on 127.0.0.1:19530
#   RAG_STORE=milvus RAG_MILVUS_URI=http://127.0.0.1:19530 aaalion backend
#
# Why a separate process (R15): embedded Milvus Lite runs faiss inside our
# process. On macOS torch and faiss-cpu each bundle their own libomp; once both
# are initialized in one process the next faiss call aborts it (OMP Error #15),
# and the suggested KMP_DUPLICATE_LIB_OK=TRUE workaround crashed too when tested.
# Server mode also lets the API, evals and migrations share one index — an
# embedded Lite database file can only be opened by one process at a time.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${RAG_MILVUS_DATA_DIR:-$ROOT/data/.milvus/lionpick.db}"
HOST="${RAG_MILVUS_HOST:-127.0.0.1}"
PORT="${RAG_MILVUS_PORT:-19530}"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
mkdir -p "$(dirname "$DATA_DIR")"
echo "milvus-lite server: data=$DATA_DIR  listen=$HOST:$PORT"
exec "$PY" -m milvus_lite server --data-dir "$DATA_DIR" --host "$HOST" --port "$PORT"
