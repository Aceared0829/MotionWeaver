"""兼容入口：实现已迁到 data.tools.ingest.import_prepared_motions。"""
from data.tools._compat import shim

shim(__name__, "data.tools.ingest.import_prepared_motions")
