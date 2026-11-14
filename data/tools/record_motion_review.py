"""兼容入口：实现已迁到 data.tools.review.record_motion_review。"""
from data.tools._compat import shim

shim(__name__, "data.tools.review.record_motion_review")
