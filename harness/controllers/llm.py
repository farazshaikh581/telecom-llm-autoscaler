"""Frozen LLM as autoscaler, served locally by vLLM (OpenAI-compatible API).

mode=joint: one prompt with every service, one JSON answer with a replica count per service.
mode=per-service: one prompt per service with only its own data, sent in parallel.
mode=oracl: ORACL-style prompting (Bai et al., arXiv 2602.05292; their fine-tuned model is not
  released, so its prompting runs on our frozen models): one agent per application (a subgraph of
  the call graph), sent in parallel. Each prompt carries deployment descriptions (role, resource
  type C/M/N, readiness time), the call graph, the cluster state in Low/Medium/High bins, and the
  telemetry. Each agent first reasons in ORACL's four tagged stages (<think>, <Fault>,
  <Counterfactual>, <root>; free text, stops at </root>, at most 700 tokens), then gives the
  replica counts in a second, schema-enforced call that sees its own reasoning. (One JSON answer
  holding the long reasoning fields degenerated into endless whitespace under grammar-forced
  decoding, 2026-09-28.) A rule-based check rejects unsafe
  decisions (no scale-in of an application that is over its SLO); agents do not overlap, so no
  conflicts to resolve.
The answer is forced to a JSON schema (vLLM structured outputs). Temperature 0. Thinking off
unless thinking=1. If the reply is late (past timeout) or invalid, replicas stay as they are
and the failure is logged. Every prompt and reply is logged with its timing and token counts.

Params: port (required), mode=joint|per-service|oracl, thinking=0|1, timeout=<s> (default 50),
max_tokens (default 400, 6000 with thinking; oracl: 700 for the reasoning, 100 for the counts),
prompt=default|alt (joint only: alt is SYSTEM_ALT, the same facts reworded and reordered, for the
prompt-wording check in docs/analysis_plan.md), ablate=zeroshot|history|domain|cot (joint only; the
prompt ablation of docs/analysis_plan.md, after the strategies of our earlier LLM-autoscaling papers):
  zeroshot: SYSTEM_BARE (task, interval, replica range, goal; no cluster, budget or start-up facts)
            and the current interval only;
  history:  SYSTEM_BARE and the current and previous 5 intervals;
  domain:   SYSTEM and the current interval only;
  cot:      SYSTEM_COT, current and previous 5 intervals; step-by-step reasoning first (free text, at most 700
            tokens), then the counts in a second, schema-enforced call that sees it (as mode=oracl).
  The default (no ablate) is SYSTEM and the current and previous 5 intervals.
order=oldest|newest (default oldest): the order of the intervals in the prompt. newest: newest first, each labeled
  with its age ("now", "1 min ago", ...); the history-order test of docs/decisions.md (2026-10-27).
The system prompt is the one timed in tools/llm_decision_timing.py (docs/harness_notes.md).
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import CFG, SERVICES

from .base import Controller

FIXED_COPIES = sum(f["copies"] for f in CFG.get("fixed", {}).values())
ROOM = CFG["copies_available"] - FIXED_COPIES   # replicas the scaled services can use together

# New-service shift (docs/decisions.md 2026-10-28): one line on the added service, as an operator would add it;
# empty for the four-service configuration, so SYSTEM is unchanged there.
IMGREC = ("image-recognition is a serverless function that classifies images with a ResNet-50 model on CPU "
          "(one request at a time per replica, about 110 ms each); a new replica is ready in about 2 s, and its first "
          "request then takes about 2 s while it loads the model.\n") if "image-recognition" in SERVICES else ""
SYSTEM = (
    "You are the autoscaler for a Kubernetes cluster that runs several applications at once.\n"
    "Every 60 seconds you set the replica count of each scaled service.\n"
    f"Cluster: {CFG['workers']} worker nodes, {CFG['workers'] * CFG['worker_vcpus']} vCPU in total. "
    "Each replica requests 0.5 CPU and may use up to 1 CPU.\n"
    f"Replicas per service: {CFG['min_replicas']} to {CFG['max_replicas']}. "
    f"The sum of the scaled services' replicas should stay within {ROOM} unless an SLO is at risk "
    f"(the applications' other services use {FIXED_COPIES} more replicas, fixed).\n"
    "Goal: keep every application's P95 latency below its SLO with as few replicas as possible, and avoid oscillation.\n"
    "A new replica of most services is ready in about 2 s; one of teastore-webui needs about 25 s, then about "
    "a minute of Java warm-up. hotel-reservation is the hotel application's scaled service; its SLO is the hotel "
    "application's end-to-end latency.\n"
    + IMGREC +
    "Reply only with the JSON object requested."
)
SYSTEM_ONE = SYSTEM.replace("the replica count of each scaled service", "the replica count of one scaled service") \
    .replace(f"The sum of the scaled services' replicas should stay within {ROOM} unless an SLO is at risk "
             f"(the applications' other services use {FIXED_COPIES} more replicas, fixed).\n",
             "Other services share the cluster; use no more replicas than needed.\n")

# Prompt-wording check (2026-10-12): the same facts and numbers as SYSTEM, reworded and reordered.
SYSTEM_ALT = (
    "Role: horizontal autoscaler for a shared Kubernetes cluster that hosts several applications.\n"
    "Objectives, in order: (1) each application's 95th-percentile latency stays under its SLO; (2) with the "
    "fewest replicas that achieve it; (3) without moving replica counts up and down repeatedly.\n"
    f"Once a minute, choose how many replicas each scaled service runs, from {CFG['min_replicas']} to "
    f"{CFG['max_replicas']}.\n"
    f"Capacity: {CFG['workers'] * CFG['worker_vcpus']} vCPU over {CFG['workers']} worker nodes. A replica reserves "
    "0.5 CPU and can use at most 1 CPU.\n"
    f"Budget: the scaled services together should use at most {ROOM} replicas, and more only to protect an SLO; "
    f"{FIXED_COPIES} replicas of the applications' other services always run besides them.\n"
    "Start-up: a new teastore-webui replica serves after about 25 s and stays slow for about a minute while its "
    "Java code warms up; other services' replicas are ready in about 2 s.\n"
    "The hotel application scales only hotel-reservation; the hotel SLO applies to the application's "
    "end-to-end latency.\n"
    "Answer with the requested JSON object and nothing else."
)

# Prompt ablation (2026-10-13): the task without the domain facts, and the full prompt with reasoning first.
SYSTEM_BARE = (
    "You are the autoscaler for a Kubernetes cluster that runs several applications at once.\n"
    "Every 60 seconds you set the replica count of each scaled service.\n"
    f"Replicas per service: {CFG['min_replicas']} to {CFG['max_replicas']}.\n"
    "Goal: keep every application's P95 latency below its SLO with as few replicas as possible, and avoid oscillation.\n"
    "Reply only with the JSON object requested."
)
SYSTEM_COT = SYSTEM.replace("Reply only with the JSON object requested.",
                            "First reason step by step, in a few sentences: the load trend, each application's latency "
                            "against its SLO, and what more or fewer replicas of each service would do. You will then "
                            "be asked for the replica counts as JSON.")
assert SYSTEM_COT != SYSTEM


# ORACL-style context. Resource types as ORACL's tokens: C CPU-, M memory-, N network-intensive.
DEPLOY = {
    "teastore-webui": "TeaStore web front end (Java servlet, renders pages, calls persistence, auth, image and "
                      "recommender, which are not scaled); type C; ready after about 25 s, then slow for about "
                      "a minute while its Java code warms up",
    "thumbnailer": "serverless function, resizes images read from object storage (one request at a time per "
                   "replica, about 10 ms each); type C,N; ready after about 2 s",
    "graph-bfs": "serverless function, generates a graph and runs a breadth-first search in memory (one request at "
                 "a time per replica, about 25 ms each); type C; ready after about 2 s",
    "hotel-reservation": "hotel reservation and availability (Go, gRPC, cache and database behind it); type C; "
                         "ready after about 2 s; the hotel application's bottleneck",
    "image-recognition": "serverless function, classifies images read from object storage with a ResNet-50 model on "
                         "CPU (one request at a time per replica, about 110 ms each); type C; ready after about 2 s, "
                         "its first request then takes about 2 s while it loads the model",
}
CALL_GRAPH = {
    "teastore": "client -> teastore-webui -> {persistence, auth, image, recommender (fixed)}",
    "thumbnailer": "client -> thumbnailer -> object storage",
    "graph-bfs": "client -> graph-bfs",
    "imgrec": "client -> image-recognition -> object storage",
    "hotel": "client -> frontend (fixed) -> {search (fixed) -> {geo, rate (fixed)}, hotel-reservation, "
             "profile, recommendation, user (fixed)}",
}
ORACL_SYSTEM = (
    "You are an autoscaling agent for one application in a Kubernetes cluster shared with other applications.\n"
    "Every 60 seconds you set the replica count of the application's scaled services.\n"
    f"Cluster: {CFG['workers']} worker nodes, {CFG['workers'] * CFG['worker_vcpus']} vCPU in total, room for "
    f"{ROOM} replicas of all applications' scaled services together. Each replica requests 0.5 CPU and may use up "
    f"to 1 CPU. Replicas per service: {CFG['min_replicas']} to {CFG['max_replicas']}.\n"
    "Goal: keep the application's P95 latency below its SLO with as few replicas as possible, and avoid oscillation.\n"
    "Assess the interdependent system state systematically before proposing scaling actions. Reason in four stages, "
    "each in its tag and a few sentences long: <think>hypotheses about the current state and the next minutes</think> "
    "<Fault>possible causes of any latency or errors</Fault> <Counterfactual>the expected effect of adding or removing "
    "replicas of each service</Counterfactual> <root>the root cause and its type: CPU, memory, network, cold start, "
    "load surge, or none</root>. You will then be asked for the replica counts."
)


def _bin(x, lo, hi):
    return "Low" if x < lo else ("Medium" if x < hi else "High")


def oracl_prompt(obs, app, services, newest=False):
    e = obs["entries"][app]
    lat = e.get("latency_ms", {})
    p95 = lat.get("p95")
    total = sum(v["desired"] for v in obs["services"].values())
    cluster_cpu = sum(v["cpu_m"] for v in obs["services"].values()) / (1000 * CFG["workers"] * CFG["worker_vcpus"])
    lines = [f"Application: {app}, SLO P95 {e['slo_ms']} ms.",
             f"Call graph: {CALL_GRAPH[app]}.",
             "Deployment:"] + [f"- {s}: {DEPLOY[s]}" for s in services]
    lines.append(f"Cluster state: CPU {_bin(cluster_cpu, 0.4, 0.75)} ({100 * cluster_cpu:.0f}% of worker vCPU), "
                 f"scaled replicas {total} of {ROOM} ({_bin(total / ROOM, 0.6, 0.9)}), "
                 f"pending replicas {sum(v['pending'] for v in obs['services'].values())}.")
    lines.append(f"Application state: latency {_bin((p95 or 0) / e['slo_ms'], 0.5, 1.0)}, "
                 f"errors {_bin(e.get('error_rate', 0), 0.001, 0.01)}.")
    lines.append(prompt(obs, services, newest=newest))
    return "\n".join(lines)


def _fmt(o, svc):
    s = o["services"][svc]
    e = o["entries"].get(s["entry"], {})
    lat = e.get("latency_ms", {})
    p95 = f"{lat['p95']:.0f} ms" if "p95" in lat else "n/a"
    return (f"{e.get('rps', 0):.1f} req/s, P95 {p95}, errors {100 * e.get('error_rate', 0):.1f}%, "
            f"CPU {100 * s['cpu_util']:.0f}%, replicas {s['ready']} ready/{s['desired']} set"
            + (f", {s['pending']} pending" if s["pending"] else ""))


def age(i):
    return "now" if i == 0 else f"{i} min ago"


def prompt(obs, services, history=True, newest=False):
    rows = (obs["history"] if history else []) + [{k: v for k, v in obs.items() if k != "history"}]
    if newest and history:
        return prompt_newest(obs, services, rows[::-1])
    lines = [f"Last {len(rows)} intervals (60 s each, oldest first) per service. Request rate, latency and errors "
             f"are for the service's application entry; CPU is the service's own, as % of its replicas' requests:"
             if history else
             "Last interval (60 s) per service. Request rate, latency and errors are for the service's application "
             "entry; CPU is the service's own, as % of its replicas' requests:"]
    for svc in services:
        s = obs["services"][svc]
        app = s["entry"]
        lines.append(f"- {svc} (app {app}, SLO P95 {obs['entries'][app]['slo_ms']} ms): "
                     + " | ".join(_fmt(o, svc) for o in rows))
    if len(services) > 1:
        lines.append(f"Replicas set now, all services: {sum(obs['services'][s]['desired'] for s in SERVICES)}.")
    lines.append("Choose the replica count for " + ("every service" if len(services) > 1 else "this service")
                 + " for the next interval.")
    return "\n".join(lines)


def prompt_newest(obs, services, rows):
    """As prompt(), with the intervals newest first, each labeled with its age."""
    lines = [f"Last {len(rows)} intervals (60 s each, newest first, each labeled with its age; \"now\" is the latest) "
             f"per service. Request rate, latency and errors are for the service's application entry; CPU is the "
             f"service's own, as % of its replicas' requests:"]
    for svc in services:
        s = obs["services"][svc]
        app = s["entry"]
        lines.append(f"- {svc} (app {app}, SLO P95 {obs['entries'][app]['slo_ms']} ms): "
                     + " | ".join(f"{age(i)}: {_fmt(o, svc)}" for i, o in enumerate(rows)))
    if len(services) > 1:
        lines.append(f"Replicas set now, all services: {sum(obs['services'][s]['desired'] for s in SERVICES)}.")
    lines.append("Choose the replica count for " + ("every service" if len(services) > 1 else "this service")
                 + " for the next interval.")
    return "\n".join(lines)


class LLM(Controller):
    def __init__(self, **params):
        super().__init__(**params)
        self.base = f"http://127.0.0.1:{params['port']}/v1"
        self.mode = params.get("mode", "joint")
        self.thinking = params.get("thinking", "0") == "1"
        self.timeout = float(params.get("timeout", 50))
        self.max_tokens = int(params.get("max_tokens", 6000 if self.thinking else 400))
        self.system = {"default": SYSTEM, "alt": SYSTEM_ALT}[params.get("prompt", "default")]
        if self.system is SYSTEM_ALT and self.mode != "joint":
            raise ValueError("prompt=alt exists for mode=joint only")
        self.ablate = params.get("ablate")
        if self.ablate:
            if self.mode != "joint" or self.system is not SYSTEM:
                raise ValueError("ablate exists for mode=joint with the default prompt only")
            self.system = {"zeroshot": SYSTEM_BARE, "history": SYSTEM_BARE, "domain": SYSTEM, "cot": SYSTEM_COT}[self.ablate]
        self.history = self.ablate not in ("zeroshot", "domain")
        self.newest = {"oldest": False, "newest": True}[params.get("order", "oldest")]
        self.model = None

    def setup(self, ctx):
        with urllib.request.urlopen(self.base + "/models", timeout=10) as r:
            self.model = json.load(r)["data"][0]["id"]

    def info(self):
        system = {"joint": self.system, "per-service": SYSTEM_ONE, "oracl": ORACL_SYSTEM}[self.mode]
        return dict(super().info(), model=self.model, system=system)

    def _post(self, body):
        req = urllib.request.Request(self.base + "/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)

    def _ask_oracl(self, user, services):
        """ORACL-style: tagged reasoning (free text), then the counts (schema-enforced)."""
        t, rec = time.time(), {"prompt": user}
        msgs = [{"role": "system", "content": ORACL_SYSTEM}, {"role": "user", "content": user}]
        try:
            r1 = self._post({"model": self.model, "temperature": 0, "max_tokens": 700, "messages": msgs,
                             "stop": ["</root>"], "chat_template_kwargs": {"enable_thinking": False}})
            reasoning = r1["choices"][0]["message"]["content"].strip() + "</root>"
            rec.update(stages=reasoning, reasoning_tokens=r1["usage"]["completion_tokens"],
                       reasoning_finish=r1["choices"][0]["finish_reason"])
            follow = msgs + [{"role": "assistant", "content": reasoning},
                             {"role": "user", "content": "Now give the replica count for each service as JSON."}]
            sub = self._ask(None, None, services, messages=follow, max_tokens=100)
            rec.update({k: v for k, v in sub.items() if k != "prompt"})
        except Exception as e:
            rec["error"] = repr(e)[:300]
        rec["seconds"] = round(time.time() - t, 3)
        return rec

    def _ask_cot(self, user, services):
        """Step-by-step reasoning (free text), then the counts (schema-enforced), as _ask_oracl."""
        t, rec = time.time(), {"prompt": user}
        msgs = [{"role": "system", "content": SYSTEM_COT}, {"role": "user", "content": user}]
        try:
            r1 = self._post({"model": self.model, "temperature": 0, "max_tokens": 700, "messages": msgs,
                             "chat_template_kwargs": {"enable_thinking": False}})
            reasoning = r1["choices"][0]["message"]["content"].strip()
            rec.update(stages=reasoning, reasoning_tokens=r1["usage"]["completion_tokens"],
                       reasoning_finish=r1["choices"][0]["finish_reason"])
            follow = msgs + [{"role": "assistant", "content": reasoning},
                             {"role": "user", "content": "Now give the replica count for each service as JSON."}]
            sub = self._ask(None, None, services, messages=follow, max_tokens=100)
            rec.update({k: v for k, v in sub.items() if k != "prompt"})
        except Exception as e:
            rec["error"] = repr(e)[:300]
        rec["seconds"] = round(time.time() - t, 3)
        return rec

    def _ask(self, system, user, services, messages=None, max_tokens=None):
        reps = {s: {"type": "integer", "minimum": CFG["min_replicas"], "maximum": CFG["max_replicas"]} for s in services}
        schema = {"type": "object", "additionalProperties": False, "required": services, "properties": reps}
        body = {"model": self.model, "temperature": 0, "max_tokens": max_tokens or self.max_tokens,
                "messages": messages or [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "chat_template_kwargs": {"enable_thinking": self.thinking},
                "response_format": {"type": "json_schema", "json_schema": {"name": "replicas", "schema": schema}}}
        t = time.time()
        rec = {"prompt": user}
        try:
            resp = self._post(body)
            c = resp["choices"][0]
            rec.update(reply=c["message"]["content"], finish=c["finish_reason"],
                       reasoning=c["message"].get("reasoning_content") or c["message"].get("reasoning"),
                       prompt_tokens=resp["usage"]["prompt_tokens"], completion_tokens=resp["usage"]["completion_tokens"])
            dec = json.loads(c["message"]["content"])
            rec["decision"] = {s: int(dec[s]) for s in services}
        except Exception as e:
            rec["error"] = repr(e)[:300]
        rec["seconds"] = round(time.time() - t, 3)
        return rec

    def decide(self, obs):
        if self.mode == "joint":
            services = list(SERVICES)
            user = prompt(obs, services, self.history, self.newest)
            calls = [self._ask_cot(user, services) if self.ablate == "cot" else self._ask(self.system, user, services)]
        elif self.mode == "oracl":
            apps = {}
            for svc, sc in SERVICES.items():
                apps.setdefault(sc["entry"], []).append(svc)
            with ThreadPoolExecutor(len(apps)) as ex:
                calls = list(ex.map(lambda a: self._ask_oracl(oracl_prompt(obs, a[0], a[1], self.newest), a[1]), apps.items()))
            for c in calls:   # rule-based check: no scale-in of an application over its SLO
                for svc, n in list(c.get("decision", {}).items()):
                    e = obs["entries"][SERVICES[svc]["entry"]]
                    p95 = e.get("latency_ms", {}).get("p95", 0)
                    if n < obs["services"][svc]["desired"] and (p95 > e["slo_ms"] or e.get("error_rate", 0) > 0.01):
                        c.setdefault("rejected", {})[svc] = n
                        c["decision"][svc] = obs["services"][svc]["desired"]
        else:
            with ThreadPoolExecutor(len(SERVICES)) as ex:
                calls = list(ex.map(lambda s: self._ask(SYSTEM_ONE, prompt(obs, [s], newest=self.newest), [s]), SERVICES))
        want = {}
        for c in calls:
            want.update(c.get("decision", {}))
        self.last = {"calls": calls, "valid": all("decision" in c for c in calls)}
        return want or None
