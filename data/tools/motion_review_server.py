"""兼容入口：实现已迁到 data.tools.review.motion_review_server。"""
from data.tools._compat import shim

shim(__name__, "data.tools.review.motion_review_server")
