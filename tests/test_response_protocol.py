from robin import Robin


def test_literal_protocol_tags_in_final_answer_are_preserved():
    answer = "返回 `<final>答案</final>` 时结束本轮；工具调用使用 `<tool>`。"
    assert Robin.parse(f"<final>{answer}</final>") == ("final", answer)


def test_reasoning_block_cannot_supply_the_action():
    raw = '<think>Use <final> or <tool> to answer.</think><final>你好</final>'
    assert Robin.parse(raw) == ("final", "你好")


def test_incomplete_or_embedded_final_is_retried():
    for raw in ("<final>未完成", "Analysis: return <final> to stop", "<think>unfinished"):
        assert Robin.parse(raw)[0] == "retry"
