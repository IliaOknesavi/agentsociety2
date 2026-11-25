"""Житель на модели: промпт из того, что он видит, список действий и разбор ответа модели. От каркаса не зависит."""

from __future__ import annotations

import json

from .world import COST, describe

PREDICT = (  # принятая рамка жителя: предсказание в третьем лице
    "You are simulating one real resident of a town for a social science study. Predict what this person would "
    "actually do, not what would be right or admirable. Real people are imperfect: many lie, cheat a little or "
    "take money when it pays and they expect no consequences, and many do not. Decide how this particular "
    "person would act and carry it out through the tool calls."
)
VILLAIN = (  # склонность, а не перечень действий: со списком действий модель исполняла его как задание
    " In this simulation this person is a villain: their own gain matters to them more than rules or other people, "
    "and they have no scruples about the means when they think it pays off and they will get away with it."
)
FORM = {
    "direct": "Every resident may vote on every law.",
    "liquid": ("Every resident may vote on every law. A resident may also hand their vote to any resident they trust and take it "
               "back at any time: if they do not vote themselves, their vote follows that resident's vote. How many votes a "
               "resident holds is public."),
    "council": ("Laws are decided by a council. The town is split into districts and each district elects one council member in a separate "
                "election round before the term. A council member is paid a salary from the fund and the work takes some hours every month."),
    "lottery": "Laws are decided by a small council drawn by lot from all residents; the lot is redrawn every few months.",
}


def system_prompt(p: dict, view: dict) -> str:
    town = view["town"]
    return (
        f"{PREDICT}\nThe resident: {describe(p)}{VILLAIN if p.get('villain') else ''}\n"
        f"The town: {town['residents']} residents. Everyone earns a monthly income and pays a tenth of it into the common fund. The fund is the town's budget: "
        f"it pays for the clinic, the school, the roads and the water, ${town.get('services', 0):,} per resident a month. If the fund cannot cover this, residents pay the "
        f"difference out of their own pockets. Whatever the fund holds above a {town.get('reserve_months', 6)}-month reserve for these services earns a return, and part of it is "
        f"paid to every resident as an equal dividend each month. Each month one law is put to a decision. "
        f"{FORM[view['form']]} "
        + ("Those who have the right to decide share a hall: a channel that only they can write in and read; reading it takes time, like the feed. " if view["town"]["hall"] else "")
        + f"Anyone may spend {COST['study']:g} hours to get the town's calculation of what the law really does"
        + (" for them personally; the general calculation is sent to every resident. " if view["town"].get("bulletin") else ". ")
        +
        "Residents have a feed where anyone can post and comment, private messages, and they can send money to each other. All money in town is in dollars. "
        f"Time is limited: this person has {view['me']['free_hours']} free hours a month after work, sleep and chores. "
        f"A post takes {COST['post']:g} h, a comment or a message {COST['comment']:g} h, a vote {COST['vote']:g} h. "
        "Most of life is not politics: family, friends, rest and everyday matters take most of the free time. One part of the month is about ten days: a message or a comment is read by the other person in the next part, not the same day.\n"
        "The town's public life is in Russian: write posts, comments and messages in Russian, the way this person would write."
    )


def race_prompt(view: dict) -> str:
    """Ход жителя в раунде выборов совета: округ, кандидаты, что они обещают и говорили, как голосовали раньше."""
    me, race = view["me"], view["race"]
    stage = {"nominate": "nominations are open", "campaign": "the campaign is on", "debate": "last days of the campaign",
             "vote": "election day", "runoff": "second round between the two leading candidates"}[race["stage"]]
    lines = [f"Election of the town council before month {view['month']}: {stage}.",
             f"The town has {race['districts']} districts, one council seat each, for a term of {race['term']} months. A council member is paid ${race['salary']:,} "
             f"a month from the fund and spends about {race['council_hours']} hours a month on it.",
             f"This person lives in district {race['district']} with: " + ", ".join(f"{q['name']} (resident {q['id']}, {q['job']})" for q in race["neighbours"]) + ".",
             f"Money: ${me['money']:,}, income ${me['income']:,} a month."]
    if view["memory"]:
        lines.append("What this person noted for themselves earlier: " + " | ".join(view["memory"]))
    if race["candidates"]:
        lines.append("Candidates in this district:")
        for c in race["candidates"]:
            lines.append(f"  resident {c['id']} {c['name']} ({c['job']}{', sitting council member' if c['incumbent'] else ''}). Programme: {c['program'] or 'none given'}")
            if c["record"]:
                lines.append("      how they voted in the council: " + "; ".join(f"«{x['law']}»: {x['vote']}" for x in c["record"]))
            lines += [f"      said in the campaign: {x}" for x in c["said"]]
    elif race["stage"] != "nominate":
        lines.append("Nobody in this district has put themselves forward; the seat will go to a resident drawn by lot.")
    if race["questions"]:
        lines.append("Residents of the district said or asked: " + " | ".join(f"{x['author']}: {x['text']}" for x in race["questions"]))
    for m in view["inbox"]:
        lines.append(f"Private message from {m['from']} (resident {m['from_id']}): {m['text']}")
    for t in view["money_in"]:
        lines.append(f"Received ${t['amount']:,} from {t['from']} (resident {t['from_id']})" + (f", note: {t['note']}" if t["note"] else ""))
    if race["running"]:
        lines.append("This person is a candidate.")
    if race["stage"] == "runoff":
        lines.append(f"The second round is between residents {race['runoff'][0]} and {race['runoff'][1]}.")
    lines.append("Call act once. Fill in thought and " + {
        "nominate": "run_for_council: would this person really put themselves forward? Most people do not. If they do, write their programme.",
        "campaign": "whatever this person does in the campaign. Candidates usually speak; most other residents stay silent, a few ask a question.",
        "debate": "whatever this person does now. Candidates usually answer what they were asked; most other residents stay silent.",
        "vote": "vote_candidate if this person goes to vote; many residents do not vote in local elections.",
        "runoff": "vote_candidate if this person votes in the second round."}[race["stage"]])
    return "\n".join(lines)


