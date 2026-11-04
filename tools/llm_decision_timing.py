"""Time one joint scaling decision by a local LLM on a realistic 8-service prompt.

Observations are synthetic but built from calibration numbers (docs/calibration.md):
5 scenarios (quiet, TeaStore rising, function burst, hotel near its knee, whole
cluster at its peak), each with 5 intervals of history for every scaled service.
Output is forced to a JSON schema with one replica count per service (vLLM
structured outputs). Thinking is off unless --thinking.

Usage: python3 llm_decision_timing.py <port> <out.json> [--thinking] [--repeats N]
"""
import json
import sys
import time
import urllib.request

PORT, OUT = sys.argv[1], sys.argv[2]
THINKING = "--thinking" in sys.argv
REPEATS = int(sys.argv[sys.argv.index("--repeats") + 1]) if "--repeats" in sys.argv else 2
BASE = f"http://127.0.0.1:{PORT}/v1"

SERVICES = {  # name: (entry, one-copy capacity req/s, SLO ms)
    "teastore-webui": ("teastore", 150, 39),
    "thumbnailer": ("thumbnailer", 12.5, 200),
    "compression": ("compression", 1.1, 1300),
    "imgrec": ("imgrec", 4.5, 400),
    "hotel-frontend": ("hotel", 500, 27),
    "hotel-search": ("hotel", 500, 27),
    "hotel-reservation": ("hotel", 500, 27),
    "hotel-rate": ("hotel", 500, 27),
}

SYSTEM = (
    "You are the autoscaler for a Kubernetes cluster that runs several applications at once.\n"
    "Every 60 seconds you set the replica count of each scaled service.\n"
    "Cluster: 4 worker nodes, 16 vCPU in total. Each replica requests 0.5 CPU and may use up to 1 CPU.\n"
    "Replicas per service: 1 to 12. The sum of all replicas should stay within 24 unless an SLO is at risk.\n"
    "Goal: keep every application's P95 latency below its SLO with as few replicas as possible, and avoid oscillation.\n"
    "Services of the hotel application share one end-to-end SLO. A new replica takes about 2 s to start; "
    "the imgrec service's first request on a new replica takes about 1.2 s longer (model load).\n"
    "Reply only with the JSON object requested."
)


def scenario(name, load):
    """load: service -> list of 5 offered req/s values (oldest first); replicas fixed at 1-2."""
    lines = [f"Scenario time: now. Last 5 intervals (60 s each, oldest first) per service:"]
    for svc, (entry, cap, slo) in SERVICES.items():
        rates = load.get(svc, [cap * 0.3] * 5)
        reps = 1 if max(rates) < cap else 2
        rows = []
        for r in rates:
            util = r / (cap * reps)
            p95 = slo / 3 * (1 + 4 * max(0.0, util - 0.5) ** 2 / max(1e-3, 1.05 - min(util, 1.0)))
            cpu = min(100, round(100 * util * 0.9))
            rows.append(f"{r:.1f} req/s, P95 {p95:.0f} ms, CPU {cpu}%, replicas {reps}")
        lines.append(f"- {svc} (app {entry}, SLO P95 {slo} ms): " + " | ".join(rows))
    lines.append("Choose the replica count for every service for the next interval.")
    return name, "\n".join(lines)


def ramp(a, b):
    return [a + (b - a) * i / 4 for i in range(5)]


SCENARIOS = [
    scenario("quiet", {}),
    scenario("teastore_rising", {"teastore-webui": ramp(80, 190)}),
    scenario("function_burst", {"thumbnailer": ramp(4, 22), "imgrec": ramp(1.5, 8)}),
    scenario("hotel_near_knee", {s: ramp(300, 560) for s in SERVICES if s.startswith("hotel")}),
    scenario("cluster_peak", {"teastore-webui": ramp(150, 380), "thumbnailer": ramp(10, 30),
                              "compression": ramp(1, 3), "imgrec": ramp(3, 10),
                              **{s: ramp(500, 900) for s in SERVICES if s.startswith("hotel")}}),
]

SCHEMA = {"type": "object", "additionalProperties": False, "required": list(SERVICES),
          "properties": {s: {"type": "integer", "minimum": 1, "maximum": 12} for s in SERVICES}}

model = json.load(urllib.request.urlopen(BASE + "/models"))["data"][0]["id"]
results = []
for name, user in SCENARIOS:
    for rep in range(REPEATS):
        body = {"model": model, "temperature": 0, "max_tokens": 6000 if THINKING else 400,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                "chat_template_kwargs": {"enable_thinking": THINKING},
                "response_format": {"type": "json_schema", "json_schema": {"name": "replicas", "schema": SCHEMA}}}
        t = time.time()
        req = urllib.request.Request(BASE + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=900))
        secs = time.time() - t
        c = r["choices"][0]
        try:
            decision = json.loads(c["message"]["content"])
        except (TypeError, ValueError):
            decision = None
        results.append({"scenario": name, "rep": rep, "seconds": round(secs, 2),
                        "prompt_tokens": r["usage"]["prompt_tokens"], "completion_tokens": r["usage"]["completion_tokens"],
                        "finish": c["finish_reason"], "valid": decision is not None, "decision": decision})
        print(f"{model} {name:16} rep{rep}: {secs:6.1f} s, in {r['usage']['prompt_tokens']} tok, "
              f"out {r['usage']['completion_tokens']} tok, finish={c['finish_reason']}, decision={decision}", flush=True)

json.dump({"model": model, "thinking": THINKING, "system": SYSTEM,
           "prompts": dict(SCENARIOS), "results": results}, open(OUT, "w"), indent=1)
