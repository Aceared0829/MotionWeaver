"""旧 data.tools 模块路径的兼容转发。

实现迁入分组子包后，旧模块通过 sys.modules 指向新模块，保留函数、类和
模块级状态的同一性；以 -m 运行时用 runpy 保持原 __main__ 行为。
"""
import runpy
import sys
from importlib import import_module


def shim(name, target):
    """把旧模块名转发到 target；CLI 入口在新模块中执行。"""
    if name == "__main__":
        runpy.run_module(target, run_name="__main__")
    else:
        sys.modules[name] = import_module(target)
