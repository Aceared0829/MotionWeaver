"""兼容入口：实现已迁到 data.tools.build.rewrite_export_index。"""
from data.tools._compat import shim

shim(__name__, "data.tools.build.rewrite_export_index")
