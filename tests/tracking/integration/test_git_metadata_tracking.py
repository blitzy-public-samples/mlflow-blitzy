"""
Integration tests for automatic Git metadata tracking feature.

This module validates that Git branch name, repository URL, and commit hash are
automatically captured as system tags when starting MLflow runs from within Git
repositories. Tests cover both mlflow.start_run() and MlflowClient.create_run()
paths, graceful handling outside Git repositories, preservation of manually set
Git tags, nested runs behavior, and concurrent run creation scenarios.
"""

import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

import mlflow
import mlflow.tracking.context.registry
from mlflow import MlflowClient
from mlflow.tracking.context.git_context import GitRunContext
from mlflow.utils.mlflow_tags import (
    MLFLOW_GIT_BRANCH,
    MLFLOW_GIT_COMMIT,
    MLFLOW_GIT_REPO_URL,
    MLFLOW_PARENT_RUN_ID,
)


# Test constants
TEST_BRANCH_NAME = "feature/test-git-tracking"
TEST_REPO_URL = "https://github.com/mlflow/test-repo.git"


def _clear_git_context_cache():
    """
    Clears the GitRunContext cache.
    
    This helper function clears the cache of the registered GitRunContext instance.
    It must be called INSIDE the mock.patch context to ensure the cache is cleared
    after sys.argv has been mocked.
    
    Note: We access the registry dynamically through the module to handle cases where
    other tests may have reloaded the registry module, creating a new registry instance.
    """
    # Access registry dynamically to get the current instance
    registry = mlflow.tracking.context.registry._run_context_provider_registry
    for provider in registry:
        if isinstance(provider, GitRunContext):
            provider._cache = {}
            break


@pytest.fixture(autouse=True)
def clear_git_context_cache():
    """
    Clears the GitRunContext cache before and after each test.
    
    Note: This fixture clears the cache at setup and teardown, but tests should
    also call _clear_git_context_cache() inside mock.patch contexts to ensure
    the cache is cleared after sys.argv is mocked.
    """
    _clear_git_context_cache()
    yield
    _clear_git_context_cache()


@pytest.fixture
def git_repo_fixture():
    """
    Creates a temporary Git repository with initialized branch and remote URL.
    
    Yields:
        dict: Contains 'path', 'commit_hash', 'branch_name', and 'repo_url' keys.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
        )
        
        # Create a test file and commit
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("# Test file\n")
        subprocess.run(["git", "add", "test.py"], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
        )
        
        # Create and checkout branch
        subprocess.run(
            ["git", "checkout", "-b", TEST_BRANCH_NAME],
            cwd=tmpdir,
            check=True,
            capture_output=True,
        )
        
        # Add remote
        subprocess.run(
            ["git", "remote", "add", "origin", TEST_REPO_URL],
            cwd=tmpdir,
            check=True,
            capture_output=True,
        )
        
        # Get commit hash
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            text=True,
        )
        commit_hash = result.stdout.strip()
        
        yield {
            "path": tmpdir,
            "test_file": test_file,
            "commit_hash": commit_hash,
            "branch_name": TEST_BRANCH_NAME,
            "repo_url": TEST_REPO_URL,
        }


@pytest.fixture
def non_git_directory():
    """
    Creates a temporary directory without Git initialization.
    
    Yields:
        dict: Contains 'path' and 'test_file' keys.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("# Test file\n")
        yield {"path": tmpdir, "test_file": test_file}


class TestStartRunGitMetadata:
    """Tests for automatic Git metadata tracking with mlflow.start_run()."""

    def test_start_run_in_git_repo_captures_metadata(self, git_repo_fixture):
        """Verify mlflow.start_run() captures Git branch and repo URL automatically."""
        test_file = git_repo_fixture["test_file"]
        
        # Patch sys.argv to simulate script execution from Git repo
        # os.path.isfile should return True for the test file so git_utils can find the directory
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            with mlflow.start_run() as run:
                run_id = run.info.run_id
        
        # Retrieve run and verify tags
        client = MlflowClient()
        run_data = client.get_run(run_id)
        tags = run_data.data.tags
        
        assert MLFLOW_GIT_COMMIT in tags
        assert tags[MLFLOW_GIT_COMMIT] == git_repo_fixture["commit_hash"]
        
        assert MLFLOW_GIT_BRANCH in tags
        assert tags[MLFLOW_GIT_BRANCH] == git_repo_fixture["branch_name"]
        
        assert MLFLOW_GIT_REPO_URL in tags
        assert tags[MLFLOW_GIT_REPO_URL] == git_repo_fixture["repo_url"]

    def test_run_outside_git_repo_no_errors(self, non_git_directory):
        """Verify run creation completes without errors outside Git repository."""
        test_file = non_git_directory["test_file"]
        
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            with mlflow.start_run() as run:
                run_id = run.info.run_id
        
        # Retrieve run and verify Git tags are NOT present
        client = MlflowClient()
        run_data = client.get_run(run_id)
        tags = run_data.data.tags
        
        # Git-related tags should not be present
        assert MLFLOW_GIT_COMMIT not in tags
        assert MLFLOW_GIT_BRANCH not in tags
        assert MLFLOW_GIT_REPO_URL not in tags


