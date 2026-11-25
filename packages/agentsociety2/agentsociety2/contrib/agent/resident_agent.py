"""Житель города на рельсах: лёгкий агент, один вызов модели на волну месяца.

Ход: спросить у среды, что житель видит; дать модели описание человека, увиденное и список действий;
отправить выбранные действия в среду. Запросы к среде написаны постоянным текстом, маршрутизатор переводит
каждый из них один раз. Своей памяти у агента нет: всё, что житель помнит о городе, приходит в month_view.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agentsociety2.agent.base import AgentBase
from agentsociety2.contrib.env.city_rails import resident

VIEW = ("Please call month_view() of CityRailsEnv with agent_id from ctx['agent_id'] and store the returned dict "
        "in results['view'].")
SUBMIT = ("Please call submit_actions() of CityRailsEnv with agent_id from ctx['agent_id'] and actions taken as is from "
          "ctx['variables']['actions'], and store the returned dict in results['outcome'].")


class ResidentAgent(AgentBase):
    """Resident of the town on rails: one model call per wave of the month."""

    @classmethod
    def init_description(cls) -> str:
        return (f"{cls.__name__}: resident of CityRailsEnv.\n**Initialization Parameters:** id (int), "
                "profile (dict made by the town: name, age, job, temper, kept, villain). No config keys.\n")

    @classmethod
    def create(cls, workspace_path: Path, profile: dict, config: dict) -> None:
        workspace_path = Path(workspace_path)
        workspace_path.mkdir(parents=True, exist_ok=True)
        (workspace_path / "config.json").write_text(json.dumps(config or {}, ensure_ascii=False), encoding="utf-8")
        agent_id = int(profile.get("agent_id", 0))
        (workspace_path / "AGENT.json").write_text(json.dumps(
            {"id": agent_id, "name": str(profile.get("name") or f"Agent_{agent_id}"), "profile": profile, "step_count": 0},
            ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    async def from_workspace(cls, workspace_path: Path, service_proxy: Any) -> "ResidentAgent":
        agent = cls()
        await agent._restore(workspace_path, service_proxy)
        return agent

    async def _restore(self, workspace_path: Path, service_proxy: Any) -> None:
        meta = json.loads((Path(workspace_path) / "AGENT.json").read_text(encoding="utf-8"))
        self._id = int(meta.get("agent_id", meta.get("id", 0)))
        self._profile = meta.get("profile", {})
        self._name = meta.get("name") or f"Agent_{self._id}"
        self._config = {}
        self._bind_services(service_proxy)
        self._step_count = int(meta.get("step_count", 0))

    async def to_workspace(self, workspace_path: Path) -> None:
        (Path(workspace_path) / "AGENT.json").write_text(json.dumps(
            {"id": self._id, "name": self._name, "profile": self.get_profile(), "step_count": self._step_count},
            ensure_ascii=False, indent=2), encoding="utf-8")

    async def ask(self, message: str, readonly: bool = True) -> str:
        response = await self.acompletion([{"role": "user", "content": f"{resident.PREDICT}\nThe resident: "
                                            f"{resident.describe(self._profile)}.\n\n{message}"}], stream=False)
        return response.choices[0].message.content or ""

    async def step(self, tick: int, t: datetime) -> str:
        self._step_count += 1
        results, answer = await self.ask_env({}, VIEW, readonly=False, template_mode=True)
        view = results.get("view") if isinstance(results, dict) else None
        if not isinstance(view, dict):
            raise RuntimeError(f"[{self.name}] среда не отдала month_view: {str(answer)[:200]}")
        if view.get("over") or view.get("idle"):
            return f"[{self.name}] нет хода"
        tools = resident.tools(view)
        try:
            response = await self.acompletion(
                [{"role": "system", "content": resident.system_prompt(self._profile, view)},
                 {"role": "user", "content": resident.user_prompt(view)}],
                stream=False, tools=tools, tool_choice="auto", max_retries=2, max_delay=20.0)
            actions = resident.parse(getattr(response.choices[0].message, "tool_calls", None))
        except Exception as exc:  # отказ модели не роняет прогон, но остаётся в записи
            actions = [{"type": "model_error", "note": f"{type(exc).__name__}: {exc}"[:200]}]
        if not actions:
            return f"[{self.name}] ничего не сделал"
        results, answer = await self.ask_env({"variables": {"actions": actions}}, SUBMIT, readonly=False, template_mode=True)
        self._ensure_env_ask_ok(results, answer, op="submit_actions")
        return f"[{self.name}] действий: {len(actions)}"


__all__ = ["ResidentAgent"]
