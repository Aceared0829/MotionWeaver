"""兼容入口：实现已迁到 data.tools.eval.run_soma_eval。"""
from data.tools._compat import shim

shim(__name__, "data.tools.eval.run_soma_eval")