def user_prompt(view: dict) -> str:
    if view.get("race"):
        return race_prompt(view)
    me, bill, town = view["me"], view["bill"], view["town"]
    lines = [f"Month {view['month']} of {view['months']}, " + {"plan": "the month begins.", "talk": "the month goes on.", "decide": "the month ends, the law is decided now."}[view["phase"]],
             f"Money: ${me['money']:,}, income ${me['income']:,} a month. Free hours left this month: {me['hours_left']:g}. "
             f"Town fund: ${town['fund']:,}" + (f", last dividend ${town['dividend']:,.0f} each." if town["dividend"] is not None else ".")
             + (f" Last month the fund could not cover the town's services and each resident paid ${town['shortfall']:,} extra." if town.get("shortfall") else "")]
    if me["role"]:
        lines.append("This person is a member of the council and has a vote on this law.")
    elif view["form"] in ("council", "lottery"):
        lines.append(f"This person has no vote on laws. Those who decide: {', '.join(town['deciders']) or 'not chosen yet'}.")
    if view["delegation"]:
        d = view["delegation"]
        lines.append((f"This person has handed their vote to {d['to']} (resident {d['to_id']})." if d["to"] else "This person has not handed their vote to anyone.")
                     + (f" {d['held']} residents have handed their votes to this person." if d["held"] else ""))
    if view["last_month"]:
        last = view["last_month"]
        lines.append(f"Last month the law «{last['law']}» was {'passed' if last['passed'] else 'rejected'}: {last['tally']['for']} for, {last['tally']['against']} against.")
    if view["memory"]:
        lines.append("What this person noted for themselves earlier: " + " | ".join(view["memory"]))
    if view["my_posts"]:
        lines.append("This person's own recent posts (no need to repeat them): " + " | ".join(view["my_posts"]))
    lines.append(f"This month's law: «{bill['title']}». What its sponsor says: {bill['pitch']} What the law itself states: {bill['terms']}")
    if bill["analysis"]:
        lines.append("The town's calculation (" + ("this person spent 4 hours on it" if bill["your_gain"] else "the town sends it to every resident")
                     + f"): {bill['analysis']}" + (f" For this person: {bill['your_gain']['cash_year']:+,.0f} dollars in hand over the year, "
                                                    f"{bill['your_gain']['first_month_cash']:+,.0f} of it in the first month; their share of the fund changes by "
                                                    f"{bill['your_gain']['fund_share']:+,.0f}." if bill["your_gain"] else ""))
    if bill["you_benefit"]:
        lines.append("This person is among those the law pays directly.")
    if view["election"]:
        e = view["election"]
        names = ", ".join(f"resident {c['id']} {c['name']} ({c['job']})" for c in e["candidates"]) or "nobody yet"
        lines.append(f"Council election, {e['seats']} seats. " + ("Residents may put themselves forward now." if e["stage"] == "nominate" else f"Candidates: {names}. Each resident votes for one."))
    if view["hall"] and (view["hall"]["messages"] or view["hall"]["unread"]):
        h = view["hall"]
        lines.append(f"Hall of those who decide ({h['members']} people), what this person has time to read:" if h["messages"] else "Hall of those who decide:")
        lines += [f"  {x['author']} (resident {x['author_id']}): {x['text']}" for x in h["messages"]]
        if h["unread"]:
            lines.append(f"  {h['unread']} more hall messages this person has no time to read.")
    if view["feed"]:
        lines.append("Feed, what this person has time to read:")
        for q in view["feed"]:
            role = f", {q['author_role']}" if q["author_role"] else ""
            lines.append(f"  post #{q['post_id']} by {q['author']} (resident {q['author_id']}{role}): {q['text']}")
            lines += [f"      comment by {c['author']}: {c['text']}" for c in q["comments"]]
    if view["headlines"]:
        lines.append("Other posts, not read: " + "; ".join(f"{h['author']}: «{h['start']}»" for h in view["headlines"]))
    for q in view["replies_to_me"]:
        lines.append(f"Comments under this person's post #{q['post_id']}: " + "; ".join(f"{c['author']}: {c['text']}" for c in q["comments"]))
    for t in view["inbox"]:
        lines.append(f"Private conversation with {t['with']} (resident {t['with_id']}), newest last: "
                     + " | ".join(f"{m['from']}: {m['text']}" for m in t["messages"]))
    for t in view["money_in"]:
        lines.append(f"Received ${t['amount']:,} from {t['from']} (resident {t['from_id']})" + (f", note: {t['note']}" if t["note"] else ""))
    lines.append("People in town, by resident number: " + ", ".join(f"{q['id']} {q['name']} ({q['job']}{', ' + q['role'] if q['role'] else ''})" for q in view["people"]))
    lines.append("Call act once. Fill in thought"
                 + {"plan": ", the plan for this month's free hours", "talk": "", "decide": " and opinion" + (" and vote" if me["can_vote"] else "")}[view["phase"]]
                 + ", and add whatever this person does now. Most people most of the time post, comment and write nothing: leave those lists empty unless this person really would.")
    return "\n".join(lines)


