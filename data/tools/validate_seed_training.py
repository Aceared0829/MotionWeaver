"""兼容入口：实现已迁到 data.tools.audit.validate_seed_training。"""
from data.tools._compat import shim

shim(__name__, "data.tools.audit.validate_seed_training")
