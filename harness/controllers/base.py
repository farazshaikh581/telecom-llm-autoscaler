"""Common controller interface.

The harness calls decide(obs) every interval. A controller returns replicas per service (only
the services it wants to set; values are clamped to [min, max] by the harness), or None to
leave everything as is. Native Kubernetes controllers (HPA, KEDA) act on their own: they
create their objects in setup(), return None from decide(), and remove them in teardown().
info() is stored with the run; last (set in decide) is stored with each decision.
"""


class Controller:
    native = False

    def __init__(self, **params):
        self.params = params
        self.last = {}

    def setup(self, ctx):
        pass

    def decide(self, obs):
        raise NotImplementedError

    def teardown(self, ctx):
        pass

    def info(self):
        return {"kind": type(self).__name__.lower(), "params": self.params}
