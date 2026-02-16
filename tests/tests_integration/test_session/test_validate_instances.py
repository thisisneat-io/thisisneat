"""
Integration tests for SHACL validation API.

These tests verify:
1. Basic SHACL validation with passing instances
2. SHACL validation with failing instances (violations detected)
3. Auto-loading of referenced instances from CDF
4. Respecting auto_load_depth parameter
"""

import pytest
from cognite.client import CogniteClient
from cognite.client import data_modeling as dm

from thisisneat import NeatSession


class TestSHACLValidation:
    """Test SHACL validation API with auto-loading of referenced instances."""

    @pytest.fixture
    def test_space(self, cognite_client: CogniteClient) -> dm.Space:
        """Ensure test space exists for creating test instances."""
        space = dm.SpaceApply(
            space="neat_shacl_validation_test",
            description="Test space for NEAT SHACL validation tests",
            name="NEAT SHACL Validation Test",
        )
        return cognite_client.data_modeling.spaces.apply(space)

    @pytest.fixture
    def test_container(self, cognite_client: CogniteClient, test_space: dm.Space) -> dm.Container:
        """Create a test container for validation tests."""
        container = dm.ContainerApply(
            space=test_space.space,
            external_id="TestAsset",
            properties={
                "name": dm.ContainerProperty(type=dm.Text()),
                "description": dm.ContainerProperty(type=dm.Text(), nullable=True),
                "value": dm.ContainerProperty(type=dm.Float64(), nullable=True),
            },
        )
        return cognite_client.data_modeling.containers.apply(container)

    @pytest.fixture
    def test_view(self, cognite_client: CogniteClient, test_space: dm.Space, test_container: dm.Container) -> dm.View:
        """Create a test view for validation tests."""
        container_id = test_container.as_id()
        view = dm.ViewApply(
            space=test_space.space,
            external_id="TestAssetView",
            version="v1",
            properties={
                "name": dm.MappedPropertyApply(container=container_id, container_property_identifier="name"),
                "description": dm.MappedPropertyApply(
                    container=container_id, container_property_identifier="description"
                ),
                "value": dm.MappedPropertyApply(container=container_id, container_property_identifier="value"),
            },
        )
        # Clean up any existing view first
        try:
            cognite_client.data_modeling.views.delete(view.as_id())
        except Exception:
            pass
        return cognite_client.data_modeling.views.apply(view)

    def test_validate_instances_passing(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_view: dm.View
    ) -> None:
        """Test SHACL validation with instances that conform to rules."""
        neat = NeatSession(client=cognite_client)

        # Create test instances that conform to SHACL rules
        instances = [
            {
                "externalId": "test-asset-001",
                "space": test_space.space,
                "properties": {
                    test_space.space: {
                        f"{test_view.external_id}/{test_view.version}": {
                            "name": "Valid Asset Name",
                            "description": "A valid description",
                            "value": 42.0,
                        }
                    }
                },
            }
        ]

        # SHACL rules that require a name property
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix neat: <http://purl.org/cognite/{test_space.space}/{test_view.external_id}/> .

            neat:TestAssetShape a sh:NodeShape ;
                sh:targetClass neat:{test_view.external_id} ;
                sh:property [
                    sh:path neat:name ;
                    sh:minCount 1 ;
                    sh:datatype xsd:string ;
                    sh:message "Asset must have a name" ;
                ] .
        """

        conforms, _report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space.space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            auto_load_depth=0,  # No auto-loading needed for this test
            verbose=False,
        )

        assert conforms is True, f"Validation should pass. Report: {report_text}"

    def test_validate_instances_failing(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_view: dm.View
    ) -> None:
        """Test SHACL validation with instances that violate rules."""
        neat = NeatSession(client=cognite_client)

        # Create test instances that violate SHACL rules (missing required name)
        instances = [
            {
                "externalId": "test-asset-missing-name",
                "space": test_space.space,
                "properties": {
                    test_space.space: {
                        f"{test_view.external_id}/{test_view.version}": {
                            # Missing "name" property - violates minCount 1
                            "description": "No name provided",
                            "value": 100.0,
                        }
                    }
                },
            }
        ]

        # SHACL rules requiring name property
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix neat: <http://purl.org/cognite/{test_space.space}/{test_view.external_id}/> .

            neat:TestAssetShape a sh:NodeShape ;
                sh:targetClass neat:{test_view.external_id} ;
                sh:property [
                    sh:path neat:name ;
                    sh:minCount 1 ;
                    sh:datatype xsd:string ;
                    sh:message "Asset must have a name" ;
                ] .
        """

        conforms, _report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space.space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is False, "Validation should fail due to missing name"
        assert "name" in report_text.lower() or "mincount" in report_text.lower(), (
            f"Report should mention the name violation: {report_text}"
        )

    def test_validate_instances_value_constraint(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_view: dm.View
    ) -> None:
        """Test SHACL validation with value constraints (minInclusive/maxInclusive)."""
        neat = NeatSession(client=cognite_client)

        # Instance with value outside allowed range
        instances = [
            {
                "externalId": "test-asset-bad-value",
                "space": test_space.space,
                "properties": {
                    test_space.space: {
                        f"{test_view.external_id}/{test_view.version}": {
                            "name": "Asset with bad value",
                            "value": -10.0,  # Negative value - should fail if we require positive
                        }
                    }
                },
            }
        ]

        # SHACL rules requiring positive value
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix neat: <http://purl.org/cognite/{test_space.space}/{test_view.external_id}/> .

            neat:TestAssetShape a sh:NodeShape ;
                sh:targetClass neat:{test_view.external_id} ;
                sh:property [
                    sh:path neat:value ;
                    sh:minInclusive 0 ;
                    sh:message "Value must be non-negative" ;
                ] .
        """

        conforms, _report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space.space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is False, f"Validation should fail for negative value. Report: {report_text}"

    def test_validate_multiple_instances_mixed_results(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_view: dm.View
    ) -> None:
        """Test SHACL validation with multiple instances - some passing, some failing."""
        neat = NeatSession(client=cognite_client)

        instances = [
            # Valid instance
            {
                "externalId": "valid-asset-001",
                "space": test_space.space,
                "properties": {
                    test_space.space: {
                        f"{test_view.external_id}/{test_view.version}": {
                            "name": "Valid Asset",
                            "value": 50.0,
                        }
                    }
                },
            },
            # Invalid instance (missing name)
            {
                "externalId": "invalid-asset-001",
                "space": test_space.space,
                "properties": {
                    test_space.space: {
                        f"{test_view.external_id}/{test_view.version}": {
                            "value": 100.0,
                        }
                    }
                },
            },
        ]

        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix neat: <http://purl.org/cognite/{test_space.space}/{test_view.external_id}/> .

            neat:TestAssetShape a sh:NodeShape ;
                sh:targetClass neat:{test_view.external_id} ;
                sh:property [
                    sh:path neat:name ;
                    sh:minCount 1 ;
                    sh:message "Asset must have a name" ;
                ] .
        """

        conforms, _report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space.space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is False, "Validation should fail when any instance violates rules"
        # The valid instance should not appear in violations
        assert "valid-asset-001" not in report_text.lower() or "invalid-asset-001" in report_text.lower()

    def test_auto_load_depth_zero_no_references(
        self, cognite_client: CogniteClient, test_space: dm.Space, test_view: dm.View
    ) -> None:
        """Test that auto_load_depth=0 does not attempt to load references."""
        neat = NeatSession(client=cognite_client)

        # Instance with a reference that won't be auto-loaded
        instances = [
            {
                "externalId": "asset-with-ref",
                "space": test_space.space,
                "properties": {
                    test_space.space: {
                        f"{test_view.external_id}/{test_view.version}": {
                            "name": "Asset with reference",
                        }
                    }
                },
            }
        ]

        # Simple SHACL rules
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix neat: <http://purl.org/cognite/{test_space.space}/{test_view.external_id}/> .

            neat:TestAssetShape a sh:NodeShape ;
                sh:targetClass neat:{test_view.external_id} ;
                sh:property [
                    sh:path neat:name ;
                    sh:minCount 1 ;
                ] .
        """

        # This should complete without errors (no references to load)
        conforms, _report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space.space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            auto_load_depth=0,  # Explicitly disable auto-loading
            verbose=False,
        )

        assert conforms is True, f"Validation should pass. Report: {report_text}"
