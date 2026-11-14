"""兼容入口：实现已迁到 data.tools.audit.audit_conditioned_dataset。"""
from data.tools._compat import shim

shim(__name__, "data.tools.audit.audit_conditioned_dataset")
