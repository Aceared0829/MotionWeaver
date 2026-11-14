"""兼容入口：实现已迁到 data.tools.build.compress_training_views。"""
from data.tools._compat import shim

shim(__name__, "data.tools.build.compress_training_views")