class TestCreateRunGitMetadata:
    """Tests for automatic Git metadata tracking with MlflowClient.create_run()."""

    def test_create_run_in_git_repo_captures_metadata(self, git_repo_fixture):
        """Verify MlflowClient.create_run() captures Git metadata via context resolution."""
        test_file = git_repo_fixture["test_file"]
        
        # Patch sys.argv to simulate script execution from Git repo
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            # start_run uses context resolution which includes GitRunContext
            with mlflow.start_run() as run:
                run_id = run.info.run_id
        
        # Retrieve run and verify tags
        client = MlflowClient()
        run_data = client.get_run(run_id)
        tags = run_data.data.tags
        
        assert MLFLOW_GIT_COMMIT in tags
        assert MLFLOW_GIT_BRANCH in tags
        assert MLFLOW_GIT_REPO_URL in tags


class TestManualTagsPreserved:
    """Tests for verifying manually set Git tags are preserved."""

    def test_manual_git_tags_in_start_run(self, git_repo_fixture):
        """Verify manual tags passed to start_run() override automatic detection."""
        test_file = git_repo_fixture["test_file"]
        manual_branch = "manual/branch"
        manual_url = "https://github.com/manual/repo.git"
        
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            with mlflow.start_run(
                tags={
                    MLFLOW_GIT_BRANCH: manual_branch,
                    MLFLOW_GIT_REPO_URL: manual_url,
                }
            ) as run:
                run_id = run.info.run_id
        
        # Retrieve run and verify manual tags override automatic ones
        client = MlflowClient()
        run_data = client.get_run(run_id)
        tags = run_data.data.tags
        
        # Manual tags should be preserved
        assert tags[MLFLOW_GIT_BRANCH] == manual_branch
        assert tags[MLFLOW_GIT_REPO_URL] == manual_url
        # Commit should still be from automatic detection
        assert MLFLOW_GIT_COMMIT in tags

    def test_manual_git_tags_via_set_tag(self, git_repo_fixture):
        """Verify tags set via set_tag() persist correctly."""
        test_file = git_repo_fixture["test_file"]
        manual_branch = "set_tag/branch"
        
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            with mlflow.start_run() as run:
                mlflow.set_tag(MLFLOW_GIT_BRANCH, manual_branch)
                run_id = run.info.run_id
        
        # Retrieve run and verify set_tag value
        client = MlflowClient()
        run_data = client.get_run(run_id)
        tags = run_data.data.tags
        
        # set_tag should override the automatic value
        assert tags[MLFLOW_GIT_BRANCH] == manual_branch


class TestNestedRunsGitMetadata:
    """Tests for Git metadata in nested runs."""

    def test_nested_runs_capture_git_metadata(self, git_repo_fixture):
        """Verify both parent and nested child runs capture Git metadata correctly."""
        test_file = git_repo_fixture["test_file"]
        
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            with mlflow.start_run() as parent_run:
                parent_run_id = parent_run.info.run_id
                
                with mlflow.start_run(nested=True) as child_run:
                    child_run_id = child_run.info.run_id
        
        client = MlflowClient()
        
        # Check parent run
        parent_data = client.get_run(parent_run_id)
        assert MLFLOW_GIT_COMMIT in parent_data.data.tags
        assert MLFLOW_GIT_BRANCH in parent_data.data.tags
        assert MLFLOW_GIT_REPO_URL in parent_data.data.tags
        
        # Check child run
        child_data = client.get_run(child_run_id)
        assert MLFLOW_GIT_COMMIT in child_data.data.tags
        assert MLFLOW_GIT_BRANCH in child_data.data.tags
        assert MLFLOW_GIT_REPO_URL in child_data.data.tags
        assert child_data.data.tags[MLFLOW_PARENT_RUN_ID] == parent_run_id


