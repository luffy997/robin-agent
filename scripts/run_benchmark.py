#!/usr/bin/env python3
"""从源码工作区运行确定性回归任务，将生成结果写入 artifacts。"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluator import run_fixed_benchmark  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run deterministic Robin harness regression.")
    parser.add_argument("--benchmark-path", default="benchmarks/coding_tasks.json")
    parser.add_argument("--artifact-path", default="artifacts/benchmark-v1.json")
    parser.add_argument("--workspace-root", default="artifacts/benchmark-workspaces")
    args = parser.parse_args(argv)
    artifact = run_fixed_benchmark(
        benchmark_path=args.benchmark_path,
        artifact_path=args.artifact_path,
        workspace_root=args.workspace_root,
    )
    print(artifact["summary"])
    return 0 if artifact["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
