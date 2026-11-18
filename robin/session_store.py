"""Session JSON persistence."""

import json
from pathlib import Path


class SessionStore:
    def __init__(self, root):
        """初始化对象并保存运行所需依赖。"""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id):
        """返回指定 session_id 对应的 JSON 文件路径。"""
        return self.root / f"{session_id}.json"

    def save(self, session):
        """把 session 原子写入 sessions 目录。"""
        path = self.path(session["id"])
        path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def load(self, session_id):
        """从磁盘读取并反序列化指定 session。"""
        return json.loads(self.path(session_id).read_text(encoding="utf-8"))

    def latest(self):
        """返回最近修改的 session_id；时间戳并列时用文件名做确定性兜底。"""
        files = sorted(self.root.glob("*.json"), key=lambda path: (path.stat().st_mtime_ns, path.name))
        return files[-1].stem if files else None
