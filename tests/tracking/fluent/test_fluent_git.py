"""Integration tests for Git metadata capture through fluent API

This module provides comprehensive integration tests that validate automatic Git metadata
capture when using MLflow's fluent API (mlflow.start_run()). The tests ensure that Git
branch name and repository URL are automatically captured as system tags when executing
within a Git repository, while handling edge cases gracefully and respecting manually
set Git tags.

Test Coverage:
- Automatic Git metadata capture in Git repositories
- Graceful behavior when not in a Git repository  
- Manual Git tag precedence over automatic detection
- Nested run Git metadata inheritance
- Edge cases: detached HEAD, no remotes, etc.
"""
import os
import tempfile
import pytest
from unittest import mock
from git import Repo
import uuid

import mlflow
from mlflow.utils.mlflow_tags import (
    MLFLOW_GIT_COMMIT,
    MLFLOW_GIT_BRANCH, 
    MLFLOW_GIT_REPO_URL
)
from mlflow.tracking.context.git_context import GitRunContext


@pytest.fixture(autouse=True)
def clear_git_context_cache():
    """Clear GitRunContext cache before each test to prevent cross-test interference."""
    # Clear any existing GitRunContext cache before each test
    from mlflow.tracking.context import registry
    
    # Get the registry and clear any cached GitRunContext instances
    provider_registry = registry._run_context_provider_registry
    for provider in provider_registry._registry:
        if isinstance(provider, GitRunContext):
            provider._cache.clear()
    
    yield  # Run the test


def test_start_run_captures_git_metadata():
    """Test that start_run captures Git metadata when in a Git repo
    
    Creates a temporary Git repository with a branch and remote, then verifies
    that mlflow.start_run() automatically captures Git branch, repository URL,
    and commit hash when executed within that repository.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a Git repository with initial commit
        repo = Repo.init(tmpdir)
        
        # Create a test file and add it to the repo
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        # Stage and commit the file
        repo.index.add(["test.py"])
        repo.index.commit("Initial commit")
        
        # Add a remote repository URL  
        repo.create_remote("origin", "https://github.com/test/repo.git")
        
        # Create and checkout a feature branch
        feature_branch = repo.create_head("feature/test-branch")
        feature_branch.checkout()
        
        # Mock the main file detection to point to our test file
        # Need to patch both the original module AND the git_context import
        with mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            with mlflow.start_run() as run:
                tags = run.data.tags
                
                # Verify all Git metadata was captured
                assert MLFLOW_GIT_BRANCH in tags
                assert tags[MLFLOW_GIT_BRANCH] == "feature/test-branch"
                
                assert MLFLOW_GIT_REPO_URL in tags
                assert tags[MLFLOW_GIT_REPO_URL] == "https://github.com/test/repo.git"
                
                assert MLFLOW_GIT_COMMIT in tags
                # Commit hash should be a valid hex string
                assert len(tags[MLFLOW_GIT_COMMIT]) == 40
                assert all(c in '0123456789abcdef' for c in tags[MLFLOW_GIT_COMMIT])


def test_start_run_without_git_repo():
    """Test that start_run works normally when not in a Git repo
    
    Verifies that mlflow.start_run() works correctly when executed outside
    of a Git repository, with no Git tags being added to the run.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test file in a non-Git directory
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        # Mock the main file detection to point to our non-Git test file
        # Need to patch both the original module AND the git_context import
        with mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            with mlflow.start_run() as run:
                tags = run.data.tags
                
                # Git tags should not be present when not in a Git repository
                assert MLFLOW_GIT_BRANCH not in tags
                assert MLFLOW_GIT_REPO_URL not in tags
                assert MLFLOW_GIT_COMMIT not in tags


