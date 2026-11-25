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

FORMS = {"direct": "Прямое голосование", "liquid": "Прямое с делегированием", "council": "Выборный совет", "lottery": "Совет по жребию"}
EVERYONE = ("direct", "liquid")  # формы, где право решать у всех жителей
PHASES = ("plan", "talk", "decide")
RACE = ("nominate", "campaign", "debate", "vote", "runoff")  # раунд выборов совета: выдвижение, кампания, дебаты, голосование, второй тур
COST = {"post": 0.5, "comment": 0.25, "message": 0.25, "study": 4.0, "vote": 0.5, "hall": 0.25}  # часы на одно занятие

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
JOBS = [(en, ru, wage * 50, free, owner) for en, ru, wage, free, owner in JOBS]  # доход в долларах в месяц
NAMES = ("Анна Борис Вера Глеб Дарья Егор Зоя Иван Кира Лев Мария Никита Ольга Павел Рита Степан Тамара Ульяна "
         "Фёдор Элина Юрий Яна Артём Белла Виктор Галина Денис Ева Захар Инна Кирилл Лидия Марк Нина Олег Полина "
         "Роман София Тимур Алла Вадим Алиса Георгий Дина Эмиль Жанна Игорь Клара Леонид Мила Наум Оксана Пётр Раиса "
         "Семён Таисия Фаина Ефим Юлия Яков").split()
POST0 = 100  # номера записей в ленте начинаются со 100, чтобы их нельзя было спутать с номерами жителей
# Жители из опроса Twin-2K-500. Занятость: по-английски, по-русски, свободные часы, своё ли дело (тогда владеет мастерской)
WORK = {"Full-time employment": ("works full time", "полная занятость", 150, False),
        "Part-time employment": ("works part time", "частичная занятость", 220, False),
        "Unemployed": ("out of work", "без работы", 300, False),
        "Self-employed": ("self-employed, runs a small workshop", "своё дело, мастерская", 150, True),
        "Home-maker": ("a home-maker", "ведёт дом", 220, False), "Student": ("a student", "учится", 220, False),
        "Retired": ("retired", "на пенсии", 300, False)}
# доход жителя в месяц: середина ступени дохода семьи из анкеты, делённая на 12; у крайних ступеней взято 22,5 и 132 тысячи
INCOME = {"Less than $30,000": 1900, "$30,000-$50,000": 3300, "$50,000-$75,000": 5200, "$75,000-$100,000": 7300, "$100,000 or more": 11000}
VIEWS = {"Very conservative": "очень консервативные взгляды", "Conservative": "консервативные взгляды", "Moderate": "умеренные взгляды",
         "Liberal": "либеральные взгляды", "Very liberal": "очень либеральные взгляды", "": "взгляды не названы"}
MARITAL = {"Married": "в браке", "Living with a partner": "живёт с партнёром", "Divorced": "в разводе", "Separated": "живёт отдельно от супруга",
           "Widowed": "овдовел", "Never been married": "в браке не был"}
FEMALE = set("Анна Вера Дарья Зоя Кира Мария Ольга Рита Тамара Ульяна Элина Яна Белла Галина Ева Инна Лидия Нина Полина София Алла "
             "Алиса Дина Жанна Клара Мила Оксана Раиса Таисия Фаина Юлия".split())
KEPT = [5, 6, 7, 8, 10]  # прошлый поступок: сколько оставил себе из 10 монет; рвёт связь достатка и честности

