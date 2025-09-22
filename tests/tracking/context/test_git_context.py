from unittest import mock

import git
import pytest

from mlflow.tracking.context.git_context import GitRunContext
from mlflow.utils.mlflow_tags import MLFLOW_GIT_COMMIT, MLFLOW_GIT_BRANCH, MLFLOW_GIT_REPO_URL

MOCK_SCRIPT_NAME = "/path/to/script.py"
MOCK_COMMIT_HASH = "commit-hash"
MOCK_BRANCH_NAME = "feature/test-branch"
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
    
    # Mock remotes to be iterable with url attribute
    mock_remote = mock.Mock()
    mock_remote.url = MOCK_REPO_URL
    mock_repo.remotes = [mock_remote]
    
    mock_repo.ignored.return_value = []
    with mock.patch("git.Repo", return_value=mock_repo):
        yield mock_repo


def test_git_run_context_in_context_true(patch_script_name, patch_git_repo):
    assert GitRunContext().in_context()


def test_git_run_context_in_context_false(patch_script_name):
    with mock.patch("git.Repo", side_effect=git.InvalidGitRepositoryError):
        assert not GitRunContext().in_context()


def test_git_run_context_tags(patch_script_name, patch_git_repo):
    expected_tags = {
        MLFLOW_GIT_COMMIT: MOCK_COMMIT_HASH,
        MLFLOW_GIT_BRANCH: MOCK_BRANCH_NAME,
        MLFLOW_GIT_REPO_URL: MOCK_REPO_URL
    }
    assert GitRunContext().tags() == expected_tags


def test_git_run_context_caching(patch_script_name, patch_git_repo):
    """Check that Git metadata is cached properly."""
    context = GitRunContext()
    
    # First access - should populate cache
    first_tags = context.tags()
    first_branch = context._git_branch
    first_repo_url = context._git_repo_url
    
    # Second access - should use cache
    second_tags = context.tags()
    second_branch = context._git_branch
    second_repo_url = context._git_repo_url
    
    # Results should be identical (cached)
    assert first_tags == second_tags
    assert first_branch == second_branch
    assert first_repo_url == second_repo_url
    
    # Verify cache is populated
    assert "source_version" in context._cache
    assert "git_branch" in context._cache
    assert "git_repo_url" in context._cache


def test_git_run_context_partial_metadata(patch_script_name):
    """Test when only some Git metadata is available."""
    mock_repo = mock.Mock()
    mock_repo.head.commit.hexsha = MOCK_COMMIT_HASH
    mock_repo.active_branch.name = MOCK_BRANCH_NAME
    mock_repo.remotes = []  # No remotes configured
    mock_repo.ignored.return_value = []
    
    with mock.patch("git.Repo", return_value=mock_repo):
        context = GitRunContext()
        tags = context.tags()
        
        # Should have commit and branch, but not repo URL
        assert MLFLOW_GIT_COMMIT in tags
        assert MLFLOW_GIT_BRANCH in tags
        assert MLFLOW_GIT_REPO_URL not in tags
        
        assert tags[MLFLOW_GIT_COMMIT] == MOCK_COMMIT_HASH
        assert tags[MLFLOW_GIT_BRANCH] == MOCK_BRANCH_NAME


def test_git_run_context_detached_head(patch_script_name):
    """Test behavior in detached HEAD state."""
    mock_repo = mock.Mock()
    mock_repo.head.commit.hexsha = MOCK_COMMIT_HASH
    mock_repo.head.is_detached = True
    mock_repo.active_branch = None  # No active branch in detached state
    
    # Mock remotes to be iterable with url attribute
    mock_remote = mock.Mock()
    mock_remote.url = MOCK_REPO_URL
    mock_repo.remotes = [mock_remote]
    
    mock_repo.ignored.return_value = []
    
    with mock.patch("git.Repo", return_value=mock_repo):
        # Mock get_git_branch to return None for detached HEAD
        with mock.patch("mlflow.utils.git_utils.get_git_branch", return_value=None):
            context = GitRunContext()
            tags = context.tags()
            
            # Should have commit and repo URL, but not branch
            assert MLFLOW_GIT_COMMIT in tags
            assert MLFLOW_GIT_BRANCH not in tags
            assert MLFLOW_GIT_REPO_URL in tags
            
            assert tags[MLFLOW_GIT_COMMIT] == MOCK_COMMIT_HASH
            assert tags[MLFLOW_GIT_REPO_URL] == MOCK_REPO_URL
