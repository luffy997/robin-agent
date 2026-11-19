import os
from pathlib import Path

import pytest

from robin.tool_output import READ_PAGE, bound_result, read_page
from test_robin import build_agent


def test_read_pages_continue_without_losing_lines():
    lines = [f"line {n} " + "字" * 80 for n in range(300)]
    start = 1
    seen = []
    while start <= len(lines):
        text = read_page("demo", lines, start, 300)
        assert len(text) <= 4000
        page = READ_PAGE.search(text)
        cursor = page.group(2)
        last = len(lines) if cursor == "EOF" else int(cursor) - 1
        seen.extend(range(start, last + 1))
        start = last + 1
    assert seen == list(range(1, 301))


def test_context_reduction_recalculates_cursor_and_handles_long_line():
    text = read_page("demo", ["x" * 80] * 100, 1, 100)
    reduced = bound_result(text, 500)
    page = READ_PAGE.search(reduced)
    assert int(page.group(2)) < int(READ_PAGE.search(text).group(2))
    assert len(reduced) <= 500
    long_line = read_page("demo", ["x" * 5000, "second"], 1, 2)
    assert "No line returned" in long_line
    assert "next=1" in long_line


def test_repeated_read_across_other_calls_allows_changed_file_and_new_ranges(tmp_path):
    agent = build_agent(tmp_path, [])
    path = tmp_path / "sample"
    path.write_text("one\ntwo\nthree\n")
    args = {"path": "sample", "start": 1, "end": 1}
    for _ in range(2):
        assert agent.execute_tool("read_file", args).metadata["tool_status"] == "ok"
        agent.execute_tool("list_files", {})
    assert agent.execute_tool("read_file", args).metadata["tool_error_code"] == "repeated_read_range"
    assert agent.execute_tool("read_file", {**args, "end": 3}).metadata["tool_status"] == "ok"
    path.write_text("changed\ntwo\nthree\n")
    assert "changed" in agent.execute_tool("read_file", args).content


def test_shell_output_saved_redacted_and_preview_keeps_failure_status(tmp_path, monkeypatch):
    agent = build_agent(tmp_path, [])
    monkeypatch.setenv("ROBIN_API_KEY", "secret-test-value")
    raw = "exit_code: 1\nstdout:\n" + "x" * 10000 + "secret-test-value"
    agent.tools["run_shell"]["run"] = lambda args: raw
    result = agent.execute_tool("run_shell", {"command": "demo"})
    assert result.metadata["tool_status"] == "error"
    path = result.content.split("[full_output: ")[1].split("]")[0]
    saved = (tmp_path / path).read_text()
    assert "secret-test-value" not in saved
    assert "<redacted>" in saved
    assert len(saved) > 10000
    assert path in bound_result(result.content, 500)


def test_merged_full_read_is_retained_until_file_changes(tmp_path):
    from robin.providers.context import build_request
    agent = build_agent(tmp_path, [])
    path = tmp_path / "sample"
    path.write_text("first\nsecond\nthird\nfourth\n")
    for start, end in [(1, 2), (3, 4)]:
        assert agent.execute_tool("read_file", {"path": "sample", "start": start, "end": end}).metadata["tool_status"] == "ok"
    result = agent.execute_tool("read_file", {"path": "sample", "start": 2, "end": 4})
    assert "already fully read" in result.content
    assert "start=5" not in result.content
    request = build_request(agent, "task", {"section_budgets": {"history": 80}})
    assert "first\nsecond\nthird\nfourth" in request.messages[0]["content"]
    path.write_text("changed\n")
    assert "first\nsecond" not in agent.tool_executor.read_context()
    assert "changed" in agent.execute_tool("read_file", {"path": "sample"}).content
    agent.reset()
    assert not agent.tool_executor.read_context()


def test_repeated_range_hint_skips_all_covered_pages(tmp_path):
    agent = build_agent(tmp_path, [])
    (tmp_path / "sample").write_text("line\n" * 10)
    for start, end in [(1, 2), (3, 4), (1, 2)]:
        agent.execute_tool("read_file", {"path": "sample", "start": start, "end": end})
    result = agent.execute_tool("read_file", {"path": "sample", "start": 1, "end": 2})
    assert "First unread line: 5" in result.content


@pytest.mark.parametrize("replacement", ["fifo", "directory", "missing", "grown", "changed"])
def test_read_context_evicts_invalid_file_without_unbounded_read(tmp_path, monkeypatch, replacement):
    agent = build_agent(tmp_path, [])
    path = tmp_path / "sample"
    path.write_text("original\n")
    agent.execute_tool("read_file", {"path": "sample"})
    assert agent.tool_executor._read_context
    path.unlink()
    if replacement == "fifo":
        os.mkfifo(path)
    elif replacement == "directory":
        path.mkdir()
    elif replacement == "grown":
        path.write_bytes(b"x" * 12001)
    elif replacement == "changed":
        path.write_text("changed\n")

    def forbid_unbounded_read(self):
        raise AssertionError("cache validation must use bounded descriptor reads")

    monkeypatch.setattr(Path, "read_bytes", forbid_unbounded_read)
    assert agent.tool_executor.read_context() == ""
    assert not agent.tool_executor._read_context


@pytest.mark.parametrize("replacement", ["fifo", "symlink"])
def test_read_context_handles_replacement_between_stat_and_open(tmp_path, monkeypatch, replacement):
    agent = build_agent(tmp_path, [])
    path = tmp_path / "sample"
    path.write_text("original\n")
    agent.execute_tool("read_file", {"path": "sample"})
    real_open = os.open

    def replace_before_open(target, flags):
        assert flags & os.O_NONBLOCK
        assert flags & os.O_NOFOLLOW
        path.unlink()
        if replacement == "fifo":
            os.mkfifo(path)
        else:
            target_path = tmp_path / "other"
            target_path.write_text("original\n")
            path.symlink_to(target_path)
        return real_open(target, flags)

    monkeypatch.setattr("robin.tool_executor.os.open", replace_before_open)
    assert agent.tool_executor.read_context() == ""
    assert not agent.tool_executor._read_context


def test_read_context_bounds_read_if_file_grows_after_fstat(tmp_path, monkeypatch):
    agent = build_agent(tmp_path, [])
    path = tmp_path / "sample"
    path.write_text("original\n")
    agent.execute_tool("read_file", {"path": "sample"})
    real_fstat = os.fstat

    def grow_after_fstat(fd):
        info = real_fstat(fd)
        with path.open("ab") as stream:
            stream.write(b"x" * 20000)
        return info

    monkeypatch.setattr("robin.tool_executor.os.fstat", grow_after_fstat)
    assert agent.tool_executor.read_context() == ""
    assert not agent.tool_executor._read_context
