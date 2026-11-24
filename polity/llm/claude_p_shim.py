"""OpenAI-совместимая прокладка к `claude -p`: Claude по подписке как модель AgentSociety 2.

AgentSociety 2 ходит в модель по HTTP через LiteLLM (`POST /v1/chat/completions`). Прокладка принимает
такой запрос, выполняет его одним вызовом `claude -p` и возвращает ответ в формате OpenAI, включая
`tool_calls`. Ответы кэшируются: повтор прогона с теми же промптами не тратит квоту и даёт те же ответы.

Запуск:              python claude_p_shim.py [порт]
Проверка без модели: python claude_p_shim.py --selftest
Счётчики:            GET /stats
Настройки (переменные окружения): SHIM_CACHE, SHIM_LOG, SHIM_PROMPTS, SHIM_MAX_CALLS,
SHIM_CONCURRENCY, SHIM_TIMEOUT, SHIM_EFFORT.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / ".cache"
CACHE_PATH = Path(os.getenv("SHIM_CACHE", STATE_DIR / "llm.sqlite"))
LOG_PATH = Path(os.getenv("SHIM_LOG", STATE_DIR / "calls.jsonl"))
PROMPTS_PATH = os.getenv("SHIM_PROMPTS")  # полный текст запросов: для разбора промптов
MAX_CALLS = int(os.getenv("SHIM_MAX_CALLS", "0"))  # предел настоящих вызовов модели, 0: без предела
TIMEOUT = float(os.getenv("SHIM_TIMEOUT", "240"))
EFFORT = os.getenv("SHIM_EFFORT", "low")
SEMAPHORE = threading.Semaphore(int(os.getenv("SHIM_CONCURRENCY", "8")))
LOCK = threading.Lock()
STATS = {"calls": 0, "cache_hits": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}
# claude -p подмешивает в запрос индекс памяти Claude Code своей рабочей папки, поэтому папка пустая
EMPTY_CWD = Path(tempfile.gettempdir()) / "polity-claude-p-empty"


class CallLimit(RuntimeError):
    pass


def find_claude() -> str:
    found = shutil.which("claude")
    if found:
        return found
    bundled = sorted(
        glob.glob(os.path.expanduser(
            "~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"
        )),
        key=os.path.getmtime,
    )
    if not bundled:
        raise FileNotFoundError("claude не найден: установите Claude Code")
    return bundled[-1]


def text_of(content) -> str:
    """Содержимое сообщения OpenAI: строка или список частей."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))


def render(messages: list[dict]) -> tuple[str, str]:
    """Сообщения чата -> (системный промпт, текст диалога для stdin)."""
    system = "\n\n".join(text_of(m.get("content")) for m in messages if m.get("role") == "system")
    parts = []
    for m in messages:
        role, body = m.get("role"), text_of(m.get("content"))
        if role == "user":
            parts.append(f"<user>\n{body}\n</user>")
        elif role == "assistant":
            calls = [
                {"id": c.get("id"), "name": c["function"]["name"], "arguments": c["function"].get("arguments")}
                for c in (m.get("tool_calls") or [])
            ]
            if calls:
                body = (body + "\n" if body else "") + json.dumps({"tool_calls": calls}, ensure_ascii=False)
            parts.append(f"<assistant>\n{body}\n</assistant>")
        elif role == "tool":
            parts.append(f'<tool_result id="{m.get("tool_call_id", "")}">\n{body}\n</tool_result>')
    return system, "\n\n".join(parts)


