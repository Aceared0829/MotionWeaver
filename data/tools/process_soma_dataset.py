"""兼容入口：实现已迁到 data.tools.build.process_soma_dataset。"""
from data.tools._compat import shim

shim(__name__, "data.tools.build.process_soma_dataset")
