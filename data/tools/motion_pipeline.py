"""兼容入口：实现已迁到 data.tools.ops.motion_pipeline。"""
from data.tools._compat import shim

shim(__name__, "data.tools.ops.motion_pipeline")
