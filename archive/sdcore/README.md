# Archived: 5G core scenario (SD-Core)

Dropped from the paper on 2026-09-24. A 5G core scenario needs protocol-level depth that the paper cannot defend. Kept here in case it becomes future work.

What was done:
- `feasibility.md`: why Open5GS cannot scale AMF horizontally, and why SD-Core can (SCTP load balancer, UE context in MongoDB).
- SD-Core v4.1.1 control plane (chart settings in `sdcore-cp-values.yaml`, `subprov-values.yaml`) was deployed on the workers. All pods came up. It was removed the same day, before the multi-AMF registration test ran.
- `gnbsim-values.yaml`: the simulator setup (single interface, no Multus). Render with `helm template` and drop the NetworkAttachmentDefinition before applying.
