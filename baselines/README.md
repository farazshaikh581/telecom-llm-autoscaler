# Baselines

| Baseline | Source | Code | Notes |
|---|---|---|---|
| AWARE | Qiu et al., USENIX ATC 2023 | Local copy of commit 7cd13d6 (2023-06-04), not vendored here. Apache-2.0. Upstream: gitlab.engr.illinois.edu/DEPEND/aware (VPN-only from 2026-10-05) | Meta-learning PPO. Uses its own MPA (horizontal plus vertical). We restrict it to horizontal actions. Pinned 2023 deps (torch 1.12.1, kubernetes 25.3.0): run in its own environment. |
| FIRM (DDPG) | Qian et al., OSDI 2020 | `baselines/firm` inside the AWARE repo | RL core only, not the full FIRM system. |
| ORACL | Bai et al., arXiv 2602.05292 | Not released | Fine-tuned LLM, base model not named. Ask the authors, or run an ORACL-style prompt on our frozen models. |
| Predictive HPA | github.com/jthomperoo/predictive-horizontal-pod-autoscaler | Open source | Holt-Winters. Used as a baseline in AAPA (arXiv 2507.05653). |
| HPA, KEDA | Kubernetes, KEDA | Installed on the cluster | Tuned on development days with the same budget as the other controllers. |