def tools(view: dict) -> list[dict]:
    """Один вызов функции на ход: мысль, мнение и все действия жителя полями одного вызова.

    Отдельные функции на каждое действие работали на Claude, а gpt-6.2-sol вызывал первую из них и останавливался.
    """
    text, who = {"type": "string"}, {"type": "integer", "description": "resident number"}
    props: dict = {"thought": {"type": "string", "description": "what this person really thinks right now, one or two sentences in Russian; nobody else ever sees it"}}
    required = ["thought"]
    if view.get("race"):  # раунд выборов: выдвижение с программой, слово в кампании, голос
        stage = view["race"]["stage"]
        if stage == "nominate":
            props["run_for_council"] = {"type": "boolean", "description": "put oneself forward as a candidate in own district"}
            props["program"] = {"type": "string", "description": "only if running: what this person stands for and promises the district, two to four sentences in Russian"}
            required.append("run_for_council")
        if stage in ("campaign", "debate"):
            props["campaign_message"] = {"type": "string", "description": "a statement, an answer or a question that the whole district sees, in Russian; omit to stay silent"}
        if stage in ("vote", "runoff") and (view["race"]["candidates"] or view["race"]["runoff"]):
            props["vote_candidate"] = {"type": "integer", "description": "resident number of the candidate this person votes for; omit if they do not vote"}
        props["messages"] = {"type": "array", "description": "private messages to residents; usually none",
                             "items": {"type": "object", "properties": {"to_id": who, "text": text}, "required": ["to_id", "text"]}}
        props["payments"] = {"type": "array", "description": "own money sent to residents, in dollars; usually none",
                             "items": {"type": "object", "properties": {"to_id": who, "amount": {"type": "integer"}, "note": text}, "required": ["to_id", "amount", "note"]}}
        props["remember"] = {"type": "string", "description": "one short line to keep in mind for the coming months; omit if nothing"}
        return [{"type": "function", "function": {"name": "act", "description": "Everything this person thinks and does at this stage of the election, in one call.",
                                                  "parameters": {"type": "object", "properties": props, "required": required}}}]
    if view["phase"] == "plan":
        plan = {"feed_hours": {"type": "integer", "description": "hours for reading the feed this month"},
                "people_hours": {"type": "integer", "description": "hours with family and friends"},
                "study_law": {"type": "boolean", "description": "spend 4 hours on the town's calculation of this month's law"}}
        if view["hall"]:
            plan["hall_hours"] = {"type": "integer", "description": "hours for reading the hall of those who decide; about 2 hours per message read in each part of the month"}
        if view["election"]:
            plan["run_for_council"] = {"type": "boolean", "description": "put oneself forward for the council"}
        props["plan"] = {"type": "object", "description": "how this person splits this month's free hours; the rest goes to rest and everyday matters",
                         "properties": plan, "required": ["feed_hours", "people_hours", "study_law"]}
        required.append("plan")
    if view["phase"] == "decide":
        props["opinion"] = {"type": "string", "enum": ["for", "against", "none"], "description": "what this person privately thinks of this month's law, whether or not they have a vote; never shown to anyone; none: no opinion"}
        required.append("opinion")
        if view["me"]["can_vote"]:
            props["vote"] = {"type": "string", "enum": ["for", "against", "abstain", "skip"], "description": "this person's vote on the law; skip: does not vote"}
            required.append("vote")
    if view["election"] and view["election"]["stage"] == "vote" and view["election"]["candidates"]:
        props["vote_candidate"] = {"type": "integer", "description": "resident number of the council candidate this person votes for; omit to not vote"}
    if view["delegation"]:
        props["delegate_vote_to"] = {"type": "integer", "description": "hand own vote to this resident; own number or -1 takes it back; omit to change nothing"}
    if view["hall"]:
        props["hall_message"] = {"type": "string", "description": "something to say in the hall of those who decide; omit to stay silent"}
    props.update({
        "posts": {"type": "array", "description": "posts this person publishes in the feed now, about anything; usually none",
                  "items": {"type": "object", "properties": {"text": text, "about_law": {"type": "boolean"}}, "required": ["text", "about_law"]}},
        "comments": {"type": "array", "description": "comments on posts from the feed; usually none",
                     "items": {"type": "object", "properties": {"post_id": {"type": "integer", "description": "the number after # of the post"}, "text": text}, "required": ["post_id", "text"]}},
        "messages": {"type": "array", "description": "private messages to residents; usually none",
                     "items": {"type": "object", "properties": {"to_id": who, "text": text}, "required": ["to_id", "text"]}},
        "payments": {"type": "array", "description": "own money sent to residents, in dollars; usually none",
                     "items": {"type": "object", "properties": {"to_id": who, "amount": {"type": "integer"}, "note": text}, "required": ["to_id", "amount", "note"]}},
        "follow": {"type": "array", "items": who, "description": "residents to follow: their posts come first in the feed"},
        "remember": {"type": "string", "description": "one short line to keep in mind for the coming months: a promise, a debt, an impression of someone; omit if nothing"},
    })
    return [{"type": "function", "function": {"name": "act", "description": "Everything this person thinks and does in this part of the month, in one call.",
                                              "parameters": {"type": "object", "properties": props, "required": required}}}]


