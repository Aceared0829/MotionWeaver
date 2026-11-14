"""兼容入口：实现已迁到 data.tools.publish.close_seed_batch。"""
from data.tools._compat import shim

shim(__name__, "data.tools.publish.close_seed_batch")
