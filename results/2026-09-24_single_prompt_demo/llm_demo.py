"""Send one autoscaling decision to a local vLLM model, thinking off and on, and log everything."""
import json
import sys
import time
import urllib.request

PORT = sys.argv[1]
OUT = sys.argv[2]
BASE = f"http://127.0.0.1:{PORT}/v1"

SYSTEM = (
    "You are the autoscaler for one Kubernetes deployment.\n"
    "Cluster: 4 worker nodes, each 4 vCPU and 16 GiB (16 vCPU total).\n"
    "Each replica requests 500m CPU. Allowed replicas: 1 to 20.\n"
    "SLO: P95 latency below 200 ms.\n"
    "Goal: meet the SLO with as few replicas as possible, and avoid oscillation.\n"
    'Reply only with JSON: {"replicas": <int>, "reason": "<one sentence>"}'
)

USER = (
    "Deployment: open5gs-amf (5G core Access and Mobility Management Function; "
    "handles UE registration and handover signaling).\n"
    "Last 5 intervals, 30 s each (oldest first):\n"
    "t      req/s  P95_ms  CPU_util%  replicas\n"
    "-120s  120    60      45         3\n"
    "-90s   150    75      56         3\n"
    "-60s   190    110     70         3\n"
    "-30s   240    165     85         3\n"
    "now    300    240     96         3\n"
    "Choose the replica count for the next interval."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "replicas": {"type": "integer", "minimum": 1, "maximum": 20},
        "reason": {"type": "string", "maxLength": 300},
    },
    "required": ["replicas", "reason"],
}


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        resp = json.load(r)
    return resp, time.time() - t0


model = json.load(urllib.request.urlopen(BASE + "/models"))["data"][0]["id"]
messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}]

runs = {
    # Thinking off: JSON schema enforced by vLLM structured outputs.
    "thinking_off": {
        "model": model, "messages": messages, "temperature": 0, "max_tokens": 512,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "decision", "schema": SCHEMA}},
    },
    # Thinking on: no schema (no reasoning parser on the server, a schema would also constrain the thinking).
    "thinking_on": {
        "model": model, "messages": messages, "temperature": 0, "max_tokens": 6144,
        "chat_template_kwargs": {"enable_thinking": True},
    },
}

log = {"model": model, "system": SYSTEM, "user": USER, "runs": {}}
for name, body in runs.items():
    resp, secs = post("/chat/completions", body)
    choice = resp["choices"][0]
    content = choice["message"].get("content") or ""
    reasoning = choice["message"].get("reasoning_content") or choice["message"].get("reasoning")
    usage = resp["usage"]
    log["runs"][name] = {
        "request": body, "seconds": round(secs, 2), "finish_reason": choice["finish_reason"],
        "prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"],
        "tok_per_s": round(usage["completion_tokens"] / secs, 1),
        "reasoning_field": reasoning, "content": content,
    }
    print(f"{model} {name}: {secs:.1f}s, {usage['completion_tokens']} tokens, finish={choice['finish_reason']}")

json.dump(log, open(OUT, "w"), indent=2, ensure_ascii=False)