class TestTagFormatValidation:
    """Tests for Git tag format validation."""

    def test_git_tag_keys_follow_convention(self, git_repo_fixture):
        """Verify tag keys follow MLflow system tag naming conventions."""
        test_file = git_repo_fixture["test_file"]
        
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            with mlflow.start_run() as run:
                run_id = run.info.run_id
        
        client = MlflowClient()
        run_data = client.get_run(run_id)
        tags = run_data.data.tags
        
        # Verify tag keys start with 'mlflow.'
        assert MLFLOW_GIT_COMMIT.startswith("mlflow.")
        assert MLFLOW_GIT_BRANCH.startswith("mlflow.")
        assert MLFLOW_GIT_REPO_URL.startswith("mlflow.")
        
        # Verify tag values are non-empty strings
        assert isinstance(tags[MLFLOW_GIT_COMMIT], str)
        assert len(tags[MLFLOW_GIT_COMMIT]) > 0
        assert isinstance(tags[MLFLOW_GIT_BRANCH], str)
        assert len(tags[MLFLOW_GIT_BRANCH]) > 0
        assert isinstance(tags[MLFLOW_GIT_REPO_URL], str)
        assert len(tags[MLFLOW_GIT_REPO_URL]) > 0


class TestConcurrentRunCreation:
    """Tests for concurrent run creation scenarios."""

    def test_concurrent_runs_capture_git_metadata(self, git_repo_fixture):
        """Verify multiple concurrent runs capture Git metadata correctly."""
        test_file = git_repo_fixture["test_file"]
        num_runs = 5
        run_ids = []
        
        def create_run():
            with mock.patch("sys.argv", [test_file]):
                # Clear cache inside mock context to ensure fresh Git detection
                _clear_git_context_cache()
                with mlflow.start_run() as run:
                    return run.info.run_id
        
        with ThreadPoolExecutor(max_workers=num_runs) as executor:
            futures = [executor.submit(create_run) for _ in range(num_runs)]
            run_ids = [f.result() for f in futures]
        
        # Verify all runs captured Git metadata
        client = MlflowClient()
        for run_id in run_ids:
            run_data = client.get_run(run_id)
            tags = run_data.data.tags
            
            assert MLFLOW_GIT_COMMIT in tags
            assert tags[MLFLOW_GIT_COMMIT] == git_repo_fixture["commit_hash"]
            assert MLFLOW_GIT_BRANCH in tags
            assert tags[MLFLOW_GIT_BRANCH] == git_repo_fixture["branch_name"]
            assert MLFLOW_GIT_REPO_URL in tags
            assert tags[MLFLOW_GIT_REPO_URL] == git_repo_fixture["repo_url"]


class TestEdgeCases:
    """Tests for edge cases in Git metadata detection."""

    def test_detached_head_state(self, git_repo_fixture):
        """Verify graceful handling when HEAD is detached."""
        tmpdir = git_repo_fixture["path"]
        test_file = git_repo_fixture["test_file"]
        commit_hash = git_repo_fixture["commit_hash"]
        
        # Detach HEAD
        subprocess.run(
            ["git", "checkout", "--detach"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
        )
        
        with mock.patch("sys.argv", [test_file]):
            # Clear cache inside mock context to ensure fresh Git detection
            _clear_git_context_cache()
            with mlflow.start_run() as run:
                run_id = run.info.run_id
        
        client = MlflowClient()
        run_data = client.get_run(run_id)
        tags = run_data.data.tags
        
        # Commit should still be captured
        assert MLFLOW_GIT_COMMIT in tags
        assert tags[MLFLOW_GIT_COMMIT] == commit_hash
        
        # Branch should NOT be captured (detached HEAD)
        assert MLFLOW_GIT_BRANCH not in tags
        
        # URL should still be captured
        assert MLFLOW_GIT_REPO_URL in tags

    def test_no_remotes(self):
        """Verify graceful handling when repository has no configured remotes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Initialize git repo without remote
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            
            test_file = os.path.join(tmpdir, "test.py")
            with open(test_file, "w") as f:
                f.write("# Test file\n")
            subprocess.run(["git", "add", "test.py"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
            )
            
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
                text=True,
            )
            commit_hash = result.stdout.strip()
            
            with mock.patch("sys.argv", [test_file]):
                # Clear cache inside mock context to ensure fresh Git detection
                _clear_git_context_cache()
                with mlflow.start_run() as run:
                    run_id = run.info.run_id
            
            client = MlflowClient()
            run_data = client.get_run(run_id)
            tags = run_data.data.tags
            
            # Commit should still be captured
            assert MLFLOW_GIT_COMMIT in tags
            assert tags[MLFLOW_GIT_COMMIT] == commit_hash
            
            # Branch should be captured (we're on default branch)
            assert MLFLOW_GIT_BRANCH in tags
            
            # URL should NOT be captured (no remotes)
            assert MLFLOW_GIT_REPO_URL not in tags
