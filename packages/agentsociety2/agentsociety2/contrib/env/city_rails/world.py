"""Город на рельсах: правила мира без жителей.

Правила одинаковы для всех жителей: свободное время, цены занятий в часах, лента с показом по вниманию,
деньги с переводами, один закон в месяц с расчётом последствий, который может заказать любой. Форма власти
меняет одно: у кого право решать. Кто кого читает, кто разобрался в законе, кому заплатили и что принято,
получается из действий жителей. Класс не знает, кто им управляет: житель на модели и житель по правилам
ходят через одни и те же view() и submit().

Месяц идёт тремя волнами: plan (житель делит часы), talk (обсуждение), decide (решение), затем расчёт месяца.
"""

from __future__ import annotations

import copy
import json
import random
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

FORMS = {"direct": "Прямое голосование", "council": "Выборный совет", "lottery": "Совет по жребию"}
PHASES = ("plan", "talk", "decide")
COST = {"post": 0.5, "comment": 0.25, "message": 0.25, "study": 4.0, "vote": 0.5}  # часы на одно занятие

# занятие: по-английски для промпта, по-русски для экрана, доход в месяц, свободные часы, владеет ли мастерской
JOBS = [
    ("a nurse", "медсестра", 110, 150, False), ("a builder", "строитель", 120, 150, False),
    ("a shop assistant", "продавец", 90, 150, False), ("a taxi driver", "таксист", 100, 150, False),
    ("a clerk", "служащий", 105, 150, False), ("a teacher", "учитель", 110, 150, False),
    ("an engineer", "инженер", 180, 150, False), ("a workshop owner", "владелец мастерской", 380, 150, True),
    ("a retired factory worker", "пенсионер", 70, 300, False), ("a programmer", "программист", 220, 150, False),
    ("a doctor", "врач", 200, 150, False), ("out of work", "без работы", 40, 300, False),
]
TEMPER = [("cautious", "осторожный"), ("easy-going", "лёгкий"), ("practical", "практичный"),
          ("ambitious", "честолюбивый"), ("talkative", "общительный")]
NAMES = ("Anna Boris Vera Gleb Daria Egor Zoya Ivan Kira Lev Maria Nikita Olga Pavel Rita Stepan Tamara Ulyana "
         "Fedor Elina Yuri Yana Artem Bella Viktor Galina Denis Eva Zakhar Inna Kirill Lidia Mark Nina Oleg Polina "
         "Roman Sofia Timur Alla Vadim Alisa Georgy Dina Emil Zhanna Igor Klara Leonid Mila Naum Oksana Petr Raisa "
         "Semyon Taisia Faina Efim Yulia Yakov").split()
LIFE = [  # будничные события месяца: у жителя есть о чём писать и помимо закона
    "the roof at home started leaking", "their child started school this month", "a fair is coming to the market square on Saturday",
    "the bus line they use was cancelled", "they are selling an old bicycle", "the garden finally recovered after the rains",
    "a neighbour's dog keeps barking at night", "they caught a bad cold and stayed home for a week", "an old friend came to visit",
    "prices at the market went up again", "their team won the town football game", "the workshop asked them to work two extra shifts",
    "they are looking for someone to fix the stove", "a relative is getting married next month",
]
POST0 = 100  # номера записей в ленте начинаются со 100, чтобы их нельзя было спутать с номерами жителей
# Жители из опроса Twin-2K-500. Занятость: по-английски, по-русски, свободные часы, своё ли дело (тогда владеет мастерской)
WORK = {"Full-time employment": ("works full time", "полная занятость", 150, False),
        "Part-time employment": ("works part time", "частичная занятость", 220, False),
        "Unemployed": ("out of work", "без работы", 300, False),
        "Self-employed": ("self-employed, runs a small workshop", "своё дело, мастерская", 150, True),
        "Home-maker": ("a home-maker", "ведёт дом", 220, False), "Student": ("a student", "учится", 220, False),
        "Retired": ("retired", "на пенсии", 300, False)}
INCOME = {"Less than $30,000": 45, "$30,000-$50,000": 75, "$50,000-$75,000": 105, "$75,000-$100,000": 145, "$100,000 or more": 220}
VIEWS = {"Very conservative": "очень консервативные взгляды", "Conservative": "консервативные взгляды", "Moderate": "умеренные взгляды",
         "Liberal": "либеральные взгляды", "Very liberal": "очень либеральные взгляды", "": "взгляды не названы"}
FEMALE = {"Anna", "Vera", "Daria", "Zoya", "Kira", "Maria", "Olga", "Rita", "Tamara", "Ulyana", "Elina", "Yana", "Bella", "Galina",
          "Eva", "Inna", "Lidia", "Nina", "Polina", "Sofia", "Alla", "Alisa", "Dina", "Zhanna", "Klara", "Mila", "Oksana", "Raisa",
          "Taisia", "Faina", "Yulia"}
KEPT = [5, 6, 7, 8, 10]  # прошлый поступок: сколько оставил себе из 10 монет; рвёт связь достатка и честности

