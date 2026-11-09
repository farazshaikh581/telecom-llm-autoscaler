"""Prompt-layout replay (docs/decisions.md, 2026-11-06): order and age labels of the history, separated.

Offline, no cluster. 500 (decision, application) pairs per model from the reasoning-first runs of the main matrix
(test2_{tgpt,qwen}_oracl, scored minutes 15-74, 50 per run, seed 2026). Each observation is rebuilt into the
reasoning-first prompt in four layouts (order oldest|newest x labels no|yes) and sent through the same two calls as
in the runs. The rebuilt oldest-first prompt without labels must equal the logged prompt.

  .venv/bin/python tools/layout_replay.py call  <name> <port> [workers]   # writes results/analysis/layout_replay/<name>.jsonl
  .venv/bin/python tools/layout_replay.py check <name> [<name> ...]       # writes results/analysis/layout_replay.csv
"""
import json
import pathlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, "harness")
sys.path.insert(0, "tools")
from controllers import llm  # noqa: E402
import faithfulness as F     # noqa: E402

R = pathlib.Path("results/runs")
OUT = pathlib.Path("results/analysis/layout_replay")
LAYOUTS = [("oldest", False), ("oldest", True), ("newest", False), ("newest", True)]
APPS = {}
for _s, _c in llm.SERVICES.items():
    APPS.setdefault(_c["entry"], []).append(_s)


def history(obs, services, order, labels):
    """The history block of prompt(), in one of the four layouts. ('oldest', False) is the main matrix."""
    if (order, labels) == ("oldest", False):
        return llm.prompt(obs, services)
    if (order, labels) == ("newest", True):
        return llm.prompt(obs, services, newest=True)
    rows = obs["history"] + [{k: v for k, v in obs.items() if k != "history"}]
    n = len(rows)
    if order == "newest":
        rows = rows[::-1]
        head = f"Last {n} intervals (60 s each, newest first) per service."
        tag = [""] * n
    else:
        head = f"Last {n} intervals (60 s each, oldest first, each labeled with its age; \"now\" is the latest) per service."
        tag = [f"{llm.age(n - 1 - i)}: " for i in range(n)]
    lines = [head + " Request rate, latency and errors are for the service's application entry; CPU is the "
             "service's own, as % of its replicas' requests:"]
    for svc in services:
        app = obs["services"][svc]["entry"]
        lines.append(f"- {svc} (app {app}, SLO P95 {obs['entries'][app]['slo_ms']} ms): "
                     + " | ".join(t + llm._fmt(o, svc) for t, o in zip(tag, rows)))
    lines.append("Choose the replica count for " + ("every service" if len(services) > 1 else "this service")
                 + " for the next interval.")
    return "\n".join(lines)


def oracl_prompt(obs, app, order, labels):
    base = llm.oracl_prompt(obs, app, APPS[app])
    head = base[:base.index(llm.prompt(obs, APPS[app]))]
    return head + history(obs, APPS[app], order, labels)


def sample():
    rng = np.random.default_rng(2026)
    out = []
    for m in ("tgpt", "qwen"):
        for d in range(5):
            for rep in (1, 2):
                run = f"test2_{m}_oracl_t{d}_r{rep}"
                recs = [json.loads(x) for x in open(R / run / "decisions.jsonl")]
                pairs = [(x, c) for x in recs if 15 <= x["k"] < 75 for c in x["controller"].get("calls", [])
                         if c.get("stages") and c.get("decision")]
                for i in rng.choice(len(pairs), 50, replace=False):
                    x, c = pairs[i]
                    app = c["prompt"].split(",")[0].removeprefix("Application: ")
                    assert oracl_prompt(x["obs"], app, "oldest", False) == c["prompt"], (run, x["k"], app)
                    out.append({"model": m, "run": run, "day": d, "rep": rep, "k": x["k"], "app": app,
                                "obs": x["obs"], "current": {s: x["obs"]["services"][s]["desired"] for s in APPS[app]}})
    return out


def post(port, body):
    r = requests.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=body, timeout=180)
    r.raise_for_status()
    return r.json()


