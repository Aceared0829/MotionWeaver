"""兼容入口：实现已迁到 data.tools.common.portable_lock。"""
from data.tools._compat import shim

shim(__name__, "data.tools.common.portable_lock")
