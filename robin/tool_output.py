"""工具输出分页及上下文缩减时的续读信息。"""

import re

READ_PAGE = re.compile(r"\[read_page start=(\d+) next=(\d+|EOF) total=(\d+)\]")


def merge_ranges(ranges):
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def read_page(path, lines, start, end, budget=4000):
    total = len(lines)
    if start > total:
        return f"# {path}\n[read_page start={start} next=EOF total={total}]"
    header = f"# {path}\n"
    body = []
    size = len(header)
    next_line = start
    for number in range(start, min(end, total) + 1):
        line = f"{number:>4}: {lines[number - 1]}"
        if size + len(line) + 1 > budget - 240:
            if not body:
                return (header + f"[Line {number} exceeds page budget; use run_shell to extract a specific portion. "
                        f"No line returned.]\n[read_page start={start} next={number} total={total}]")
            break
        body.append(line)
        size += len(line) + 1
        next_line = number + 1
    cursor = str(next_line) if next_line <= total else "EOF"
    hint = f"Continue with read_file start={cursor}." if cursor != "EOF" else "End of file."
    return header + "\n".join(body) + f"\n[read_page start={start} next={cursor} total={total}] {hint}"


def bound_result(text, budget):
    if len(text) <= budget:
        return text
    page = READ_PAGE.search(text)
    if page:
        # 模型上下文再次压缩时重算续读位置，不沿用原始页面的结束位置。
        start, _, total = page.groups()
        rows = re.findall(r"^\s*(\d+): (.*)$", text, re.MULTILINE)
        kept = []
        for number, line in rows:
            candidate = "\n".join(kept + [f"{number}: {line}"])
            if len(candidate) > max(0, budget - 130):
                break
            kept.append(f"{number}: {line}")
        next_line = int(start) + len(kept)
        return "\n".join(kept) + f"\n[read_page start={start} next={next_line} total={total}] Continue with read_file start={next_line}."
    marker = "[full_output:"
    index = text.rfind(marker)
    if index >= 0:
        footer = text[index:]
        return text[:max(0, budget - len(footer) - 25)] + "\n[preview truncated]\n" + footer
    return text[:budget] + "\n[tool result truncated]"
