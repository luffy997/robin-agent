from pathlib import Path
from unittest.mock import patch

from robin.snapshots import capture
from robin.runtime import Robin


def test_target_snapshot_never_walks_workspace(tmp_path):
    target = tmp_path / 'app.py'
    with patch('robin.snapshots.os.scandir', side_effect=AssertionError('walk')):
        before = capture(tmp_path, [target])
        target.write_text('hello')
        after = capture(tmp_path, [target])
    assert Robin.diff_workspace_snapshots(before, after)[0] == ['app.py']
    assert after.complete


def test_shell_snapshot_prunes_dependencies_and_never_reads_contents(tmp_path):
    deps = tmp_path / 'node_modules'
    deps.mkdir()
    (deps / 'huge').write_text('ignored')
    (tmp_path / 'app').write_text('before')
    original = __import__('os').scandir

    def scan(path):
        assert Path(path) != deps
        return original(path)

    with patch('robin.snapshots.os.scandir', side_effect=scan), patch('robin.snapshots.os.open', side_effect=AssertionError('read')):
        before = capture(tmp_path)
        (tmp_path / 'app').write_text('after changed')
        (tmp_path / 'new').write_text('new')
        after = capture(tmp_path)
    assert set(Robin.diff_workspace_snapshots(before, after)[0]) == {'app', 'new'}
    assert not after.complete
    assert 'excluded-paths' in after.reasons


def test_limits_and_symlinks_are_explicit(tmp_path):
    target = tmp_path / 'big'
    target.write_bytes(b'x' * 20)
    with patch('robin.snapshots.MAX_FILE_BYTES', 10):
        result = capture(tmp_path, [target])
    assert not result.complete and 'file-size-limit' in result.reasons
    with patch('robin.snapshots.MAX_ENTRIES', 0):
        result = capture(tmp_path)
    assert not result.complete and 'scan-limit' in result.reasons
    assert Robin.diff_workspace_snapshots(result, capture(tmp_path))[0] == []
    link = tmp_path / 'link'
    link.symlink_to('/etc/passwd')
    result = capture(tmp_path, [link])
    assert not result and 'symlink' in result.reasons
