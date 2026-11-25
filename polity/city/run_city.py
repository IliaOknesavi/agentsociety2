"""Прогон города на рельсах в AgentSociety 2 с жителями на модели.

Запуск через прокладку к подписке:
  MODEL=chatgpt/gpt-6.2-sol polity/run_with_subscription.sh polity/city/run_city.py --form council --residents 12 --months 4
Запись прогона пополняется после каждой волны и лежит в polity/city/runs/<имя>.json, её показывает приложение.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("MEM0_TELEMETRY", "False")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from agentsociety2.contrib.env.city_rails import City, Config
from agentsociety2.env import create_env_router_proxy
from agentsociety2.society import AgentSociety

RUNS = Path(__file__).resolve().parent / "runs"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form", default="direct", choices=["direct", "council", "lottery"])
    ap.add_argument("--residents", type=int, default=12)
    ap.add_argument("--months", type=int, default=4)
    ap.add_argument("--villains", type=float, default=0.15, help="доля злодеев")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--population", default="synthetic", choices=["synthetic", "twins"])
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    name = a.name or f"model-{a.form}-n{a.residents}-m{a.months}-v{round(a.villains * 100)}-s{a.seed}"
    record = RUNS / f"{name}.json"
    RUNS.mkdir(exist_ok=True)
    conf = {"residents": a.residents, "months": a.months, "form": a.form, "villain_share": a.villains,
            "seed": a.seed, "threshold": a.threshold, "population": a.population,
            "twins_path": str(RUNS.parent / "data" / "twins.json") if a.population == "twins" else ""}
    people = City(Config(**conf)).people  # тот же город, что построит среда: состав задаётся зерном
    run_dir = Path(f"run_city_{name}")
    shutil.rmtree(run_dir, ignore_errors=True)
    model = os.getenv("SHIM_MODEL_NAME") or os.getenv("AGENTSOCIETY_LLM_MODEL", "")
    env_router = await create_env_router_proxy(
        ["CityRailsEnv"], {"CityRailsEnv": dict(conf, record_path=str(record), source=f"жители на модели {model}")},
        run_dir=run_dir)
    society = AgentSociety(
        agent_specs=[{"id": p["id"] + 1, "profile": dict(p, agent_id=p["id"] + 1), "config": {}} for p in people],
        agent_class_name="ResidentAgent", env_router=env_router, start_t=datetime.now(), run_dir=run_dir, enable_replay=True)
    await society.init()
    started = time.perf_counter()
    for month in range(1, a.months + 1):
        for wave in ("план", "обсуждение", "решение"):
            await society.step(tick=1)
            print(f"месяц {month}, волна «{wave}» пройдена, {time.perf_counter() - started:.0f} с", flush=True)
    print("TOKENS", society.all_token_stats())
    await society.close()
    print(f"RECORD {record}")


if __name__ == "__main__":
    asyncio.run(main())
