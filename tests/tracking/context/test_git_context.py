from unittest import mock

import git
import pytest

from mlflow.tracking.context.git_context import GitRunContext
from mlflow.utils.mlflow_tags import MLFLOW_GIT_BRANCH, MLFLOW_GIT_COMMIT, MLFLOW_GIT_REPO_URL

MOCK_SCRIPT_NAME = "/path/to/script.py"
MOCK_COMMIT_HASH = "commit-hash"
MOCK_BRANCH_NAME = "feature/git-tracking"
MOCK_REPO_URL = "https://github.com/mlflow/mlflow.git"


@pytest.fixture
def patch_script_name():
    patch_sys_argv = mock.patch("sys.argv", [MOCK_SCRIPT_NAME])
    patch_os_path_isfile = mock.patch("os.path.isfile", return_value=False)
    with patch_sys_argv, patch_os_path_isfile:
        yield


@pytest.fixture
def patch_git_repo():
    mock_repo = mock.Mock()
    mock_repo.head.commit.hexsha = MOCK_COMMIT_HASH
    mock_repo.active_branch.name = MOCK_BRANCH_NAME
    mock_repo.remotes = [mock.Mock(url=MOCK_REPO_URL)]
    mock_repo.ignored.return_value = []
    with mock.patch("git.Repo", return_value=mock_repo):
        yield mock_repo


def test_git_run_context_in_context_true(patch_script_name, patch_git_repo):
    assert GitRunContext().in_context()


def test_git_run_context_in_context_false(patch_script_name):
    with mock.patch("git.Repo", side_effect=git.InvalidGitRepositoryError):
        assert not GitRunContext().in_context()


def test_git_run_context_tags(patch_script_name, patch_git_repo):
    tags = GitRunContext().tags()
    assert tags[MLFLOW_GIT_COMMIT] == MOCK_COMMIT_HASH
    assert tags[MLFLOW_GIT_BRANCH] == MOCK_BRANCH_NAME
    assert tags[MLFLOW_GIT_REPO_URL] == MOCK_REPO_URL


def test_git_run_context_caching(patch_script_name):
    """Check that the git information is looked up once per property (3 times total for commit, branch, URL)."""

    with mock.patch("git.Repo") as mock_repo:
        context = GitRunContext()
        # Call in_context() and tags() multiple times
        context.in_context()
        context.in_context()
        context.tags()
        context.tags()

    # Each of the 3 Git properties (commit, branch, URL) creates its own Repo instance
    # but within GitRunContext, caching ensures each is fetched only once
    assert mock_repo.call_count == 3


def test_git_run_context_detached_head(patch_script_name):
    """Test handling when active_branch raises TypeError (detached HEAD state)."""
    mock_repo = mock.Mock()
    mock_repo.head.commit.hexsha = MOCK_COMMIT_HASH
    type(mock_repo).active_branch = mock.PropertyMock(side_effect=TypeError("HEAD is detached"))
    mock_repo.remotes = [mock.Mock(url=MOCK_REPO_URL)]
    mock_repo.ignored.return_value = []
    with mock.patch("git.Repo", return_value=mock_repo):
        context = GitRunContext()
        assert context.in_context()
        tags = context.tags()
        assert tags[MLFLOW_GIT_COMMIT] == MOCK_COMMIT_HASH
        assert MLFLOW_GIT_BRANCH not in tags
        assert tags[MLFLOW_GIT_REPO_URL] == MOCK_REPO_URL


def test_git_run_context_no_remotes(patch_script_name):
    """Test handling when remotes list is empty."""
    mock_repo = mock.Mock()
    mock_repo.head.commit.hexsha = MOCK_COMMIT_HASH
    mock_repo.active_branch.name = MOCK_BRANCH_NAME
    mock_repo.remotes = []
    mock_repo.ignored.return_value = []
    with mock.patch("git.Repo", return_value=mock_repo):
        context = GitRunContext()
        assert context.in_context()
        tags = context.tags()
        assert tags[MLFLOW_GIT_COMMIT] == MOCK_COMMIT_HASH
        assert tags[MLFLOW_GIT_BRANCH] == MOCK_BRANCH_NAME
        assert MLFLOW_GIT_REPO_URL not in tags


def test_git_run_context_partial_git_info(patch_script_name):
    """Test when only some Git information is available (e.g., commit but no branch or URL)."""
    mock_repo = mock.Mock()
    mock_repo.head.commit.hexsha = MOCK_COMMIT_HASH
    type(mock_repo).active_branch = mock.PropertyMock(side_effect=TypeError("HEAD is detached"))
    mock_repo.remotes = []
    mock_repo.ignored.return_value = []
    with mock.patch("git.Repo", return_value=mock_repo):
        context = GitRunContext()
        assert context.in_context()  # Should be in context because commit is available
        tags = context.tags()
        assert tags[MLFLOW_GIT_COMMIT] == MOCK_COMMIT_HASH
        assert MLFLOW_GIT_BRANCH not in tags
        assert MLFLOW_GIT_REPO_URL not in tags
