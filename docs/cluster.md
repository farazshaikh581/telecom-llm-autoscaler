# Cluster state and prep

Read-only check on 2026-09-24, then CPU pinning (below). Host names, addresses, and credentials are kept out of this repo on purpose.

## Versions

- microk8s v1.30.14, client and server v1.30.14.
- No feature gates set on apiserver, kubelet, scheduler, or controller manager.
- In-place pod resize (needed only for the vertical-scaling future work) is alpha in 1.30 behind `InPlacePodVerticalScaling`. Options later: enable the gate on apiserver and kubelet, or move to 1.33+ where it is beta and on by default. Not needed for the current horizontal-only scope.

## Nodes

| Node | Size | State |
|---|---|---|
| Control node | 16 vCPU, 47 GiB, NVIDIA L40S | Ready, untainted. Runs control plane, KEDA, observability, Kepler, one idle pod of an older workload, vLLM, Open WebUI |
| worker11-14 | 4 vCPU, 15.5 GiB each | Ready, no pressure. 6-12% CPU requested |
| One extra node | 16 vCPU, 63 GiB | Belongs to another project. Powered off since 2026-08-21. Kept in the cluster |

All nodes are KVM guests on one physical host. The extra node carries the `unreachable` taints, and the scheduler places nothing new on it. The pods it still lists are stale records whose replacements run elsewhere.

## Fixed: kubelet certificates on worker11-13 (2026-09-24)

worker11, worker12, and worker13 served a kubelet certificate issued for another node (they were cloned from one VM image). `kubectl logs`, `exec`, and `port-forward` failed on them. Metrics-server and Prometheus still worked (they skip kubelet certificate checks).
Fix: on each worker, a CSR from its own kubelet key, signed with the cluster CA for the worker's own hostname and IP, installed as `kubelet.crt` (old one kept as `kubelet.crt.bak-ins`), kubelet restarted. Verified: all four workers serve their own certificate and `kubectl logs` works on each.

## Worker disks (2026-09-24)

Each worker has a 50 GiB virtual disk, but its root filesystem uses only about 24.6 GiB (about 23.5 GiB allocatable for pods). A first attempt to deploy TeaStore and the hotel app together filled the worker disks with image pulls. All four workers went into DiskPressure, and the kubelet evicted our pods and the workers' monitoring DaemonSet pods (Kepler, node exporter, promtail, ingress). Calico and all Deployments survived. Deleting the two namespaces let image garbage collection clear the pressure in about 3 minutes.
Fixed the same day, per worker: journal trimmed to 200 MB, rotated logs and disabled snap revisions removed (snap keeps 2 revisions from now on), apt cache cleared, then the root filesystem grown to the full virtual disk (growpart, pvresize, lvextend, live). Result: 47.1 GiB root filesystem with about 28 GB free on each worker (was about 3 GB). The cleanup freed about 3 GB, the growth added about 24 GB. The node status still shows the old ephemeral-storage capacity until the kubelet restarts; eviction uses the real free space. Always check disk headroom before deploying.

## Leftover objects

- An HPA from an earlier study (CPU target 50%, 1 to 100 replicas) and its idle deployment, in their own namespace. Left in place: they may still be needed for the RL papers. Our workloads get new namespaces.
- KEDA: only its own interceptor ScaledObject.

## Prep plan

1. Keep workload pods off the control node with a node label on worker11-14 and node affinity in our manifests only. Do not taint the control node: a taint would push new system pods onto the workers and add noise there.
2. Keep the extra node as it is.
3. Fairness risk: vLLM inference on the control node used host CPU that the worker VMs share. LLM controllers would then add host load that other controllers do not. Fixed with CPU pinning (below).

## CPU pinning (applied 2026-09-24)

Physical host: 2x Intel Xeon Silver 4416+, 20 cores per socket, 2 threads per core, 2 NUMA nodes. On NUMA node 0, host threads 0-19 are the first thread of each core and 40-59 their siblings (sibling of thread N is N+40).
The control node's GPU and nearly all worker memory sit on NUMA node 0. Our cluster therefore lives on node 0. Each pair of vCPUs gets one full physical core.

| VM | vCPUs | Physical cores | Host threads |
|---|---|---|---|
| Control node | 16 | 0-7 | 0-7, 40-47 |
| worker11 | 4 | 8-9 | 8, 9, 48, 49 |
| worker12 | 4 | 10-11 | 10, 11, 50, 51 |
| worker13 | 4 | 12-13 | 12, 13, 52, 53 |
| worker14 | 4 | 14-15 | 14, 15, 54, 55 |
| QEMU emulator threads (all five) | | 16-19 | 16-19, 56-59 |

Applied with `virsh vcpupin` and `virsh emulatorpin`, flags `--live --config` (survives VM restarts). No VM was restarted. Memory placement was not changed: the control node's memory is split about evenly across both NUMA nodes, and both nodes are nearly full. This is the same for every controller.

Before this, every vCPU floated over all 80 host threads.
Undo, per VM: `virsh vcpupin <vm> <vcpu> 0-79 --live --config` for each vCPU, and `virsh emulatorpin <vm> 0-79 --live --config`.

