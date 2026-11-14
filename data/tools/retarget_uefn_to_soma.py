"""兼容入口：实现已迁到 data.tools.ingest.retarget_uefn_to_soma。"""
from data.tools._compat import shim

shim(__name__, "data.tools.ingest.retarget_uefn_to_soma")
