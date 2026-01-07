"""Tests for CDF SDK wrapper functions."""

from unittest.mock import MagicMock

import pytest
from rdflib import Literal

from thisisneat.core._cdf_sparql_functions._sdk_wrappers import create_sdk_wrappers


class TestCreateSdkWrappers:
    """Tests for SDK wrapper creation."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock CogniteClient."""
        client = MagicMock()
        return client

    def test_creates_all_expected_wrappers(self, mock_client):
        """Test that all expected wrapper functions are created."""
        wrappers = create_sdk_wrappers(mock_client)

        expected_functions = [
            "datapoints_aggregate",
            "datapoints_count",
            "datapoints_latest",
            "timeseries_exists",
            "datapoints_average",
            "datapoints_min",
            "datapoints_max",
        ]

        for func_name in expected_functions:
            assert func_name in wrappers, f"Missing wrapper: {func_name}"
            assert callable(wrappers[func_name])


class TestDatapointsAggregateWrapper:
    """Tests for datapoints_aggregate wrapper."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock CogniteClient with aggregate response."""
        client = MagicMock()
        return client

    def test_aggregate_returns_count(self, mock_client):
        """Test that aggregate returns count value."""
        # Mock datapoint with count attribute - retrieve returns iterable of datapoints
        mock_datapoint = MagicMock()
        mock_datapoint.count = 42

        # retrieve() with aggregates returns an iterable of datapoints
        mock_client.time_series.data.retrieve.return_value = [mock_datapoint]

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_aggregate"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
            "count",
            "1h",
            "7d-ago",
            "now",
        )

        assert result == Literal(42.0)

    def test_aggregate_with_multiple_buckets(self, mock_client):
        """Test that aggregate sums values across multiple granularity buckets."""
        # Mock multiple datapoints - retrieve() returns iterable of datapoints
        mock_dp1 = MagicMock()
        mock_dp1.count = 10
        mock_dp2 = MagicMock()
        mock_dp2.count = 15
        mock_dp3 = MagicMock()
        mock_dp3.count = 20

        # retrieve() with aggregates returns an iterable of datapoint objects
        mock_client.time_series.data.retrieve.return_value = [mock_dp1, mock_dp2, mock_dp3]

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_aggregate"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
            "count",
            "1h",
        )

        assert result == Literal(45.0)  # 10 + 15 + 20

    def test_aggregate_returns_zero_on_empty_result(self, mock_client):
        """Test that aggregate returns 0 when no data found."""
        mock_client.time_series.data.retrieve.return_value = []

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_aggregate"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
            "count",
            "1h",
        )

        assert result == Literal(0)

    def test_aggregate_handles_exception(self, mock_client):
        """Test that aggregate returns 0 on exception."""
        mock_client.time_series.data.retrieve.side_effect = Exception("API error")

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_aggregate"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
            "count",
            "1h",
        )

        assert result == Literal(0)


class TestDatapointsCountWrapper:
    """Tests for datapoints_count wrapper."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock CogniteClient with retrieve response."""
        client = MagicMock()
        return client

    def test_count_returns_datapoint_count(self, mock_client):
        """Test that count returns number of datapoints."""
        # retrieve() with aggregates=["count"] returns iterable of datapoints
        # each datapoint has a .count attribute with the count for that bucket
        mock_dp1 = MagicMock()
        mock_dp1.count = 50
        mock_dp2 = MagicMock()
        mock_dp2.count = 50

        mock_client.time_series.data.retrieve.return_value = [mock_dp1, mock_dp2]

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_count"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
            "7d-ago",
            "now",
        )

        assert result == Literal(100)  # 50 + 50

    def test_count_returns_zero_on_empty(self, mock_client):
        """Test that count returns 0 when no data found."""
        mock_client.time_series.data.retrieve.return_value = []

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_count"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
        )

        assert result == Literal(0)


class TestTimeseriesExistsWrapper:
    """Tests for timeseries_exists wrapper."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock CogniteClient."""
        client = MagicMock()
        return client

    def test_exists_returns_true_when_found(self, mock_client):
        """Test that exists returns True when time series exists."""
        mock_client.time_series.retrieve.return_value = MagicMock()

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["timeseries_exists"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
        )

        assert result == Literal(True)

    def test_exists_returns_false_when_not_found(self, mock_client):
        """Test that exists returns False when time series not found."""
        mock_client.time_series.retrieve.return_value = None

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["timeseries_exists"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
        )

        assert result == Literal(False)

    def test_exists_returns_false_on_exception(self, mock_client):
        """Test that exists returns False on exception."""
        mock_client.time_series.retrieve.side_effect = Exception("Not found")

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["timeseries_exists"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
        )

        assert result == Literal(False)


class TestDatapointsLatestWrapper:
    """Tests for datapoints_latest wrapper."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock CogniteClient."""
        client = MagicMock()
        return client

    def test_latest_returns_value(self, mock_client):
        """Test that latest returns the latest datapoint value."""
        mock_datapoint = MagicMock()
        mock_datapoint.value = 123.45

        mock_result = MagicMock()
        mock_result.datapoints = [mock_datapoint]
        mock_client.time_series.data.retrieve_latest.return_value = [mock_result]

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_latest"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
        )

        assert result == Literal(123.45)

    def test_latest_returns_nan_on_empty(self, mock_client):
        """Test that latest returns NaN when no data."""
        mock_client.time_series.data.retrieve_latest.return_value = []

        wrappers = create_sdk_wrappers(mock_client)
        result = wrappers["datapoints_latest"](
            "http://purl.org/cognite/test_space/TimeSeries/ts-001",
        )

        import math

        assert math.isnan(result.toPython())

