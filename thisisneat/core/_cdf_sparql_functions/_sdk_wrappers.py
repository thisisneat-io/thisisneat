"""
CDF SDK function wrappers for cdf_sdk: SPARQL namespace.

Uses instance_id (space + external_id) for all time series operations,
which is the recommended approach for DMS-integrated time series.

Functions available:
- cdf_sdk:datapoints_aggregate - Aggregate datapoints over time range
- cdf_sdk:datapoints_count - Count datapoints in time range
- cdf_sdk:datapoints_latest - Get latest datapoint value
- cdf_sdk:timeseries_exists - Check if time series exists

Reference:
- Cognite SDK Datapoints API: https://cognite-sdk-python.readthedocs-hosted.com/en/latest/time_series.html
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from rdflib import Literal

from ._helpers import literal_to_python, parse_instance_id_from_uri, safe_sparql_wrapper

if TYPE_CHECKING:
    from cognite.client import CogniteClient
    from cognite.client.data_classes.data_modeling import NodeId

logger = logging.getLogger(__name__)


def create_sdk_wrappers(client: "CogniteClient") -> dict[str, Callable]:
    """
    Create CDF SDK wrapper functions for SPARQL registration.

    All functions use instance_id (space + external_id) for time series
    identification, leveraging the existing session client.

    Args:
        client: CogniteClient for CDF operations (from session state)

    Returns:
        Dict mapping function names to wrapper functions.
        Keys are the local part of the SPARQL function URI.

    Example:
        >>> wrappers = create_sdk_wrappers(client)
        >>> wrappers["datapoints_aggregate"]("http://...uri...", "count", "1h")
        Literal(42)
    """
    wrappers: dict[str, Callable] = {}

    # 1. Datapoints Aggregate
    @safe_sparql_wrapper(default_value=Literal(0))
    def datapoints_aggregate(
        timeseries_uri: str,
        aggregate: str = "count",
        granularity: str = "1h",
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Aggregate datapoints for a time series using instance_id.

        Supported aggregates: count, average, min, max, sum, interpolation,
        step_interpolation, continuous_variance, discrete_variance, total_variation.

        Args:
            timeseries_uri: URI containing space and external_id
            aggregate: Aggregation type (default: count)
            granularity: Time granularity (e.g., 1h, 1d, 1w)
            start: Start time (e.g., "30d-ago", "2024-01-01")
            end: End time (e.g., "now", "2024-12-31")

        Returns:
            Sum of aggregated values as Literal (for count, this is total count)
        """
        # Convert rdflib types to Python
        timeseries_uri = literal_to_python(timeseries_uri)
        aggregate = literal_to_python(aggregate)
        granularity = literal_to_python(granularity)
        start = literal_to_python(start)
        end = literal_to_python(end)

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        result = client.time_series.data.aggregate(
            instance_id=instance_id,
            aggregates=[aggregate],
            granularity=granularity,
            start=start,
            end=end,
        )

        if result and len(result) > 0:
            # Sum up all aggregate values across the granularity buckets
            total = 0.0
            for datapoint in result[0].datapoints:
                value = getattr(datapoint, aggregate, None)
                if value is not None:
                    total += float(value)
            return Literal(total)

        return Literal(0)

    wrappers["datapoints_aggregate"] = datapoints_aggregate

    # 2. Datapoints Count (convenience wrapper)
    @safe_sparql_wrapper(default_value=Literal(0))
    def datapoints_count(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Count datapoints in a time range (simpler than aggregate).

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Number of datapoints as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = literal_to_python(start)
        end = literal_to_python(end)

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            start=start,
            end=end,
        )

        if result and len(result) > 0:
            return Literal(len(result[0]))

        return Literal(0)

    wrappers["datapoints_count"] = datapoints_count

    # 3. Datapoints Latest
    @safe_sparql_wrapper(default_value=Literal(float("nan")))
    def datapoints_latest(timeseries_uri: str) -> Literal:
        """
        Get the latest datapoint value for a time series.

        Args:
            timeseries_uri: URI containing space and external_id

        Returns:
            Latest value as Literal, or NaN if no data
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        instance_id = parse_instance_id_from_uri(timeseries_uri)

        result = client.time_series.data.retrieve_latest(
            instance_id=instance_id,
        )

        if result and len(result) > 0 and result[0].datapoints:
            latest = result[0].datapoints[-1]
            return Literal(latest.value)

        return Literal(float("nan"))

    wrappers["datapoints_latest"] = datapoints_latest

    # 4. Time Series Exists
    @safe_sparql_wrapper(default_value=Literal(False))
    def timeseries_exists(timeseries_uri: str) -> Literal:
        """
        Check if a time series exists using instance_id.

        Args:
            timeseries_uri: URI containing space and external_id

        Returns:
            True if exists, False otherwise
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        instance_id = parse_instance_id_from_uri(timeseries_uri)

        try:
            ts = client.time_series.retrieve(instance_id=instance_id)
            return Literal(ts is not None)
        except Exception:
            return Literal(False)

    wrappers["timeseries_exists"] = timeseries_exists

    # 5. Datapoints Average
    @safe_sparql_wrapper(default_value=Literal(float("nan")))
    def datapoints_average(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Calculate average value over time range.

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Average value as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = literal_to_python(start)
        end = literal_to_python(end)

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        # Get all datapoints and calculate average
        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            start=start,
            end=end,
        )

        if result and len(result) > 0 and len(result[0]) > 0:
            df = result.to_pandas()
            if not df.empty:
                return Literal(float(df.iloc[:, 0].mean()))

        return Literal(float("nan"))

    wrappers["datapoints_average"] = datapoints_average

    # 6. Datapoints Min
    @safe_sparql_wrapper(default_value=Literal(float("nan")))
    def datapoints_min(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Get minimum value over time range.

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Minimum value as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = literal_to_python(start)
        end = literal_to_python(end)

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            start=start,
            end=end,
        )

        if result and len(result) > 0 and len(result[0]) > 0:
            df = result.to_pandas()
            if not df.empty:
                return Literal(float(df.iloc[:, 0].min()))

        return Literal(float("nan"))

    wrappers["datapoints_min"] = datapoints_min

    # 7. Datapoints Max
    @safe_sparql_wrapper(default_value=Literal(float("nan")))
    def datapoints_max(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Get maximum value over time range.

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Maximum value as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = literal_to_python(start)
        end = literal_to_python(end)

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            start=start,
            end=end,
        )

        if result and len(result) > 0 and len(result[0]) > 0:
            df = result.to_pandas()
            if not df.empty:
                return Literal(float(df.iloc[:, 0].max()))

        return Literal(float("nan"))

    wrappers["datapoints_max"] = datapoints_max

    return wrappers

