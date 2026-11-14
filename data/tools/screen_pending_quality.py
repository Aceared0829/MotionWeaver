"""兼容入口：实现已迁到 data.tools.audit.screen_pending_quality。"""
from data.tools._compat import shim

shim(__name__, "data.tools.audit.screen_pending_quality")
