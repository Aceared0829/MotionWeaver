"""历史入口；共享实现位于 inference.export.refpose.onnx。"""
from inference.export.refpose.onnx import *  # noqa: F401,F403
from inference.export.refpose.onnx import main as _main

if __name__ == "__main__":
    _main()
