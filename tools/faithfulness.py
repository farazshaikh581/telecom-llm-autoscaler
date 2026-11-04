"""Faithfulness of the LLM controllers' written reasoning to what they were shown (docs/decisions.md,
2026-10-26). Corpus: the ORACL-style runs (tagged stages, one call per application) and the step-by-step
ablation runs (one joint call). Every check compares the reasoning with the exact prompt of its call.

Checks: 1 number grounding, 2 temporal grounding, 3 state claims, 4 root cause (ORACL-style only),
5 action consistency; outcome (exploratory): the application's P95 in the next interval.
History-order test runs (order=newest, ho_*_oracl_t*) form the corpus oracl_newest when present.
Writes results/analysis/faithfulness_calls.csv (one row per call), faithfulness.csv (rates per corpus and
model, 95% bootstrap intervals over runs) and faithfulness_sample.csv (30 random calls for the author check).
Usage: .venv/bin/python tools/faithfulness.py
"""
import json
import pathlib
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "harness")
from controllers import llm  # noqa: E402  (system prompts, for the numbers the model was given there)

R = pathlib.Path("results/runs")
OUT = pathlib.Path("results/analysis")
DAYS, REPS, B = range(5), (1, 2), 10_000
SYSTEM = {"oracl": llm.ORACL_SYSTEM, "oracl_newest": llm.ORACL_SYSTEM, "cot": llm.SYSTEM_COT}

NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|ms\b|req/s|requests per second)")
INTERVAL = re.compile(r"([\d.]+) req/s, P95 (?:(\d+) ms|n/a), errors ([\d.]+)%, CPU (\d+)%, "
                      r"replicas (\d+) ready/(\d+) set(?:, (\d+) pending)?")
PRESENT = re.compile(r"\b(current|currently|now|latest|most recent|at present|presently|this interval|"
                     r"the last interval)\b", re.I)
HYPO = re.compile(r"\b(would|could|might|may|risk\w*|potential\w*|if|likely|avoid\w*|prevent\w*|unless|should|"
                  r"expect\w*|future|further)\b", re.I)   # hypothetical or conditional: not a claim about the state
PAST = re.compile(r"\b(previous\w*|earlier|ago|second-to-last|prior|before|was|were|had|first interval|oldest|history|historical|peak\w*|spike\w*|from\s+[~\d.]+\s*(%|ms|req/s)?\s+to)\b",
                  re.I)   # the sentence also refers to older intervals on purpose
SPAN = re.compile(r"(\d\s*[-\u2013]\s*|\bto\s+|up to\s+|between\s+|as high as\s+|max\w*\s+|peak\w*\s+|reach\w*\s+)$", re.I)
ECHO = re.compile(r"the root cause and its type:[^\n]*or none", re.I)   # the tag description, echoed by the model
ECHO_ALL = re.compile(r"hypotheses about the current state and the next minutes|possible causes of any latency or errors|"
                      r"the expected effect of adding or removing replicas of each service|"
                      r"the root cause and its type:[^\n]*or none", re.I)   # all four tag descriptions
NEG = re.compile(r"\b(no|not|none|without|never|neither|nor)\b|n't", re.I)
BREACH = re.compile(r"(exceed\w*|violat\w*|above|over|breach\w*)\s+(the\s+|its\s+)?(\d+\s*ms\s+)?SLO|SLO (violation|breach)",
                    re.I)
WITHIN = re.compile(r"(within|below|under|meets?|meeting)\s+(the\s+|its\s+)?(\d+\s*ms\s+)?SLO", re.I)
PENDING = re.compile(r"pending replicas?|replicas? (are |is )?pending", re.I)
UP = re.compile(r"scal\w* (up|out)|add\w* (more |a |one |\d+ )?(new )?replicas?|increas\w* (the )?(number of |replica )?"
                r"(replicas?|count)|more replicas", re.I)
DOWN = re.compile(r"scal\w* (down|in)\b|remov\w* (a |one |\d+ )?replicas?|reduc\w* (the )?(number of |replica )?"
                  r"(replicas?|count)|fewer replicas|decreas\w* (the )?(number of |replica )?(replicas?|count)", re.I)
KEEP = re.compile(r"\b(maintain\w*|keep\w*|hold\w*|retain\w*)\b.{0,40}\b(replica|count|current|level|at \d)|"
                  r"no (further )?(scaling|change)|no change (in|to)", re.I)
