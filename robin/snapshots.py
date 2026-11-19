"""有界文件变化观察；shell 只读元数据，不为审计读取整个工作区内容。"""

import hashlib
import os
import stat
import time
from pathlib import Path

from .workspace import IGNORED_PATH_NAMES

MAX_ENTRIES = 2000
MAX_SECONDS = 1.0
MAX_FILE_BYTES = 2 * 1024 * 1024
EXCLUDED = set(IGNORED_PATH_NAMES) | {"node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


class Snapshot(dict):
    def __init__(self, scope):
        super().__init__()
        self.scope = scope
        self.complete = True
        self.reasons = set()
        self.covered_directories = set()

    def skip(self, reason):
        self.complete = False
        self.reasons.add(reason)


def capture(root, paths=None):
    root = Path(root)
    snapshot = Snapshot("targets" if paths is not None else "bounded-workspace")
    started = time.monotonic()
    entries = 0

    def observe(path, content=False):
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                if stat.S_ISLNK(info.st_mode):
                    snapshot.skip("symlink")
                return
            stamp = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino, info.st_mode)
            value = ("stat", stamp)
            if content and info.st_size <= MAX_FILE_BYTES:
                # 不跟随扫描后被替换的符号链接，也不读取 FIFO 等特殊文件。
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        snapshot.skip("special-file")
                        return
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) <= MAX_FILE_BYTES:
                    value = ("hash", hashlib.sha256(data).hexdigest())
                else:
                    snapshot.skip("file-size-limit")
            elif content:
                snapshot.skip("file-size-limit")
            snapshot[path.relative_to(root).as_posix()] = value
        except FileNotFoundError:
            return
        except OSError:
            snapshot.skip("io-error")

    if paths is not None:
        for path in paths:
            observe(Path(path), content=True)
        return snapshot

    # scandir 在进入目录前剪枝，限制遍历条目数；不跟随目录符号链接。
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as children:
                for entry in children:
                    entries += 1
                    if entries > MAX_ENTRIES or time.monotonic() - started >= MAX_SECONDS:
                        snapshot.skip("scan-limit")
                        return snapshot
                    if entry.name in EXCLUDED:
                        snapshot.skip("excluded-paths")
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    else:
                        observe(Path(entry.path))
                snapshot.covered_directories.add(directory.relative_to(root).as_posix())
        except OSError:
            snapshot.skip("io-error")
    return snapshot