# Повестка. kind: impatient (выгода сейчас, убыток потом), deceptive (обещание расходится с расчётом),
# investment (затраты сейчас, выгода потом), redistribution, selfpay. promise: во сколько монет закон оценит
# житель, который видел только обещание.
BILLS = [
    {"id": "payout", "kind": "impatient", "title": "Разовая выплата из фонда: по 60 монет каждому",
     "pitch": "Деньги фонда лежат без дела. Раздадим часть людям уже в этом месяце: по 60 монет каждому жителю.",
     "promise": 60, "effect": {"payout": 60}},
    {"id": "subsidy", "kind": "deceptive", "title": "Поддержка мастерских из фонда",
     "pitch": "Фонд поддержит мастерские города. Это сохранит рабочие места и доходы каждой семьи.",
     "promise": 25, "effect": {"to_owners": 40}},
    {"id": "tax_up", "kind": "investment", "title": "Поднять взнос в фонд на 5 пунктов на год",
     "pitch": "Год платим в фонд больше. Фонд растёт и возвращает это дивидендом каждому.",
     "promise": -10, "effect": {"tax_delta": 0.05, "tax_months": 12}},
    {"id": "manager", "kind": "deceptive", "title": "Передать фонд частному управляющему",
     "pitch": "Опытный управляющий вложит фонд умнее города. Доходность вырастет, дивиденд каждого станет больше.",
     "promise": 30, "effect": {"manager_fee": 0.02}},
    {"id": "holiday", "kind": "impatient", "title": "Отменить взнос в фонд на полгода",
     "pitch": "Полгода никто не платит в фонд. Каждый оставляет себе весь заработок.",
     "promise": 40, "effect": {"tax_holiday": 6}},
    {"id": "invest", "kind": "investment", "title": "Вложить пятую часть фонда в оборудование мастерских",
     "pitch": "Город покупает мастерским оборудование в обмен на рост зарплат: доходы всех работающих вырастут на 8%.",
     "promise": 15, "effect": {"invest_share": 0.2, "wage_growth": 0.08}},
    {"id": "support", "kind": "redistribution", "title": "Пособие тем, кто в долгах: 20 монет в месяц из фонда",
     "pitch": "Жители с долгами получают из фонда 20 монет в месяц, пока не выберутся.",
     "promise": 5, "effect": {"support": 20}},
    {"id": "road", "kind": "investment", "title": "Починить дорогу к рынку за счёт фонда",
     "pitch": "Фонд оплачивает ремонт дороги: по 30 монет с жителя. Подвоз подешевеет, жизнь каждого станет дешевле на 6 монет в месяц.",
     "promise": 10, "effect": {"road_cost": 30, "road_saving": 6}},
    {"id": "audit", "kind": "investment", "title": "Нанять аудитора фонда: 2 монеты с жителя в месяц",
     "pitch": "Каждый платит 2 монеты в месяц за проверку вложений фонда. По оценке города доходность фонда вырастет на один пункт.",
     "promise": -5, "effect": {"audit_fee": 2, "return_delta": 0.01}},
    {"id": "decider_pay", "kind": "selfpay", "title": "Оплата работы тех, кто принимает решения: 40 монет в месяц",
     "pitch": "Решения требуют времени. Каждый, кто имеет право решать, получает из фонда 40 монет в месяц.",
     "promise": 10, "effect": {"decider_pay": 40}},
]


@dataclass
class Config:
    residents: int = 12
    months: int = 6
    form: str = "direct"
    seats: int = 0  # 0: по размеру города (3, 5 или 7)
    villain_share: float = 0.15
    seed: int = 1
    threshold: float = 0.5  # доля «за» среди отданных голосов, выше которой закон принят
    term: int = 3  # на сколько месяцев выпадает жребий
    tax: float = 0.10
    fund_return: float = 0.02
    dividend_rate: float = 0.03
    subsistence: int = 60
    fund_per_head: int = 300
    hours_per_post: int = 10  # сколько часов ленты дают одну показанную запись в волне; допущение
    horizon: int = 12  # на сколько месяцев вперёд считается расчёт последствий
    population: str = "synthetic"  # synthetic: условные жители; twins: люди из опроса Twin-2K-500
    twins_path: str = ""  # файл, который пишет polity/city/export_twins.py