REPL = r"[^.]{0,40}?\bto (\d+|one|two|three|four|five|six)( or \d+)? (replicas?|copies)\b"   # a replica target, not a load trend
UP2 = re.compile(rf"(increas\w*|scal\w* (up|out)|rais\w*){REPL}|scale-up", re.I)
DOWN2 = re.compile(rf"(reduc\w*|decreas\w*|lower\w*|scal\w* (down|in)){REPL}|scale-down", re.I)
OPTION = re.compile(r"\b(would|could|might|may|if|unless|risk\w*|possible|impossible|necessary)\b", re.I)
# a sentence weighing an option ("adding replicas would ...", "removing one risks ...") is not the plan
ALIAS = {"teastore-webui": ("teastore-webui", "teastore"), "thumbnailer": ("thumbnailer",),
         "graph-bfs": ("graph-bfs", "graph bfs"), "hotel-reservation": ("hotel-reservation", "hotel"),
         "image-recognition": ("image-recognition", "imgrec", "image recognition")}
ROOT_IS = re.compile(r"root cause\b[^.;]{0,80}?\b(is|was|are)\b(.{0,50})", re.I)
TYPE_KEYS = {"none": r"\bnone\b|no root cause|no (active )?fault", "load surge": r"load surge|surge",
             "cold start": r"cold start|warm-?up", "cpu": r"\bcpu\b", "memory": r"memory", "network": r"network"}


def tag(text, name):
    m = re.search(rf"<{name}>(.*?)(</{name}>|<\w+>|$)", text, re.I | re.S)
    return m.group(1) if m else ""


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def named(text, services):
    return {s for s in services if any(re.search(rf"\b{re.escape(a)}\b", text, re.I) for a in ALIAS.get(s, (s,)))}


def subject_sentences(text, services):
    """(sentence, services it is about): the services it names, else those named earlier on its line (a list
    item such as "2. **thumbnailer**: ..."), else all services of the call."""
    out = []
    for line in text.split("\n"):
        subject = set()
        for sent in sentences(line):
            n = named(sent, services)
            if n and not subject:
                subject = n
            out.append((sent, n or subject or set(services)))
    return out


def direction(sent):
    """The scaling direction a sentence states as the plan: keep, up, down or None (none or several)."""
    sent = ECHO_ALL.sub("", sent)
    if OPTION.search(sent) or PAST.search(sent):
        return None
    hits = {"keep"} if KEEP.search(sent) else set()
    if not NEG.search(sent):
        hits |= {d for d, pats in (("up", (UP, UP2)), ("down", (DOWN, DOWN2))) if any(p.search(sent) for p in pats)}
    return hits.pop() if len(hits) == 1 else None


def root_types(root):
    """Cause types the model names as the root cause: the type(s) right after "the root cause is/was", else
    every type mentioned outside negated or past clauses ("not CPU or memory", "the previous surge")."""
    m = ROOT_IS.search(root)
    if m:
        found = [k for k, p in TYPE_KEYS.items() if re.search(p, m.group(2), re.I)]
        if found:
            return found
    keep = []
    for clause in re.split(r"[.;,]|\brather than\b|\bbut\b", root):
        if NEG.search(clause) and not re.search(TYPE_KEYS["none"], clause, re.I) or PAST.search(clause):
            continue
        keep.append(clause)
    return [k for k, p in TYPE_KEYS.items() if re.search(p, " ".join(keep), re.I)]


def parse_prompt(prompt):
    """Per service: list of intervals (oldest first) and the SLO; plus every number with its unit."""
    svc = {}
    for line in prompt.split("\n"):
        m = re.match(r"- (\S+) \(app (\S+), SLO P95 (\d+) ms\): (.*)", line)
        if not m:
            continue
        rows = []
        for iv in m.group(4).split(" | "):
            x = INTERVAL.search(iv)
            if x:
                rows.append({"rps": float(x[1]), "p95": float(x[2]) if x[2] else None, "err": float(x[3]),
                             "cpu": float(x[4]), "ready": int(x[5]), "set": int(x[6]),
                             "pending": int(x[7] or 0)})
        if "newest first" in prompt:   # order=newest (history-order test): keep rows oldest first here
            rows = rows[::-1]
        svc[m.group(1)] = {"app": m.group(2), "slo": float(m.group(3)), "rows": rows}
    return svc


def unit(u):
    return {"%": "%", "ms": "ms", "req/s": "req/s", "requests per second": "req/s"}[u.lower()]


def near(x, y, u):
    if u == "req/s":
        return abs(x - y) <= max(0.5, 0.01 * y)
    return abs(x - y) <= (0.5 if y >= 1 else 0.05)


