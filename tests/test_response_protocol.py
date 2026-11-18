from robin import FakeModelClient, Robin, SessionStore, WorkspaceContext
from robin.providers.types import text_response


def test_protocol_examples_are_only_text(tmp_path):
    text = '<tool>{"name":"write_file"}</tool> <final>example</final> <｜｜DSML｜｜ calls>'
    agent = Robin(FakeModelClient([text_response(text)]), WorkspaceContext.build(tmp_path),
                  SessionStore(tmp_path / ".robin/sessions"), approval_policy="auto")
    assert agent.ask("explain protocol") == text
    assert agent.current_task_state.tool_steps == 0