class Economy:
    """Деньги города: заработок, взнос в фонд, доходность фонда, дивиденд поровну. Копируется для расчёта «что будет»."""

    def __init__(self, cfg: Config, wages: list[float], savings: list[float], owners: list[int]):
        self.cfg, self.wages, self.savings, self.owners = cfg, list(wages), list(savings), list(owners)
        self.fund = float(cfg.fund_per_head * len(wages))
        self.tax, self.tax_extra, self.tax_extra_left, self.holiday = cfg.tax, 0.0, 0, 0
        self.fee, self.manager, self.support, self.decider_pay = 0.0, None, 0, 0
        self.living, self.ret, self.audit_fee = float(cfg.subsistence), cfg.fund_return, 0
        self.last: dict = {}

    def apply(self, effect: dict, deciders: list[int], sponsor: int | None) -> None:
        n = len(self.wages)
        if "payout" in effect:
            each = min(effect["payout"], self.fund / n)
            self.fund -= each * n
            self.savings = [s + each for s in self.savings]
        if "to_owners" in effect and self.owners:
            total = min(effect["to_owners"] * n, self.fund)
            self.fund -= total
            for i in self.owners:
                self.savings[i] += total / len(self.owners)
        if "tax_delta" in effect:
            self.tax_extra, self.tax_extra_left = effect["tax_delta"], effect["tax_months"]
        if "manager_fee" in effect:
            self.fee, self.manager = effect["manager_fee"], sponsor
        if "tax_holiday" in effect:
            self.holiday = effect["tax_holiday"]
        if "invest_share" in effect:
            self.fund *= 1 - effect["invest_share"]
            self.wages = [w if i in self.owners else w * (1 + effect["wage_growth"]) for i, w in enumerate(self.wages)]
        if "support" in effect:
            self.support = effect["support"]
        if "decider_pay" in effect:
            self.decider_pay = effect["decider_pay"]
        if "road_cost" in effect:
            self.fund -= min(self.fund, effect["road_cost"] * n)
            self.living -= effect["road_saving"]
        if "audit_fee" in effect:
            self.audit_fee, self.ret = effect["audit_fee"], self.ret + effect["return_delta"]

    def month(self, deciders: list[int]) -> None:
        n = len(self.wages)
        tax = 0.0 if self.holiday > 0 else self.tax + (self.tax_extra if self.tax_extra_left > 0 else 0.0)
        self.holiday, self.tax_extra_left = max(0, self.holiday - 1), max(0, self.tax_extra_left - 1)
        for i, w in enumerate(self.wages):
            self.savings[i] += w * (1 - tax) - self.living - self.audit_fee
        self.fund = self.fund * (1 + self.ret) + tax * sum(self.wages)
        if self.fee and self.manager is not None:
            cut = self.fund * self.fee
            self.fund -= cut
            self.savings[self.manager] += cut
        for i in ([k for k in range(n) if self.savings[k] < 0] if self.support else []):
            pay = min(self.support, self.fund)
            self.fund -= pay
            self.savings[i] += pay
        for i in (deciders if self.decider_pay else []):
            pay = min(self.decider_pay, self.fund)
            self.fund -= pay
            self.savings[i] += pay
        dividend = self.fund * self.cfg.dividend_rate / n
        self.fund -= dividend * n
        self.savings = [s + dividend for s in self.savings]
        self.last = {"tax": round(tax, 3), "dividend": round(dividend, 1), "fund": round(self.fund)}

    def wealth(self) -> list[float]:
        """Достаток жителя: его деньги и его равная доля фонда."""
        share = self.fund / len(self.wages)
        return [s + share for s in self.savings]


