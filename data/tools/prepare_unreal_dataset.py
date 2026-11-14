"""兼容入口：实现已迁到 data.tools.ingest.prepare_unreal_dataset。"""
from data.tools._compat import shim

shim(__name__, "data.tools.ingest.prepare_unreal_dataset")