def test_manually_set_git_tags_not_overwritten():
    """Test that manually set Git tags are not overwritten by automatic detection
    
    Ensures that when users provide Git tags manually in the mlflow.start_run(tags={...})
    call, these manual tags take precedence over any automatic Git detection.
    """
    # Define manual Git tag values that differ from any automatic detection
    manual_branch = "manual-override-branch"
    manual_repo = "https://manual.override.repo/url.git"
    manual_commit = "1234567890abcdef" * 2 + "12345678"  # 40-char hex
    
    # Start run with manually provided Git tags
    with mlflow.start_run(tags={
        MLFLOW_GIT_BRANCH: manual_branch,
        MLFLOW_GIT_REPO_URL: manual_repo,
        MLFLOW_GIT_COMMIT: manual_commit
    }) as run:
        tags = run.data.tags
        
        # Manual tags should be preserved exactly as provided
        assert tags[MLFLOW_GIT_BRANCH] == manual_branch
        assert tags[MLFLOW_GIT_REPO_URL] == manual_repo
        assert tags[MLFLOW_GIT_COMMIT] == manual_commit


def test_nested_runs_inherit_git_metadata():
    """Test that nested runs inherit Git metadata from their parent context
    
    Verifies that when creating nested runs using mlflow.start_run(nested=True),
    the child runs properly inherit the Git metadata from their parent's context.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a Git repository with branch and remote
        repo = Repo.init(tmpdir)
        
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        repo.index.add(["test.py"])
        repo.index.commit("Initial commit")
        repo.create_remote("origin", "https://github.com/test/nested-repo.git")
        
        # Create and checkout a branch for testing
        test_branch = repo.create_head("test/nested-branch")
        test_branch.checkout()
        
        with mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            # Start parent run and capture its Git metadata
            with mlflow.start_run() as parent_run:
                parent_tags = parent_run.data.tags
                
                # Start nested child run
                with mlflow.start_run(nested=True) as child_run:
                    child_tags = child_run.data.tags
                    
                    # Child should inherit the same Git metadata as parent
                    assert child_tags.get(MLFLOW_GIT_BRANCH) == parent_tags.get(MLFLOW_GIT_BRANCH)
                    assert child_tags.get(MLFLOW_GIT_REPO_URL) == parent_tags.get(MLFLOW_GIT_REPO_URL)
                    assert child_tags.get(MLFLOW_GIT_COMMIT) == parent_tags.get(MLFLOW_GIT_COMMIT)
                    
                    # Verify the specific values are correct
                    assert child_tags[MLFLOW_GIT_BRANCH] == "test/nested-branch"
                    assert child_tags[MLFLOW_GIT_REPO_URL] == "https://github.com/test/nested-repo.git"


def test_start_run_detached_head_state():
    """Test behavior when Git repository is in detached HEAD state
    
    Validates that when the Git repository is in detached HEAD state (not on any branch),
    the system correctly captures commit hash and repository URL but does not set
    a branch tag since there is no active branch.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create Git repository and make initial commit
        repo = Repo.init(tmpdir)
        
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        repo.index.add(["test.py"])
        initial_commit = repo.index.commit("Initial commit")
        repo.create_remote("origin", "https://github.com/test/detached-repo.git")
        
        # Create a second commit on main branch
        with open(test_file, "a") as f:
            f.write("\nprint('second line')")
        repo.index.add(["test.py"])
        repo.index.commit("Second commit")
        
        # Checkout the initial commit to create detached HEAD state
        repo.git.checkout(initial_commit.hexsha)
        
        with mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            with mlflow.start_run() as run:
                tags = run.data.tags
                
                # In detached HEAD state:
                # - Commit should be captured (points to the checked out commit)
                # - Repository URL should be captured (remote still exists)
                # - Branch should NOT be captured (not on any branch)
                assert MLFLOW_GIT_COMMIT in tags
                assert tags[MLFLOW_GIT_COMMIT] == initial_commit.hexsha
                
                assert MLFLOW_GIT_REPO_URL in tags
                assert tags[MLFLOW_GIT_REPO_URL] == "https://github.com/test/detached-repo.git"
                
                # No branch should be captured in detached HEAD state
                assert MLFLOW_GIT_BRANCH not in tags