class City:
    def __init__(self, cfg: Config | dict | None = None):
        self.cfg = cfg if isinstance(cfg, Config) else Config(**(cfg or {}))
        c, rng = self.cfg, random.Random(self.cfg.seed)
        self.rng = rng
        assert c.form in FORMS, c.form
        n = c.residents
        self.seats = c.seats or (3 if n < 16 else 5 if n < 30 else 7)
        jobs = [JOBS[k % len(JOBS)] for k in rng.sample(range(n * 3), n)]
        if not any(j[4] for j in jobs):  # в городе должна быть хотя бы одна мастерская, иначе закон о поддержке мастерских пуст
            for k in rng.sample(range(n), max(1, n // 10)):
                jobs[k] = next(j for j in JOBS if j[4])
        villains = set(rng.sample(range(n), round(n * c.villain_share)))
        self.people = []
        twins = rng.sample(json.loads(Path(c.twins_path).read_text(encoding="utf-8")), n) if c.population == "twins" else []
        women, men = [x for x in NAMES if x in FEMALE], [x for x in NAMES if x not in FEMALE]
        for i, t in enumerate(twins):  # житель из опроса: занятие, доход и взгляды его собственные, имя условное
            en, ru, free, owner = WORK[t["employment"]]
            pool = women if t["sex"] == "Female" else men
            name = pool.pop(0) if pool else f"Resident{i}"
            wage = INCOME[t["income"]]
            desc = (f"{name}, {t['age']} years old, {t['sex'].lower()}, lives in the {t['region']}, {t['education'].lower()}, "
                    f"{t['marital'].lower()}, household of {t['household'].lower()}, {en}, family income last year {t['income']}, "
                    f"political views: {(t['views'] or 'not stated').lower()}, party: {t['party'] or 'none'}"
                    + (f". In an earlier study this person was given $5 to split with a stranger and kept ${t['kept']}" if t["kept"] is not None else ""))
            self.people.append({
                "id": i, "name": name, "age": t["age"], "job": en, "job_ru": ru, "temper": "", "temper_ru": f"{VIEWS[t['views']]}, {t['party']}",
                "wage": wage, "free": free, "owner": owner, "villain": i in villains, "kept": t["kept"], "desc": desc, "twin": t["pid"],
                "interest": round(rng.betavariate(2, 3), 2), "patience": round(rng.uniform(0.15, 0.85), 2),
                "social": round(rng.betavariate(2, 2), 2), "start_savings": round(wage * rng.choice([-2, -0.5, 0.5, 1, 2, 4, 8]) * (3 if owner else 1)),
            })
        for i in range(0 if twins else n):
            en, ru, wage, free, owner = jobs[i]
            temper = TEMPER[rng.randrange(len(TEMPER))]
            savings = round(wage * rng.choice([-2, -0.5, 0.5, 1, 2, 4, 8]) * (3 if owner else 1))
            self.people.append({
                "id": i, "name": NAMES[i % len(NAMES)] + ("" if i < len(NAMES) else str(i)),
                "age": rng.randint(62, 78) if "retired" in en else rng.randint(22, 60), "job": en, "job_ru": ru, "temper": temper[0], "temper_ru": temper[1],
                "wage": wage, "free": free, "owner": owner, "villain": i in villains, "kept": rng.choice(KEPT),
                "interest": round(rng.betavariate(2, 3), 2), "patience": round(rng.uniform(0.15, 0.85), 2),
                "social": round(rng.betavariate(2, 2), 2), "start_savings": savings,
            })
        self.eco = Economy(c, [p["wage"] for p in self.people], [p["start_savings"] for p in self.people],
                           [p["id"] for p in self.people if p["owner"]])
        self.start = copy.deepcopy(self.eco)
        bad = rng.sample([b for b in BILLS if b["kind"] in ("impatient", "deceptive", "selfpay")], 5)
        other = rng.sample([b for b in BILLS if b["kind"] in ("investment", "redistribution")], 5)
        self.agenda = [b for pair in (zip(bad, other) if rng.random() < 0.5 else zip(other, bad)) for b in pair]
        self.month, self.phase, self.done = 1, 0, False
        self.deciders: list[int] = list(range(n)) if c.form == "direct" else []
        self.election: dict | None = {"candidates": [], "votes": {}} if c.form == "council" else None
        self.posts: list[dict] = []
        self.transfers: list[dict] = []
        self.messages: list[dict] = []
        self.follows: dict[int, set[int]] = {i: set() for i in range(n)}
        self.months: list[dict] = []
        self.spent: dict[int, float] = {}
        self._views: dict = {}
        self._outcomes: dict = {}
        self.source = "мир без жителей"
        self._open_month()

    # ---------- ход времени ----------
    def _open_month(self) -> None:
        c, n = self.cfg, self.cfg.residents
        if c.form == "lottery" and (self.month - 1) % c.term == 0:
            self.deciders = sorted(self.rng.sample(range(n), self.seats))
        bill = dict(self.agenda[(self.month - 1) % len(self.agenda)])
        owners = self.eco.owners
        # управляющим фондом становится случайный житель: он получает выгоду от закона и знает об этом
        bill["sponsor"] = self.rng.randrange(n) if bill["id"] == "manager" else None
        bill["beneficiaries"] = owners if bill["id"] == "subsidy" else [bill["sponsor"]] if bill["sponsor"] is not None else []
        bill["effects"] = self._forecast(bill)
        typical = statistics.median(bill["effects"]["total"])
        bill["harmful"] = typical < 0
        # в счёт убыточных и выгодных не идут: перераспределение (расчётом не решается) и законы почти без последствий
        bill["contested"] = bill["kind"] == "redistribution" or abs(typical) < 1
        bill["analysis"] = (
            f"Расчёт на {c.horizon} месяцев. Типичный житель: {typical:+.0f} монет к достатку (деньги на руках и доля фонда), "
            f"из них в первый месяц на руки {statistics.median(bill['effects']['now']):+.0f}. Фонд города к концу срока: "
            f"{bill['effects']['fund']:+.0f} монет. "
            + (f"Получатели выгоды: {', '.join(self.people[i]['name'] for i in bill['beneficiaries'])}, каждый в среднем "
               f"{statistics.mean(bill['effects']['total'][i] for i in bill['beneficiaries']):+.0f}. " if bill["beneficiaries"] else "")
            + ("Для большинства жителей закон убыточен." if bill["harmful"] else "Для большинства жителей закон выгоден."))
        assert len(BILLS) == 10
        self.cur = {"month": self.month, "bill": bill, "hours": {}, "studied": [], "votes": {}, "plans": {},
                    "deciders": list(self.deciders), "events": []}
        self.spent = {i: 0.0 for i in range(n)}

    def _forecast(self, bill: dict) -> dict:
        """Что изменит закон за горизонт: два хода экономики вперёд, с законом и без, разница по каждому жителю."""
        deciders = self.deciders or list(range(self.seats))
        base, with_bill = copy.deepcopy(self.eco), copy.deepcopy(self.eco)
        with_bill.apply(bill["effect"], deciders, bill["sponsor"])
        now_base, now_bill = None, None
        for k in range(self.cfg.horizon):
            base.month(deciders)
            with_bill.month(deciders)
            if k == 0:
                now_base, now_bill = list(base.savings), list(with_bill.savings)
        return {"total": [round(a - b, 1) for a, b in zip(with_bill.wealth(), base.wealth())],
                "now": [round(a - b, 1) for a, b in zip(now_bill, now_base)],
                "fund": round(with_bill.fund - base.fund)}

    def advance(self) -> None:
        """Закрыть волну. После третьей волны: подсчёт голосов, закон, деньги месяца, следующий месяц."""
        if self.done:
            return
        name = PHASES[self.phase]
        if name == "talk" and self.election is not None:
            self._close_election()
        if name != "decide":
            self.phase += 1
            return
        cur, bill = self.cur, self.cur["bill"]
        yes = sum(v == "for" for v in cur["votes"].values())
        no = sum(v == "against" for v in cur["votes"].values())
        cur["passed"] = yes + no > 0 and yes / (yes + no) > self.cfg.threshold
        cur["tally"] = {"for": yes, "against": no, "entitled": len(self.deciders)}
        if cur["passed"]:
            self.eco.apply(bill["effect"], self.deciders, bill["sponsor"])
        self.eco.month(self.deciders)
        cur["economy"] = dict(self.eco.last, savings=[round(s) for s in self.eco.savings])
        cur["deciders"] = list(self.deciders)
        self.months.append(cur)
        self.month, self.phase = self.month + 1, 0
        if self.month > self.cfg.months:
            self.done = True
        else:
            self._open_month()

    def _close_election(self) -> None:
        votes, n = self.election["votes"], self.cfg.residents
        count = {i: 0 for i in self.election["candidates"]}
        for c in votes.values():
            count[c] = count.get(c, 0) + 1
        order = sorted(count, key=lambda i: (-count[i], self.rng.random()))
        rest = [i for i in self.rng.sample(range(n), n) if i not in order]  # если кандидатов меньше мест, добор жребием
        self.deciders = sorted((order + rest)[:self.seats])
        self.cur["election"] = {"candidates": list(self.election["candidates"]), "count": count, "elected": self.deciders}
        self.cur["deciders"] = list(self.deciders)
        self.election = None

    # ---------- что видит житель ----------
    def role(self, i: int) -> str:
        if self.cfg.form == "direct":
            return ""
        if i in self.deciders:
            return "член совета" if self.cfg.form == "council" else "член совета по жребию"
        return ""

    def view(self, i: int) -> dict:
        """Всё, что житель видит в этой волне. Повторный вызов в той же волне отдаёт тот же ответ и не добавляет просмотров."""
        key = (i, self.month, self.phase)
        if key in self._views:
            return self._views[key]
        p, cur, bill, phase = self.people[i], self.cur, self.cur["bill"], PHASES[self.phase]
        feed_hours = cur["plans"].get(i, {}).get("feed_hours", 0 if self.phase else p["free"] * 0.1)
        shown = min(8, int(-(-feed_hours // self.cfg.hours_per_post)))
        fresh = [q for q in self.posts if q["author"] != i and i not in q["seen"]]
        fresh.sort(key=lambda q: -self._rank(q, i))
        feed = []
        for q in fresh[:shown]:
            q["seen"].add(i)
            q["views"] += 1
            feed.append({"post_id": q["id"] + POST0, "author_id": q["author"], "author": self.people[q["author"]]["name"],
                         "author_role": self.role(q["author"]), "text": q["text"],
                         "comments": [{"author": self.people[c["author"]]["name"], "text": c["text"]} for c in q["comments"][-4:]]})
        headlines = [{"post_id": q["id"] + POST0, "author": self.people[q["author"]]["name"], "start": q["text"][:60]} for q in fresh[shown:shown * 2 + 2]]
        last = (self.month, self.phase - 1) if self.phase else (self.month - 1, 2)  # прошлая волна: новое показывается один раз
        fresh_at = lambda x: (x["month"], x["phase"]) == last
        mine = [q for q in self.posts if q["author"] == i and any(fresh_at(c) and c["author"] != i for c in q["comments"])][-2:]
        writers = sorted({m["from"] for m in self.messages if m["to"] == i and fresh_at(m)})
        threads = [{"with_id": w, "with": self.people[w]["name"],
                    "messages": [{"from": "this person" if m["from"] == i else self.people[w]["name"], "text": m["text"]}
                                 for m in self.messages if {m["from"], m["to"]} == {i, w}][-4:]} for w in writers]
        view = {
            "month": self.month, "months": self.cfg.months, "phase": phase, "form": self.cfg.form,
            "me": {"id": i, "name": p["name"], "money": round(self.eco.savings[i]), "income": round(self.eco.wages[i]),
                   "free_hours": p["free"], "hours_left": round(p["free"] - self.spent[i], 2),
                   "role": self.role(i), "can_vote": i in self.deciders and self.election is None},
            "town": {"residents": self.cfg.residents, "fund": round(self.eco.fund), "tax": self.eco.last.get("tax", self.cfg.tax),
                     "dividend": self.eco.last.get("dividend"), "deciders": [self.people[d]["name"] for d in self.deciders] if self.cfg.form != "direct" else "all residents"},
            "last_month": ({"law": self.months[-1]["bill"]["title"], "passed": self.months[-1]["passed"], "tally": self.months[-1]["tally"]} if self.months else None),
            "bill": {"title": bill["title"], "pitch": bill["pitch"], "analysis": bill["analysis"] if i in cur["studied"] else None,
                     "your_gain": ({"over_horizon": bill["effects"]["total"][i], "first_month_cash": bill["effects"]["now"][i]} if i in cur["studied"] else None),
                     "you_benefit": i in bill["beneficiaries"], "votes_cast": len(cur["votes"])},
            "feed": feed, "headlines": headlines,
            "life": random.Random(self.cfg.seed * 100003 + i * 1009 + self.month).choice(LIFE),
            "my_posts": [q["text"] for q in self.posts if q["author"] == i and q["month"] >= self.month - 1][-3:],
            "replies_to_me": [{"post_id": q["id"] + POST0, "comments": [{"author": self.people[c["author"]]["name"], "text": c["text"]} for c in q["comments"][-3:]]} for q in mine],
            "inbox": threads,
            "money_in": [{"from_id": t["from"], "from": self.people[t["from"]]["name"], "amount": t["amount"], "note": t["note"]} for t in self.transfers if t["to"] == i and t["month"] == self.month],
            "people": [{"id": q["id"], "name": q["name"], "job": q["job"], "role": self.role(q["id"])} for q in self.people if q["id"] != i],
            "election": ({"stage": "nominate" if phase == "plan" else "vote", "seats": self.seats,
                          "candidates": [{"id": c, "name": self.people[c]["name"], "job": self.people[c]["job"]} for c in self.election["candidates"]]}
                         if self.election is not None else None),
        }
        # в волнах обсуждения и решения жителю нечего делать, если он ничего не читает, не решает и ему никто не писал
        view["idle"] = bool(self.phase and not feed and not view["me"]["can_vote"] and not view["inbox"] and not view["money_in"]
                            and not view["election"] and not view["replies_to_me"] and i not in cur["studied"])
        self._views[key] = view
        return view

    def _rank(self, q: dict, reader: int) -> float:
        """Порядок ленты: свежесть, отклик, число подписчиков автора, моя подписка. Про право решать лента не знает."""
        age = (self.month - q["month"]) * 3 + (self.phase - q["phase"])
        followers = sum(q["author"] in f for f in self.follows.values())
        return -0.7 * age + 0.5 * len(q["comments"]) + 0.4 * followers + (2.0 if q["author"] in self.follows[reader] else 0.0) + q["jitter"]

    # ---------- что житель делает ----------
    def submit(self, i: int, actions: list[dict]) -> dict:
        """Действия жителя за волну. Повторный вызов в той же волне ничего не меняет и отдаёт прежний итог."""
        key = (i, self.month, self.phase)
        if key in self._outcomes:
            return self._outcomes[key]
        done, refused = [], []
        for a in actions or []:
            try:
                note = self._act(i, dict(a))
                (done if note is None else refused).append(a.get("type") if note is None else f"{a.get('type')}: {note}")
            except (KeyError, ValueError, TypeError) as exc:
                refused.append(f"{a.get('type')}: {exc}")
        out = {"status": "success", "done": done, "refused": refused, "hours_left": round(self.people[i]["free"] - self.spent[i], 2)}
        self._outcomes[key] = out
        return out

    def _spend(self, i: int, hours: float) -> bool:
        if self.spent[i] + hours > self.people[i]["free"]:
            return False
        self.spent[i] += hours
        return True

    def _event(self, i: int, kind: str, **more) -> None:
        self.cur["events"].append({"phase": PHASES[self.phase], "who": i, "kind": kind, **more})

    def _act(self, i: int, a: dict) -> str | None:
        kind, cur, n = a["type"], self.cur, self.cfg.residents
        if kind == "model_error":  # модель не ответила: ход жителя пропал, это видно в записи
            self._event(i, "model_error", note=str(a.get("note", ""))[:200])
            return None
        if kind == "plan":
            if self.phase != 0 or i in cur["plans"]:
                return "план месяца уже принят"
            free = self.people[i]["free"]
            feed, people = max(0, int(a.get("feed_hours", 0))), max(0, int(a.get("people_hours", 0)))
            study = bool(a.get("study_law"))
            if feed + people + (COST["study"] if study else 0) > free:
                feed = max(0, int(free - people - (COST["study"] if study else 0)))
            self.spent[i] += feed + people + (COST["study"] if study else 0)
            cur["plans"][i] = {"feed_hours": feed, "people_hours": people, "study": study}
            cur["hours"][i] = {"feed": feed, "people": people, "study": COST["study"] if study else 0, "public": 0.0}
            if study:
                cur["studied"].append(i)
            if a.get("run_for_council") and self.election is not None and i not in self.election["candidates"]:
                self.election["candidates"].append(i)
                self._event(i, "candidate")
            return None
        cur["hours"].setdefault(i, {"feed": 0, "people": 0, "study": 0, "public": 0.0})
        if kind == "post":
            text = str(a["text"]).strip()[:600]
            if not text or not self._spend(i, COST["post"]):
                return "нет текста или часов"
            self.posts.append({"id": len(self.posts), "author": i, "month": self.month, "phase": self.phase, "text": text,
                               "about_bill": bool(a.get("about_bill", False)), "stance": a.get("stance"), "views": 0,
                               "seen": set(), "comments": [], "jitter": self.rng.random() * 0.5, "role": self.role(i)})
        elif kind == "comment":
            number = int(a["post_id"]) - POST0
            if not 0 <= number < len(self.posts) or not self._spend(i, COST["comment"]):
                return "нет такой записи или часов"
            q = self.posts[number]
            q["comments"].append({"author": i, "text": str(a["text"]).strip()[:300], "month": self.month, "phase": self.phase})
        elif kind == "follow":
            target = int(a["person_id"])
            if target == i or not 0 <= target < n:
                return "нет такого жителя"
            self.follows[i].add(target)
            return None
        elif kind == "message":
            to = int(a["to_id"])
            if to == i or not 0 <= to < n or not self._spend(i, COST["message"]):
                return "нет адресата или часов"
            self.messages.append({"from": i, "to": to, "text": str(a["text"]).strip()[:400], "month": self.month, "phase": self.phase})
        elif kind == "transfer":
            to, amount = int(a["to_id"]), int(a["amount"])
            if to == i or not 0 <= to < n or amount <= 0 or amount > max(0, self.eco.savings[i]):
                return "нет адресата или денег"
            self.eco.savings[i] -= amount
            self.eco.savings[to] += amount
            self.transfers.append({"from": i, "to": to, "amount": amount, "note": str(a.get("note", ""))[:200],
                                   "month": self.month, "phase": self.phase, "to_decider": to in self.deciders and self.cfg.form != "direct"})
        elif kind == "vote":
            if self.phase != 2 or i not in self.deciders or self.election is not None:
                return "сейчас голосовать нельзя"
            if a["choice"] not in ("for", "against", "abstain") or i in cur["votes"] or not self._spend(i, COST["vote"]):
                return "голос уже отдан или нет часов"
            cur["votes"][i] = a["choice"]
        elif kind == "vote_candidate":
            c = int(a["candidate_id"])
            if self.election is None or self.phase != 1 or c not in self.election["candidates"] or i in self.election["votes"]:
                return "выборы сейчас не идут или кандидата нет"
            self.election["votes"][i] = c
            return None
        else:
            return "неизвестное действие"
        if kind in ("post", "comment", "message", "vote"):
            cur["hours"][i]["public"] = round(cur["hours"][i]["public"] + COST[kind], 2)
        return None

    # ---------- запись и метрики ----------
    def record(self) -> dict:
        n, months = self.cfg.residents, self.months
        none, ideal = copy.deepcopy(self.start), copy.deepcopy(self.start)
        for m in months:  # два мира для сравнения: ни одного закона и только выгодные большинству
            if not m["bill"]["harmful"] and not m["bill"]["contested"]:
                ideal.apply(m["bill"]["effect"], m["deciders"], m["bill"]["sponsor"])
            none.month(m["deciders"])
            ideal.month(m["deciders"])
        mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else 0.0

        def ahead(eco: Economy) -> list[float]:  # достаток каждого жителя через горизонт: вложения успевают окупиться
            eco = copy.deepcopy(eco)
            for _ in range(self.cfg.horizon):
                eco.month(self.deciders)
            return eco.wealth()

        # что принятые законы дали типичному жителю: медиана личных разниц с тем же городом без единого закона.
        # Медиана самого достатка не годится: в малом городе в середине списка может оказаться тот, кому закон платит
        base = ahead(none)
        gain = lambda eco: round(statistics.median(a - b for a, b in zip(ahead(eco), base)), 1)

        harmful = [m for m in months if m["bill"]["harmful"] and not m["bill"]["contested"]]
        good = [m for m in months if not m["bill"]["harmful"] and not m["bill"]["contested"]]
        views = {}
        for q in self.posts:
            views[q["author"]] = views.get(q["author"], 0) + q["views"]
        total_views = sum(views.values())
        ever = {d for m in months for d in m["deciders"]} if self.cfg.form != "direct" else set()
        entitled_studied = [len([i for i in m["studied"] if i in m["deciders"]]) / max(1, len(m["deciders"])) for m in months]
        hours = [h for m in months for h in m["hours"].values()]
        villains = [p["id"] for p in self.people if p["villain"]]
        metrics = {
            "laws": len(months), "passed": sum(m["passed"] for m in months),
            "harmful_total": len(harmful), "harmful_passed": sum(m["passed"] for m in harmful),
            "good_total": len(good), "good_passed": sum(m["passed"] for m in good),
            "gain": gain(self.eco), "gain_best": gain(ideal),
            "turnout": mean([(m["tally"]["for"] + m["tally"]["against"]) / max(1, m["tally"]["entitled"]) for m in months]),
            "studied_all": mean([len(m["studied"]) / n for m in months]), "studied_deciders": mean(entitled_studied),
            "posts": len(self.posts), "political_posts": sum(q["about_bill"] for q in self.posts),
            "comments": sum(len(q["comments"]) for q in self.posts), "views": total_views,
            "views_top_author_share": round(max(views.values()) / total_views, 2) if total_views else 0.0,
            "views_deciders_share": round(sum(v for a, v in views.items() if a in ever) / total_views, 2) if total_views and ever else None,
            "posts_deciders_share": round(sum(q["author"] in ever for q in self.posts) / len(self.posts), 2) if self.posts and ever else None,
            "hours_feed": mean([h["feed"] for h in hours]), "hours_study": mean([h["study"] for h in hours]),
            "hours_people": mean([h["people"] for h in hours]), "hours_public": mean([h["public"] for h in hours]),
            "transfers": len(self.transfers), "transfers_sum": sum(t["amount"] for t in self.transfers),
            "transfers_to_deciders": sum(t["to_decider"] for t in self.transfers),
            "messages": len(self.messages), "villains": len(villains),
            "model_errors": sum(e["kind"] == "model_error" for m in months for e in m["events"]),
            "villains_deciding": len([v for v in villains if v in ever]) if self.cfg.form != "direct" else len(villains),
            "transfers_by_villains": sum(t["from"] in villains for t in self.transfers),
        }
        people = [dict(p, money=round(self.eco.savings[p["id"]]), views=views.get(p["id"], 0),
                       followers=sum(p["id"] in f for f in self.follows.values()),
                       posts=sum(q["author"] == p["id"] for q in self.posts),
                       decided_months=sum(p["id"] in m["deciders"] for m in months)) for p in self.people]
        clean = lambda q: {k: v for k, v in q.items() if k not in ("seen", "jitter")}
        return {"config": asdict(self.cfg), "form_name": FORMS[self.cfg.form], "seats": self.seats, "source": self.source,
                "done": self.done, "month": self.month, "phase": PHASES[self.phase], "people": people, "months": months,
                "current": None if self.done else self.cur,
                "posts": [clean(q) for q in self.posts], "transfers": self.transfers, "messages": self.messages, "metrics": metrics}


def describe(p: dict) -> str:
    """Житель одной строкой для промпта. Достаток словами не называется: вместо него числа и прошлый поступок."""
    if p.get("desc"):
        return p["desc"]
    return (f"{p['name']}, {p['age']}, {p['job']}, {p['temper']}. In an earlier study this person was given 10 coins "
            f"to split with a stranger and kept {p['kept']}")


def scripted(view: dict, p: dict, rng: random.Random) -> list[dict]:
    """Житель по простым правилам, без модели. Нужен, чтобы проверять мир и показывать экран; выводов по нему не делается."""
    phase, bill, me = view["phase"], view["bill"], view["me"]
    actions: list[dict] = []
    decider = me["can_vote"] and view["form"] != "direct"
    if phase == "plan":
        study = rng.random() < p["interest"] * (2.0 if decider else 0.7)
        actions.append({"type": "plan", "feed_hours": round(me["free_hours"] * (0.05 + 0.2 * p["social"])),
                        "people_hours": round(me["free_hours"] * 0.25), "study_law": study,
                        "run_for_council": bool(view["election"]) and (p["temper"] == "ambitious" or rng.random() < 0.25)})
        if rng.random() < p["social"] * 0.5:
            actions.append({"type": "post", "about_bill": False, "text": rng.choice([
                "Кто знает хорошего мастера по крышам? Течёт второй год.", "В субботу ярмарка у мастерских, приходите.",
                "Младший пошёл в школу. Время летит.", "Автобус опять отменили, сорок минут пешком.",
                "Продаю велосипед, почти новый.", "Наконец-то дожди кончились, сад ожил."])})
    feed_about = [q for q in view["feed"] if bill["title"][:12] in q["text"] or "закон" in q["text"].lower()]
    if bill["your_gain"]:
        gain = bill["your_gain"]
        value = gain["first_month_cash"] * (1 - p["patience"]) + gain["over_horizon"] * p["patience"]
    else:
        against = sum("против" in q["text"].lower() or "убыт" in q["text"].lower() for q in feed_about)
        value = next(b["promise"] for b in BILLS if b["title"] == bill["title"]) * (1 - min(1.0, against / 2))
    if p["villain"] and bill["you_benefit"]:
        value = 1000
    bought = p["villain"] and any(t["amount"] >= 20 for t in view["money_in"])
    stance = "for" if value > 0 or bought else "against"
    if phase == "talk":
        for q in view["feed"][:2]:
            if rng.random() < 0.25:
                actions.append({"type": "follow", "person_id": q["author_id"]})
            if rng.random() < p["social"] * 0.3:
                actions.append({"type": "comment", "post_id": q["post_id"], "text": rng.choice(["Согласен.", "Не уверен.", "А цифры есть?", "Спасибо, что написали."])})
        if rng.random() < p["interest"] * (1.0 if bill["your_gain"] else 0.4) or (p["villain"] and bill["you_benefit"]):
            text = (f"Про закон «{bill['title']}». " + (
                "Поддерживаю: городу это на пользу." if stance == "for" else "Я против: по расчёту большинству это в убыток."))
            if bill["your_gain"] and not p["villain"]:
                text += f" Мой расчёт за год: {bill['your_gain']['over_horizon']:+.0f} монет."
            actions.append({"type": "post", "about_bill": True, "stance": stance, "text": text})
        if p["villain"] and bill["you_benefit"]:
            targets = [q for q in view["people"] if q["role"]] or view["people"]
            for q in rng.sample(targets, min(len(targets), 3, max(0, me["money"]) // 25)):
                actions.append({"type": "transfer", "to_id": q["id"], "amount": 25, "note": "за поддержку закона"})
                actions.append({"type": "message", "to_id": q["id"], "text": "Поддержите закон, это всем на пользу. Благодарность отправил."})
        if view["election"] and view["election"]["candidates"]:
            actions.append({"type": "vote_candidate", "candidate_id": rng.choice(view["election"]["candidates"])["id"]})
    if phase == "decide" and me["can_vote"] and rng.random() < (0.95 if decider else 0.3 + 0.6 * p["interest"]):
        actions.append({"type": "vote", "choice": stance})
    return actions


def run_scripted(cfg: Config | dict | None = None) -> dict:
    city = City(cfg)
    city.source = "жители по правилам, без модели"
    rng = random.Random(city.cfg.seed + 1000)
    while not city.done:
        for i in rng.sample(range(city.cfg.residents), city.cfg.residents):
            city.submit(i, scripted(city.view(i), city.people[i], rng))
        city.advance()
    return city.record()


if __name__ == "__main__":  # самопроверка: деньги сходятся, формы власти дают решающих, повтор действий безвреден
    for form in FORMS:
        c = City({"form": form, "residents": 20, "months": 6, "seed": 3})
        before = sum(c.eco.savings) + c.eco.fund
        assert c.submit(0, [{"type": "plan", "feed_hours": 20, "people_hours": 10, "study_law": True}])["done"] == ["plan"]
        assert c.submit(0, [{"type": "post", "text": "x"}])["done"] == ["plan"], "повтор в той же волне не должен действовать"
        assert c.view(0) is c.view(0)
        c.submit(1, [{"type": "transfer", "to_id": 2, "amount": 10 ** 9}])
        assert abs(sum(c.eco.savings) + c.eco.fund - before) < 1e-6, "перевод не создаёт денег"
        r = run_scripted({"form": form, "residents": 20, "months": 6, "seed": 3})
        assert all(describe(p) for p in r["people"])
        m = r["metrics"]
        assert r["done"] and len(r["months"]) == 6 and all(len(x["deciders"]) == (20 if form == "direct" else 5) for x in r["months"]), form
        print(f"{form:8} принято {m['passed']}/{m['laws']}, убыточных {m['harmful_passed']}/{m['harmful_total']}, типичному жителю {m['gain']:+} "
              f"(только выгодные дали бы {m['gain_best']:+}), записей {m['posts']}, просмотров {m['views']}, переводов {m['transfers']}")
