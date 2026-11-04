"""Locations of repository resources, independent of the process working directory."""
import os
from pathlib import Path


def repository_root():
    root = Path(os.environ.get('AIANIMATION_ROOT', Path(__file__).resolve().parents[2])).resolve()
    if not (root / 'model').is_dir() or not (root / 'training').is_dir():
        raise RuntimeError('Repository resources are unavailable; set AIANIMATION_ROOT to the MotionWeaver checkout.')
    return root


def base_weights():
    return repository_root() / 'model-weight/base/motionbricks'


def unreal_scripts():
    """UE 宿主内脚本目录；2026-11 已随 unreal-script/python 一并移除。

    保留函数名是为了让旧的种子数据批次脚本给出明确原因，而不是把不存在的路径
    传给 UnrealEditor-Cmd 后在下游以难以定位的方式失败。
    """
    path = repository_root() / 'unreal-script/python'
    if not path.is_dir():
        raise RuntimeError(
            'unreal-script/python 已移除：UE 内 IK Retargeter 与导出脚本不再随仓库分发。'
            '需要时从 git 历史取回（git show e7330af:unreal-script/python/<文件名>）。'
        )
    return path
