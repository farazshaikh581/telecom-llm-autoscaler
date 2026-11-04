# Insights

Newest first. One prompt or one run is an anecdote, not a result. Mark the sample size on each entry.

## 2026-09-24: TelecomGPT-R1-27B serving checks (n = 1 per check)

- Same flags as Qwen3.5-27B. All checks passed.
- On the same "17 + 25" prompt with thinking on, TelecomGPT used 315 tokens (15 s) against 638 for Qwen3.5-27B. It thinks about half as long here, consistent with the earlier AMF demo (288 vs 5,783 tokens for Qwen3.5-9B). Worth measuring properly: thinking length drives decision latency.

## 2026-09-24: Qwen3.5-27B serving checks (n = 1 per check)

- Speed: about 25 tok/s single-stream (TelecomGPT-R1-27B: about 21.5 tok/s).
- Thinking is long. On a short scaling prompt it hit the 6,000-token cap after 240 s with no answer. On "17 + 25" it used 638 tokens (25 s).
- Consequence: thinking-on runs need a thinking budget or a longer control interval, and the context limit (8,192) must be raised for thinking-on cells. Otherwise decisions will be cut off, which repeats the old truncation bug.
- With `--reasoning-parser qwen3`, the JSON schema applies to the answer only. Verified: valid JSON after a full thinking pass.

## 2026-09-24: single-prompt demo (n = 1 per cell)

Setup: one AMF scaling decision under rising load (3 replicas, 300 req/s, P95 240 ms against a 200 ms SLO, CPU 96%). Temperature 0. Thinking off with an enforced JSON schema, and thinking on without a schema. Logs and script: `results/2026-09-24_single_prompt_demo/`.

| Model | Thinking | Replicas | Time | Output tokens |
|---|---|---|---|---|
| Qwen3.5-9B | off | 4 | 3.9 s | 59 |
| Qwen3.5-9B | on | 4 | 132.5 s | 5,783 |
| TelecomGPT-R1-27B | off | 5 | 5.0 s | 54 |
| TelecomGPT-R1-27B | on | 5 | 13.4 s | 288 |

- Both answers are defensible. A 70% CPU HPA would pick 5 (3 x 96 / 70 = 4.1, rounded up).
- Qwen3.5-9B thinking took 132 s, far above a 30 s control interval. It also used 6,083 of 8,192 context tokens, which risks truncation with a longer history prompt.
- TelecomGPT thinking was short (288 tokens, 13 s).
- Without `--reasoning-parser qwen3`, the thinking text lands in the answer field, and a JSON schema would constrain the thinking too. Serve with the reasoning parser. The schema then applies to the answer only.
- Structured output worked: both thinking-off runs returned valid JSON.