A second cluster on the same host (3 VMs, 40 vCPUs, its own GPU on NUMA node 1) was confined to NUMA node 1 (host threads 20-39 and 60-79), vCPUs and emulator threads, with `--live --config`. Its vCPUs float within node 1 only. The two clusters now share no physical cores. Both clusters were checked healthy afterwards.

## Host swap (fixed 2026-09-25)

The host had 14.3 of 15.6 GiB swap in use, left over from earlier memory pressure (no swapping was happening at the time). The host's own disk cache (about 85 GiB) had pushed guest memory out, 2.0-3.3 GiB per worker. A guest touching such a page stalls while the host reads it back from disk, which would add random spikes to tail latency.
Fixed with `swapoff -a && swapon -a` (75 s, all guest memory back in RAM, 66 GiB still available), and `vm.swappiness = 1` in `/etc/sysctl.d/90-swappiness.conf`. The tuned profile sets 10 and re-applies sysctl.d after itself, so the value stays in place after a reboot. With 1, the host drops disk cache before it swaps guest memory.
Check before each batch of runs: swap used on the host and `vmstat` si/so both at 0. The harness preflight checks both before every run and logs them (swap in use, and si + so over 10 s); it refuses to start above 512 MiB. From 09:15 to 16:38 UTC on 2026-09-26 the limit was 1 GiB when the host showed no swap activity (until the next reset); the reset was done at 16:38 UTC (442 MiB to 0 in 4 s), during the unscored warm-up of finetune_aware_dev2, and the limit is back to 512 MiB. Since 2026-09-27 the preflight waits (no load running, up to 3 h) instead of failing when swap is at or above 512 MiB, so a reset can be done between runs without losing the rest of a chain; first used at 11:51 UTC that day (528 MiB to 0, between test runs). Since 2026-09-30 (second testbed) the limit is `host_swap_limit_mib` in config/harness.yaml, set to 4096 MiB: the control node VM holds about 166 GB of the host's 251 GB, and swap grows faster than before; si + so is still logged per run. `tools/swap_watch.sh` resets swap when a run waits for it (2026-09-30), so chains do not stall; it never acts while load runs. The phase-2 pilot (2026-09-30) still swapped 5-7 GiB within each 75-min run, workers included (350 MiB of one worker). The control node VM holds all of its 160 GiB on the host although the guest uses about 12 GiB: its GPUs are passed through (VFIO), so its memory stays pinned and a balloon cannot return it (tried 2026-10-01: setmem 128G changed nothing on the host). Since 2026-10-01 the four worker VMs' memory cgroups have swappiness 0 (live), so the host swaps only the control node and drops its own disk cache instead of touching the measured workers. Every run logs swapped memory per VM at start and end (`VM swap at start/end` in events.jsonl). The control node was left at 128 GiB after the balloon test until it is set back to 160 GiB. Effect, same controller and day (HPA 70%, dev_0): pilot2_hpa_dev0 (host swapping 5-7 GiB) 17.3% violations (k = 3), 0.44% errors; pilot2b_hpa_dev0 (workers at swappiness 0) 8.7%, 0.002%, host swap 612 to 567 MiB over the run, no VM gained swap. The pilot2_* runs are kept as a record and replaced by pilot2b_*. Swappiness 0 held for three runs, then failed: node 0 had 2 GiB of file cache left, so the kernel swapped the workers anyway (1.3-2.2 GiB each in pilot2b_telecomgpt_dev1, moved to results/failed/). Swap inside the worker guests is off (fstab). Until the VM layout is changed, `host_swap_limit_mib` is 64 MiB: every run starts from a reset swap (the watcher resets it while the run waits), and a run whose workers end with more than 100 MiB swapped is invalid and repeated. Workers were still swapped within minutes of a reset (13-70 MiB in 7 min), so since 2026-10-01 14:50 UTC the host's swap is OFF (no VM is swapped during any measured run, for any controller). `tools/swap_watch.sh` is now an emergency brake: if the host's available memory falls below 8 GiB it turns swap back on, logs it, and the runs live at that time are invalid. At switch-off the host had 31 GiB available; the control VM's memory is fixed (GPU passthrough) and the workers can grow by a few GiB at most. Host swap comes back after a host reboot (fstab unchanged). Lasting fix with the planned re-layout: worker memory locked and bound to its NUMA node. Likely cause of the creep: the VMs on the host are assigned 256 GB (this cluster 112, the second cluster 144) against 251 GB of RAM, and the host's disk cache competes for the rest.

The global setting alone did not hold: swap was back to 883 MiB two hours later, mostly our workers. The host uses cgroup v1, where each memory cgroup keeps its own swappiness, and every VM's cgroup (and `machine.slice`) still had 60. Set to 1 for all 17 VM cgroups under `machine.slice` (live, no restart), then swap cleared again. New VM cgroups inherit 1 from `machine.slice`; after a reboot, `machine.slice` inherits the system value (1) because sysctl runs before libvirt starts. The harness preflight waits for a reset when host swap in use reaches `host_swap_limit_mib` (512 MiB until 2026-09-30, 4096 MiB since).
