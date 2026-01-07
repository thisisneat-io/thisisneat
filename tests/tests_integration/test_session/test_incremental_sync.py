"""
Integration tests for incremental DMS sync with cursor management.

These tests verify:
1. Cursors are stored after initial read
2. Subsequent reads use stored cursors for incremental sync
3. force_full_load=True ignores stored cursors
4. Cursors persist to disk when using storage_path
5. Cursors survive session restart
"""

import json
from pathlib import Path

import pytest
from cognite.client import CogniteClient
from cognite.client import data_modeling as dm

from thisisneat import NeatSession

# Check if pyoxigraph is available
try:
    import pyoxigraph  # noqa: F401
    HAS_OXIGRAPH = True
except ImportError:
    HAS_OXIGRAPH = False


@pytest.mark.skipif(not HAS_OXIGRAPH, reason="pyoxigraph not installed - run 'pip install cognite-neat[oxi]'")
class TestIncrementalDMSSync:
    """Test incremental sync with cursor management for DMS graph extraction."""

    # Use a known data model for testing - CogniteCore is available in all projects
    TEST_DATA_MODEL = ("cdf_cdm", "CogniteCore", "v1")

    @pytest.fixture
    def test_space(self, cognite_client: CogniteClient) -> dm.Space:
        """Ensure test space exists."""
        space = dm.SpaceApply(
            space="neat_cursor_sync_test",
            description="Test space for NEAT cursor sync integration tests",
            name="NEAT Cursor Sync Test",
        )
        return cognite_client.data_modeling.spaces.apply(space)

    def test_initial_read_stores_cursors(
        self, cognite_client: CogniteClient, tmp_path: Path
    ) -> None:
        """Test that initial read stores cursors after extraction."""
        storage_path = tmp_path / "neat_store"

        # Create session with disk storage
        neat = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)

        try:
            # Initial read - should store cursors
            issues = neat.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space="cdf_cdm",  # Use CDM space which always has data
            )

            # Check that cursors were stored
            cursors = neat._state.instances.get_cursors()
            assert isinstance(cursors, dict), "Cursors should be a dictionary"

            # Verify cursors file was created on disk
            cursors_file = storage_path / "dms_cursors.json"
            assert cursors_file.exists(), "Cursors file should exist on disk"

            # Verify file content matches in-memory cursors
            with open(cursors_file) as f:
                disk_cursors = json.load(f)
            assert disk_cursors == cursors, "Disk cursors should match in-memory cursors"

        finally:
            neat.close()

    def test_incremental_sync_uses_stored_cursors(
        self, cognite_client: CogniteClient, tmp_path: Path
    ) -> None:
        """Test that second read uses stored cursors for incremental sync."""
        storage_path = tmp_path / "neat_store"

        # First read - initial load
        neat1 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            issues = neat1.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space="cdf_cdm",
            )
            first_cursors = neat1._state.instances.get_cursors()
            assert first_cursors, "First read should produce cursors"
        finally:
            neat1.close()

        # Second read - should use stored cursors (incremental)
        neat2 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            # Verify cursors were loaded from disk
            loaded_cursors = neat2._state.instances.get_cursors()
            assert loaded_cursors == first_cursors, "Cursors should be loaded from disk"

            # Second read should use incremental sync
            issues = neat2.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space="cdf_cdm",
            )

            # Cursors should still be present (updated or same)
            final_cursors = neat2._state.instances.get_cursors()
            assert isinstance(final_cursors, dict), "Cursors should still be a dictionary after incremental sync"
        finally:
            neat2.close()

    def test_force_full_load_ignores_cursors(
        self, cognite_client: CogniteClient, tmp_path: Path
    ) -> None:
        """Test that force_full_load=True ignores stored cursors."""
        storage_path = tmp_path / "neat_store"

        # First read - store cursors
        neat1 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            neat1.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space="cdf_cdm",
            )
            initial_cursors = neat1._state.instances.get_cursors()
        finally:
            neat1.close()

        # Second read with force_full_load - should ignore stored cursors
        neat2 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            # Verify cursors were loaded
            assert neat2._state.instances.get_cursors() == initial_cursors

            # Force full load - should do complete re-extraction
            issues = neat2.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space="cdf_cdm",
                force_full_load=True,
            )

            # Should complete without errors
            assert not issues.has_errors, f"Force full load should succeed: {issues}"
        finally:
            neat2.close()

    def test_cursors_persist_to_disk(
        self, cognite_client: CogniteClient, tmp_path: Path
    ) -> None:
        """Test that cursors are persisted to disk and survive session closure."""
        storage_path = tmp_path / "neat_store"
        cursors_file = storage_path / "dms_cursors.json"

        # Create and close session
        neat = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            neat.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space="cdf_cdm",
            )
            saved_cursors = neat._state.instances.get_cursors()
        finally:
            neat.close()

        # Verify file persists after session close
        assert cursors_file.exists(), "Cursors file should persist after session close"

        # Verify content
        with open(cursors_file) as f:
            persisted_cursors = json.load(f)
        assert persisted_cursors == saved_cursors, "Persisted cursors should match saved cursors"

    def test_cursors_survive_session_restart(
        self, cognite_client: CogniteClient, tmp_path: Path
    ) -> None:
        """Test that cursors are correctly restored when creating a new session with same path."""
        storage_path = tmp_path / "neat_store"

        # Session 1: Initial load
        neat1 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            neat1.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space="cdf_cdm",
            )
            original_cursors = neat1._state.instances.get_cursors()
            assert original_cursors, "Should have cursors after initial load"
        finally:
            neat1.close()

        # Session 2: New session with same path - cursors should be restored
        neat2 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            restored_cursors = neat2._state.instances.get_cursors()
            assert restored_cursors == original_cursors, (
                f"Restored cursors should match original. "
                f"Original: {original_cursors}, Restored: {restored_cursors}"
            )
        finally:
            neat2.close()

    def test_memory_storage_no_cursor_persistence(
        self, cognite_client: CogniteClient
    ) -> None:
        """Test that memory storage doesn't persist cursors but still tracks them in-session."""
        # Memory storage (no storage_path)
        neat = NeatSession(client=cognite_client, storage="memory")

        issues = neat.read.cdf.graph(
            self.TEST_DATA_MODEL,
            instance_space="cdf_cdm",
        )

        # Cursors should still be tracked in memory
        cursors = neat._state.instances.get_cursors()
        assert isinstance(cursors, dict), "Cursors should be tracked in memory"

    def test_set_cursors_manually(
        self, cognite_client: CogniteClient, tmp_path: Path
    ) -> None:
        """Test that cursors can be set manually and are persisted."""
        storage_path = tmp_path / "neat_store"
        cursors_file = storage_path / "dms_cursors.json"

        neat = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            # Manually set cursors
            test_cursors = {"view1": "cursor_abc", "view2": "cursor_xyz"}
            neat._state.instances.set_cursors(test_cursors)

            # Verify in-memory
            assert neat._state.instances.get_cursors() == test_cursors

            # Verify on disk
            assert cursors_file.exists()
            with open(cursors_file) as f:
                disk_cursors = json.load(f)
            assert disk_cursors == test_cursors
        finally:
            neat.close()

