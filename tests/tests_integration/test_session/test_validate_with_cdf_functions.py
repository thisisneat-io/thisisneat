"""
Integration tests for SHACL validation with CDF SPARQL functions.

These tests verify:
1. Registration of cdf_sdk: and cdf_indsl: SPARQL functions
2. SHACL validation with cdf_sdk: functions (datapoints, aggregates)
3. SHACL validation with cdf_indsl: functions (data quality) when INDSL is installed
4. Error handling for missing time series
"""

import pytest
from cognite.client import CogniteClient

from thisisneat import NeatSession
from thisisneat.core._cdf_sparql_functions import (
    get_registered_functions,
    is_indsl_available,
    register_cdf_sparql_functions,
    unregister_cdf_sparql_functions,
)


class TestCdfSparqlFunctionRegistration:
    """Test CDF SPARQL function registration."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset registry state before and after each test."""
        unregister_cdf_sparql_functions()
        yield
        unregister_cdf_sparql_functions()

    def test_register_functions_with_client(self, cognite_client: CogniteClient) -> None:
        """Test that functions are registered with a real client."""
        registered = register_cdf_sparql_functions(cognite_client, force=True)

        # SDK functions should always be registered
        assert "cdf_sdk" in registered
        assert len(registered["cdf_sdk"]) >= 5
        assert "datapoints_aggregate" in registered["cdf_sdk"]
        assert "datapoints_count" in registered["cdf_sdk"]
        assert "timeseries_exists" in registered["cdf_sdk"]

    def test_indsl_functions_optional(self, cognite_client: CogniteClient) -> None:
        """Test that INDSL functions are registered only when INDSL is installed."""
        registered = register_cdf_sparql_functions(cognite_client, force=True)

        if is_indsl_available():
            assert len(registered["cdf_indsl"]) > 0
            assert "extreme_outliers" in registered["cdf_indsl"]
        else:
            assert registered["cdf_indsl"] == []


class TestShaclValidationWithSdkFunctions:
    """Test SHACL validation with cdf_sdk: SPARQL functions."""

    @pytest.fixture
    def test_space(self, cognite_client: CogniteClient) -> str:
        """Return test space name."""
        return "neat_cdf_sparql_test"

    def test_validate_with_cdf_functions_enabled(
        self, cognite_client: CogniteClient, test_space: str
    ) -> None:
        """Test that validation works with CDF functions enabled."""
        neat = NeatSession(client=cognite_client)

        # Simple instances (no actual time series needed for this test)
        instances = [
            {
                "externalId": "test-asset-001",
                "space": test_space,
                "properties": {
                    test_space: {
                        "TestAssetView/v1": {
                            "name": "Test Asset",
                        }
                    }
                },
            }
        ]

        # Simple SHACL rules (no cdf_sdk: functions, just testing enable_cdf_functions flag)
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix neat: <http://purl.org/cognite/{test_space}/TestAssetView/> .

            neat:TestAssetShape a sh:NodeShape ;
                sh:targetClass neat:TestAssetView ;
                sh:property [
                    sh:path neat:name ;
                    sh:minCount 1 ;
                    sh:datatype xsd:string ;
                ] .
        """

        # This should work with enable_cdf_functions=True
        conforms, report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            enable_cdf_functions=True,
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is True, f"Validation should pass. Report: {report_text}"

    def test_validate_with_cdf_functions_disabled(
        self, cognite_client: CogniteClient, test_space: str
    ) -> None:
        """Test that validation works with CDF functions disabled."""
        neat = NeatSession(client=cognite_client)

        instances = [
            {
                "externalId": "test-asset-002",
                "space": test_space,
                "properties": {
                    test_space: {
                        "TestAssetView/v1": {
                            "name": "Another Asset",
                        }
                    }
                },
            }
        ]

        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix neat: <http://purl.org/cognite/{test_space}/TestAssetView/> .

            neat:TestAssetShape a sh:NodeShape ;
                sh:targetClass neat:TestAssetView ;
                sh:property [
                    sh:path neat:name ;
                    sh:minCount 1 ;
                ] .
        """

        # This should work with enable_cdf_functions=False
        conforms, report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            enable_cdf_functions=False,
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is True


