# telecom-llm-autoscaler

Code and data for the paper "Auditing LLM Autoscalers: Robustness to Workload Shift and Faithfulness of Their Reasoning" (F. Shaikh, G. Reali, M. Femminella, University of Perugia).

Two frozen 27B LLMs (TelecomGPT-R1 and its base model Qwen3.5-27B, each in three prompt modes) scale four services on a shared Kubernetes cluster, against three rule-based autoscalers (HPA, KEDA, predictive HPA) and three RL agents (AWARE, FIRM, LSTM-DDQN). The study tests them on held-out days of production traces, under 25% more load and with a service no controller had seen, and checks the written reasoning of the LLMs against the prompt of each call.

## Layout

| Path | Content |
|---|---|
| `harness/` | Runs one controller through one trace (`run.py`), the controllers (`controllers/`), load generator, recorder, and the run chains of every test (`chains/`) |
| `rl/` | AWARE, FIRM and LSTM-DDQN: training in the simulator, fine-tuning and evaluation |
| `sim/` | Cluster simulator used to train the RL agents, with its validation against the testbed |
| `workloads/` | Manifests and patches of TeaStore, the hotel application (DeathStarBench) and the SeBS functions |
| `serving/` | Model versions and vLLM launch commands |
| `config/` | Harness settings and trace scaling |
| `data/traces/` | Scaled request-rate shapes derived from the public traces, and the per-run traces |
| `tools/` | Trace preparation, statistics, faithfulness checks, and the numbers quoted in the paper |
| `results/runs/` | One folder per run (below) |
| `results/analysis/` | Tables the paper reports, produced by `tools/` |
| `paper/figures/make_results_figs.py` | Draws Figs. 2 to 4 of the paper from the run data |
| `docs/` | Dated design log (`decisions.md`), analysis plan, calibration, testbed and traces |

## Runs

Each folder in `results/runs/` holds one 75-minute run (15 minutes of warm-up, 60 scored). `docs/data.md` gives every field.

| File | Content |
|---|---|
| `meta.json` | Controller and its settings, trace, seed, and for the LLMs the system prompt |
| `summary.json` | Scored KPIs: violation rate, copies, actions, reversals, energy |
| `timeline.csv` | Per minute: rate, P95 latency and copies of every service |
| `decisions.jsonl` | Every decision; for the LLMs the full prompt, reply and timing |
| `fast.jsonl` | Every 5 s: load generator statistics, copies and pod states |
| `metrics.jsonl` | Every 15 s: CPU and memory per service |
| `events.jsonl` | Run events: preflight checks, resets, faults |
| `gpu.csv`, `host_power.csv` | GPU power every 1 s, host power every 5 s |

Run names give the test: `test2_` main matrix, `hl_` 25% more load, `ns_` unseen service, `ho_` history listed newest first, `ab_` step-by-step prompt, `pa_` alternative prompt wording, `xb_` budget-aware RL variant (exploratory). The others are calibration, tuning and development runs. Runs repeated after a testbed fault are listed with the reason in `results/invalid_runs.json`. Per-request logs (`requests.csv.gz`, about 25 MB per run) are included only for the runs the simulator and the SLOs are fitted on (`fp_`, `slo_`, `warm_`, `tune_` and `pilot_qwen35_dev0`). The harness reduces each run's per-request log to `summary.json` and `timeline.csv` at its end (`harness/analyze_run.py`), and the results of the paper are computed from these files and `decisions.jsonl`.

## Reproducing the tables and figures

The analysis needs only the run folders, not a cluster. It takes about a minute.

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt
sh tools/reproduce.sh
```

The script runs, in order, `tools/analyze.py` (main matrix, +25% load, robustness), `tools/new_service.py` (unseen service), `tools/faithfulness.py` (checks of the written reasoning), `tools/history_order.py` (history order), `tools/slo_sensitivity.py` (SLO factor 2, 3, 5) and `paper/figures/make_results_figs.py` (Figs. 2 to 4). Tables go to `results/analysis/` and figures to `paper/figures/`, and the script lists any table that differs from the committed files. `docs/analysis_plan.md` gives the tests and corrections.

## Not used in the paper

Kept for the record of how the study was built:

- `archive/sdcore/`: a 5G core scenario, dropped before any run.
- `docs/testbed_4w.md`: notes of the first testbed, replaced before the runs of the paper.
- `docs/insights.md`, `results/2026-09-24_single_prompt_demo/`: first serving checks of the models.
- Runs named `smoke*`, `pilot*` and `test_` (without the 2): harness checks, pilots and an earlier test matrix that was redone. Runs named `ref*` are the drift-reference runs of each block, which the paper does use.

## Testbed

MicroK8s 1.30 on virtual machines of one server (two Intel Xeon Silver 4416+). Four workers with 4 vCPUs and 16 GB each run the applications; a control machine runs the harness and the two LLMs, each on its own NVIDIA L40S. Rerunning the experiments needs a similar cluster; `docs/cluster.md` and `docs/calibration.md` describe the setup and how the SLOs were measured.

## Traces

`data/traces/` holds only scaled shapes, with the scripts in `tools/traces/` that build them from the original files (`docs/traces.md`):

- English Wikipedia page views, Wikimedia Foundation (CC0).
- Huawei Cloud 2023 serverless traces, Joosen et al., SoCC 2023 (CC BY 4.0).
- Alibaba 2022 microservice traces, Luo et al., SoCC 2022 (released for research use).

## Citation

Please cite the paper when you use this code or data. `CITATION.cff` gives the citation of the repository itself.

## License

Code: MIT, except `rl/aware_agent.py` and `rl/firm_agent.py`, which are ported from AWARE (Qiu et al., USENIX ATC 2023) and stay under its Apache-2.0 license (`rl/LICENSE-AWARE`). The SeBS functions and the hotel application are built from their upstream repositories (`workloads/`). Derived traces and run data: CC BY 4.0, subject to the terms of the source traces above.
