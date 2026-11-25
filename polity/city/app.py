"""Приложение «Город на рельсах»: локальный экран для запуска и просмотра прогонов.

  .venv/bin/python polity/city/app.py            открыть http://127.0.0.1:8790

Прогон с жителями по правилам считается сразу в этом процессе. Прогон с жителями на модели запускается
отдельным процессом через AgentSociety 2 и прокладку к подписке; его запись пополняется после каждой волны,
экран показывает её по мере появления.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = HERE / "runs"
TWINS = HERE / "data" / "twins.json"
MODELS = {"chatgpt/gpt-6.2-sol": "gpt-6.2-sol, подписка ChatGPT", "sonnet": "Claude Sonnet, подписка", "haiku": "Claude Haiku, подписка"}
JOBS: dict[str, subprocess.Popen] = {}

# мир не зависит от каркаса, поэтому грузится файлом: импорт пакета agentsociety2 требует настроек модели
_spec = importlib.util.spec_from_file_location(
    "city_world", ROOT / "packages/agentsociety2/agentsociety2/contrib/env/city_rails/world.py")
world = importlib.util.module_from_spec(_spec)
sys.modules["city_world"] = world
_spec.loader.exec_module(world)


def clean(body: dict) -> dict:
    """Параметры прогона из запроса экрана: только известные поля в разумных пределах."""
    form = body.get("form", "direct")
    if form not in world.FORMS:
        raise ValueError("неизвестная форма власти")
    twins = body.get("population") == "twins" and TWINS.is_file()
    return {"population": "twins" if twins else "synthetic", "twins_path": str(TWINS) if twins else "",
            "form": form, "residents": min(200, max(4, int(body.get("residents", 12)))),
            "months": min(24, max(1, int(body.get("months", 6)))),
            "villain_share": min(0.5, max(0.0, float(body.get("villain_share", 0.15)))),
            "seed": int(body.get("seed", 1)) % 100000, "threshold": min(0.9, max(0.5, float(body.get("threshold", 0.5))))}


def run_id(prefix: str, c: dict) -> str:
    return (f"{prefix}-{c['form']}-n{c['residents']}-m{c['months']}-v{round(c['villain_share'] * 100)}"
            f"-t{round(c['threshold'] * 100)}-s{c['seed']}" + ("-twins" if c["population"] == "twins" else ""))


def start(body: dict) -> list[str]:
    RUNS.mkdir(exist_ok=True)
    forms = list(world.FORMS) if body.get("all_forms") else [body.get("form", "direct")]
    ids = []
    for form in forms:
        c = clean(dict(body, form=form))
        if body.get("mode") != "model":
            name = run_id("rules", c)
            (RUNS / f"{name}.json").write_text(json.dumps(world.run_scripted(c), ensure_ascii=False), encoding="utf-8")
        else:
            model = body.get("model", "chatgpt/gpt-6.2-sol")
            if model not in MODELS:
                raise ValueError("неизвестная модель")
            if c["residents"] * c["months"] > 400:
                raise ValueError("для жителей на модели прогон ограничен: жителей × месяцев не больше 400")
            name = run_id("model-" + model.split("/")[-1].replace(".", ""), c)
            if name in JOBS and JOBS[name].poll() is None:
                raise ValueError("такой прогон уже идёт")
            (RUNS / f"{name}.json").unlink(missing_ok=True)
            with socket.socket() as s:  # свободный порт для прокладки этого прогона
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            log = (RUNS / f"{name}.log").open("w")
            JOBS[name] = subprocess.Popen(
                [str(ROOT / "polity/run_with_subscription.sh"), str(HERE / "run_city.py"), "--form", c["form"],
                 "--residents", str(c["residents"]), "--months", str(c["months"]), "--villains", str(c["villain_share"]),
                 "--seed", str(c["seed"]), "--threshold", str(c["threshold"]), "--population", c["population"], "--name", name],
                env=dict(os.environ, MODEL=model, PORT=str(port), SHIM_CONCURRENCY="6"), stdout=log, stderr=subprocess.STDOUT, cwd=ROOT)
        ids.append(name)
    return ids


def listing() -> list[dict]:
    out = []
    for path in sorted(RUNS.glob("*.json"), key=lambda p: -p.stat().st_mtime):
        try:
            r = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        job = JOBS.get(path.stem)
        out.append({"id": path.stem, "config": r["config"], "form_name": r["form_name"], "source": r["source"], "done": r["done"],
                    "month": r["month"], "phase": r["phase"], "metrics": r["metrics"], "seats": r["seats"],
                    "running": bool(job and job.poll() is None), "failed": bool(job and job.poll() not in (None, 0) and not r["done"])})
    known = {x["id"] for x in out}
    for name, job in JOBS.items():  # прогон запущен, а первой записи ещё нет: каркас поднимается около минуты
        if name not in known:
            tail = (RUNS / f"{name}.log").read_text(encoding="utf-8", errors="replace")[-400:] if (RUNS / f"{name}.log").exists() else ""
            out.insert(0, {"id": name, "starting": job.poll() is None, "failed": job.poll() not in (None, 0), "log": tail})
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, kind: str = "application/json; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, (HERE / "web/index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/runs":
            self._json({"runs": listing(), "models": MODELS, "forms": world.FORMS,
                        "proxy": bool(os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")), "twins": TWINS.is_file()})
        elif url.path == "/api/run":
            name = parse_qs(url.query).get("id", [""])[0]
            path = RUNS / f"{Path(name).name}.json"
            if path.is_file():
                self._send(200, path.read_bytes())
            else:
                self._json({"error": "нет такого прогона"}, 404)
        else:
            self._json({"error": "нет такой страницы"}, 404)

    def do_POST(self) -> None:
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            if self.path == "/api/run":
                self._json({"ids": start(body)})
            elif self.path == "/api/delete":
                name = Path(str(body.get("id", ""))).name
                if name in JOBS and JOBS[name].poll() is None:
                    JOBS[name].terminate()
                JOBS.pop(name, None)
                for suffix in (".json", ".log"):
                    (RUNS / f"{name}{suffix}").unlink(missing_ok=True)
                self._json({"ok": True})
            else:
                self._json({"error": "нет такой страницы"}, 404)
        except (ValueError, KeyError, TypeError) as exc:
            self._json({"error": str(exc)}, 400)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    RUNS.mkdir(exist_ok=True)
    print(f"Город на рельсах: http://127.0.0.1:{port}", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    finally:
        for job in JOBS.values():
            if job.poll() is None:
                job.terminate()
