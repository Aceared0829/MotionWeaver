"""兼容入口：实现已迁到 data.tools.ops.retry_seed_review_batch。"""
from data.tools._compat import shim

shim(__name__, "data.tools.ops.retry_seed_review_batch")
