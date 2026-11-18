"""运行工件落盘。

session.json 负责保存“可恢复的会话状态”；RunStore 负责保存“单次运行的审计工件”，
例如 task_state、trace 和 report。两者分开后，恢复现场和复盘证据不会混在一起。
"""

import json
import tempfile
from pathlib import Path


def _run_id(value):
    """从 task_state 中取出合法的 run_id。"""
    if hasattr(value, "run_id"):
        return value.run_id
    return str(value)


class RunStore:
    def __init__(self, root):
        """初始化对象并保存运行所需依赖。"""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id):
        """返回指定 run_id 对应的工件目录。"""
        return self.root / _run_id(run_id)

    def task_state_path(self, run_id):
        """返回 task_state.json 的路径。"""
        return self.run_dir(run_id) / "task_state.json"

    def trace_path(self, run_id):
        """返回 trace.jsonl 的路径。"""
        return self.run_dir(run_id) / "trace.jsonl"

    def report_path(self, run_id):
        """返回 report.json 的路径。"""
        return self.run_dir(run_id) / "report.json"

    def start_run(self, task_state):
        # 每次 ask() 都会生成一个 run 目录。
        # 这样一次用户请求对应一组独立工件，后续排查更容易。
        """创建一次运行的工件目录并写入初始 task_state。"""
        run_dir = self.run_dir(task_state)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.write_task_state(task_state)
        return run_dir

    def write_task_state(self, task_state):
        """写入 write task state 生成的结果。"""
        path = self.task_state_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(path, task_state.to_dict())
        return path

    def append_trace(self, task_state, event):
        """追加 append trace 对应的信息。"""
        path = self.trace_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        # trace 采用 jsonl 追加写入，原因是 agent 运行过程是流式事件序列，
        # 逐条落盘比“最后一次性写整份 trace”更稳，也更适合调试。
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True))
            handle.write("\n")
        return path

    def write_report(self, task_state, report):
        """写入 write report 生成的结果。"""
        path = self.report_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(path, report)
        return path

    def load_task_state(self, task_id):
        """加载 load task state 需要的数据。"""
        return json.loads(self.task_state_path(task_id).read_text(encoding="utf-8"))

    def load_report(self, task_id):
        """加载 load report 需要的数据。"""
        return json.loads(self.report_path(task_id).read_text(encoding="utf-8"))

    def _write_json_atomic(self, path, payload):
        # 原子写：先写临时文件，再 replace。
        # 这样即使中途异常，也不容易留下半截 JSON。
        """写入 write json atomic 生成的结果。"""
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temp_name = handle.name
        Path(temp_name).replace(path)
