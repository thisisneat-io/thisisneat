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
from collections.abc import Callable
from typing import TYPE_CHECKING

from rdflib import Literal

from ._helpers import literal_to_python, parse_instance_id_from_uri, safe_sparql_wrapper

if TYPE_CHECKING:
    from cognite.client import CogniteClient

logger = logging.getLogger(__name__)


def _parse_time_param(value: str | int) -> str | int:
    """
    Parse time parameter, converting millisecond timestamp strings to integers.

    The CDF SDK accepts:
    - Relative strings: "30d-ago", "7d-ago", "now"
    - Integer milliseconds: 1736848800000

    But NOT string milliseconds: "1736848800000"

    This function converts numeric strings to integers for SDK compatibility.
    """
    if isinstance(value, str):
        # Check if it's a numeric string (milliseconds timestamp)
        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return int(value)
    return value


def create_sdk_wrappers(client: CogniteClient) -> dict[str, Callable]:
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
        Aggregate datapoints for a time series using SDK aggregation.

        Supported aggregates: count, average, min, max, sum, interpolation,
        step_interpolation, continuous_variance, discrete_variance, total_variation.

        Args:
            timeseries_uri: URI containing space and external_id
            aggregate: Aggregation type (e.g., count, average, min, max, sum)
            granularity: Time granularity (e.g., 1h, 1d, 1w)
            start: Start time (e.g., "30d-ago", "2024-01-01")
            end: End time (e.g., "now", "2024-12-31")

        Returns:
            Sum of aggregated values across all granularity buckets as Literal
        """
        # Convert rdflib types to Python
        timeseries_uri = literal_to_python(timeseries_uri)
        aggregate = literal_to_python(aggregate)
        granularity = literal_to_python(granularity)
        start = _parse_time_param(literal_to_python(start))
        end = _parse_time_param(literal_to_python(end))

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        # Use SDK aggregation - returns Datapoints object with aggregate values
        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            aggregates=[aggregate],
            granularity=granularity,
            start=start,
            end=end,
        )

        if result is None or len(result) == 0:
            return Literal(0)

        # Sum up all aggregate values across the granularity buckets
        total = 0.0
        for datapoint in result:
            value = getattr(datapoint, aggregate, None)
            if value is not None:
                total += float(value)

        return Literal(total)

    wrappers["datapoints_aggregate"] = datapoints_aggregate

    # 2. Datapoints Count (convenience wrapper using SDK aggregation)
    @safe_sparql_wrapper(default_value=Literal(0))
    def datapoints_count(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Count datapoints in a time range using SDK aggregation.

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Total number of datapoints as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = _parse_time_param(literal_to_python(start))
        end = _parse_time_param(literal_to_python(end))

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        # Use SDK aggregation with count - use appropriate granularity based on time range
        # For short ranges (< 1 day), use hourly granularity
        granularity = "1h"
        if isinstance(start, int) and isinstance(end, int):
            range_ms = end - start
            if range_ms > 86400000:  # More than 1 day
                granularity = "1d"

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            aggregates=["count"],
            granularity=granularity,
            start=start,
            end=end,
        )

        if result is None or len(result) == 0:
            return Literal(0)

        # Sum counts across all daily buckets
        total_count = 0
        for datapoint in result:
            if datapoint.count is not None:
                total_count += datapoint.count

        return Literal(total_count)

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

    # 5. Datapoints Average (using SDK aggregation)
    @safe_sparql_wrapper(default_value=Literal(float("nan")))
    def datapoints_average(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Calculate weighted average value over time range using SDK aggregation.

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Weighted average value as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = _parse_time_param(literal_to_python(start))
        end = _parse_time_param(literal_to_python(end))

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        # Use SDK aggregation with both average and count to compute weighted average
        # Use appropriate granularity based on time range
        granularity = "1h"
        if isinstance(start, int) and isinstance(end, int):
            range_ms = end - start
            if range_ms > 86400000:  # More than 1 day
                granularity = "1d"

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            aggregates=["average", "count"],
            granularity=granularity,
            start=start,
            end=end,
        )

        if result is None or len(result) == 0:
            return Literal(float("nan"))

        # Compute weighted average across buckets
        total_weighted = 0.0
        total_count = 0
        for datapoint in result:
            if datapoint.average is not None and datapoint.count is not None:
                total_weighted += datapoint.average * datapoint.count
                total_count += datapoint.count

        if total_count > 0:
            return Literal(total_weighted / total_count)

        return Literal(float("nan"))

    wrappers["datapoints_average"] = datapoints_average

    # 6. Datapoints Min (using SDK aggregation)
    @safe_sparql_wrapper(default_value=Literal(float("nan")))
    def datapoints_min(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Get minimum value over time range using SDK aggregation.

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Minimum value as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = _parse_time_param(literal_to_python(start))
        end = _parse_time_param(literal_to_python(end))

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        # Use SDK aggregation with min - use appropriate granularity based on time range
        granularity = "1h"
        if isinstance(start, int) and isinstance(end, int):
            range_ms = end - start
            if range_ms > 86400000:  # More than 1 day
                granularity = "1d"

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            aggregates=["min"],
            granularity=granularity,
            start=start,
            end=end,
        )

        if result is None or len(result) == 0:
            return Literal(float("nan"))

        # Get minimum across all buckets
        min_value = None
        for datapoint in result:
            if datapoint.min is not None:
                if min_value is None or datapoint.min < min_value:
                    min_value = datapoint.min

        if min_value is not None:
            return Literal(float(min_value))

        return Literal(float("nan"))

    wrappers["datapoints_min"] = datapoints_min

    # 7. Datapoints Max (using SDK aggregation)
    @safe_sparql_wrapper(default_value=Literal(float("nan")))
    def datapoints_max(
        timeseries_uri: str,
        start: str = "30d-ago",
        end: str = "now",
    ) -> Literal:
        """
        Get maximum value over time range using SDK aggregation.

        Args:
            timeseries_uri: URI containing space and external_id
            start: Start time
            end: End time

        Returns:
            Maximum value as Literal
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        start = _parse_time_param(literal_to_python(start))
        end = _parse_time_param(literal_to_python(end))

        instance_id = parse_instance_id_from_uri(timeseries_uri)

        # Use SDK aggregation with max - use appropriate granularity based on time range
        granularity = "1h"
        if isinstance(start, int) and isinstance(end, int):
            range_ms = end - start
            if range_ms > 86400000:  # More than 1 day
                granularity = "1d"

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            aggregates=["max"],
            granularity=granularity,
            start=start,
            end=end,
        )

        if result is None or len(result) == 0:
            return Literal(float("nan"))

        # Get maximum across all buckets
        max_value = None
        for datapoint in result:
            if datapoint.max is not None:
                if max_value is None or datapoint.max > max_value:
                    max_value = datapoint.max

        if max_value is not None:
            return Literal(float(max_value))

        return Literal(float("nan"))

    wrappers["datapoints_max"] = datapoints_max

    return wrappers
