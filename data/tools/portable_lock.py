"""跨平台的单写者文件锁。

原来三处批次脚本各自 `import msvcrt` 并调用 `msvcrt.locking(..., LK_NBLCK, 1)`，
那在非 Windows 上连 import 都过不去（ModuleNotFoundError），整个模块无法加载，
测试与调度脚本只能在 Windows 上跑。这里把「拿非阻塞独占锁」收成一个函数：

    lock_exclusive(handle)   # 拿不到锁抛 OSError（与 msvcrt.locking 同义）

Windows 走 msvcrt.locking，其它平台走 fcntl.flock。两者的共同点正是调用方依赖的
那一点：非阻塞、失败抛 OSError、进程退出即释放。
"""

try:  # Windows
    import msvcrt as _msvcrt
except ImportError:  # POSIX
    _msvcrt = None
    import fcntl as _fcntl


def lock_exclusive(handle):
    """对已打开的文件句柄拿一把非阻塞独占锁；已有持有者时抛 OSError。"""
    if _msvcrt is not None:
        handle.seek(0)
        _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
        return
    _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
