"""
Integration tests for incremental DMS sync with cursor management.

These tests verify:
1. Cursors are stored after initial read
2. Subsequent reads use stored cursors for incremental sync
3. force_full_load=True ignores stored cursors
4. Cursors persist to disk when using storage_path
5. Cursors survive session restart

Test instances are created in a dedicated space to ensure data exists for sync.
"""

import json
import time
from collections.abc import Generator
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

# Test space for sync tests
TEST_SPACE = "neat_cursor_sync_test"

# Core Data Model views
CDM_SPACE = "cdf_cdm"
CDM_ASSET_VIEW = "CogniteAsset"
CDM_ASSET_VERSION = "v1"


@pytest.mark.skip(reason="Flaky when run with other tests - test isolation issue")
@pytest.mark.skipif(not HAS_OXIGRAPH, reason="pyoxigraph not installed - run 'pip install cognite-neat[oxi]'")
class TestIncrementalDMSSync:
    """Test incremental sync with cursor management for DMS graph extraction."""

    # Use CogniteCore data model
    TEST_DATA_MODEL = ("cdf_cdm", "CogniteCore", "v1")

    @pytest.fixture(scope="class")
    def test_space(self, cognite_client: CogniteClient) -> Generator[dm.Space, None, None]:
        """Create test space for sync tests."""
        space = dm.SpaceApply(
            space=TEST_SPACE,
            description="Test space for NEAT cursor sync integration tests",
            name="NEAT Cursor Sync Test",
        )
        result = cognite_client.data_modeling.spaces.apply(space)
        yield result
        # Note: We don't delete the space to avoid issues with concurrent tests

    @pytest.fixture(scope="class")
    def cdm_asset_view(self) -> dm.ViewId:
        """Get the CogniteAsset view from Core Data Model."""
        return dm.ViewId(space=CDM_SPACE, external_id=CDM_ASSET_VIEW, version=CDM_ASSET_VERSION)

    @pytest.fixture(scope="class")
    def test_assets(
        self, cognite_client: CogniteClient, test_space: dm.Space, cdm_asset_view: dm.ViewId
    ) -> Generator[list[str], None, None]:
        """
        Create test assets to ensure we have data for sync.
        Returns list of created asset external_ids.
        """
        timestamp = int(time.time())
        asset_external_ids = [
            f"neat_sync_test_asset_{timestamp}_1",
            f"neat_sync_test_asset_{timestamp}_2",
            f"neat_sync_test_asset_{timestamp}_3",
        ]

        # Create DMS instances using CogniteAsset type
        nodes = []
        for i, external_id in enumerate(asset_external_ids):
            node = dm.NodeApply(
                space=test_space.space,
                external_id=external_id,
                sources=[
                    dm.NodeOrEdgeData(
                        source=cdm_asset_view,
                        properties={
                            "name": f"NEAT Sync Test Asset {i + 1}",
                            "description": "Test asset for cursor sync tests",
                        },
                    )
                ],
            )
            nodes.append(node)

        cognite_client.data_modeling.instances.apply(nodes)

        # Wait for instances to be indexed
        time.sleep(2)

        yield asset_external_ids

        # Cleanup - delete test instances
        try:
            cognite_client.data_modeling.instances.delete(
                nodes=[(test_space.space, ext_id) for ext_id in asset_external_ids]
            )
        except Exception:
            pass

    def test_initial_read_stores_cursors(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_assets: list[str], tmp_path: Path
    ) -> None:
        """Test that initial read stores cursors after extraction."""
        storage_path = tmp_path / "neat_store"

        # Create session with disk storage
        neat = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)

        try:
            # Initial read - should store cursors
            # Note: skip_cognite_views=False is required to include CDM views
            neat.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
            )

            # Check that cursors were stored
            cursors = neat._state.instances.get_cursors()
            assert isinstance(cursors, dict), "Cursors should be a dictionary"

            # Verify cursors file was created on disk
            cursors_file = storage_path / "dms_cursors.json"
            assert cursors_file.exists(), "Cursors file should exist on disk"

            # Verify file content matches in-memory cursors
            with cursors_file.open() as f:
                disk_cursors = json.load(f)
            assert disk_cursors == cursors, "Disk cursors should match in-memory cursors"

        finally:
            neat.close()

    def test_incremental_sync_uses_stored_cursors(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_assets: list[str], tmp_path: Path
    ) -> None:
        """Test that second read uses stored cursors for incremental sync."""
        storage_path = tmp_path / "neat_store"

        # First read - initial load
        neat1 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            neat1.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
            )
            first_cursors = neat1._state.instances.get_cursors()
            assert first_cursors, f"First read should produce cursors, got: {first_cursors}"
        finally:
            neat1.close()

        # Second read - should use stored cursors (incremental)
        neat2 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            # Verify cursors were loaded from disk
            loaded_cursors = neat2._state.instances.get_cursors()
            assert loaded_cursors == first_cursors, "Cursors should be loaded from disk"

            # Second read should use incremental sync
            neat2.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
            )

            # Cursors should still be present (updated or same)
            final_cursors = neat2._state.instances.get_cursors()
            assert isinstance(final_cursors, dict), "Cursors should still be a dictionary after incremental sync"
        finally:
            neat2.close()

    def test_force_full_load_ignores_cursors(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_assets: list[str], tmp_path: Path
    ) -> None:
        """Test that force_full_load=True ignores stored cursors and does a complete re-extraction."""
        storage_path1 = tmp_path / "neat_store1"
        storage_path2 = tmp_path / "neat_store2"

        # First read - store cursors
        neat1 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path1)
        try:
            neat1.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
            )
            initial_cursors = neat1._state.instances.get_cursors()
            assert initial_cursors, "Should have cursors after first read"
        finally:
            neat1.close()

        # Copy cursors file to second storage path (simulating existing cursors)
        storage_path2.mkdir(parents=True, exist_ok=True)
        cursors_file1 = storage_path1 / "dms_cursors.json"
        cursors_file2 = storage_path2 / "dms_cursors.json"
        if cursors_file1.exists():
            import shutil

            shutil.copy(cursors_file1, cursors_file2)

        # Second read with force_full_load - should ignore cursors and do complete extraction
        neat2 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path2)
        try:
            # Verify cursors were loaded from disk
            loaded_cursors = neat2._state.instances.get_cursors()
            assert loaded_cursors == initial_cursors, "Cursors should be loaded from disk"

            # Force full load - should ignore cursors and do complete re-extraction
            issues = neat2.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
                force_full_load=True,
            )

            # Should complete without errors
            assert issues is not None, "Should return issues list"
            assert not issues.has_errors, f"Force full load should succeed: {issues}"

            # Cursors should be updated after force_full_load
            new_cursors = neat2._state.instances.get_cursors()
            assert new_cursors, "Should have cursors after force full load"
        finally:
            neat2.close()

    def test_cursors_persist_to_disk(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_assets: list[str], tmp_path: Path
    ) -> None:
        """Test that cursors are persisted to disk and survive session closure."""
        storage_path = tmp_path / "neat_store"
        cursors_file = storage_path / "dms_cursors.json"

        # Create and close session
        neat = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            neat.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
            )
            saved_cursors = neat._state.instances.get_cursors()
        finally:
            neat.close()

        # Verify file persists after session close
        assert cursors_file.exists(), "Cursors file should persist after session close"

        # Verify content
        with cursors_file.open() as f:
            persisted_cursors = json.load(f)
        assert persisted_cursors == saved_cursors, "Persisted cursors should match saved cursors"

    def test_cursors_survive_session_restart(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_assets: list[str], tmp_path: Path
    ) -> None:
        """Test that cursors are correctly restored when creating a new session with same path."""
        storage_path = tmp_path / "neat_store"

        # Session 1: Initial load
        neat1 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            neat1.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
            )
            original_cursors = neat1._state.instances.get_cursors()
            assert original_cursors, f"Should have cursors after initial load, got: {original_cursors}"
        finally:
            neat1.close()

        # Session 2: New session with same path - cursors should be restored
        neat2 = NeatSession(client=cognite_client, storage="oxigraph", storage_path=storage_path)
        try:
            restored_cursors = neat2._state.instances.get_cursors()
            assert restored_cursors == original_cursors, (
                f"Restored cursors should match original. Original: {original_cursors}, Restored: {restored_cursors}"
            )
        finally:
            neat2.close()

    def test_memory_storage_tracks_cursors(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_assets: list[str]
    ) -> None:
        """Test that memory storage tracks cursors in-session (but doesn't persist)."""
        # Memory storage (no storage_path)
        neat = NeatSession(client=cognite_client, storage="memory")

        try:
            neat.read.cdf.graph(
                self.TEST_DATA_MODEL,
                instance_space=test_space.space,
                skip_cognite_views=False,
            )

            # Cursors should still be tracked in memory
            cursors = neat._state.instances.get_cursors()
            assert isinstance(cursors, dict), "Cursors should be tracked in memory"
        finally:
            neat.close()

    def test_set_cursors_manually(self, cognite_client: CogniteClient, test_space: dm.Space, tmp_path: Path) -> None:
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
            with cursors_file.open() as f:
                disk_cursors = json.load(f)
            assert disk_cursors == test_cursors
        finally:
            neat.close()
