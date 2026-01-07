"""
Integration tests for CDF SPARQL functions in SHACL validation.

These tests verify:
1. cdf_sdk: functions (datapoints_count, datapoints_average, etc.)
2. cdf_indsl: functions (extreme_outliers, gaps_identification, etc.)

Test time series are created using cdf_cdm/CogniteTimeSeries/v1 type
with specific data patterns to test pass/fail scenarios.
"""

# ruff: noqa: E501
import datetime
import time
from collections.abc import Generator

import numpy as np
import pytest
from cognite.client import CogniteClient
from cognite.client import data_modeling as dm
from cognite.client.data_classes.data_modeling import NodeId

from thisisneat import NeatSession
from thisisneat.core._client import NeatClient

# Test space - persists between tests
TEST_SPACE = "neat_timeseries_shacl_testing"

# Core Data Model CogniteTimeSeries view
CDM_SPACE = "cdf_cdm"
CDM_TIMESERIES_VIEW = "CogniteTimeSeries"
CDM_TIMESERIES_VERSION = "v1"


class TestCDFSparqlFunctions:
    """Integration tests for CDF SPARQL functions in SHACL validation."""

    @pytest.fixture(scope="class")
    def test_space(self, cognite_client: CogniteClient) -> dm.Space:
        """Ensure test space exists (persisted between tests)."""
        space = dm.SpaceApply(
            space=TEST_SPACE,
            description="Test space for NEAT CDF SPARQL function tests",
            name="NEAT TimeSeries SHACL Testing",
        )
        return cognite_client.data_modeling.spaces.apply(space)

    @pytest.fixture(scope="class")
    def cdm_timeseries_view(self, cognite_client: CogniteClient) -> dm.ViewId:
        """Get the CogniteTimeSeries view from Core Data Model."""
        return dm.ViewId(space=CDM_SPACE, external_id=CDM_TIMESERIES_VIEW, version=CDM_TIMESERIES_VERSION)

    @pytest.fixture(scope="class")
    def test_timeseries_good(
        self, cognite_client: CogniteClient, test_space: dm.Space, cdm_timeseries_view: dm.ViewId
    ) -> Generator[NodeId, None, None]:
        """
        Create a 'good' time series with regular data (should pass most tests).
        - Regular sampling (every minute)
        - No outliers
        - No gaps
        - No value decreases
        """
        ts_external_id = f"neat_test_ts_good_{int(time.time())}"
        instance_id = NodeId(space=test_space.space, external_id=ts_external_id)

        # Create DMS instance using CogniteTimeSeries type
        node = dm.NodeApply(
            space=test_space.space,
            external_id=ts_external_id,
            sources=[
                dm.NodeOrEdgeData(
                    source=cdm_timeseries_view,
                    properties={
                        "name": "NEAT Test - Good Data",
                        "description": "Time series with good quality data for testing",
                        "type": "numeric",
                        "isStep": False,
                    },
                )
            ],
        )
        cognite_client.data_modeling.instances.apply(node)

        # Generate good data: regular intervals, stable values around 100
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        timestamps = [now - datetime.timedelta(minutes=i) for i in range(1000)]
        timestamps.reverse()  # Oldest first

        # Stable values with small noise (no outliers)
        np.random.seed(42)
        values = 100 + np.random.normal(0, 2, len(timestamps))  # Mean 100, stddev 2

        # Insert datapoints using instance_id
        datapoints = list(zip(timestamps, values, strict=True))
        cognite_client.time_series.data.insert(datapoints, instance_id=instance_id)

        # Wait for data to be available
        time.sleep(3)

        yield instance_id

        # Cleanup - delete DMS instance (time series data will be orphaned but that's ok for tests)
        try:
            cognite_client.data_modeling.instances.delete(nodes=[(test_space.space, ts_external_id)])
        except Exception:
            pass

    @pytest.fixture(scope="class")
    def test_timeseries_bad(
        self, cognite_client: CogniteClient, test_space: dm.Space, cdm_timeseries_view: dm.ViewId
    ) -> Generator[NodeId, None, None]:
        """
        Create a 'bad' time series with data quality issues (should fail tests).
        - Contains gaps (missing data periods)
        - Contains outliers
        - Contains value decreases (for counter-like data)
        """
        ts_external_id = f"neat_test_ts_bad_{int(time.time())}"
        instance_id = NodeId(space=test_space.space, external_id=ts_external_id)

        # Create DMS instance using CogniteTimeSeries type
        node = dm.NodeApply(
            space=test_space.space,
            external_id=ts_external_id,
            sources=[
                dm.NodeOrEdgeData(
                    source=cdm_timeseries_view,
                    properties={
                        "name": "NEAT Test - Bad Data",
                        "description": "Time series with quality issues for testing",
                        "type": "numeric",
                        "isStep": False,
                    },
                )
            ],
        )
        cognite_client.data_modeling.instances.apply(node)

        # Generate bad data with issues
        now = datetime.datetime.now(tz=datetime.timezone.utc)

        np.random.seed(123)
        timestamps = []
        values = []

        # Create data with gaps (irregular sampling)
        for i in range(500):
            # Normal data with some gaps (skip every ~50 points for a larger gap)
            if 100 <= i <= 150:
                # Create a gap by skipping timestamps
                continue

            timestamps.append(now - datetime.timedelta(minutes=i * 2))

            # Base value around 100 with some noise
            base_value = 100 + np.random.normal(0, 5)

            # Add outliers at specific positions
            if i in [50, 200, 300]:
                base_value = 1000  # Extreme outlier

            # Add value decreases (counter resets)
            if i > 250:
                base_value = 50 + np.random.normal(0, 5)  # Drop in values

            values.append(base_value)

        timestamps.reverse()
        values.reverse()

        # Insert datapoints using instance_id
        datapoints = list(zip(timestamps, values, strict=True))
        cognite_client.time_series.data.insert(datapoints, instance_id=instance_id)

        # Wait for data to be available
        time.sleep(3)

        yield instance_id

        # Cleanup
        try:
            cognite_client.data_modeling.instances.delete(nodes=[(test_space.space, ts_external_id)])
        except Exception:
            pass

    @pytest.fixture(scope="class")
    def test_timeseries_empty(
        self, cognite_client: CogniteClient, test_space: dm.Space, cdm_timeseries_view: dm.ViewId
    ) -> Generator[NodeId, None, None]:
        """Create an empty time series (no datapoints)."""
        ts_external_id = f"neat_test_ts_empty_{int(time.time())}"
        instance_id = NodeId(space=test_space.space, external_id=ts_external_id)

        # Create DMS instance using CogniteTimeSeries type
        node = dm.NodeApply(
            space=test_space.space,
            external_id=ts_external_id,
            sources=[
                dm.NodeOrEdgeData(
                    source=cdm_timeseries_view,
                    properties={
                        "name": "NEAT Test - Empty",
                        "description": "Time series with no data for testing",
                        "type": "numeric",
                        "isStep": False,
                    },
                )
            ],
        )
        cognite_client.data_modeling.instances.apply(node)

        yield instance_id

        # Cleanup
        try:
            cognite_client.data_modeling.instances.delete(nodes=[(test_space.space, ts_external_id)])
        except Exception:
            pass

    def _create_instances_dict(self, space: str, external_id: str) -> list[dict]:
        """Helper to create instances dict for validation."""
        return [
            {
                "externalId": external_id,
                "space": space,
                "properties": {
                    CDM_SPACE: {
                        f"{CDM_TIMESERIES_VIEW}/{CDM_TIMESERIES_VERSION}": {
                            "name": f"Test TS {external_id}",
                        }
                    }
                },
            }
        ]

    # =========================================================================
    # cdf_sdk: function tests
    # =========================================================================

    def test_datapoints_count_with_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_good: NodeId,
    ) -> None:
        """Test cdf_sdk:datapoints_count returns count > 0 for time series with data."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_good.space,
            test_timeseries_good.external_id,
        )

        # SHACL rule that fails if count is 0
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:CountShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "No datapoints found" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_sdk" ; sh:namespace "https://cognite.com/cdf/sdk/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_sdk:datapoints_count($this, \\"7d-ago\\", \\"now\\") AS ?c) FILTER(?c < 1) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is True, f"Should pass - time series has data. Report: {report_text}"

    def test_datapoints_count_empty_ts(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_empty: NodeId,
    ) -> None:
        """Test cdf_sdk:datapoints_count returns 0 for empty time series."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_empty.space,
            test_timeseries_empty.external_id,
        )

        # SHACL rule that fails if count is 0
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:CountShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "No datapoints found" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_sdk" ; sh:namespace "https://cognite.com/cdf/sdk/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_sdk:datapoints_count($this, \\"7d-ago\\", \\"now\\") AS ?c) FILTER(?c < 1) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is False, f"Should fail - time series is empty. Report: {report_text}"
        assert "No datapoints found" in report_text

    def test_datapoints_average(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_good: NodeId,
    ) -> None:
        """Test cdf_sdk:datapoints_average returns correct average."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_good.space,
            test_timeseries_good.external_id,
        )

        # SHACL rule that checks average is around 100 (our test data mean)
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:AvgShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Average should be between 90 and 110" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_sdk" ; sh:namespace "https://cognite.com/cdf/sdk/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_sdk:datapoints_average($this, \\"7d-ago\\", \\"now\\") AS ?avg) FILTER(?avg < 90 || ?avg > 110) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        assert conforms is True, f"Should pass - average should be ~100. Report: {report_text}"

    # =========================================================================
    # cdf_indsl: function tests
    # =========================================================================

    def test_extreme_outliers_good_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_good: NodeId,
    ) -> None:
        """Test cdf_indsl:extreme_outliers with lenient parameters for good data."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_good.space,
            test_timeseries_good.external_id,
        )

        # SHACL rule that fails if outliers detected
        # Using very lenient parameters (alpha=0.5, small bc_relaxation) to avoid false positives
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:OutlierShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Extreme outliers detected" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_indsl:out_of_range($this) AS ?o) FILTER(?o = true) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # Good data with stable values around 100 should pass out_of_range check
        assert conforms is True, f"Should pass - good data has no out of range values. Report: {report_text}"

    def test_extreme_outliers_bad_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_bad: NodeId,
    ) -> None:
        """Test cdf_indsl:extreme_outliers returns True for bad data with outliers."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_bad.space,
            test_timeseries_bad.external_id,
        )

        # SHACL rule that fails if outliers detected
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:OutlierShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Extreme outliers detected" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_indsl:extreme_outliers($this, 0.05, 0.167, 3) AS ?o) FILTER(?o = true) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # Bad data has outliers - should fail validation
        assert conforms is False, f"Should fail - bad data has outliers. Report: {report_text}"

    def test_gaps_identification_good_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_good: NodeId,
    ) -> None:
        """Test cdf_indsl:gaps_identification returns False for regular data."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_good.space,
            test_timeseries_good.external_id,
        )

        # SHACL rule that fails if gaps detected
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:GapsShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Data gaps detected" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_indsl:gaps_identification($this, 3.0) AS ?g) FILTER(?g = true) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # Good data with regular sampling should not have gaps
        assert conforms is True, f"Should pass - good data has no gaps. Report: {report_text}"

    def test_gaps_identification_bad_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_bad: NodeId,
    ) -> None:
        """Test cdf_indsl:gaps_identification returns True for data with gaps."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_bad.space,
            test_timeseries_bad.external_id,
        )

        # SHACL rule that fails if gaps detected
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:GapsShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Data gaps detected" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_indsl:gaps_identification($this, 3.0) AS ?g) FILTER(?g = true) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # Bad data has gaps - should fail validation
        assert conforms is False, f"Should fail - bad data has gaps. Report: {report_text}"

    def test_value_decrease_good_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_good: NodeId,
    ) -> None:
        """Test cdf_indsl:value_decrease_check for stable data."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_good.space,
            test_timeseries_good.external_id,
        )

        # Use a high threshold - good data shouldn't have big decreases
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:DecreaseShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Significant value decrease detected" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_indsl:value_decrease_check($this, 50.0) AS ?d) FILTER(?d = true) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # Good stable data shouldn't have decreases > 50
        assert conforms is True, f"Should pass - stable data. Report: {report_text}"

    def test_value_decrease_bad_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_bad: NodeId,
    ) -> None:
        """Test cdf_indsl:value_decrease_check detects decreases in bad data."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_bad.space,
            test_timeseries_bad.external_id,
        )

        # Low threshold to catch any decrease
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:DecreaseShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Value decrease detected" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_indsl:value_decrease_check($this, 10.0) AS ?d) FILTER(?d = true) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # Bad data has value drops
        assert conforms is False, f"Should fail - bad data has decreases. Report: {report_text}"

    def test_out_of_range_good_data(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_good: NodeId,
    ) -> None:
        """Test cdf_indsl:out_of_range returns False for normal data."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_good.space,
            test_timeseries_good.external_id,
        )

        # SHACL rule that fails if out of range values detected
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:RangeShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Out of range values detected" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_indsl:out_of_range($this) AS ?r) FILTER(?r = true) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # Good data should be in range
        assert conforms is True, f"Should pass - data is in range. Report: {report_text}"

    def test_combined_validation_report(
        self,
        cognite_client: CogniteClient,
        test_space: dm.Space,
        test_timeseries_good: NodeId,
    ) -> None:
        """Test combined validation showing multiple function results in message."""
        neat = NeatSession(client=NeatClient(cognite_client))

        instances = self._create_instances_dict(
            test_timeseries_good.space,
            test_timeseries_good.external_id,
        )

        # SHACL rule that binds multiple values and shows them in message
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
            @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
            @prefix ts: <http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/> .

            ts:ReportShape a sh:NodeShape ;
                sh:targetClass ts:{CDM_TIMESERIES_VIEW} ;
                sh:sparql [
                    a sh:SPARQLConstraint ;
                    sh:message "Report: count={{?count}}, avg={{?avg}}, outliers={{?outliers}}" ;
                    sh:prefixes [
                        sh:declare [ sh:prefix "cdf_sdk" ; sh:namespace "https://cognite.com/cdf/sdk/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                        sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{CDM_SPACE}/{CDM_TIMESERIES_VIEW}/"^^xsd:anyURI ]
                    ] ;
                    sh:select "SELECT $this ?count ?avg ?outliers WHERE {{ $this a ts:{CDM_TIMESERIES_VIEW} . BIND(cdf_sdk:datapoints_count($this, \\"7d-ago\\", \\"now\\") AS ?count) BIND(cdf_sdk:datapoints_average($this, \\"7d-ago\\", \\"now\\") AS ?avg) BIND(cdf_indsl:extreme_outliers($this, 0.05, 0.167, 3) AS ?outliers) FILTER(?count > 0) }}"
                ] .
        """

        conforms, _, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=CDM_SPACE,
            datamodel_external_id="CogniteCore",
            datamodel_version="v1",
            auto_load_depth=0,
            verbose=False,
        )

        # The validation "fails" because FILTER(?count > 0) matches, which generates a report
        assert conforms is False, "Should fail to generate report"
        assert "count=" in report_text.lower() or "count:" in report_text.lower()
