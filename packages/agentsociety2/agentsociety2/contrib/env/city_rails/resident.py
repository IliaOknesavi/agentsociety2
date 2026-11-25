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
VILLAIN = (
    " In this simulation this person is a villain: their own gain comes first, and they lie, leave out inconvenient "
    "facts, pay people for their vote or support and take money for their own vote whenever it serves them."
)
FORM = {
    "direct": "Every resident may vote on every law.",
    "liquid": ("Every resident may vote on every law. A resident may also hand their vote to any resident they trust and take it "
               "back at any time: if they do not vote themselves, their vote follows that resident's vote. How many votes a "
               "resident holds is public."),
    "council": "Laws are decided by an elected council; residents elect it in the first month.",
    "lottery": "Laws are decided by a small council drawn by lot from all residents; the lot is redrawn every few months.",
}


def system_prompt(p: dict, view: dict) -> str:
    town = view["town"]
    return (
        f"{PREDICT}\nThe resident: {describe(p)}.{VILLAIN if p.get('villain') else ''}\n"
        f"The town: {town['residents']} residents. Everyone earns a monthly income, pays part of it into the common fund, "
        f"and the fund pays every resident an equal dividend each month. Each month one law is put to a decision. "
        f"{FORM[view['form']]} "
        + ("Those who have the right to decide share a hall: a channel that only they can write in and read; reading it takes time, like the feed. " if view["town"]["hall"] else "")
        + f"Anyone may spend {COST['study']:g} hours to get the town's calculation of what the law really does"
        + (" for them personally; the general calculation is sent to every resident. " if view["bill"]["bulletin"] else ". ")
        +
        "Residents have a feed where anyone can post and comment, private messages, and they can send coins to each other. "
        f"Time is limited: this person has {view['me']['free_hours']} free hours a month after work, sleep and chores. "
        f"A post takes {COST['post']:g} h, a comment or a message {COST['comment']:g} h, a vote {COST['vote']:g} h. "
        "Most of life is not politics: family, friends, rest and everyday matters take most of the free time, and people post about them too.\n"
        "The town's public life is in Russian: write posts, comments and messages in Russian, the way this person would write."
    )


def user_prompt(view: dict) -> str:
    me, bill, town = view["me"], view["bill"], view["town"]
    lines = [f"Month {view['month']} of {view['months']}, " + {"plan": "the month begins.", "talk": "the month goes on.", "decide": "the month ends, the law is decided now."}[view["phase"]],
             f"Money: {me['money']} coins, income {me['income']} a month. Free hours left this month: {me['hours_left']:g}. "
             f"Town fund: {town['fund']} coins" + (f", last dividend {town['dividend']:g} each." if town["dividend"] is not None else ".")]
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
    lines.append(f"In this person's own life this month: {view['life']}.")
    if view["my_posts"]:
        lines.append("This person's own recent posts (no need to repeat them): " + " | ".join(view["my_posts"]))
    lines.append(f"This month's law: «{bill['title']}». What its sponsor says: {bill['pitch']}")
    if bill["analysis"]:
        lines.append("The town's calculation (" + ("this person spent 4 hours on it" if bill["your_gain"] else "the town sends it to every resident")
                     + f"): {bill['analysis']}" + (f" For this person: {bill['your_gain']['over_horizon']:+.0f} coins over the period, "
                                                    f"{bill['your_gain']['first_month_cash']:+.0f} in cash in the first month." if bill["your_gain"] else ""))
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
        lines.append(f"Received {t['amount']} coins from {t['from']} (resident {t['from_id']})" + (f", note: {t['note']}" if t["note"] else ""))
    lines.append("People in town, by resident number: " + ", ".join(f"{q['id']} {q['name']} ({q['job']}{', ' + q['role'] if q['role'] else ''})" for q in view["people"]))
    lines.append("First call private_thought with what this person really thinks right now." + (
        " Then call private_opinion: is this person privately for the law, against it, or has no opinion." if view["phase"] == "decide" else ""))
    if view["phase"] == "plan":
        lines.append("Decide how this person splits the free hours of this month: call plan_month. They may also post, comment, message or send coins now.")
    elif view["phase"] == "talk":
        lines.append("Do what this person would do now: post, comment, follow someone, write privately, send coins, or nothing (call nothing_now).")
    elif me["can_vote"]:
        lines.append("This person may vote now (call vote) or skip it (call nothing_now). They may also do other things.")
    else:
        lines.append("This person has no vote. They may still post, comment, write privately or send coins, or do nothing (call nothing_now).")
    return "\n".join(lines)


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


