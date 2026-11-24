#!/usr/bin/env bash
# Запускает сценарий AgentSociety 2 на Claude по подписке: поднимает прокладку, задаёт переменные каркаса,
# в конце печатает счётчики вызовов и гасит прокладку.
# Пример: MODEL=sonnet polity/run_with_claude.sh packages/agentsociety2/examples/games/02_public_goods.py
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(dirname "$HERE")/.venv/bin/python"
MODEL="${MODEL:-haiku}"
PORT="${PORT:-18765}"
SCRIPT="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
shift

mkdir -p "$HERE/runs" && cd "$HERE/runs"
"$PY" "$HERE/llm/claude_p_shim.py" "$PORT" &
SHIM=$!
trap 'echo "STATS $(curl -s --noproxy "*" "http://127.0.0.1:$PORT/stats" || true)"; kill "$SHIM" 2>/dev/null || true' EXIT
for _ in $(seq 50); do
  curl -s --noproxy '*' -o /dev/null "http://127.0.0.1:$PORT/stats" && break
  sleep 0.1
done

# Прокси убираем только у каркаса: Ray и прокладка общаются по localhost,
# а claude -p берёт прокси из окружения самой прокладки, запущенной выше.
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy -u ALL_PROXY -u all_proxy \
  AGENTSOCIETY_LLM_API_KEY=local \
  AGENTSOCIETY_LLM_API_BASE="http://127.0.0.1:$PORT/v1" \
  AGENTSOCIETY_LLM_MODEL="$MODEL" \
  AGENTSOCIETY_LLM_REQUEST_TIMEOUT=300 \
  "$PY" "$SCRIPT" "$@"