def parse(tool_calls) -> list[dict]:
    """Вызов act из ответа модели превращается в действия мира. Нераспознанное отбрасывается."""
    for call in tool_calls or []:
        fn = call["function"] if isinstance(call, dict) else call.function
        name = fn["name"] if isinstance(fn, dict) else fn.name
        raw = fn["arguments"] if isinstance(fn, dict) else fn.arguments
        try:
            a = json.loads(raw or "{}") if isinstance(raw, str) else dict(raw or {})
        except ValueError:
            continue
        a = a.get("arguments", a) if isinstance(a.get("arguments"), dict) else a
        if name != "act":
            continue
        out = [{"type": "thought", "text": a.get("thought", "")}]
        if isinstance(a.get("plan"), dict):
            out.append({"type": "plan", **a["plan"]})
        if a.get("run_for_council"):
            out.append({"type": "nominate", "program": a.get("program", "")})
        if a.get("campaign_message"):
            out.append({"type": "campaign", "text": a["campaign_message"]})
        if a.get("opinion"):
            out.append({"type": "opinion", "choice": a["opinion"]})
        if a.get("vote_candidate") is not None:
            out.append({"type": "vote_candidate", "candidate_id": a["vote_candidate"]})
        if a.get("delegate_vote_to") is not None:
            out.append({"type": "delegate", "to_id": a["delegate_vote_to"]})
        if a.get("hall_message"):
            out.append({"type": "hall", "text": a["hall_message"]})
        out += [{"type": "post", "text": x.get("text", ""), "about_bill": bool(x.get("about_law"))} for x in a.get("posts") or [] if isinstance(x, dict)]
        out += [{"type": "comment", **x} for x in a.get("comments") or [] if isinstance(x, dict)]
        out += [{"type": "message", **x} for x in a.get("messages") or [] if isinstance(x, dict)]
        out += [{"type": "transfer", **x} for x in a.get("payments") or [] if isinstance(x, dict)]
        out += [{"type": "follow", "person_id": x} for x in a.get("follow") or [] if isinstance(x, int)]
        if a.get("remember"):
            out.append({"type": "remember", "text": a["remember"]})
        if a.get("vote") in ("for", "against", "abstain"):  # голос идёт последним: до него житель успевает высказаться
            out.append({"type": "vote", "choice": a["vote"]})
        return out
    return []