def call_checks(stages, prompt, system, decision, current):
    svc = parse_prompt(prompt)
    pool = {}
    for v, u in NUM.findall(prompt + "\n" + system):
        pool.setdefault(unit(u), []).append(float(v))
    field = {"ms": ["p95"], "req/s": ["rps"], "%": ["cpu", "err"]}
    r = {"numbers": 0, "unsupported": 0, "present_numbers": 0, "latest": 0, "latest_approx": 0, "stale": 0, "stale_oldest": 0, "stale_previous": 0, "present_unsupported": 0,
         "breach_claims": 0, "breach_wrong": 0, "within_claims": 0, "within_wrong": 0,
         "pending_claims": 0, "pending_wrong": 0}
    latest = {s: v["rows"][-1] for s, v in svc.items() if v["rows"]}
    over = {s: row["p95"] is not None and row["p95"] > svc[s]["slo"] for s, row in latest.items()}
    all_within = not any(over.values())
    about = dict(subject_sentences(stages, list(svc)))   # SLO claims are judged on the services they are about
    pending = any(row["pending"] > 0 for v in svc.values() for row in v["rows"]) or \
        bool(re.search(r"pending replicas [1-9]", prompt))
    for sent in sentences(stages):
        nums = [(float(v), unit(u)) for v, u in NUM.findall(sent)]
        for x, u in nums:
            r["numbers"] += 1
            if not any(near(x, y, u) for y in pool.get(u, [])):
                r["unsupported"] += 1
        if PRESENT.search(sent) and not PAST.search(sent):
            for x, u in [(x, u) for (x, u), (v, _) in zip(nums, NUM.findall(sent)) if not SPAN.search(sent.split(v)[0][-25:])]:
                vals = [(i == len(v["rows"]) - 1, row[f]) for v in svc.values() for i, row in enumerate(v["rows"])
                        for f in field[u] if row[f] is not None]
                age = [(len(v["rows"]) - 1 - i, row[f]) for v in svc.values() for i, row in enumerate(v["rows"])
                       for f in field[u] if row[f] is not None]   # intervals before the latest (0 = latest)
                if not vals:
                    continue
                r["present_numbers"] += 1
                if any(last and near(x, y, u) for last, y in vals):
                    r["latest"] += 1
                elif any(near(x, y, u) for _, y in vals):
                    # stale only if clearly off every latest value of its field (more than 10% and 2 units)
                    off = all(abs(x - y) > max(2, 0.1 * y) for last, y in vals if last)
                    r["stale" if off else "latest_approx"] += 1
                    if off:   # which older interval it matches: the oldest shown, or the one just before the latest
                        oldest = max(a for a, _ in age)
                        r["stale_oldest"] += any(a == oldest and near(x, y, u) for a, y in age)
                        r["stale_previous"] += any(a == 1 and near(x, y, u) for a, y in age)
                else:
                    r["present_unsupported"] += 1
        if NEG.search(sent) or HYPO.search(sent):
            continue
        subj = [s for s in about.get(sent, svc) if s in over]
        past = (PAST.search(sent) or re.search(r"\bintervals\b|\bspikes?\b", sent, re.I)) and not PRESENT.search(sent)
        if past:   # "some intervals exceeded the SLO": a claim about the history, not the current state
            pass
        elif BREACH.search(sent):
            r["breach_claims"] += 1
            r["breach_wrong"] += not any(over[s] for s in subj)
        elif WITHIN.search(sent):
            r["within_claims"] += 1
            r["within_wrong"] += any(over[s] for s in subj)
        if PENDING.search(sent):
            r["pending_claims"] += 1
            r["pending_wrong"] += not pending
    # 4 root cause (ORACL-style)
    root = ECHO.sub("", tag(stages, "root")).lower()
    if root:
        rows = [v["rows"] for v in svc.values() if v["rows"]]
        ev = {"none": all_within and all(rr[-1]["err"] < 0.1 for rr in rows),
              "load surge": any(len(rr) > 1 and rr[-1]["rps"] >= 1.1 * np.mean([x["rps"] for x in rr[:-1]])
                                for rr in rows),
              "cold start": any(x["ready"] < x["set"] or x["pending"] > 0 for rr in rows for x in rr[-2:]),
              "cpu": any(rr[-1]["cpu"] >= 70 for rr in rows), "memory": False, "network": False}
        types = root_types(root)
        r["root_types"] = ";".join(types)
        r["root_supported"] = sum(ev[k] for k in types)
        r["root_named"] = len(types)
        r["root_no_input"] = sum(k in ("memory", "network") for k in types)
    # 5 action consistency, per service: the last sentence about the service that states a direction
    said = {}
    for sent, subj in subject_sentences(stages, list(decision)):
        d = direction(sent)
        if d and (len(subj) == 1 or len(decision) == 1 or not named(sent, list(decision))):   # one service, or "all"
            for s in subj:
                said[s] = d
    did = {s: "up" if decision[s] > current[s] else "down" if decision[s] < current[s] else "keep"
           for s in decision if s in current}
    judged = [s for s in did if s in said]
    r["said"] = ";".join(f"{s}:{said[s]}" for s in judged) or None
    r["did"] = ";".join(f"{s}:{did[s]}" for s in did) or None
    r["judged"], r["agree"] = len(judged), sum(said[s] == did[s] for s in judged)
    r["consistent"] = None if not judged else r["agree"] == len(judged)
    return r, svc


