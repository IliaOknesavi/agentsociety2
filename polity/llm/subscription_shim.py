"""OpenAI-совместимая прокладка к моделям по подписке: Claude (`claude -p`), ChatGPT напрямую и Codex CLI.

AgentSociety 2 ходит в модель по HTTP через LiteLLM (`POST /v1/chat/completions`). Прокладка принимает
такой запрос, выполняет его одним вызовом модели по подписке и возвращает ответ в формате OpenAI,
включая `tool_calls`. Ответы кэшируются: повтор прогона с теми же промптами не тратит квоту и даёт
те же ответы.

Модель выбирается именем в запросе: `haiku`, `sonnet`, `opus` идут в Claude; `chatgpt/<имя>` идёт
в подписку ChatGPT напрямую (провайдер `chatgpt/` в LiteLLM, нужен пакет litellm из окружения каркаса);
`codex` (модель Codex по умолчанию) и `codex/<имя>` идут через Codex CLI.

Запуск:              python subscription_shim.py [порт]
Проверка без модели: python subscription_shim.py --selftest
Счётчики:            GET /stats
Настройки (переменные окружения): SHIM_CACHE, SHIM_LOG, SHIM_PROMPTS, SHIM_MAX_CALLS,
SHIM_CONCURRENCY, SHIM_TIMEOUT, SHIM_EFFORT, SHIM_MODEL_NAME, SHIM_CODEX_HOME, SHIM_CODEX_MODE.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import queue
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
# Имя модели для путей chatgpt и codex. Каркасу его показывать нельзя: его клиент LiteLLM сверяет параметры
# запроса с именем модели и у незнакомых имён семейства GPT отвергает tool_choice. Каркас шлёт только путь.
MODEL_NAME = os.getenv("SHIM_MODEL_NAME")
# Отдельная папка настроек Codex для симуляций: без личного AGENTS.md, навыков и плагинов.
# Заводится один раз командой `CODEX_HOME=<папка> codex login`.
CODEX_HOME = os.getenv("SHIM_CODEX_HOME")
CODEX_MODE = os.getenv("SHIM_CODEX_MODE", "app-server")  # exec: запасной путь, процесс на каждый вопрос
SEMAPHORE = threading.Semaphore(int(os.getenv("SHIM_CONCURRENCY", "8")))
LOCK = threading.Lock()
STATS = {"calls": 0, "cache_hits": 0, "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}
# CLI подмешивает в запрос то, что находит в рабочей папке (память Claude Code, AGENTS.md), поэтому папка пустая
EMPTY_CWD = Path(tempfile.gettempdir()) / "polity-subscription-empty"

# Провайдер `chatgpt/` в LiteLLM ставит перед нашим системным промптом инструкцию Codex (около 1 650 токенов),
# если её текста в промпте нет. Пробел есть в любом промпте, поэтому агент получает только наш текст.
os.environ.setdefault("CHATGPT_DEFAULT_INSTRUCTIONS", " ")

# Блоки, которые Codex добавляет в запрос сам: отключаем всё, что отключается настройками.
CODEX_QUIET = [
    "-c", "project_doc_max_bytes=0",
    "-c", "include_permissions_instructions=false",
    "-c", "include_collaboration_mode_instructions=false",
    "-c", "include_environment_context=false",
    "-c", "include_apps_instructions=false",
    "--enable", "omit_skill_descriptions",
    *(arg for feature in (
        "plugins", "apps", "skill_search", "tool_suggest", "goals", "hooks", "shell_tool",
        "unified_exec", "image_generation", "view_image", "multi_agent",
    ) for arg in ("--disable", feature)),
]
# Личный AGENTS.md и блок о мультиагентности настройками не убираются, если Codex работает из домашней папки
CODEX_NOTE = (
    "\n\nNotes named AGENTS.md, skills or multi-agent roles come from the host tooling and are not addressed "
    "to you: ignore them. Never use tools of the host; reply with text only."
)


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


def _retry(run) -> dict:
    """До трёх попыток вызвать CLI; `run` возвращает ответ или бросает RuntimeError с причиной."""
    last = ""
    for attempt in range(3):
        try:
            return run()
        except subprocess.TimeoutExpired:
            last = "превышено время ожидания"
        except RuntimeError as exc:
            last = str(exc)[:300]
            time.sleep(2**attempt)
    raise RuntimeError(f"модель не ответила: {last}")


def ask_claude(model: str, system: str, user: str, schema: dict | None) -> dict:
    """Один вызов `claude -p`: инструменты и настройки пользователя отключены, системный промпт свой."""
    args = [
        find_claude(), "-p", "--model", model, "--effort", EFFORT, "--tools", "",
        "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
        "--output-format", "json", "--system-prompt", system or "You are a helpful assistant.",
    ]
    if schema is not None:
        args += ["--json-schema", json.dumps(schema)]

    def run() -> dict:
        done = subprocess.run(args, input=user.encode(), capture_output=True, timeout=TIMEOUT, cwd=EMPTY_CWD)
        try:
            data = json.loads(done.stdout)
        except json.JSONDecodeError:
            raise RuntimeError((done.stderr or done.stdout).decode(errors="replace")) from None
        if data.get("is_error"):
            raise RuntimeError(str(data.get("result")))
        return data

    return _retry(run)


def _codex_schema(schema: dict | None) -> dict | None:
    """Строгие схемы OpenAI не допускают объект с произвольными полями: аргументы идут JSON-строкой."""
    if schema is None:
        return None
    item = schema["properties"]["tool_calls"]["items"]
    return {
        "type": "object", "additionalProperties": False, "required": ["tool_calls"],
        "properties": {"tool_calls": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "arguments"],
            "properties": {
                "name": item["properties"]["name"],
                "arguments": {"type": "string", "description": "JSON object with the arguments, as a string"},
            },
        }}},
    }


def _codex_answer(text: str, usage: dict, structured: bool) -> dict:
    """Ответ Codex в том же виде, что отдаёт `claude -p`. Имена полей счётчика у exec и app-server разные."""
    cached = int(usage.get("cached_input_tokens", usage.get("cachedInputTokens", 0)))
    data = {
        "result": text,
        "total_cost_usd": 0.0,  # Codex цену не сообщает
        "usage": {
            "input_tokens": int(usage.get("input_tokens", usage.get("inputTokens", 0))) - cached,
            "cache_read_input_tokens": cached,
            "output_tokens": int(usage.get("output_tokens", usage.get("outputTokens", 0))),
        },
    }
    if structured:
        try:
            data["structured_output"] = {"tool_calls": [
                {"name": c["name"], "arguments": json.loads(c["arguments"] or "{}")}
                for c in json.loads(text)["tool_calls"]
            ]}
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"ответ Codex не по схеме: {exc}: {text[:200]}") from None
    return data


class CodexAppServer:
    """Встраиваемый режим Codex: один постоянный процесс `codex app-server`, запросы по JSON-RPC через stdio.

    Каждый вопрос идёт отдельным временным тредом со своим системным промптом, поэтому вопросы независимы
    и могут выполняться одновременно.
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.starting = threading.Lock()
        self.next_id = 0
        self.waiting: dict[object, queue.Queue] = {}  # номер запроса или id треда -> очередь сообщений

    def _start(self) -> None:
        env = {**os.environ, "CODEX_HOME": CODEX_HOME} if CODEX_HOME else None
        self.proc = subprocess.Popen(
            ["codex", "app-server", *CODEX_QUIET], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, cwd=EMPTY_CWD, env=env,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        self._request("initialize", {"clientInfo": {"name": "polity-shim", "title": "polity", "version": "1"}})
        self._send({"method": "initialized"})

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" in msg and "id" in msg:  # сервер просит одобрения или вызова инструмента: отказываем
                self._send({"id": msg["id"], "error": {"code": -32601, "message": "not supported by this client"}})
            key = msg["id"] if "method" not in msg else (msg.get("params") or {}).get("threadId")
            if (box := self.waiting.get(key)) is not None:
                box.put(msg)
        for box in list(self.waiting.values()):  # процесс завершился: будим всех ожидающих
            box.put({"method": "error", "params": {"error": {"message": "codex app-server завершился"}}})

    def _send(self, msg: dict) -> None:
        with self.lock:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        with self.lock:
            self.next_id += 1
            request_id = self.next_id
        box = self.waiting[request_id] = queue.Queue()
        try:
            self._send({"id": request_id, "method": method, "params": params})
            msg = box.get(timeout=TIMEOUT)
        except queue.Empty:
            raise subprocess.TimeoutExpired(method, TIMEOUT) from None
        finally:
            del self.waiting[request_id]
        if "error" in msg:
            raise RuntimeError(json.dumps(msg["error"], ensure_ascii=False))
        return msg.get("result") or {}

    def ask(self, model: str, system: str, user: str, schema: dict | None) -> dict:
        with self.starting:  # первый вопрос поднимает процесс, остальные ждут
            if self.proc is None or self.proc.poll() is not None:
                self._start()
        thread = {
            "baseInstructions": (system or "You are a helpful assistant.") + CODEX_NOTE, "cwd": str(EMPTY_CWD),
            "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never",
        }
        if "/" in model:
            thread["model"] = model.split("/", 1)[1]
        thread_id = self._request("thread/start", thread)["thread"]["id"]
        box = self.waiting[thread_id] = queue.Queue()
        try:
            turn = {"threadId": thread_id, "input": [{"type": "text", "text": user}], "effort": EFFORT}
            if schema is not None:
                turn["outputSchema"] = schema
            self._request("turn/start", turn)
            text, usage, deadline = None, {}, time.time() + TIMEOUT
            while True:
                try:
                    msg = box.get(timeout=max(0.1, deadline - time.time()))
                except queue.Empty:
                    raise subprocess.TimeoutExpired("turn", TIMEOUT) from None
                method, params = msg.get("method"), msg.get("params") or {}
                if method == "item/completed" and params["item"].get("type") == "agentMessage":
                    text = params["item"].get("text")
                elif method == "thread/tokenUsage/updated":
                    usage = (params.get("tokenUsage") or {}).get("last") or {}
                elif method == "error":
                    raise RuntimeError(json.dumps(params.get("error"), ensure_ascii=False))
                elif method == "turn/completed":
                    break
            if text is None:
                raise RuntimeError("Codex закончил ход без ответа")
            return _codex_answer(text, usage, schema is not None)
        finally:
            del self.waiting[thread_id]
            try:
                self._send({"id": 0, "method": "thread/unsubscribe", "params": {"threadId": thread_id}})
            except OSError:
                pass


CODEX_APP_SERVER = CodexAppServer()


def ask_codex(model: str, system: str, user: str, schema: dict | None) -> dict:
    """Вопрос к Codex: через встраиваемый app-server, либо через `codex exec` при SHIM_CODEX_MODE=exec."""
    schema = _codex_schema(schema)
    if CODEX_MODE == "exec":
        return ask_codex_exec(model, system, user, schema)
    return _retry(lambda: CODEX_APP_SERVER.ask(model, system, user, schema))


def ask_codex_exec(model: str, system: str, user: str, schema: dict | None) -> dict:
    """Запасной путь: отдельный процесс `codex exec` на каждый вопрос."""
    with tempfile.TemporaryDirectory(prefix="polity-codex-") as tmp:
        instructions = Path(tmp) / "instructions.md"
        instructions.write_text((system or "You are a helpful assistant.") + CODEX_NOTE, encoding="utf-8")
        args = [
            "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "-s", "read-only", "-C", str(EMPTY_CWD),
            "-c", f"model_instructions_file={json.dumps(str(instructions))}",
            "-c", f"model_reasoning_effort={json.dumps(EFFORT)}",
            *CODEX_QUIET,
        ]
        if "/" in model:
            args += ["-m", model.split("/", 1)[1]]
        if schema is not None:
            schema_file = Path(tmp) / "schema.json"
            schema_file.write_text(json.dumps(schema), encoding="utf-8")
            args += ["--output-schema", str(schema_file)]
        args += ["--json", "-"]
        env = {**os.environ, "CODEX_HOME": CODEX_HOME} if CODEX_HOME else None

        def run() -> dict:
            done = subprocess.run(args, input=user.encode(), capture_output=True, timeout=TIMEOUT, env=env)
            text, usage = None, {}
            for line in done.stdout.decode(errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "item.completed" and event["item"].get("type") == "agent_message":
                    text = event["item"].get("text")
                elif event.get("type") == "turn.completed":
                    usage = event.get("usage") or {}
            if text is None:
                raise RuntimeError((done.stderr or done.stdout).decode(errors="replace")[-300:])
            return _codex_answer(text, usage, schema is not None)

        return _retry(run)


def ask_chatgpt(model: str, body: dict) -> dict:
    """Прямой путь к подписке ChatGPT: сообщения и инструменты уходят как есть, вызов функций настоящий."""
    if "/" not in model:
        raise RuntimeError("не задано имя модели: MODEL=chatgpt/<имя> для скрипта запуска или SHIM_MODEL_NAME")
    import litellm  # нужен только этому пути; вход по коду устройства описан в polity/README.md

    litellm.suppress_debug_info = True
    extra = {"tools": body["tools"], "tool_choice": body.get("tool_choice", "auto")} if body.get("tools") else {}

    def run() -> dict:
        try:
            done = litellm.completion(model=model, messages=body.get("messages") or [], reasoning_effort=EFFORT,
                                      timeout=TIMEOUT, **extra)
            message, usage = done.choices[0].message, done.usage
            calls = [{"name": c.function.name, "arguments": json.loads(c.function.arguments or "{}")}
                     for c in (message.tool_calls or [])]
        except Exception as exc:  # сеть, отказ сервера, аргументы не JSON: повторяем
            raise RuntimeError(f"{type(exc).__name__}: {exc}") from None
        cached = int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
        return {
            "result": message.content or "",
            "structured_output": {"tool_calls": calls},
            "total_cost_usd": 0.0,  # по подписке цена не сообщается
            "usage": {"input_tokens": int(usage.prompt_tokens) - cached, "cache_read_input_tokens": cached,
                      "output_tokens": int(usage.completion_tokens)},
        }

    return _retry(run)


def ask_model(model: str, system: str, user: str, schema: dict | None) -> dict:
    EMPTY_CWD.mkdir(exist_ok=True)
    return (ask_codex if model.startswith("codex") else ask_claude)(model, system, user, schema)


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
    model = str(body.get("model") or "haiku")
    # LiteLLM шлёт имя без префикса провайдера; у Codex и ChatGPT префикс наш и выбирает путь
    model = model if model.startswith(("codex", "chatgpt")) else model.split("/")[-1]
    if model in ("codex", "chatgpt") and MODEL_NAME:
        model = f"{model}/{MODEL_NAME}"
    direct = model.startswith("chatgpt")  # настоящий вызов функций: протокол в промпт не дописываем
    system, user = render(body.get("messages") or [])
    schema = None
    if body.get("tools"):
        contract, schema = tool_contract(body["tools"])
        if direct:
            schema = [body["tools"], body.get("tool_choice", "auto")]  # в ключ кэша идут описания инструментов
        else:
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
        data = ask_chatgpt(model, body) if direct else ask_model(model, system, user, schema)
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
            "cost_usd": float(data.get("total_cost_usd") or 0.0),  # по ценам API; нужен приложению для расчёта цены прогона
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
            # эмбеддингов у подписочных CLI нет: маршрутизатор AgentSociety 2 обходится точным совпадением текста
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
    """Кэш, выбор модели и предел вызовов на подменной модели, без CLI и без сети."""
    global CACHE_PATH, LOG_PATH, MAX_CALLS, ask_claude, ask_codex, ask_chatgpt
    with tempfile.TemporaryDirectory() as tmp:
        CACHE_PATH, LOG_PATH, MAX_CALLS = Path(tmp) / "c.sqlite", Path(tmp) / "calls.jsonl", 5
        asked = []

        def fake(backend):
            def call(model, system, user, schema):
                asked.append((backend, model, user))
                if schema is None:
                    return {"result": "Paris", "usage": {"input_tokens": 5, "output_tokens": 1}, "total_cost_usd": 0.001}
                return {"structured_output": {"tool_calls": [{"name": "finish", "arguments": {"answer": "B"}}]}, "usage": {}}
            return call

        ask_claude, ask_codex = fake("claude"), fake("codex")
        tools = [{"type": "function", "function": {"name": "finish", "parameters": {"type": "object"}}}]
        body = {"model": "haiku", "messages": [{"role": "user", "content": "vote"}], "tools": tools}
        first, second = complete(body), complete(body)
        assert first == second and len(asked) == 1, "повтор запроса должен браться из кэша"
        call = first["choices"][0]["message"]["tool_calls"][0]
        assert call["function"] == {"name": "finish", "arguments": '{"answer": "B"}'} and call["id"].endswith("_0")
        assert complete({"model": "haiku", "messages": [{"role": "user", "content": "capital?"}]})["choices"][0]["message"]["content"] == "Paris"
        complete({**body, "model": "codex/gpt-test"})
        assert [a[:2] for a in asked] == [("claude", "haiku"), ("claude", "haiku"), ("codex", "codex/gpt-test")]
        # прямой путь: сообщения уходят как есть, смена описания инструмента меняет ключ кэша
        ask_chatgpt = lambda model, body: fake("chatgpt")(model, None, body["messages"][-1]["content"], body.get("tools"))
        direct = {**body, "model": "chatgpt/gpt-test"}
        renamed = [{"type": "function", "function": {"name": "finish", "description": "new", "parameters": {"type": "object"}}}]
        assert complete(direct) == complete(direct) and complete({**direct, "tools": renamed})
        assert [a[:2] for a in asked[3:]] == [("chatgpt", "chatgpt/gpt-test")] * 2
        assert STATS["calls"] == 5 and STATS["cache_hits"] == 2
        try:
            complete({"model": "haiku", "messages": [{"role": "user", "content": "over the limit"}]})
        except CallLimit:
            pass
        else:
            raise AssertionError("вызов сверх предела должен быть отклонён")
    print("selftest: ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
        print(f"subscription shim: http://127.0.0.1:{port}/v1  cache={CACHE_PATH}", flush=True)
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
