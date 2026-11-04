"""Controller registry. A spec is "<kind>[:key=value,...]", for example
  static:replicas=2
  hpa:target=70
  keda:util=0.7
  phpa:target=70,lookahead=60,history=6
  llm:port=8001,mode=joint
  aware:ckpt=rl/checkpoints/aware.pt    firm:ckpt=rl/checkpoints/firm.pt   (PyTorch loaded only for these)
  ours:ckpt=rl/checkpoints/ours_dqn[,learn=1,save=...]    ours_joint:ckpt=rl/checkpoints/ours_joint_qrdqn
New controllers: subclass base.Controller and add them to KINDS.
"""
from .base import Controller
from .hpa import HPA
from .keda import KEDA
from .llm import LLM
from .phpa import PHPA
from .static import Static

KINDS = {"static": Static, "hpa": HPA, "keda": KEDA, "phpa": PHPA, "llm": LLM}


def make(spec):
    kind, _, rest = spec.partition(":")
    params = dict(kv.split("=", 1) for kv in rest.split(",") if "=" in kv)
    if kind == "ours":
        from .ours import Ours
        return Ours(**params)
    if kind == "ours_joint":
        from .ours import OursJoint
        return OursJoint(**params)
    if kind in ("aware", "firm"):
        from . import rl_literature
        return {"aware": rl_literature.AWARE, "firm": rl_literature.FIRM}[kind](**params)
    return KINDS[kind](**params)