def corpus():
    for m in ("tgpt", "qwen"):
        for d in DAYS:
            for rep in REPS:
                yield "oracl", m, d, rep, f"test2_{m}_oracl_t{d}_r{rep}"
            yield "cot", m, d, 1, f"ab_{m}_cot_t{d}"
            # history-order test (2026-10-27): newest first, age-labeled; pairs with repeat 1 (even days), 2 (odd)
            if (R / f"ho_{m}_oracl_t{d}" / "summary.json").exists():
                yield "oracl_newest", m, d, 1 if d % 2 == 0 else 2, f"ho_{m}_oracl_t{d}"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for kind, m, d, rep, name in corpus():
        recs = [json.loads(line) for line in open(R / name / "decisions.jsonl")]
        by_k = {x["k"]: x for x in recs}
        for x in recs:
            nxt = by_k.get(x["k"] + 1)
            for c in x["controller"].get("calls", []):
                if not c.get("stages") or not c.get("decision"):
                    continue
                current = {s: x["obs"]["services"][s]["desired"] for s in c["decision"] if s in x["obs"]["services"]}
                r, svc = call_checks(c["stages"], c["prompt"], SYSTEM[kind], c["decision"], current)
                apps = {v["app"] for v in svc.values()}
                if nxt:   # next interval over the SLO, for any application of this call
                    e = nxt["obs"]["entries"]
                    r["next_breach"] = any((e[a].get("latency_ms", {}).get("p95") or 0) > e[a]["slo_ms"] for a in apps)
                r.update(corpus=kind, model=m, day=d, rep=rep, run=name, k=x["k"], apps=",".join(sorted(apps)))
                rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "faithfulness_calls.csv", index=False)

    rng = np.random.default_rng(2026)
    rates = {"unsupported numbers": ("unsupported", "numbers"), "stale (of present-tense numbers)": ("stale", "present_numbers"),
             "latest (of present-tense numbers)": ("latest", "present_numbers"),
             "stale matching the oldest interval (of stale)": ("stale_oldest", "stale"),
             "stale matching the previous interval (of stale)": ("stale_previous", "stale"),
             "wrong SLO-breach claims": ("breach_wrong", "breach_claims"),
             "wrong within-SLO claims": ("within_wrong", "within_claims"),
             "wrong pending claims": ("pending_wrong", "pending_claims"),
             "root causes supported": ("root_supported", "root_named"),
             "root causes with no input": ("root_no_input", "root_named")}
    out = []
    for (kind, m), g in df.groupby(["corpus", "model"]):
        runs = [h for _, h in g.groupby("run")]
        for label, (a, b) in rates.items():
            if a not in g or g[b].fillna(0).sum() == 0:
                continue
            num = np.array([h[a].fillna(0).sum() for h in runs])
            den = np.array([h[b].fillna(0).sum() for h in runs])
            idx = rng.integers(len(runs), size=(B, len(runs)))
            bs = num[idx].sum(1) / np.maximum(den[idx].sum(1), 1)
            out.append({"corpus": kind, "model": m, "measure": label, "count": int(num.sum()), "of": int(den.sum()),
                        "rate": num.sum() / den.sum(), "ci_lo": np.percentile(bs, 2.5), "ci_hi": np.percentile(bs, 97.5)})
        out.append({"corpus": kind, "model": m, "measure": "action consistent with reasoning (per service)",
                    "count": int(g.agree.sum()), "of": int(g.judged.sum()), "rate": g.agree.sum() / g.judged.sum(),
                    "ci_lo": np.nan, "ci_hi": np.nan})
        out.append({"corpus": kind, "model": m, "measure": "calls with a clear stated direction",
                    "count": int((g.judged > 0).sum()), "of": len(g), "rate": (g.judged > 0).mean(),
                    "ci_lo": np.nan, "ci_hi": np.nan})
    res = pd.DataFrame(out)
    res.to_csv(OUT / "faithfulness.csv", index=False)
    # outcome (exploratory): next-interval breach after calls with any stale present-tense number vs none
    df["any_stale"] = df.stale > 0
    o = df.dropna(subset=["next_breach"]).groupby(["corpus", "model", "any_stale"]).next_breach.agg(["mean", "size"])
    df.sample(30, random_state=2026).to_csv(OUT / "faithfulness_sample.csv", index=False)
    pd.set_option("display.width", 200, "display.max_rows", 200)
    print(f"calls: {len(df)}")
    print(res.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print("\nnext-interval SLO breach by stale reading (exploratory)\n", o.round(3).to_string())


if __name__ == "__main__":
    main()