class TestShaclWithSparqlConstraints:
    """Test SHACL validation with sh:sparql constraints using cdf_sdk: functions."""

    @pytest.fixture
    def test_space(self, cognite_client: CogniteClient) -> str:
        """Return test space name."""
        return "neat_cdf_sparql_test"

    @pytest.mark.skip(reason="Requires actual time series in CDF - run manually")
    def test_sparql_constraint_with_timeseries_exists(
        self, cognite_client: CogniteClient, test_space: str
    ) -> None:
        """Test SHACL rule that checks if time series exists.

        This test requires an actual time series in CDF.
        Modify the external_id to match an existing time series.
        """
        neat = NeatSession(client=cognite_client)

        instances = [
            {
                "externalId": "ts-existing-001",  # Should match an existing time series
                "space": test_space,
                "properties": {
                    test_space: {
                        "TimeSeries/v1": {
                            "name": "Existing Time Series",
                        }
                    }
                },
            }
        ]

        # SHACL rule using cdf_sdk:timeseries_exists
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
            @prefix neat: <http://purl.org/cognite/{test_space}/TimeSeries/> .

            neat:TimeSeriesExistsShape a sh:NodeShape ;
                sh:targetClass neat:TimeSeries ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Time series must exist in CDF" ;
                    sh:select \"\"\"
                        SELECT ?this WHERE {{
                            ?this a neat:TimeSeries .
                            BIND(cdf_sdk:timeseries_exists(?this) AS ?exists)
                            FILTER (?exists = false)
                        }}
                    \"\"\" ;
                ] .
        """

        conforms, report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            enable_cdf_functions=True,
            auto_load_depth=0,
            verbose=True,
        )

        # If time series exists, should conform
        # If not, should fail with appropriate message
        print(f"Conforms: {conforms}")
        print(f"Report: {report_text}")

    @pytest.mark.skip(reason="Requires actual time series with data in CDF - run manually")
    def test_sparql_constraint_with_datapoints_count(
        self, cognite_client: CogniteClient, test_space: str
    ) -> None:
        """Test SHACL rule that checks datapoint count.

        This test requires an actual time series with datapoints in CDF.
        """
        neat = NeatSession(client=cognite_client)

        instances = [
            {
                "externalId": "ts-with-data-001",  # Should match TS with data
                "space": test_space,
                "properties": {
                    test_space: {
                        "TimeSeries/v1": {
                            "name": "Time Series with Data",
                        }
                    }
                },
            }
        ]

        # SHACL rule requiring at least 1 datapoint in last 30 days
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
            @prefix neat: <http://purl.org/cognite/{test_space}/TimeSeries/> .

            neat:TimeSeriesDataShape a sh:NodeShape ;
                sh:targetClass neat:TimeSeries ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Time series must have data in the last 30 days" ;
                    sh:select \"\"\"
                        SELECT ?this WHERE {{
                            ?this a neat:TimeSeries .
                            BIND(cdf_sdk:datapoints_count(?this, "30d-ago", "now") AS ?count)
                            FILTER (?count < 1)
                        }}
                    \"\"\" ;
                ] .
        """

        conforms, report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=test_space,
            datamodel_external_id="TestModel",
            datamodel_version="v1",
            enable_cdf_functions=True,
            auto_load_depth=0,
            verbose=True,
        )

        print(f"Conforms: {conforms}")
        print(f"Report: {report_text}")


class TestAvailableFunctions:
    """Test getting available CDF SPARQL functions."""

    def test_get_registered_functions_returns_sdk(self) -> None:
        """Test that get_registered_functions returns SDK functions."""
        funcs = get_registered_functions()

        assert "cdf_sdk" in funcs
        assert "datapoints_aggregate" in funcs["cdf_sdk"]
        assert "datapoints_count" in funcs["cdf_sdk"]
        assert "datapoints_latest" in funcs["cdf_sdk"]
        assert "timeseries_exists" in funcs["cdf_sdk"]
        assert "datapoints_average" in funcs["cdf_sdk"]
        assert "datapoints_min" in funcs["cdf_sdk"]
        assert "datapoints_max" in funcs["cdf_sdk"]

    def test_is_indsl_available_returns_bool(self) -> None:
        """Test that is_indsl_available returns a boolean."""
        result = is_indsl_available()
        assert isinstance(result, bool)

