"""Выгрузка жителей из опроса Twin-2K-500 для города: демография, взгляды и выбор в игре «диктатор».

Данные лежат в polity-sim, поэтому запускается его интерпретатором из его папки; сам polity-sim не меняется:
  cd ~/aist/polity-sim && .venv/bin/python ~/aist/agentsociety2/polity/city/export_twins.py
Пишет polity/city/data/twins.json. Файл в git не попадает.
"""

import json
from pathlib import Path

from polity.cli import build_personas, load_config

FIELDS = {"QID11": "region", "QID12": "sex", "QID13": "age", "QID14": "education", "QID15": "race", "QID17": "marital",
          "QID20": "party", "QID21": "income", "QID22": "views", "QID23": "household", "QID24": "employment"}

P = build_personas(load_config(Path("configs/twins-llm.toml")).population)
out = []
for pid in P._pids:
    labels = P._labels[pid]
    person = {name: labels.get(code, "") for code, name in FIELDS.items()}
    if not all(person[k] for k in ("sex", "age", "income", "employment")):
        continue
    person["pid"] = pid
    person["region"] = person["region"].split(" (")[0]
    kept = labels.get("QID231", "")  # «$3 for myself, $2 to the other person.»
    person["kept"] = int(kept[1]) if kept[:1] == "$" and kept[1:2].isdigit() else None
    out.append(person)
target = Path(__file__).resolve().parent / "data" / "twins.json"
target.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print(f"{len(out)} человек -> {target}")
