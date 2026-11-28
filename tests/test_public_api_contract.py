from pathlib import Path

import robin
from robin.providers import types as model_types
from robin import Robin, SessionStore, WorkspaceContext, build_agent, build_arg_parser, build_welcome, main


def test_public_api_exports_current_names_only():
    for name in ("ModelClient", "ModelRequest", "ModelResponse", "ToolCall", "ToolResult", "Usage"):
        assert name in robin.__all__
        assert getattr(robin, name) is getattr(model_types, name)
    assert Robin is not None
    assert SessionStore is not None
    assert WorkspaceContext is not None
    assert callable(build_agent)
    assert callable(build_arg_parser)
    assert callable(build_welcome)
    assert callable(main)
    assert not hasattr(robin, "MiniAgent")
    assert "MiniAgent" not in robin.__all__


def test_build_agent_returns_robin(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    args = build_arg_parser().parse_args(["--cwd", str(tmp_path), "--approval", "auto"])

    agent = build_agent(args)

    assert isinstance(agent, Robin)


def test_lightweight_package_split_uses_package_paths_without_legacy_shims():
    from evaluation.evaluator import BenchmarkEvaluator
    from evaluation.metrics import run_context_ablation_v2
    from robin.features.memory import LayeredMemory
    from robin.providers.clients import FakeModelClient as ProviderFakeModelClient

    assert BenchmarkEvaluator is not None
    assert LayeredMemory is not None
    assert ProviderFakeModelClient is not None
    assert callable(run_context_ablation_v2)
    for legacy_module in ("evaluator.py", "metrics.py", "models.py", "memory.py"):
        assert not (Path("robin") / legacy_module).exists()


def test_packaging_discovers_robin_subpackages():
    pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'include = ["robin*"]' in pyproject_text
    assert (Path("evaluation") / "evaluator.py").is_file()
    assert not (Path("robin") / "evaluation").exists()
