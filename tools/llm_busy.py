"""Keep the local LLMs busy (2026-10-06 interference check): each port gets one request after the
other, the real joint-mode prompt of a recorded decision, until stopped (SIGTERM). Worst case: an LLM
controller keeps its GPU busy about 4% of each minute (2.3 s per 60 s decision), this 100%.

  python tools/llm_busy.py 8001 8002   -> prints requests per port when stopped
"""
import json
import pathlib
import signal
import sys
import threading
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
from controllers import llm as L  # noqa: E402

rec = json.loads(next(l for l in open(ROOT / "results/runs/pilot3_telecomgpt_dev0/decisions.jsonl") if '"k": 20' in l or '"k":20' in l))
USER = rec["controller"]["calls"][0]["prompt"]
stop = threading.Event()
count = {}


def loop(port):
    base = f"http://127.0.0.1:{port}/v1"
    model = json.load(urllib.request.urlopen(base + "/models", timeout=10))["data"][0]["id"]
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 400,
                       "messages": [{"role": "system", "content": L.SYSTEM}, {"role": "user", "content": USER}],
                       "chat_template_kwargs": {"enable_thinking": False}}).encode()
    while not stop.is_set():
        req = urllib.request.Request(base + "/chat/completions", body, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=120).read()
        count[port] = count.get(port, 0) + 1


signal.signal(signal.SIGTERM, lambda *_: stop.set())
threads = [threading.Thread(target=loop, args=(p,), daemon=True) for p in sys.argv[1:]]
for t in threads:
    t.start()
stop.wait()
print(json.dumps({"requests": count}), flush=True)
