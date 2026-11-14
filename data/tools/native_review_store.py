"""兼容入口：实现已迁到 data.tools.review.native_review_store。"""
from data.tools._compat import shim

shim(__name__, "data.tools.review.native_review_store")