def tool_contract(tools: list[dict]) -> tuple[str, dict]:
    """Описание инструментов для системного промпта и JSON-схема ответа."""
    functions = [t["function"] for t in tools if t.get("type") == "function"]
    text = (
        "<tool_protocol>\n"
        "You act only through tool calls. Available tools (name, description, JSON Schema of arguments):\n"
        + json.dumps(functions, ensure_ascii=False)
        + '\nReply with one JSON object {"tool_calls": [{"name": <tool name>, "arguments": {...}}]} '
        "that follows the schema of the chosen tool. Every tool listed here is available: the simulation "
        "runtime executes your calls after you reply and returns the results in the next turn. Earlier turns "
        "of this conversation are given as <assistant> and <tool_result> blocks.\n</tool_protocol>"
    )
    schema = {
        "type": "object",
        "properties": {
            "tool_calls": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": [f["name"] for f in functions]},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                },
            }
        },
        "required": ["tool_calls"],
    }
    return text, schema


def ask_claude(model: str, system: str, user: str, schema: dict | None) -> dict:
    """Один вызов `claude -p`: инструменты и настройки пользователя отключены, системный промпт свой."""
    args = [
        find_claude(), "-p", "--model", model, "--effort", EFFORT, "--tools", "",
        "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
        "--output-format", "json", "--system-prompt", system or "You are a helpful assistant.",
    ]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema)]
    EMPTY_CWD.mkdir(exist_ok=True)
    last = ""
    for attempt in range(3):
        try:
            done = subprocess.run(args, input=user.encode(), capture_output=True, timeout=TIMEOUT, cwd=EMPTY_CWD)
            data = json.loads(done.stdout)
        except subprocess.TimeoutExpired:
            last = "превышено время ожидания"
            continue
        except json.JSONDecodeError:
            last = (done.stderr or done.stdout).decode(errors="replace")[:300]
            time.sleep(2**attempt)
            continue
        if data.get("is_error"):
            last = str(data.get("result"))[:300]
            time.sleep(2**attempt)
            continue
        return data
    raise RuntimeError(f"claude -p не ответил: {last}")


def _db() -> sqlite3.Connection:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CACHE_PATH, timeout=30)
    db.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    return db


def cache_get(key: str) -> dict | None:
    with LOCK, _db() as db:
        row = db.execute("SELECT value FROM responses WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def cache_put(key: str, value: dict) -> None:
    with LOCK, _db() as db:
        db.execute("INSERT OR REPLACE INTO responses VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False)))


def complete(body: dict) -> dict:
    model = str(body.get("model") or "haiku").split("/")[-1]
    system, user = render(body.get("messages") or [])
    schema = None
    if body.get("tools"):
        contract, schema = tool_contract(body["tools"])
        system = f"{system}\n\n{contract}"
    key = hashlib.sha256(json.dumps([model, EFFORT, system, user, schema], ensure_ascii=False).encode()).hexdigest()
    if (cached := cache_get(key)) is not None:
        with LOCK:
            STATS["cache_hits"] += 1
        return cached

    with LOCK:
        if MAX_CALLS and STATS["calls"] >= MAX_CALLS:
            raise CallLimit(f"достигнут предел вызовов модели: {MAX_CALLS}")
        STATS["calls"] += 1
    started = time.time()
    with SEMAPHORE:
        data = ask_claude(model, system, user, schema)
    usage = data.get("usage") or {}
    cached_tokens = int(usage.get("cache_read_input_tokens", 0))
    prompt_tokens = int(usage.get("input_tokens", 0)) + int(usage.get("cache_creation_input_tokens", 0)) + cached_tokens
    completion_tokens = int(usage.get("output_tokens", 0))

    message: dict = {"role": "assistant", "content": None}
    finish = "stop"
    structured = data.get("structured_output") if schema is not None else None
    if isinstance(structured, dict) and structured.get("tool_calls"):
        # номера вызовов выводятся из ключа: они попадают в следующие промпты, и случайные сломали бы кэш
        message["tool_calls"] = [
            {
                "id": f"call_{key[:10]}_{i}",
                "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False)},
            }
            for i, c in enumerate(structured["tool_calls"])
        ]
        finish = "tool_calls"
    else:
        message["content"] = str(data.get("result") or "")
    response = {
        "id": f"chatcmpl-{key[:16]}",
        "object": "chat.completion",
        "created": int(started),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
        },
    }
    cache_put(key, response)

    seconds = round(time.time() - started, 2)
    cost = float(data.get("total_cost_usd") or 0.0)  # по ценам API; по подписке деньги не списываются
    record = {
        "t": round(started, 2), "seconds": seconds, "model": model, "tools": bool(schema),
        "prompt_tokens": prompt_tokens, "cached_tokens": cached_tokens, "completion_tokens": completion_tokens,
        "cost_usd": cost, "system_chars": len(system), "user_chars": len(user),
        "answer": message.get("tool_calls") or message["content"],
    }
    with LOCK:
        STATS["cost_usd"] += cost
        STATS["prompt_tokens"] += prompt_tokens
        STATS["completion_tokens"] += completion_tokens
        STATS["seconds"] += seconds
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if PROMPTS_PATH:
            with open(PROMPTS_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": record["t"], "system": system, "user": user,
                                    "answer": record["answer"]}, ensure_ascii=False) + "\n")
    return response


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:  # всё важное пишется в журнал вызовов
        return

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.rstrip("/").endswith("/stats"):
            with LOCK:
                self._send(200, {**STATS, "cost_usd": round(STATS["cost_usd"], 4), "seconds": round(STATS["seconds"], 1)})
        else:
            self._send(404, {"error": {"message": f"не поддерживается: {self.path}", "type": "not_found"}})

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            # эмбеддингов у claude -p нет: маршрутизатор AgentSociety 2 обходится точным совпадением текста
            self._send(404, {"error": {"message": f"не поддерживается: {self.path}", "type": "not_found"}})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": {"message": f"тело запроса не JSON: {exc}", "type": "invalid_request_error"}})
            return
        try:
            self._send(200, complete(body))
        except CallLimit as exc:
            self._send(400, {"error": {"message": str(exc), "type": "call_limit"}})
        except Exception as exc:
            self._send(500, {"error": {"message": str(exc), "type": "shim_error"}})


