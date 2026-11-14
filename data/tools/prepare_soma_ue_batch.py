"""兼容入口：实现已迁到 data.tools.ingest.prepare_soma_ue_batch。"""
from data.tools._compat import shim

shim(__name__, "data.tools.ingest.prepare_soma_ue_batch")