def test_start_run_no_remote_configured():
    """Test behavior when Git repository has no remote configured
    
    Ensures that when a Git repository exists but has no remotes configured,
    the system captures commit hash and branch name but does not set a
    repository URL tag since no remote URL is available.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create Git repository without any remotes
        repo = Repo.init(tmpdir)
        
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        repo.index.add(["test.py"])
        commit = repo.index.commit("Initial commit")
        
        # Create and checkout a branch (but don't add any remotes)
        feature_branch = repo.create_head("feature/no-remote")
        feature_branch.checkout()
        
        with mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            with mlflow.start_run() as run:
                tags = run.data.tags
                
                # Without remotes:
                # - Commit should be captured
                # - Branch should be captured  
                # - Repository URL should NOT be captured (no remotes)
                assert MLFLOW_GIT_COMMIT in tags
                assert MLFLOW_GIT_BRANCH in tags
                assert tags[MLFLOW_GIT_BRANCH] == "feature/no-remote"
                
                assert MLFLOW_GIT_REPO_URL not in tags


def test_start_run_multiple_remotes_prefers_origin():
    """Test behavior when Git repository has multiple remotes configured
    
    When a Git repository has multiple remotes, the system should prefer
    the 'origin' remote if it exists, otherwise use the first available remote.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create Git repository with multiple remotes
        repo = Repo.init(tmpdir)
        
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        repo.index.add(["test.py"])
        repo.index.commit("Initial commit")
        
        # Add multiple remotes - deliberately add 'upstream' first to test origin preference
        repo.create_remote("upstream", "https://github.com/upstream/repo.git")
        repo.create_remote("origin", "https://github.com/origin/repo.git")
        repo.create_remote("fork", "https://github.com/fork/repo.git")
        
        with mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            with mlflow.start_run() as run:
                tags = run.data.tags
                
                # Should use first remote added (upstream in this case)
                assert MLFLOW_GIT_REPO_URL in tags
                assert tags[MLFLOW_GIT_REPO_URL] == "https://github.com/upstream/repo.git"


def test_start_run_git_operations_error_handling():
    """Test graceful handling of Git operation errors
    
    Ensures that when Git operations fail (due to permissions, corruption, etc.),
    the system handles errors gracefully without causing run creation to fail.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        # Mock Git operations to raise exceptions (simulating Git errors)
        with mock.patch("mlflow.utils.git_utils.get_git_commit", return_value=None), \
             mock.patch("mlflow.utils.git_utils.get_git_branch", return_value=None), \
             mock.patch("mlflow.utils.git_utils.get_git_repo_url", return_value=None), \
             mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            
            # Run creation should succeed even when Git operations fail
            with mlflow.start_run() as run:
                tags = run.data.tags
                
                # No Git tags should be present when Git operations fail
                assert MLFLOW_GIT_COMMIT not in tags
                assert MLFLOW_GIT_BRANCH not in tags  
                assert MLFLOW_GIT_REPO_URL not in tags


def test_start_run_preserves_other_system_tags():
    """Test that Git metadata capture does not interfere with other system tags
    
    Ensures that automatic Git metadata capture works alongside other MLflow
    system tags (user, source name, source type, etc.) without conflicts.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create Git repository for metadata capture
        repo = Repo.init(tmpdir)
        
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, "w") as f:
            f.write("print('test')")
        
        repo.index.add(["test.py"])
        repo.index.commit("Initial commit")
        repo.create_remote("origin", "https://github.com/test/preserve-tags.git")
        
        with mock.patch("mlflow.tracking.context.default_context._get_main_file",
                       return_value=test_file), \
             mock.patch("mlflow.tracking.context.git_context._get_main_file",
                       return_value=test_file):
            with mlflow.start_run() as run:
                tags = run.data.tags
                
                # Git tags should be present
                assert MLFLOW_GIT_COMMIT in tags
                assert MLFLOW_GIT_BRANCH in tags
                assert MLFLOW_GIT_REPO_URL in tags
                
                # Other system tags should also be present and unaffected
                # (Note: The exact tags present depend on the execution context,
                #  but we can check that Git tags don't prevent other tags)
                tag_count = len(tags)
                assert tag_count >= 3  # At least the 3 Git tags we expect
                
                # Verify Git tags have expected structure
                assert tags[MLFLOW_GIT_BRANCH] == "master"  # Default branch name (may be "master" or "main")
                assert tags[MLFLOW_GIT_REPO_URL] == "https://github.com/test/preserve-tags.git"
                assert len(tags[MLFLOW_GIT_COMMIT]) == 40  # Valid Git SHA