def selftest() -> None:
    """Кэш и предел вызовов на подменной модели, без `claude` и без сети."""
    global CACHE_PATH, LOG_PATH, MAX_CALLS, ask_claude
    with tempfile.TemporaryDirectory() as tmp:
        CACHE_PATH, LOG_PATH, MAX_CALLS = Path(tmp) / "c.sqlite", Path(tmp) / "calls.jsonl", 2
        asked = []

        def fake(model, system, user, schema):
            asked.append(user)
            if schema is None:
                return {"result": "Paris", "usage": {"input_tokens": 5, "output_tokens": 1}, "total_cost_usd": 0.001}
            return {"structured_output": {"tool_calls": [{"name": "finish", "arguments": {"answer": "B"}}]}, "usage": {}}

        ask_claude = fake
        tools = [{"type": "function", "function": {"name": "finish", "parameters": {"type": "object"}}}]
        body = {"model": "haiku", "messages": [{"role": "user", "content": "vote"}], "tools": tools}
        first, second = complete(body), complete(body)
        assert first == second and len(asked) == 1, "повтор запроса должен браться из кэша"
        call = first["choices"][0]["message"]["tool_calls"][0]
        assert call["function"] == {"name": "finish", "arguments": '{"answer": "B"}'} and call["id"].endswith("_0")
        assert complete({"model": "haiku", "messages": [{"role": "user", "content": "capital?"}]})["choices"][0]["message"]["content"] == "Paris"
        assert STATS["calls"] == 2 and STATS["cache_hits"] == 1
        try:
            complete({"model": "haiku", "messages": [{"role": "user", "content": "third"}]})
        except CallLimit:
            pass
        else:
            raise AssertionError("третий настоящий вызов должен упереться в предел")
    print("selftest: ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
        print(f"claude -p shim: http://127.0.0.1:{port}/v1  claude={find_claude()}  cache={CACHE_PATH}", flush=True)
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
