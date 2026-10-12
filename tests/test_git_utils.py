"""Tests for docdrift.core.git_utils against a real temporary repository."""

from pathlib import Path

from docdrift.core import git_utils


def test_get_changes_classifies_added_modified_deleted(repo):
    first = repo.commit({"a.py": "x = 1\n", "b.py": "y = 1\n", "ünï code.py": "z = 1\n"})
    second = repo.commit({"a.py": "x = 2\n", "b.py": None, "c.py": "w = 1\n"})
    changes = git_utils.get_changes(repo.root, first, second)
    assert changes.added == [Path("c.py")]
    assert changes.modified == [Path("a.py")]
    assert changes.deleted == [Path("b.py")]
    assert set(changes.existing) == {Path("c.py"), Path("a.py")}


def test_first_run_range_from_empty_tree_contains_every_file(repo):
    repo.commit({"a.py": "x = 1\n"})
    head = repo.commit({"b.py": "y = 1\n"})
    changes = git_utils.get_changes(repo.root, git_utils.EMPTY_TREE, head)
    assert sorted(changes.added) == [Path("a.py"), Path("b.py")]


def test_read_file_distinguishes_missing_from_empty(repo):
    sha = repo.commit({"empty.py": ""})
    assert git_utils.read_file(repo.root, Path("empty.py"), sha) == ""
    assert git_utils.read_file(repo.root, Path("missing.py"), sha) is None


def test_read_file_handles_utf8(repo):
    sha = repo.commit({"u.py": "# grüße ✓\n"})
    assert "grüße ✓" in git_utils.read_file(repo.root, Path("u.py"), sha)


def test_get_file_diff_and_list_files(repo):
    first = repo.commit({"a.py": "x = 1\n", "d/b.py": "y = 1\n"})
    second = repo.commit({"a.py": "x = 2\n"})
    diff = git_utils.get_file_diff(repo.root, Path("a.py"), first, second)
    assert "-x = 1" in diff and "+x = 2" in diff
    assert sorted(git_utils.list_files(repo.root, second)) == [Path("a.py"), Path("d/b.py")]


def test_commit_messages_for_range_and_first_run(repo):
    first = repo.commit({"a.py": "x = 1\n"}, "feat: initial")
    second = repo.commit({"a.py": "x = 2\n"}, "fix: tweak")
    assert git_utils.get_commit_messages(repo.root, first, second) == ["fix: tweak"]
    assert git_utils.get_commit_messages(repo.root, git_utils.EMPTY_TREE, second) == ["fix: tweak", "feat: initial"]
