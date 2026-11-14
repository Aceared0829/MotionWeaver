"""兼容入口：实现已迁到 data.tools.ops.run_overnight_seed_pipeline。"""
from data.tools._compat import shim

shim(__name__, "data.tools.ops.run_overnight_seed_pipeline")
