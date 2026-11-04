# Telecom scenario: feasibility

Checked 2026-09-24. Time box for the prototype: 2 to 3 days.

## The core question

Autoscaling a 5G network function only makes sense if a new replica can take load. AMF and SMF keep per-UE state (registration, security context, PDU sessions). If that state lives inside one replica, extra replicas cannot serve existing UEs.

## Open5GS: not suitable for horizontal scaling

- Open5GS keeps UE context inside each AMF instance. With several AMF replicas, requests that reach a replica without the context fail ("No UE context"). A user reported this in open5gs discussion #2655. No maintainer answer, no fix.
- Paul, Keluskar, Vutukuru (IIT Bombay, 2025, "A Scalable and Fault-Tolerant 5G Core on Kubernetes"): free5GC, Open5GS, and OpenAirInterface do not support stateless NFs (as of September 2024). They name SD-Core as the closest open-source option.
- Scaling Open5GS would mean either a static AMF-per-gNB split (new replicas only help new gNBs) or our own state store. Both are out of scope for this paper.

## SD-Core (Aether / OMEC): suitable

- Actively maintained: `omec-project/sdcore-helm-charts` v4.1.1 (2026-09-21), AMF v3.3.1 (2026-09-10). All core repos pushed 2026-09-23.
- Multi-AMF by design: an SCTP load balancer pod (`sctplb`) terminates gNB connections and forwards to all AMF replicas, found through the `amf-headless` service. AMF instances share the TMSI and NGAP ID pools.
- State in MongoDB: `enableDBStore: true` on AMF and SMF lets any replica load any UE context. Both default to `false`, and `sctplb` defaults to `deploy: false`.
- The project documents AMF and SMF autoscaling with KEDA, which gives a domain-native baseline.
- Load generator: `gnbsim` (same project, built for control-plane load: registration, PDU session setup, deregistration, many UEs). UERANSIM needs a TUN device per UE and scales worse.
- Resources: AMF and SCTPLB request 2 CPU each when limits are enabled (off by default). The workers have 16 vCPU in total. The fixed NFs (NRF, AUSF, UDM, UDR, PCF, NSSF, webui, MongoDB) must fit next to the scaled ones.

Proposed scope: scale AMF (driven by calls and SMS) and SMF (driven by internet sessions). The UPF data plane stays fixed and is out of scope.

## Worker readiness

- Kernel 6.8.0-137 on all workers. SCTP module present (loads on first use). `/dev/net/tun` present. gtp5g absent (not needed).
- Issue: worker11-13 serve a kubelet certificate issued for another node (cloned VM image). `kubectl logs`, `exec`, and `port-forward` fail on those nodes. Metrics still work. Fix: regenerate the kubelet serving certificate on each of the three workers (needs sudo on the workers). Logs can also come from Loki, which is installed.

## Milan data

- Harvard Dataverse doi:10.7910/DVN/EGZHFV. ODbL 1.0. 62 daily files (2013-11-01 to 2014-01-01), 265-359 MB each, 20.8 GB total. No file is restricted, and download by file id works without login.
- Fields: square id, 10-minute timestamp, country code, SMS in/out, calls in/out, internet. Values are normalized activity, not absolute counts.
- Design points to settle: which grid squares map to our cells, the scale from activity to UE events per second, and time compression (10-minute bins against a 30 s control interval, for example one day replayed in one hour).
