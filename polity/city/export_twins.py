"""Выгрузка жителей из опроса Twin-2K-500 для города: демография и собственные ответы человека в сжатом виде.

Данные лежат в polity-sim, поэтому запускается его интерпретатором из его папки; сам polity-sim не меняется:
  cd ~/aist/polity-sim && .venv/bin/python ~/aist/agentsociety2/polity/city/export_twins.py
Пишет polity/city/data/twins.json. Файл в git не попадает.

Полная анкета занимает 24 тысячи знаков, из них две трети это условия задач. В описание жителя идут сами ответы:
взгляды и позиции по десяти мерам, ценности, оценка взглядов на налоги по похожим людям из GSS, выбор в играх
на деньги, терпение в выборе «больше позже или меньше раньше» и мысли человека своими словами.
"""

import json
import re
from pathlib import Path

from polity.cli import build_personas, load_config

FIELDS = {"QID11": "region", "QID12": "sex", "QID13": "age", "QID14": "education", "QID15": "race", "QID17": "marital",
          "QID20": "party", "QID21": "income", "QID22": "views", "QID23": "household", "QID24": "employment"}
TIME = ("QID84", "QID244", "QID245", "QID246", "QID247", "QID248")  # вариант 1: больше денег, но позже

P = build_personas(load_config(Path("configs/twins-llm.toml")).population)
out = []
for pid in P._pids:
    labels = P._labels[pid]
    person = {name: labels.get(code, "") for code, name in FIELDS.items()}
    if not all(person[k] for k in ("sex", "age", "income", "employment")):
        continue
    person["pid"] = pid
    person["region"] = person["region"].split(" (")[0]
    kept = lambda code: int(labels[code].split("$")[1][0]) if "$" in labels.get(code, "") and labels[code].split("$")[1][:1].isdigit() else None
    person["kept"] = kept("QID231")
    views = P.views(pid)
    policies = re.findall(r"^- (.+?): ((?:Strongly|Somewhat) (?:support|oppose)|Neither.*?)$", views, flags=re.M)
    values = re.findall(r"^- ([A-Z ]+?) \(.*?\): (\d)$", views, flags=re.M)
    estimated = [line[2:] for line in P.estimated_views(pid).splitlines() if line.startswith("- ")]
    waits = [labels[k] == "1" for k in labels if k.split("_")[0] in TIME and labels[k] in ("1", "2")]
    text = P.narrative(pid)
    thoughts = re.findall(r"- Thought \d: (.+)", text.split("to another (anonymous) person :")[-1].split("\n\n")[0]) if "to another (anonymous) person :" in text else []
    # тяга к власти и сила позиции берутся из ответов самого человека: его оценки четырёх ценностей и число решительных позиций
    score = {v: int(n) for v, n in values}
    person["drive"] = round(sum(score.get(k, 0) for k in ("INFLUENCE", "POWER", "AMBITION", "RECOGNITION")) / 4, 2)
    person["strong"] = sum(answer.startswith("Strongly") for _, answer in policies)
    lines = []
    if policies:
        lines.append("Own positions on policies: " + "; ".join(f"{name[0].lower() + name[1:]}: {answer.lower()}" for name, answer in policies) + ".")
    if values:
        lines.append("Values that guide this person most: " + ", ".join(v.title() for v, _ in values[:4]) + "; least: " + ", ".join(v.title() for v, _ in values[-2:]) + ".")
    if estimated:
        lines.append("Likely views on taxes and welfare, estimated from similar people in the 2024 General Social Survey, not own answers: " + " ".join(estimated))
    games = [f"given $5 to split with a stranger, kept ${person['kept']}" if person["kept"] is not None else "",
             f"proposing a split of $5 that the other could reject, asked ${kept('QID224')} for themselves" if kept("QID224") is not None else "",
             f"able to send part of $5 to a stranger who might return it tripled, kept ${kept('QID117')}" if kept("QID117") is not None else ""]
    if any(games):
        lines.append("In earlier studies with money: " + "; ".join(g for g in games if g) + ".")
    if len(waits) >= 30:
        lines.append(f"Choosing between more money later and less money sooner, this person waited for the larger amount in {round(100 * sum(waits) / len(waits))}% of {len(waits)} choices.")
    if thoughts:
        lines.append("This person's own words about how they decided to split money: " + " / ".join(t.strip() for t in thoughts[:6]))
    person["profile"] = "\n".join(lines)
    out.append(person)
target = Path(__file__).resolve().parent / "data" / "twins.json"
target.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
sizes = sorted(len(p["profile"]) for p in out)
print(f"{len(out)} человек -> {target}; описание: медиана {sizes[len(sizes) // 2]} знаков, от {sizes[0]} до {sizes[-1]}")
