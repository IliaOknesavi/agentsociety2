"""Среда AgentSociety 2 для города на рельсах: тонкая обёртка над миром из world.py.

Один шаг общества это одна волна месяца. У жителя два входа в среду: month_view (что он видит) и
submit_actions (что он делает за волну). Обе функции выдерживают повторный вызов в той же волне.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from agentsociety2.env import EnvBase, tool
from agentsociety2.storage import ColumnDef
from agentsociety2.storage.workspace_state import atomic_write_text

from . import resident
from .world import City, Config


class CityRailsEnv(EnvBase):
    """Town with the same rules for everyone: time, feed, money, one law a month; the form of power sets who decides."""

    _env_state_columns: ClassVar[list[ColumnDef]] = [
        ColumnDef("month", "INTEGER", nullable=False),
        ColumnDef("phase", "TEXT", nullable=False),
        ColumnDef("fund", "REAL", nullable=False),
        ColumnDef("laws_passed", "INTEGER", nullable=False),
        ColumnDef("posts", "INTEGER", nullable=False),
    ]

    @classmethod
    def is_concurrency_safe(cls) -> bool:
        return True

    def __init__(self, residents: int = 12, months: int = 6, form: str = "direct", seats: int = 0,
                 villain_share: float = 0.15, seed: int = 1, threshold: float = 0.5, record_path: str = "",
                 source: str = "жители на модели", population: str = "synthetic", twins_path: str = "",
                 bulletin: bool = False, hall: bool = True):
        """
        :param residents: number of residents; agent ids are 1..residents
        :param months: length of the run in months
        :param form: who decides: direct, council or lottery
        :param record_path: file that receives the full record of the run after every wave
        :param hall: those who have the right to decide share a channel only they can write in and read
        :param bulletin: the town shows its calculation of each law to every resident for free
        :param population: synthetic residents or twins: people from the Twin-2K-500 survey, read from twins_path
        """
        super().__init__()
        self.city = City(Config(residents=residents, months=months, form=form, seats=seats,
                                villain_share=villain_share, seed=seed, threshold=threshold,
                                population=population, twins_path=twins_path, bulletin=bulletin, hall=hall))
        self.city.source = source
        self.record_path = record_path
        self._lock = asyncio.Lock()
        self._step_counter = 0

    @classmethod
    def description(cls) -> str:
        return "Town on rails: residents split free hours, read a feed, move money and decide one law a month."

    @classmethod
    def init_description(cls) -> str:
        return (f"{cls.__name__}: town with equal rules for all residents and three forms of power.\n"
                "**Initialization Parameters:** residents (int), months (int), form (direct|council|lottery), seats (int), "
                "villain_share (float), seed (int), threshold (float), record_path (str).\n")

    @tool(readonly=False)
    async def month_view(self, agent_id: int) -> dict:
        """
        Everything the resident sees in the current wave of the month: own money and hours, the law of the month,
        the part of the feed they have time to read, private messages, incoming transfers.
        Reading the feed counts views, so the tool is not read-only; calling it again in the same wave is harmless.

        :param agent_id: the agent id, taken from ctx['agent_id']
        :returns: dict with the view; key 'over' is True when the run has ended
        """
        async with self._lock:
            if self.city.done:
                return {"over": True}
            i = int(agent_id) - 1
            view = self.city.view(i)
            if not view["idle"]:  # то, что модель получит на вход, остаётся в записи прогона
                self.city.note_prompt(i, resident.user_prompt(view), resident.system_prompt(self.city.people[i], view))
            return view

    @tool(readonly=False)
    async def submit_actions(self, agent_id: int, actions: list[dict]) -> dict:
        """
        Carry out the resident's actions for the current wave. Each action is a dict with key 'type':
        plan, post, comment, follow, message, transfer, vote, vote_candidate. Calling again in the same wave changes nothing.

        :param agent_id: the agent id, taken from ctx['agent_id']
        :param actions: list of action dicts, taken as is from ctx['variables']['actions']
        :returns: dict with status 'success', the actions done and the actions refused
        """
        async with self._lock:
            if self.city.done:
                return {"status": "success", "done": [], "refused": ["прогон окончен"]}
            return self.city.submit(int(agent_id) - 1, actions)

    def _dump(self) -> None:
        text = json.dumps(self.city.record(), ensure_ascii=False, default=str)
        if self.record_path:
            path = Path(self.record_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        if self._workspace_root is not None:
            atomic_write_text(self._workspace_root / "state/CITY_RECORD.json", text)

    async def init(self, start_datetime: datetime):
        await super().init(start_datetime)
        self._dump()

    async def step(self, tick: int, t: datetime):
        async with self._lock:
            self.t = t
            self.city.advance()
            self._dump()
            city = self.city
        await self._write_env_state(step=self._step_counter, t=t, month=city.month, phase=str(city.phase),
                                    fund=float(city.eco.fund), laws_passed=sum(m["passed"] for m in city.months),
                                    posts=len(city.posts))
        self._step_counter += 1

    async def to_workspace(self, workspace_path=None) -> None:
        if workspace_path is not None:
            self._bind_workspace(workspace_path)
        if self._workspace_root is None:
            raise RuntimeError("Env module workspace is not bound")
        self._dump()

    async def restore(self, workspace_path) -> bool:
        # shortcut: продолжение прерванного прогона не поддержано, прогон запускается заново; переделать к длинным прогонам
        self._bind_workspace(workspace_path)
        return False


__all__ = ["CityRailsEnv"]
