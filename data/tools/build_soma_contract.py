"""兼容入口：实现已迁到 data.tools.build.build_soma_contract。"""
from data.tools._compat import shim

shim(__name__, "data.tools.build.build_soma_contract")