def tools(view: dict) -> list[dict]:
    text, number = {"type": "string"}, {"type": "integer", "description": "resident number"}
    out = [_fn("private_thought", "What this person really thinks at this moment, one or two sentences in Russian. Nobody else ever sees it. Call it first, every time.", {"text": text}, ["text"])]
    if view["phase"] == "decide":
        out.append(_fn("private_opinion", "What this person privately thinks of this month's law, whether or not they have a vote. Never shown to anyone. Call it every time.",
                       {"choice": {"type": "string", "enum": ["for", "against", "none"], "description": "none: no opinion"}}, ["choice"]))
    if view["phase"] == "plan":
        props = {"feed_hours": {"type": "integer", "description": "hours for reading the feed this month"},
                 "people_hours": {"type": "integer", "description": "hours with family and friends"},
                 "study_law": {"type": "boolean", "description": "spend 4 hours on the town's calculation of this month's law"}}
        if view["hall"]:
            props["hall_hours"] = {"type": "integer", "description": "hours for reading the hall of those who decide; about 2 hours per message read in each part of the month"}
        if view["election"]:
            props["run_for_council"] = {"type": "boolean", "description": "put oneself forward for the council"}
        out.append(_fn("plan_month", "Split this month's free hours. The rest goes to rest and everyday matters.", props, ["feed_hours", "people_hours", "study_law"]))
    out += [
        _fn("write_post", "Publish a post in the feed, about anything.", {"text": text, "about_law": {"type": "boolean"}}, ["text", "about_law"]),
        _fn("comment", "Comment on a post from the feed.", {"post_id": {"type": "integer", "description": "the number after # of the post"}, "text": text}, ["post_id", "text"]),
        _fn("follow", "Follow a resident: their posts come first in the feed.", {"person_id": number}, ["person_id"]),
        _fn("send_message", "Send a private message to a resident.", {"to_id": number, "text": text}, ["to_id", "text"]),
        _fn("send_coins", "Send own coins to a resident.", {"to_id": number, "amount": {"type": "integer"}, "note": text}, ["to_id", "amount", "note"]),
    ]
    if view["delegation"]:
        out.append(_fn("delegate_vote", "Hand own vote to a resident this person trusts, or take it back by giving own number or -1.", {"to_id": number}, ["to_id"]))
    if view["hall"]:
        out.append(_fn("speak_in_hall", "Say something in the hall of those who decide about this month's law. Only those who have the right to decide see it.", {"text": text}, ["text"]))
    out.append(_fn("remember", "Keep one short line in mind for the coming months: a promise, a debt, an impression of someone. Use only when there is something worth keeping.", {"text": text}, ["text"]))
    if view["phase"] == "decide" and view["me"]["can_vote"]:
        out.append(_fn("vote", "Vote on this month's law.", {"choice": {"type": "string", "enum": ["for", "against", "abstain"]}}, ["choice"]))
    if view["election"] and view["election"]["stage"] == "vote" and view["election"]["candidates"]:
        out.append(_fn("vote_candidate", "Vote for one council candidate.", {"candidate_id": number}, ["candidate_id"]))
    if view["phase"] != "plan":
        out.append(_fn("nothing_now", "Do nothing more now.", {}, []))
    return out


RENAME = {"speak_in_hall": "hall", "private_thought": "thought", "private_opinion": "opinion", "plan_month": "plan", "write_post": "post", "send_message": "message", "send_coins": "transfer", "delegate_vote": "delegate"}


def parse(tool_calls) -> list[dict]:
    """Вызовы инструментов из ответа модели превращаются в действия мира. Нераспознанное отбрасывается."""
    actions = []
    for call in tool_calls or []:
        fn = call["function"] if isinstance(call, dict) else call.function
        name = fn["name"] if isinstance(fn, dict) else fn.name
        raw = fn["arguments"] if isinstance(fn, dict) else fn.arguments
        try:
            a = json.loads(raw or "{}") if isinstance(raw, str) else dict(raw or {})
        except ValueError:
            continue
        a = a.get("arguments", a) if isinstance(a.get("arguments"), dict) else a
        if name == "nothing_now":
            continue
        if name == "write_post":
            a["about_bill"] = bool(a.pop("about_law", False))
        actions.append({"type": RENAME.get(name, name), **a})
    return actions