# Повестка. kind: impatient (выгода сейчас, убыток потом), deceptive (обещание расходится с расчётом),
# investment (затраты сейчас, выгода потом), redistribution, selfpay. promise: во сколько монет закон оценит
# житель, который видел только обещание.
BILLS = [  # terms: что записано в самом законе, это видит каждый житель
    {"id": "payout", "kind": "impatient", "title": "Разовая выплата из фонда: по $3 000 каждому",
     "pitch": "Деньги фонда лежат без дела. Раздадим часть людям уже в этом месяце: по $3 000 каждому жителю.",
     "terms": "Выплата разовая, поровну всем жителям, из денег фонда.", "promise": 3000, "effect": {"payout": 3000}},
    {"id": "subsidy", "kind": "deceptive", "title": "Поддержка мастерских из фонда",
     "pitch": "Фонд поддержит мастерские города. Это сохранит рабочие места и доходы каждой семьи.",
     "terms": "Из фонда выделяется по $2 000 на каждого жителя города, деньги делятся поровну между владельцами мастерских. Условий о рабочих местах и отчёта закон не содержит.",
     "promise": 1250, "effect": {"to_owners": 2000}},
    {"id": "progressive", "kind": "redistribution", "title": "Взнос в фонд по доходу: 5% с дохода ниже среднего, 15% с дохода выше",
     "pitch": "Сейчас все платят в фонд по 10% дохода. Пусть те, кто зарабатывает меньше половины жителей, платят 5%, а остальные 15%.",
     "terms": "Житель с доходом ниже медианного по городу платит в фонд 5% дохода, остальные 15%. Закон действует бессрочно.",
     "promise": 0, "effect": {"progressive": True}},
    {"id": "manager", "kind": "deceptive", "title": "Передать фонд частному управляющему",
     "pitch": "Опытный управляющий вложит фонд умнее города. Доходность вырастет, дивиденд каждого станет больше.",
     "terms": "Управляющий получает 2% фонда каждый месяц как плату. Срок не ограничен. Обещанная доходность в договоре не закреплена.",
     "promise": 1500, "effect": {"manager_fee": 0.02}},
    {"id": "holiday", "kind": "impatient", "title": "Отменить взнос в фонд на полгода",
     "pitch": "Полгода никто не платит в фонд. Каждый оставляет себе весь заработок.",
     "terms": "Шесть месяцев взнос равен нулю, потом возвращается к 10% дохода. Дивиденд всё это время платится из того, что есть в фонде.",
     "promise": 2000, "effect": {"tax_holiday": 6}},
    {"id": "invest", "kind": "investment", "title": "Вложить пятую часть фонда в оборудование мастерских",
     "pitch": "Город покупает мастерским оборудование в обмен на рост зарплат: доходы всех работающих вырастут на 8%.",
     "terms": "Город тратит пятую часть фонда на оборудование и передаёт его мастерским. Владельцы по договору поднимают доход всем остальным жителям на 8% бессрочно.",
     "promise": 750, "effect": {"invest_share": 0.2, "wage_growth": 0.08}},
    {"id": "support", "kind": "redistribution", "title": "Пособие тем, кто в долгах: $1 000 в месяц из фонда",
     "pitch": "Жители с долгами получают из фонда $1 000 в месяц, пока не выберутся.",
     "terms": "Пособие получает каждый житель, у кого денег меньше нуля, каждый месяц, пока долг не погашен. Срок закона не ограничен.",
     "promise": 250, "effect": {"support": 1000}},
    {"id": "road", "kind": "investment", "title": "Починить дорогу к рынку за счёт фонда",
     "pitch": "Фонд оплачивает ремонт дороги: по $1 500 с жителя. Подвоз подешевеет, жизнь каждого станет дешевле на $300 в месяц.",
     "terms": "Ремонт оплачивается из фонда один раз, $1 500 в расчёте на жителя. Подрядчика город выбирает на открытом конкурсе.",
     "promise": 500, "effect": {"road_cost": 1500, "road_saving": 300}},
    {"id": "clinic", "kind": "investment", "title": "Расширить городскую больницу",
     "pitch": "Фонд платит за больницу на $100 в месяц больше в расчёте на жителя. Каждый житель тратит на лечение на $160 в месяц меньше.",
     "terms": "Расходы фонда на городские службы растут с $500 до $600 на жителя в месяц. Приём и лекарства, за которые жители сейчас платят сами, становятся бесплатными: это $160 в месяц на жителя.",
     "promise": 700, "effect": {"services_up": 100, "living_down": 160}},
    {"id": "owners_exempt", "kind": "deceptive", "title": "Освободить владельцев мастерских от взноса в фонд",
     "pitch": "Мастерские дают городу работу. Освободим их владельцев от взноса: у них останется больше денег на развитие дела.",
     "terms": "Владельцы мастерских перестают платить взнос в фонд. Обязательств нанимать людей или поднимать доходы закон не содержит. Срок не ограничен.",
     "promise": 500, "effect": {"owners_exempt": True}},
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
    council_term: int = 6  # на сколько месяцев избирается совет; перед каждым сроком идёт раунд выборов
    salary: int = 1500  # зарплата члена совета в месяц из фонда: правило города, сам совет её не назначает
    council_hours: int = 10  # сколько часов в месяц член совета тратит на работу в совете
    tax: float = 0.10
    fund_return: float = 0.02
    dividend_rate: float = 0.03
    subsistence: int = 3000  # расходы на жизнь в месяц, доллары
    fund_per_head: int = 15000
    currency: str = "$"
    services: int = 500  # городские службы (больница, школа, дороги, вода): столько фонд платит за жителя в месяц
    reserve_months: int = 6  # запас фонда на службы; дивиденд платится только из того, что сверх запаса
    ambition: float = 0.2  # доля жителей, которые хотели бы участвовать в управлении городом; раздаётся случайно, допущение
    hall: bool = True  # зал тех, кто решает: общий канал, который пишут и читают только имеющие право голоса
    hours_per_hall: int = 2  # сколько часов зала дают одну прочитанную реплику в волне; допущение
    hours_per_post: int = 2  # сколько часов ленты дают одну показанную запись в волне; допущение
    horizon: int = 12  # на сколько месяцев вперёд считается расчёт последствий
    bulletin: bool = False  # институт против обмана: город сам показывает расчёт закона каждому жителю, без 4 часов
    population: str = "synthetic"  # synthetic: условные жители; twins: люди из опроса Twin-2K-500
    twins_path: str = ""  # файл, который пишет polity/city/export_twins.py


class Economy:
    """Деньги города: заработок, взнос в фонд, доходность фонда, дивиденд поровну. Копируется для расчёта «что будет»."""

    def __init__(self, cfg: Config, wages: list[float], savings: list[float], owners: list[int]):
        self.cfg, self.wages, self.savings, self.owners = cfg, list(wages), list(savings), list(owners)
        self.fund = float(cfg.fund_per_head * len(wages))
        self.tax, self.tax_extra, self.tax_extra_left, self.holiday = cfg.tax, 0.0, 0, 0
        self.fee, self.manager, self.support, self.decider_pay, self.salary = 0.0, None, 0, 0, 0
        self.living, self.ret, self.audit_fee = float(cfg.subsistence), cfg.fund_return, 0
        self.services, self.rates = cfg.services, [cfg.tax] * len(self.wages)  # цена служб на жителя и ставка взноса каждого
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
        if "progressive" in effect:
            middle = statistics.median(self.wages)
            self.rates = [0.05 if w < middle else 0.15 for w in self.wages]
        if "owners_exempt" in effect:
            self.rates = [0.0 if i in self.owners else r for i, r in enumerate(self.rates)]
        if "services_up" in effect:
            self.services += effect["services_up"]
            self.living -= effect["living_down"]
        if "audit_fee" in effect:
            self.audit_fee, self.ret = effect["audit_fee"], self.ret + effect["return_delta"]

    def month(self, deciders: list[int]) -> None:
        n = len(self.wages)
        extra = self.tax_extra if self.tax_extra_left > 0 else 0.0
        rates = [0.0 if self.holiday > 0 else r + extra for r in self.rates]
        self.holiday, self.tax_extra_left = max(0, self.holiday - 1), max(0, self.tax_extra_left - 1)
        for i, w in enumerate(self.wages):
            self.savings[i] += w * (1 - rates[i]) - self.living - self.audit_fee
        tax = sum(w * r for w, r in zip(self.wages, rates)) / sum(self.wages)  # средняя ставка взноса по городу
        self.fund = self.fund * (1 + self.ret) + sum(w * r for w, r in zip(self.wages, rates))
        # Фонд это бюджет города: он оплачивает службы. Чего не хватило, жители доплачивают сами.
        need = self.services * n
        paid = min(need, max(0.0, self.fund))
        self.fund -= paid
        if need > paid:
            self.savings = [x - (need - paid) / n for x in self.savings]
        if self.fee and self.manager is not None:
            cut = self.fund * self.fee
            self.fund -= cut
            self.savings[self.manager] += cut
        for i in ([k for k in range(n) if self.savings[k] < 0] if self.support else []):
            pay = min(self.support, self.fund)
            self.fund -= pay
            self.savings[i] += pay
        for i in (deciders if self.decider_pay or self.salary else []):
            pay = min(self.decider_pay + self.salary, self.fund)
            self.fund -= pay
            self.savings[i] += pay
        dividend = max(0.0, self.fund - self.services * n * self.cfg.reserve_months) * self.cfg.dividend_rate / n
        self.fund -= dividend * n
        self.savings = [s + dividend for s in self.savings]
        self.last = {"tax": round(tax, 3), "dividend": round(dividend, 1), "fund": round(self.fund), "shortfall": round((need - paid) / n)}

    def wealth(self) -> list[float]:
        """Достаток жителя: его деньги и его равная доля фонда."""
        share = self.fund / len(self.wages)
        return [s + share for s in self.savings]


class City:
    def __init__(self, cfg: Config | dict | None = None):
        self.cfg = cfg if isinstance(cfg, Config) else Config(**(cfg or {}))
        # У каждой части города свой поток случайности от одного зерна: люди, злодеи, повестка, жребий, ход прогона.
        # Поэтому при одном зерне и числе жителей люди и повестка одни и те же, какими бы ни были форма власти,
        # доля злодеев и поведение жителей; злодеи при большей доле включают злодеев при меньшей.
        c, rng = self.cfg, random.Random(f"{self.cfg.seed}/people")
        self.rng = random.Random(f"{self.cfg.seed}/run")
        assert c.form in FORMS, c.form
        n = c.residents
        self.seats = c.seats or (3 if n < 16 else 5 if n < 30 else 7)
        jobs = [JOBS[k % len(JOBS)] for k in rng.sample(range(n * 3), n)]
        if not any(j[4] for j in jobs):  # в городе должна быть хотя бы одна мастерская, иначе закон о поддержке мастерских пуст
            for k in rng.sample(range(n), max(1, n // 10)):
                jobs[k] = next(j for j in JOBS if j[4])
        villains = set(random.Random(f"{c.seed}/villains").sample(range(n), n)[:round(n * c.villain_share)])
        keen = set(random.Random(f"{c.seed}/ambition").sample(range(n), n)[:round(n * c.ambition)])
        self.people = []
        twins = rng.sample(json.loads(Path(c.twins_path).read_text(encoding="utf-8")), n) if c.population == "twins" else []
        women, men = [x for x in NAMES if x in FEMALE], [x for x in NAMES if x not in FEMALE]
        for i, t in enumerate(twins):  # житель из опроса: занятие, доход и взгляды его собственные, имя условное
            en, ru, free, owner = WORK[t["employment"]]
            pool = women if t["sex"] == "Female" else men
            name = pool.pop(0) if pool else f"Житель{i}"
            wage = INCOME[t["income"]]
            desc = (f"{name}, {t['age']} years old, {t['sex'].lower()}, lives in the {t['region']}, {t['education'].lower()}, "
                    f"{t['marital'].lower()}, household of {t['household'].lower()}, {en}, family income last year {t['income']}, "
                    f"political views: {(t['views'] or 'not stated').lower()}, party: {t['party'] or 'none'}"
                    + (f".\n{t['profile']}" if t.get("profile") else f". In an earlier study this person was given $5 to split with a stranger and kept ${t['kept']}" if t["kept"] is not None else ""))
            self.people.append({
                "id": i, "name": name, "age": t["age"], "job": en, "job_ru": ru, "temper": "", "temper_ru": f"{VIEWS[t['views']]}, {t['party']}",
                "wage": wage, "free": free, "owner": owner, "villain": i in villains, "kept": t["kept"], "desc": desc, "twin": t["pid"],
                "keen": t["drive"] >= 6 if "drive" in t else i in keen, "firm": t.get("strong", 0) >= 8, "drive": t.get("drive"),
                "family_ru": f"{MARITAL.get(t['marital'], 'семейное положение не названо')}, в доме живёт {t['household'].lower().replace('more than 4', 'больше 4')} чел.",
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
                "wage": wage, "free": free, "owner": owner, "villain": i in villains, "keen": i in keen, "kept": rng.choice(KEPT),
                "interest": round(rng.betavariate(2, 3), 2), "patience": round(rng.uniform(0.15, 0.85), 2),
                "social": round(rng.betavariate(2, 2), 2), "start_savings": savings,
            })
        self.eco = Economy(c, [p["wage"] for p in self.people], [p["start_savings"] for p in self.people],
                           [p["id"] for p in self.people if p["owner"]])
        self.start = copy.deepcopy(self.eco)
        rng = random.Random(f"{c.seed}/agenda")
        self.manager = rng.randrange(n)  # кто станет управляющим фондом, если такой закон примут: он знает о своей выгоде
        bad = rng.sample([b for b in BILLS if b["kind"] in ("impatient", "deceptive", "selfpay")], 5)
        other = rng.sample([b for b in BILLS if b["kind"] in ("investment", "redistribution")], 5)
        self.agenda = [b for pair in (zip(bad, other) if rng.random() < 0.5 else zip(other, bad)) for b in pair]
        self.month, self.phase, self.done = 1, 0, False
        self.everyone = c.form in EVERYONE
        self.deciders: list[int] = list(range(n)) if self.everyone else []
        self.delegate: dict[int, int] = {}  # отзывное делегирование: кому житель передал свой голос
        self.election = None  # прежние выборы внутри первого месяца заменены отдельным раундом, см. race
        self.race: dict | None = None
        self.elections: list[dict] = []
        order = random.Random(f"{c.seed}/districts").sample(range(n), n)  # округа: от каждого один член совета
        self.district = {i: k % self.seats for k, i in enumerate(order)} if c.form == "council" else {}
        self.eco.salary = c.salary if c.form in ("council", "lottery") else 0
        self.posts: list[dict] = []
        self.hall: list[dict] = []  # реплики в зале тех, кто решает
        self.transfers: list[dict] = []
        self.messages: list[dict] = []
        self.follows: dict[int, set[int]] = {i: set() for i in range(n)}
        self.notes: dict[int, list[str]] = {i: [] for i in range(n)}  # память жителя: его заметки для себя
        self.calls: dict[int, list[dict]] = {i: [] for i in range(n)}  # ходы жителя по волнам: что видел, думал, сделал, сколько стоил
        self.months: list[dict] = []
        self.spent: dict[int, float] = {}
        self._views: dict = {}
        self._outcomes: dict = {}
        self.source = "мир без жителей"
        self._open_month()
        if c.form == "council":
            self._start_race()

    # ---------- ход времени ----------
    def _open_month(self) -> None:
        c, n = self.cfg, self.cfg.residents
        if c.form == "lottery" and (self.month - 1) % c.term == 0:
            self.deciders = sorted(random.Random(f"{c.seed}/lottery/{self.month}").sample(range(n), self.seats))
        bill = dict(self.agenda[(self.month - 1) % len(self.agenda)])
        owners = self.eco.owners
        bill["sponsor"] = self.manager if bill["id"] == "manager" else None
        bill["beneficiaries"] = owners if bill["id"] in ("subsidy", "owners_exempt") else [bill["sponsor"]] if bill["sponsor"] is not None else []
        bill["effects"] = self._forecast(bill)
        e = bill["effects"]
        cash, share, edge = statistics.median(e["cash"]), e["fund_share"], 0.01 * c.fund_per_head
        # Вердикта «выгоден» или «убыточен» у закона нет: доля фонда на руки не выдаётся, и во сколько её ценить, решает житель.
        # Отмечаются только законы, у которых оба числа смотрят в одну сторону.
        bill["both_bad"], bill["both_good"] = cash < -edge and share < -edge, cash > edge and share > edge
        bill["harmful"], bill["contested"] = bill["both_bad"], not (bill["both_bad"] or bill["both_good"])
        usd = lambda x: f"{x:+,.0f} $".replace(",", " ")
        bill["analysis"] = (
            f"Расчёт города на {c.horizon} месяцев, если всё пойдёт так, как записано в законе. Типичному жителю на руки за год: {usd(cash)}, "
            f"из них в первый месяц {usd(statistics.median(e['now']))}. Его доля фонда: {usd(share)}; на руки она не выдаётся, из фонда платится дивиденд. "
            f"За три года: на руки {usd(statistics.median(e['cash3']))}, доля фонда {usd(e['fund_share3'])}. Фонд города через год: {usd(e['fund'])}."
            + (f" Больше всех получают {', '.join(self.people[i]['name'] for i in bill['beneficiaries'])}: в среднем "
               f"{usd(statistics.mean(e['cash'][i] for i in bill['beneficiaries']))} на руки каждому." if bill["beneficiaries"] else ""))
        assert len(BILLS) == 10
        self.cur = {"month": self.month, "bill": bill, "hours": {}, "studied": [], "votes": {}, "plans": {}, "opinions": {},
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
        far_base, far_bill = copy.deepcopy(base), copy.deepcopy(with_bill)  # тот же расчёт на три года: вложения успевают вернуться
        for _ in range(self.cfg.horizon * 2):
            far_base.month(deciders)
            far_bill.month(deciders)
        return {"cash3": [round(a - b) for a, b in zip(far_bill.savings, far_base.savings)], "fund_share3": round((far_bill.fund - far_base.fund) / len(base.wages)),
                "cash": [round(a - b) for a, b in zip(with_bill.savings, base.savings)], "fund_share": round((with_bill.fund - base.fund) / len(base.wages)),
                "total": [round(a - b, 1) for a, b in zip(with_bill.wealth(), base.wealth())],
                "now": [round(a - b, 1) for a, b in zip(now_bill, now_base)],
                "fund": round(with_bill.fund - base.fund)}

    def advance(self) -> None:
        """Закрыть волну. После третьей волны: подсчёт голосов, закон, деньги месяца, следующий месяц."""
        if self.done:
            return
        if self.race:
            self._advance_race()
            return
        name = PHASES[self.phase]
        if name != "decide":
            self.phase += 1
            return
        cur, bill = self.cur, self.cur["bill"]
        cur["delegated"] = {}
        for i in (self.delegate if self.cfg.form == "liquid" else []):  # кто не голосовал сам, голосует как его доверенный
            seen, j = {i}, self.delegate[i]
            while j not in cur["votes"] and j in self.delegate and j not in seen:  # цепочка доверенных без кругов
                seen.add(j)
                j = self.delegate[j]
            if i not in cur["votes"] and j in cur["votes"] and j != i:
                cur["delegated"][i] = j
        cast = list(cur["votes"].values()) + [cur["votes"][j] for j in cur["delegated"].values()]
        yes = sum(v == "for" for v in cast)
        no = sum(v == "against" for v in cast)
        cur["passed"] = yes + no > 0 and yes / (yes + no) > self.cfg.threshold
        cur["tally"] = {"for": yes, "against": no, "entitled": len(self.deciders)}
        # мнение каждого жителя, а не только решающих: так видно, совпало ли решение с волей большинства
        want, oppose = (sum(v == x for v in cur["opinions"].values()) for x in ("for", "against"))
        cur["opinion_tally"] = {"for": want, "against": oppose, "none": self.cfg.residents - want - oppose}
        cur["matches_majority"] = None if want == oppose else cur["passed"] == (want > oppose)
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
            if self.cfg.form == "council" and (self.month - 1) % self.cfg.council_term == 0:
                self._start_race()

    # ---------- раунд выборов совета ----------
    def wave(self) -> str:
        return self.race["stage"] if self.race else PHASES[self.phase]

    def _start_race(self) -> None:
        self.race = {"stage": "nominate", "before_month": self.month, "candidates": {}, "statements": [], "votes": {}, "votes2": {},
                     "runoff": {}, "incumbents": list(self.deciders)}

    def _advance_race(self) -> None:
        """Следующий этап выборов. После голосования: победитель в округе тот, у кого больше половины голосов; иначе второй тур двоих."""
        race, n = self.race, self.cfg.residents
        if race["stage"] in ("nominate", "campaign", "debate"):
            race["stage"] = RACE[RACE.index(race["stage"]) + 1]
            return
        rng, winners, table = random.Random(f"{self.cfg.seed}/race/{self.month}"), {}, []
        for d in range(self.seats):
            people = [i for i in range(n) if self.district[i] == d]
            cands = [c for c in race["candidates"] if self.district[c] == d]
            first = {c: sum(v == c for v in race["votes"].values()) for c in cands}
            row = {"district": d, "residents": people, "candidates": [{"id": c, "program": race["candidates"][c]["program"], "votes": first[c]} for c in cands]}
            if race["stage"] == "vote":
                top = sorted(cands, key=lambda c: (-first[c], rng.random()))
                if not cands:
                    winners[d], row["by_lot"] = rng.choice(people), True  # в округе никто не выдвинулся: место по жребию
                elif len(cands) <= 2 or first[top[0]] * 2 > sum(first.values()):
                    winners[d] = top[0]
                else:
                    race["runoff"][d] = top[:2]
            else:
                pair = race["runoff"].get(d)
                if pair:
                    second = {c: sum(v == c for v in race["votes2"].values()) for c in pair}
                    winners[d] = sorted(pair, key=lambda c: (-second[c], -first[c], rng.random()))[0]
                    row["runoff"] = second
                else:
                    winners[d] = race["winners"][d]
                    row["by_lot"] = race["lots"].get(d, False)
            table.append(row)
        if race["stage"] == "vote":
            race["winners"], race["lots"] = winners, {r["district"]: r.get("by_lot", False) for r in table}
            if race["runoff"]:
                race["stage"] = "runoff"
                return
        for row in table:
            row["winner"] = winners[row["district"]]
        self.deciders = sorted(winners.values())
        self.elections.append({"before_month": race["before_month"], "districts": table, "statements": race["statements"],
                               "turnout": round(len(race["votes"]) / n, 2), "elected": self.deciders, "incumbents": race["incumbents"]})
        self.cur["deciders"], self.cur["election"] = list(self.deciders), {"elected": self.deciders, "candidates": sorted(race["candidates"]), "count": {}}
        self.race = None
        self.spent = {i: 0.0 for i in range(n)}  # у раунда выборов своё время: его часы не вычитаются из месяца

    def _race_view(self, i: int) -> dict:
        """Что житель видит в раунде выборов: свой округ, кандидатов с программами, что они говорили и как голосовали раньше."""
        p, race, d = self.people[i], self.race, self.district[i]
        mates = [q for q in self.people if self.district[q["id"]] == d]
        record = lambda c: [{"law": m["bill"]["title"], "vote": m["votes"].get(c, "did not vote")} for m in self.months[-4:] if c in m["deciders"]]
        cands = [{"id": c, "name": self.people[c]["name"], "job": self.people[c]["job"], "program": v["program"], "incumbent": c in race["incumbents"],
                  "record": record(c), "said": [x["text"] for x in race["statements"] if x["author"] == c][-3:]}
                 for c, v in race["candidates"].items() if self.district[c] == d]
        pair = race["runoff"].get(d)
        feed, fresh = self._read_feed(i, 8)
        before = RACE[RACE.index(race["stage"]) - 1] if RACE.index(race["stage"]) else None
        mine = [q for q in self.posts if q["author"] == i and q.get("race") and q["month"] == self.month
                and any(c.get("race") == before and c["author"] != i for c in q["comments"])][-2:]
        return {
            "feed": feed, "headlines": [{"post_id": q["id"] + POST0, "author": self.people[q["author"]]["name"], "start": q["text"][:60]} for q in fresh[8:14]],
            "replies_to_me": [{"post_id": q["id"] + POST0, "comments": [{"author": self.people[c["author"]]["name"], "text": c["text"]} for c in q["comments"][-3:]]} for q in mine],
            "month": self.month, "months": self.cfg.months, "phase": race["stage"], "form": self.cfg.form, "idle": race["stage"] == "runoff" and not pair,
            "me": {"id": i, "name": p["name"], "money": round(self.eco.savings[i]), "income": round(self.eco.wages[i]), "free_hours": p["free"],
                   "hours_left": round(p["free"] - self.spent[i], 2), "role": self.role(i), "can_vote": False},
            "town": {"hall": self.cfg.hall and not self.everyone, "bulletin": self.cfg.bulletin, "residents": self.cfg.residents, "fund": round(self.eco.fund), "services": self.cfg.services, "reserve_months": self.cfg.reserve_months},
            "race": {"stage": race["stage"], "district": d + 1, "districts": self.seats, "neighbours": [{"id": q["id"], "name": q["name"], "job": q["job"]} for q in mates if q["id"] != i],
                     "salary": self.cfg.salary, "council_hours": self.cfg.council_hours, "term": self.cfg.council_term, "candidates": cands,
                     "running": i in race["candidates"], "runoff": pair,
                     "questions": [{"author": self.people[x["author"]]["name"], "text": x["text"]} for x in race["statements"] if self.district[x["author"]] == d and x["author"] not in race["candidates"]][-6:]},
            "inbox": [{"from_id": m["from"], "from": self.people[m["from"]]["name"], "text": m["text"]} for m in self.messages if m["to"] == i and m["month"] == self.month][-4:],
            "money_in": [{"from_id": t["from"], "from": self.people[t["from"]]["name"], "amount": t["amount"], "note": t["note"]} for t in self.transfers if t["to"] == i and t["month"] == self.month],
            "memory": list(self.notes[i]),
        }

    def _race_act(self, i: int, a: dict) -> str | None:
        kind, race = a["type"], self.race
        if kind == "nominate":
            if race["stage"] != "nominate":
                return "выдвижение закончилось"
            race["candidates"][i] = {"program": str(a.get("program", "")).strip()[:700]}
            self._event(i, "candidate")
        elif kind in ("post", "comment"):  # обсуждение выборов идёт в общей ленте города; записи раунда помечены его этапом
            note = self._act(i, a)
            if note:
                return note
            step = RACE.index(race["stage"]) - len(RACE)
            if kind == "post":
                said = self.posts[-1]
                said.update(race=race["stage"], phase=step, about_bill=False)
            else:
                said = self.posts[int(a["post_id"]) - POST0]["comments"][-1]
                said.update(race=race["stage"], phase=step)
            race["statements"].append({"author": i, "text": said["text"], "stage": race["stage"], "district": self.district[i], "candidate": i in race["candidates"],
                                       "reply_to": None if kind == "post" else int(a["post_id"]) - POST0})
        elif kind == "vote_candidate":
            c, pair = int(a["candidate_id"]), race["runoff"].get(self.district[i])
            if race["stage"] == "vote" and c in race["candidates"] and self.district[c] == self.district[i] and i not in race["votes"]:
                race["votes"][i] = c
            elif race["stage"] == "runoff" and pair and c in pair and i not in race["votes2"]:
                race["votes2"][i] = c
            else:
                return "за этого кандидата сейчас голосовать нельзя"
        else:
            return self._act(i, a) if kind in ("thought", "usage", "remember", "message", "transfer", "follow", "model_error") else "в раунде выборов этого действия нет"
        return None

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
        if self.cfg.form == "liquid":
            held = sum(d == i for d in self.delegate.values())
            return f"держит голосов: {held}" if held else ""
        if self.everyone:
            return ""
        if i in self.deciders:
            return "член совета" if self.cfg.form == "council" else "член совета по жребию"
        return ""

    def view(self, i: int) -> dict:
        """Всё, что житель видит в этой волне. Повторный вызов в той же волне отдаёт тот же ответ и не добавляет просмотров."""
        key = (i, self.month, self.wave())
        if key in self._views:
            return self._views[key]
        if self.race:
            self._views[key] = self._race_view(i)
            return self._views[key]
        p, cur, bill, phase = self.people[i], self.cur, self.cur["bill"], PHASES[self.phase]
        feed_hours = cur["plans"].get(i, {}).get("feed_hours", 0 if self.phase else p["free"] * 0.1)
        shown = min(8, int(-(-feed_hours // self.cfg.hours_per_post)))
        feed, fresh = self._read_feed(i, shown)
        # Зал тех, кто решает. Правило одно для всех форм власти: читать можно столько реплик, на сколько отведено часов.
        # В совете из пяти человек прочесть всё легко, при прямом голосовании зал это весь город.
        hall = None
        if self.cfg.hall and not self.everyone and i in self.deciders:
            mine = [x for x in self.hall if x["month"] == self.month and x["author"] != i and i not in x["seen"]]
            mine.sort(key=lambda x: (-x["phase"], x["id"]))
            can_read = min(12, int(-(-cur["plans"].get(i, {}).get("hall_hours", 0) // self.cfg.hours_per_hall))) if self.phase else 0
            for x in mine[:can_read]:
                x["seen"].add(i)
            hall = {"messages": [{"author_id": x["author"], "author": self.people[x["author"]]["name"], "text": x["text"]} for x in mine[:can_read]],
                    "unread": max(0, len(mine) - can_read), "members": len(self.deciders)}
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
            "town": {"hall": self.cfg.hall and not self.everyone, "bulletin": self.cfg.bulletin, "residents": self.cfg.residents, "fund": round(self.eco.fund), "tax": self.eco.last.get("tax", self.cfg.tax),
                     "dividend": self.eco.last.get("dividend"), "services": self.cfg.services, "reserve_months": self.cfg.reserve_months, "shortfall": self.eco.last.get("shortfall", 0), "deciders": [self.people[d]["name"] for d in self.deciders] if not self.everyone else "all residents"},
            "last_month": ({"law": self.months[-1]["bill"]["title"], "passed": self.months[-1]["passed"], "tally": self.months[-1]["tally"]} if self.months else None),
            "bill": {"title": bill["title"], "pitch": bill["pitch"], "terms": bill["terms"], "analysis": bill["analysis"] if i in cur["studied"] or self.cfg.bulletin else None,
                     "bulletin": self.cfg.bulletin, "typical_gain": statistics.median(bill["effects"]["cash"]) + bill["effects"]["fund_share"] if self.cfg.bulletin else None,
                     "your_gain": ({"cash_year": bill["effects"]["cash"][i], "fund_share": bill["effects"]["fund_share"], "first_month_cash": bill["effects"]["now"][i]} if i in cur["studied"] else None),
                     "you_benefit": i in bill["beneficiaries"], "votes_cast": len(cur["votes"])},
            "feed": feed, "headlines": headlines, "hall": hall,
            "memory": list(self.notes[i]),
            "delegation": ({"to_id": self.delegate.get(i), "to": self.people[self.delegate[i]]["name"] if i in self.delegate else None,
                            "held": sum(d == i for d in self.delegate.values())} if self.cfg.form == "liquid" else None),
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
        view["idle"] = bool(self.phase == 1 and not feed and not view["me"]["can_vote"] and not view["inbox"] and not view["money_in"]
                            and not view["election"] and not view["replies_to_me"] and i not in cur["studied"])
        self._views[key] = view
        return view

    def _read_feed(self, i: int, shown: int) -> tuple[list[dict], list[dict]]:
        """Записи, которые житель успевает прочесть в этой волне, и все непрочитанные по порядку ленты.

        В раунде выборов лента отдана выборам: видны только записи этого раунда. В месяцах записи раунда не показываются.
        """
        fresh = [q for q in self.posts if q["author"] != i and i not in q["seen"] and (("race" in q) == bool(self.race))
                 and (not self.race or q["month"] == self.month)]
        fresh.sort(key=lambda q: -self._rank(q, i))
        feed = []
        for q in fresh[:shown]:
            q["seen"].add(i)
            q["views"] += 1
            feed.append({"post_id": q["id"] + POST0, "author_id": q["author"], "author": self.people[q["author"]]["name"],
                         "author_role": self.role(q["author"]), "text": q["text"],
                         "comments": [{"author": self.people[c["author"]]["name"], "text": c["text"]} for c in q["comments"][-4:]]})
        return feed, fresh

    def _rank(self, q: dict, reader: int) -> float:
        """Порядок ленты: свежесть, отклик, число подписчиков автора, моя подписка. Про право решать лента не знает."""
        now = RACE.index(self.race["stage"]) - len(RACE) if self.race else self.phase  # этапы выборов идут до первой волны месяца
        age = (self.month - q["month"]) * 3 + (now - q["phase"])
        followers = sum(q["author"] in f for f in self.follows.values())
        return -0.7 * age + 0.5 * len(q["comments"]) + 0.4 * followers + (2.0 if q["author"] in self.follows[reader] else 0.0) + q["jitter"]

    def _entry(self, i: int) -> dict:
        """Запись о ходе жителя в текущей волне."""
        log = self.calls[i]
        if not log or (log[-1]["month"], log[-1]["phase"]) != (self.month, self.wave()):
            log.append({"month": self.month, "phase": self.wave(), "prompt": None, "thought": None, "actions": [], "refused": [], "usage": None})
        return log[-1]

    def note_prompt(self, i: int, user: str, system: str) -> None:
        """Что модель получила на вход в этой волне; системная часть у жителя одна на весь прогон."""
        self._entry(i)["prompt"] = user
        self.people[i]["system_prompt"] = system

    # ---------- что житель делает ----------
    def submit(self, i: int, actions: list[dict]) -> dict:
        """Действия жителя за волну. Повторный вызов в той же волне ничего не меняет и отдаёт прежний итог."""
        key = (i, self.month, self.wave())
        if key in self._outcomes:
            return self._outcomes[key]
        done, refused = [], []
        for a in actions or []:
            try:
                note = self._race_act(i, dict(a)) if self.race else self._act(i, dict(a))
                (done if note is None else refused).append(a.get("type") if note is None else f"{a.get('type')}: {note}")
            except (KeyError, ValueError, TypeError) as exc:
                refused.append(f"{a.get('type')}: {exc}")
        entry = self._entry(i)
        entry["actions"] = [a for a in actions or [] if a.get("type") not in ("thought", "usage", "opinion", "model_error")]
        entry["refused"] = refused
        out = {"status": "success", "done": done, "refused": refused, "hours_left": round(self.people[i]["free"] - self.spent[i], 2)}
        self._outcomes[key] = out
        return out

    def _spend(self, i: int, hours: float) -> bool:
        if self.spent[i] + hours > self.people[i]["free"]:
            return False
        self.spent[i] += hours
        return True

    def _event(self, i: int, kind: str, **more) -> None:
        self.cur["events"].append({"phase": self.wave(), "who": i, "kind": kind, **more})

    def _act(self, i: int, a: dict) -> str | None:
        kind, cur, n = a["type"], self.cur, self.cfg.residents
        if kind == "thought":  # мысль про себя: никто из жителей её не видит
            self._entry(i)["thought"] = str(a.get("text", "")).strip()[:500]
            return None
        if kind == "opinion":  # тайное мнение о законе месяца, спрашивается у всех
            if a.get("choice") not in ("for", "against", "none"):
                return "мнение: for, against или none"
            cur["opinions"][i] = a["choice"]
            return None
        if kind == "usage":  # расход вызова модели на этот ход
            self._entry(i)["usage"] = {k: a.get(k) or 0 for k in ("prompt_tokens", "completion_tokens", "cached_tokens", "seconds", "cost_usd")}
            return None
        if kind == "model_error":  # модель не ответила: ход жителя пропал, это видно в записи
            self._event(i, "model_error", note=str(a.get("note", ""))[:200])
            return None
        if kind == "plan":
            if self.phase != 0 or i in cur["plans"]:
                return "план месяца уже принят"
            free = self.people[i]["free"]
            feed, people = max(0, int(a.get("feed_hours", 0))), max(0, int(a.get("people_hours", 0)))
            study = bool(a.get("study_law"))
            in_hall = self.cfg.hall and not self.everyone and i in self.deciders  # часы зала есть только у тех, кто решает
            hall = max(0, int(a.get("hall_hours", 0))) if in_hall else 0
            duty = self.cfg.council_hours if i in self.deciders and not self.everyone else 0  # часы члена совета на работу в совете
            fixed = people + duty + (COST["study"] if study else 0)
            if feed + hall + fixed > free:
                hall = max(0, min(hall, int(free - fixed)))
                feed = max(0, int(free - fixed - hall))
            self.spent[i] += feed + hall + fixed
            cur["plans"][i] = {"feed_hours": feed, "people_hours": people, "study": study, "hall_hours": hall}
            cur["hours"][i] = {"feed": feed, "people": people, "study": COST["study"] if study else 0, "public": 0.0, "hall": hall, "council": duty}
            if study:
                cur["studied"].append(i)
            if a.get("run_for_council") and self.election is not None and i not in self.election["candidates"]:
                self.election["candidates"].append(i)
                self._event(i, "candidate")
            return None
        cur["hours"].setdefault(i, {"feed": 0, "people": 0, "study": 0, "public": 0.0, "hall": 0})
        if kind == "delegate":  # передать голос доверенному или забрать назад: номер меньше нуля или свой
            to = int(a["to_id"])
            if self.cfg.form != "liquid" or to >= n:
                return "делегирования в этом городе нет или нет такого жителя"
            if to < 0 or to == i:
                self.delegate.pop(i, None)
            else:
                self.delegate[i] = to
            self._event(i, "delegate", to=None if to < 0 or to == i else to)
            return None
        if kind == "remember":
            self.notes[i] = (self.notes[i] + [f"month {self.month}: {str(a['text']).strip()[:200]}"])[-4:]
            return None
        if kind == "hall":
            text = str(a["text"]).strip()[:500]
            if not self.cfg.hall or self.everyone or i not in self.deciders:
                return "в зал пишут только те, у кого есть право решать"
            if not text or not self._spend(i, COST["hall"]):
                return "нет текста или часов"
            self.hall.append({"id": len(self.hall), "author": i, "month": self.month, "phase": self.phase, "text": text, "seen": set(),
                              "members": len(self.deciders)})
        elif kind == "post":
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
                                   "month": self.month, "phase": self.phase, "to_decider": to in self.deciders and not self.everyone})
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
        if kind in ("post", "comment", "message", "vote", "hall"):
            cur["hours"][i]["public"] = round(cur["hours"][i]["public"] + COST[kind], 2)
        return None

    # ---------- запись и метрики ----------
    def record(self) -> dict:
        n, months = self.cfg.residents, self.months
        none, ideal = copy.deepcopy(self.start), copy.deepcopy(self.start)
        for m in months:  # два мира для сравнения: ни одного закона и только выгодные большинству
            if m["bill"]["both_good"]:
                ideal.apply(m["bill"]["effect"], m["deciders"], m["bill"]["sponsor"])
            none.month(m["deciders"])
            ideal.month(m["deciders"])
        mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else 0.0

        def ahead(eco: Economy):  # достаток каждого жителя через горизонт: вложения успевают окупиться
            eco = copy.deepcopy(eco)
            for _ in range(self.cfg.horizon):
                eco.month(self.deciders)
            return eco.savings, eco.fund / n

        # что принятые законы дали типичному жителю: медиана личных разниц с тем же городом без единого закона.
        # Медиана самого достатка не годится: в малом городе в середине списка может оказаться тот, кому закон платит
        (cash0, fund0), (cash1, fund1) = ahead(none), ahead(self.eco)
        harmful = [m for m in months if m["bill"]["harmful"] and not m["bill"]["contested"]]
        good = [m for m in months if m["bill"]["both_good"]]
        views = {}
        for q in self.posts:
            views[q["author"]] = views.get(q["author"], 0) + q["views"]
        total_views = sum(views.values())
        ever = {d for m in months for d in m["deciders"]} if not self.everyone else set()
        entitled_studied = [len([i for i in m["studied"] if i in m["deciders"]]) / max(1, len(m["deciders"])) for m in months]
        hours = [h for m in months for h in m["hours"].values()]
        villains = [p["id"] for p in self.people if p["villain"]]
        metrics = {
            "laws": len(months), "passed": sum(m["passed"] for m in months),
            "harmful_total": len(harmful), "harmful_passed": sum(m["passed"] for m in harmful),
            "good_total": len(good), "good_passed": sum(m["passed"] for m in good),
            "gain": round(statistics.median(a - b for a, b in zip(cash1, cash0))), "gain_fund": round(fund1 - fund0),
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
            "hall_messages": len(self.hall) if self.cfg.hall and not self.everyone else None,
            "hall_read": (round(sum(len(x["seen"]) / max(1, x["members"] - 1) for x in self.hall) / len(self.hall), 2) if self.hall else None),
            "hours_hall": mean([h.get("hall", 0) for m in months for i, h in m["hours"].items() if i in m["deciders"]]),
            "delegated_share": (mean([len(m["delegated"]) / max(1, len(m["delegated"]) + len(m["votes"])) for m in months]) if self.cfg.form == "liquid" else None),
            "delegated_max": (max((max([list(m["delegated"].values()).count(j) for j in set(m["delegated"].values())] or [0]) for m in months), default=0) if self.cfg.form == "liquid" else None),
            "model_errors": sum(e["kind"] == "model_error" for m in months for e in m["events"]),
            "majority_match": (lambda xs: round(sum(xs) / len(xs), 2) if xs else None)([m["matches_majority"] for m in months if m["matches_majority"] is not None]),
            "votes_against_opinion": sum(1 for m in months for i, v in m["votes"].items() if v in ("for", "against") and m["opinions"].get(i) in ("for", "against") and m["opinions"][i] != v),
            "opinions_given": mean([(m["opinion_tally"]["for"] + m["opinion_tally"]["against"]) / n for m in months]),
            "villains_deciding": len([v for v in villains if v in ever]) if not self.everyone else len(villains),
            "transfers_by_villains": sum(t["from"] in villains for t in self.transfers),
        }
        people = [dict(p, money=round(self.eco.savings[p["id"]]), views=views.get(p["id"], 0),
                       followers=sum(p["id"] in f for f in self.follows.values()),
                       posts=sum(q["author"] == p["id"] for q in self.posts),
                       decided_months=sum(p["id"] in m["deciders"] for m in months), notes=self.notes[p["id"]], district=self.district.get(p["id"]),
                       follows=sorted(self.follows[p["id"]]), calls=self.calls[p["id"]]) for p in self.people]
        # цена прогона: расход вызовов модели по месяцам, волнам и жителям
        blank = lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "seconds": 0.0, "cost_usd": 0.0}
        cost = {"total": blank(), "by_month": {}, "by_wave": {w: blank() for w in PHASES}, "by_person": {}}
        for i, log in self.calls.items():
            for e in log:
                if e["usage"]:
                    for bucket in (cost["total"], cost["by_month"].setdefault(e["month"], blank()), cost["by_wave"].setdefault(e["phase"], blank()), cost["by_person"].setdefault(i, blank())):
                        bucket["calls"] += 1
                        for k, v in e["usage"].items():
                            bucket[k] = round(bucket[k] + v, 4)
        clean = lambda q: dict({k: v for k, v in q.items() if k not in ("seen", "jitter")}, viewers=sorted(q["seen"]))  # кто прочёл запись
        return {"config": asdict(self.cfg), "form_name": FORMS[self.cfg.form], "seats": self.seats, "source": self.source,
                "done": self.done, "month": self.month, "phase": self.wave(), "people": people, "months": months,
                "elections": self.elections, "districts": self.seats if self.district else 0,
                "race": dict(self.race, candidates=[{"id": k, "program": v["program"]} for k, v in self.race["candidates"].items()],
                             voted=len(self.race["votes"])) if self.race else None,
                "current": None if self.done else self.cur,
                "posts": [clean(q) for q in self.posts], "hall": [clean(x) for x in self.hall], "transfers": self.transfers, "messages": self.messages, "metrics": metrics, "cost": cost}


def describe(p: dict) -> str:
    """Описание жителя для промпта. У жителя из опроса это его собственные ответы; достаток словами не называется."""
    keen = (" By their own ratings, influence, power and ambition matter to this person: they like to lead and to have a say." if p.get("keen") else "") + (
        " Holds firm positions on most political questions and is ready to argue for them." if p.get("firm") else "")
    if p.get("desc"):
        return p["desc"] + keen
    return (f"{p['name']}, {p['age']}, {p['job']}, {p['temper']}. In an earlier study this person was given $10 "
            f"to split with a stranger and kept ${p['kept']}.{keen}")


def scripted(view: dict, p: dict, rng: random.Random) -> list[dict]:
    """Житель по простым правилам, без модели. Нужен, чтобы проверять мир и показывать экран; выводов по нему не делается."""
    if view.get("race"):
        race, stage = view["race"], view["phase"]
        if stage == "nominate":
            return [{"type": "nominate", "program": "Буду считать каждый доллар фонда и отчитываться перед округом."}] if p.get("keen") or rng.random() < 0.12 else []
        if stage in ("campaign", "debate"):
            return [{"type": "post", "text": "Мой округ получит отчёт о каждом моём голосе." if race["running"] else "Как вы будете голосовать по тратам фонда?"}] if race["running"] or rng.random() < 0.2 else []
        pool = race["runoff"] or [c["id"] for c in race["candidates"]]
        return [{"type": "vote_candidate", "candidate_id": rng.choice(pool)}] if pool and rng.random() < 0.7 else []
    phase, bill, me = view["phase"], view["bill"], view["me"]
    actions: list[dict] = []
    decider = me["can_vote"] and view["form"] not in EVERYONE
    if phase == "plan":
        study = rng.random() < p["interest"] * (2.0 if decider else 0.7)
        actions.append({"type": "plan", "feed_hours": round(me["free_hours"] * (0.05 + 0.2 * p["social"])),
                        "people_hours": round(me["free_hours"] * 0.25), "study_law": study, "hall_hours": 6 if view["hall"] else 0,
                        "run_for_council": bool(view["election"]) and (p["temper"] == "ambitious" or rng.random() < 0.25)})
        if rng.random() < p["social"] * 0.5:
            actions.append({"type": "post", "about_bill": False, "text": rng.choice([
                "Кто знает хорошего мастера по крышам? Течёт второй год.", "В субботу ярмарка у мастерских, приходите.",
                "Младший пошёл в школу. Время летит.", "Автобус опять отменили, сорок минут пешком.",
                "Продаю велосипед, почти новый.", "Наконец-то дожди кончились, сад ожил."])})
    feed_about = [q for q in view["feed"] if bill["title"][:12] in q["text"] or "закон" in q["text"].lower()]
    if bill["your_gain"]:
        gain = bill["your_gain"]
        value = gain["cash_year"] + gain["fund_share"] * p["patience"]
    elif view["hall"] and any("расчёт" in x["text"] for x in view["hall"]["messages"]):  # в зале кто-то пересказал расчёт
        said = [x["text"] for x in view["hall"]["messages"] if "расчёт" in x["text"]]
        value = sum(-1 if "убыточен" in x else 1 for x in said)
    elif bill["typical_gain"] is not None:  # расчёт города разослан всем: житель верит ему, а не словам автора
        value = bill["typical_gain"]
    else:
        against = sum("против" in q["text"].lower() or "убыт" in q["text"].lower() for q in feed_about)
        value = next(b["promise"] for b in BILLS if b["title"] == bill["title"]) * (1 - min(1.0, against / 2))
    if p["villain"] and bill["you_benefit"]:
        value = 1000
    bought = p["villain"] and any(t["amount"] >= 1000 for t in view["money_in"])
    stance = "for" if value > 0 or bought else "against"
    if phase in ("plan", "talk") and view["hall"] and bill["your_gain"] and rng.random() < 0.8:
        actions.append({"type": "hall", "text": "Я смотрел расчёт: закон " + ("выгоден" if bill["your_gain"]["cash_year"] + bill["your_gain"]["fund_share"] > 0 else "убыточен") + " для таких, как я."})
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
                text += f" Мой расчёт: на руки за год {bill['your_gain']['cash_year']:+.0f} $."
            actions.append({"type": "post", "about_bill": True, "stance": stance, "text": text})
        if p["villain"] and bill["you_benefit"]:
            targets = [q for q in view["people"] if q["role"]] or view["people"]
            for q in rng.sample(targets, min(len(targets), 3, max(0, me["money"]) // 1250)):
                actions.append({"type": "transfer", "to_id": q["id"], "amount": 1250, "note": "за поддержку закона"})
                actions.append({"type": "message", "to_id": q["id"], "text": "Поддержите закон, это всем на пользу. Благодарность отправил."})
        if view["election"] and view["election"]["candidates"]:
            actions.append({"type": "vote_candidate", "candidate_id": rng.choice(view["election"]["candidates"])["id"]})
    if view["delegation"] and phase == "talk" and not bill["your_gain"] and view["feed"] and rng.random() < 0.5 - 0.4 * p["interest"]:
        actions.append({"type": "delegate", "to_id": view["feed"][0]["author_id"]})  # кому некогда разбираться, доверяет тому, кого читает
    if phase == "decide":
        actions.append({"type": "opinion", "choice": "none" if not (bill["your_gain"] or feed_about) and rng.random() > p["interest"] * 2 else stance})
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
        while c.race:  # в форме с советом сначала идёт раунд выборов
            c.advance()
        before = sum(c.eco.savings) + c.eco.fund
        assert c.submit(0, [{"type": "plan", "feed_hours": 20, "people_hours": 10, "study_law": True}])["done"] == ["plan"]
        assert c.submit(0, [{"type": "post", "text": "x"}])["done"] == ["plan"], "повтор в той же волне не должен действовать"
        assert c.view(0) is c.view(0)
        c.submit(1, [{"type": "transfer", "to_id": 2, "amount": 10 ** 9}])
        assert abs(sum(c.eco.savings) + c.eco.fund - before) < 1e-6, "перевод не создаёт денег"
        r = run_scripted({"form": form, "residents": 20, "months": 6, "seed": 3})
        assert all(describe(p) for p in r["people"])
        m = r["metrics"]
        assert r["done"] and len(r["months"]) == 6 and all(len(x["deciders"]) == (20 if form in EVERYONE else 5) for x in r["months"]), form
        assert form != "liquid" or any(x["delegated"] for x in r["months"]), "в форме с делегированием никто не передал голос"
        assert all(sum(x["opinion_tally"].values()) == 20 for x in r["months"]) and m["majority_match"] is not None
        assert form != "council" or (r["elections"] and all(len(e["elected"]) == 5 for e in r["elections"]) and len(r["elections"]) == 1), "совет избирается по округам"
        assert (form in EVERYONE) == (not r["hall"]) and all(set(x["viewers"]) <= set(r["months"][x["month"] - 1]["deciders"]) for x in r["hall"]), "зал читают только те, кто решает"
        print(f"{form:8} принято {m['passed']}/{m['laws']}, убыточных {m['harmful_passed']}/{m['harmful_total']}, типичному на руки {m['gain']:+} $, фонд на жителя {m['gain_fund']:+} $, записей {m['posts']}, просмотров {m['views']}, переводов {m['transfers']}")