def ask(port, model, user, services):
    """The two calls of _ask_oracl: tagged reasoning, then the counts (schema-enforced)."""
    msgs = [{"role": "system", "content": llm.ORACL_SYSTEM}, {"role": "user", "content": user}]
    # the Mistral tokenizer of vLLM takes no chat-template arguments (the model has no thinking mode)
    kw = {} if "Mistral" in model else {"chat_template_kwargs": {"enable_thinking": False}}
    r1 = post(port, {"model": model, "temperature": 0, "max_tokens": 700, "messages": msgs, "stop": ["</root>"], **kw})
    stages = r1["choices"][0]["message"]["content"].strip() + "</root>"
    reps = {s: {"type": "integer", "minimum": llm.CFG["min_replicas"], "maximum": llm.CFG["max_replicas"]} for s in services}
    schema = {"type": "object", "additionalProperties": False, "required": services, "properties": reps}
    msgs += [{"role": "assistant", "content": stages},
             {"role": "user", "content": "Now give the replica count for each service as JSON."}]
    r2 = post(port, {"model": model, "temperature": 0, "max_tokens": 100, "messages": msgs, **kw,
                     "response_format": {"type": "json_schema", "json_schema": {"name": "replicas", "schema": schema}}})
    dec = json.loads(r2["choices"][0]["message"]["content"])
    return stages, {s: int(dec[s]) for s in services}


def call(name, port, workers=4):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.jsonl"
    done = {(j["run"], j["k"], j["app"], j["order"], j["labels"]) for j in map(json.loads, open(path))} if path.exists() else set()
    model = requests.get(f"http://127.0.0.1:{port}/v1/models", timeout=30).json()["data"][0]["id"]
    # the two study models replay their own calls; another model replays every second pair of both (500)
    items = [s for s in sample() if s["model"] == name] if name in ("tgpt", "qwen") else sample()[::2]
    jobs = [(s, o, lab) for s in items for o, lab in LAYOUTS if (s["run"], s["k"], s["app"], o, lab) not in done]
    print(f"{model}: {len(jobs)} prompts to send ({len(done)} done)", flush=True)
    lock = threading.Lock()

    def one(job):
        s, o, lab = job
        user = oracl_prompt(s["obs"], s["app"], o, lab)
        rec = {k: s[k] for k in ("model", "run", "day", "rep", "k", "app", "current")}
        rec.update(replay_model=model, order=o, labels=lab, prompt=user)
        try:
            rec["stages"], rec["decision"] = ask(port, model, user, APPS[s["app"]])
        except Exception as e:
            rec["error"] = repr(e)[:300]
        with lock, open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    with ThreadPoolExecutor(workers) as ex:
        for i, _ in enumerate(ex.map(one, jobs), 1):
            if i % 100 == 0:
                print(i, flush=True)


def boot(df, col, den, rng, n=10_000):
    """Pooled share with a 95% interval that resamples runs."""
    g = df.groupby("run")[[col, den]].sum()
    idx = rng.integers(len(g), size=(n, len(g)))
    s = g[col].to_numpy()[idx].sum(1) / np.maximum(g[den].to_numpy()[idx].sum(1), 1)
    return 100 * g[col].sum() / max(g[den].sum(), 1), 100 * np.percentile(s, 2.5), 100 * np.percentile(s, 97.5)


def check(names):
    rows = []
    for name in names:
        for j in map(json.loads, open(OUT / f"{name}.jsonl")):
            if "error" in j:
                continue
            r, _ = F.call_checks(j["stages"], j["prompt"], llm.ORACL_SYSTEM, j["decision"], j["current"])
            rows.append({"replay": name, **{k: j[k] for k in ("model", "run", "k", "app", "order", "labels")},
                         **{k: r[k] for k in ("present_numbers", "latest", "stale", "breach_claims", "breach_wrong")},
                         "decision": json.dumps(j["decision"], sort_keys=True)})
    d = pd.DataFrame(rows)
    base = d[(d.order == "oldest") & (~d.labels)].set_index(["replay", "run", "k", "app"])["decision"]
    d["changed"] = [int(base.get((r.replay, r.run, r.k, r.app)) != r.decision) for r in d.itertuples()]
    d["one"] = 1
    rng = np.random.default_rng(1)
    res = []
    for (name, o, lab), g in d.groupby(["replay", "order", "labels"]):
        out = {"replay": name, "order": o, "labels": lab, "calls": len(g)}
        for key, col, den in (("stale", "stale", "present_numbers"), ("latest", "latest", "present_numbers"),
                              ("wrong_breach", "breach_wrong", "breach_claims"), ("changed", "changed", "one")):
            out[key], out[key + "_lo"], out[key + "_hi"] = boot(g, col, den, rng)
        res.append(out)
    res = pd.DataFrame(res).round(2)
    res.to_csv(OUT.parent / "layout_replay.csv", index=False)
    print(res.to_string())


if __name__ == "__main__":
    if sys.argv[1] == "call":
        call(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 4)
    else:
        check(sys.argv[2:])
