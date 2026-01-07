"""
INDSL function wrappers for cdf_indsl: SPARQL namespace.

Provides data quality and analysis functions from Cognite's Industrial
Data Science Library (INDSL). INDSL is an optional dependency.

Functions available (when INDSL is installed):
- cdf_indsl:extreme_outliers(ts, alpha, bc_relaxation, poly_order) - Detect extreme outliers
- cdf_indsl:value_decrease_check(ts, threshold) - Check for decreasing values
- cdf_indsl:rolling_stddev_timedelta(ts, time_window_minutes, max_stddev_seconds) - Rolling stddev
- cdf_indsl:datapoint_diff(ts, time_period_hours, threshold, tolerance_hours) - Diff check
- cdf_indsl:gaps_identification(ts, cutoff) - Identify gaps in time series
- cdf_indsl:low_density(ts, cutoff) - Identify low density periods
- cdf_indsl:out_of_range(ts) - Detect out of range values using IQR method

Reference:
- INDSL Documentation: https://indsl.docs.cognite.com/
- Data Quality Examples: https://indsl.docs.cognite.com/auto_examples/data_quality/index.html
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

import pandas as pd
from rdflib import Literal

from ._helpers import (
    create_datapoints_fetcher,
    literal_to_python,
    parse_instance_id_from_uri,
    safe_sparql_wrapper,
)

if TYPE_CHECKING:
    from cognite.client import CogniteClient
    from cognite.client.data_classes.data_modeling import NodeId

logger = logging.getLogger(__name__)

# Check if INDSL is available
_INDSL_AVAILABLE = False
_INDSL_IMPORT_ERROR: str | None = None

try:
    import indsl  # noqa: F401

    _INDSL_AVAILABLE = True
except ImportError as e:
    _INDSL_IMPORT_ERROR = str(e)


def is_indsl_available() -> bool:
    """Check if INDSL is installed and available."""
    return _INDSL_AVAILABLE


def get_indsl_import_error() -> str | None:
    """Get the INDSL import error message if any."""
    return _INDSL_IMPORT_ERROR


def create_indsl_wrappers(client: "CogniteClient") -> dict[str, Callable]:
    """
    Create INDSL wrapper functions for SPARQL registration.

    All functions fetch time series data using instance_id and apply
    INDSL data quality algorithms.

    Args:
        client: CogniteClient for fetching time series data

    Returns:
        Dict mapping function names to wrapper functions.
        Empty dict if INDSL is not installed.

    Example:
        >>> wrappers = create_indsl_wrappers(client)
        >>> if wrappers:
        ...     wrappers["extreme_outliers"]("http://...uri...")
        Literal(True)
    """
    if not _INDSL_AVAILABLE:
        logger.warning(
            f"INDSL is not installed. cdf_indsl: functions will not be available. "
            f"Install with: pip install indsl. Error: {_INDSL_IMPORT_ERROR}"
        )
        return {}

    # Import INDSL modules
    from indsl.data_quality import extreme
    from indsl.data_quality.datapoint_diff import datapoint_diff_over_time_period
    from indsl.data_quality.rolling_stddev import rolling_stddev_timedelta
    from indsl.data_quality.value_decrease_indication import value_decrease_check

    # Create cached datapoints fetcher
    fetch_datapoints = create_datapoints_fetcher(client)

    wrappers: dict[str, Callable] = {}

    # 1. Extreme Outliers Detection
    # Based on: https://indsl.docs.cognite.com/auto_examples/data_quality/plot_extreme_outlier.html
    @safe_sparql_wrapper(default_value=Literal(False))
    def extreme_outliers_wrapper(
        timeseries_uri: str,
        alpha: float = 0.05,
        bc_relaxation: float = 0.167,
        poly_order: int = 3,
    ) -> Literal:
        """
        Detect extreme outliers in time series using polynomial regression
        and Studentized residuals.

        Args:
            timeseries_uri: URI containing space and external_id
            alpha: Significance level (0-1). Higher = more lenient. Default 0.05
            bc_relaxation: Bonferroni relaxation factor. Smaller = more conservative. Default 0.167
            poly_order: Polynomial order for curve fitting. Default 3

        Returns:
            True if extreme outliers are detected, False otherwise
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        alpha = float(literal_to_python(alpha))
        bc_relaxation = float(literal_to_python(bc_relaxation))
        poly_order = int(literal_to_python(poly_order))

        instance_id = parse_instance_id_from_uri(timeseries_uri)
        data = fetch_datapoints(instance_id)

        if data.empty or len(data) < 10:  # Need enough data for analysis
            return Literal(False)

        try:
            filtered = extreme(data, alpha=alpha, bc_relaxation=bc_relaxation, poly_order=poly_order)
            # If filtered data has fewer points, outliers were removed
            has_outliers = len(filtered) < len(data)
            return Literal(has_outliers)
        except Exception as e:
            logger.warning(f"extreme_outliers failed: {e}")
            return Literal(False)

    wrappers["extreme_outliers"] = extreme_outliers_wrapper

    # 2. Value Decrease Check
    # Based on: https://indsl.docs.cognite.com/auto_examples/data_quality/plot_value_decrease_check.html
    @safe_sparql_wrapper(default_value=Literal(False))
    def value_decrease_wrapper(
        timeseries_uri: str,
        threshold: float = 0.0,
    ) -> Literal:
        """
        Check for decreasing values in time series (e.g., running hours counter).

        Useful for counters that should only increase over time.
        A decrease indicates bad data quality or sensor issues.

        Args:
            timeseries_uri: URI containing space and external_id
            threshold: Minimum decrease to flag (default: 0.0 = any decrease)

        Returns:
            True if decreasing values are detected above threshold
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        threshold = float(literal_to_python(threshold))
        instance_id = parse_instance_id_from_uri(timeseries_uri)
        data = fetch_datapoints(instance_id)

        if data.empty or len(data) < 2:
            return Literal(False)

        try:
            indicator = value_decrease_check(data, threshold)
            # indicator is 1 where decrease detected, 0 otherwise
            has_decreases = indicator.sum() > 0
            return Literal(bool(has_decreases))
        except Exception as e:
            logger.warning(f"value_decrease_check failed: {e}")
            return Literal(False)

    wrappers["value_decrease_check"] = value_decrease_wrapper

    # 3. Rolling Stddev of Time Delta
    # Based on: https://indsl.docs.cognite.com/auto_examples/data_quality/plot_rolling_stddev_timedelta.html
    @safe_sparql_wrapper(default_value=Literal(False))
    def rolling_stddev_wrapper(
        timeseries_uri: str,
        time_window_minutes: int = 5,
        max_stddev_seconds: float = 60.0,
    ) -> Literal:
        """
        Check if rolling standard deviation of time delta between data points
        exceeds a threshold.

        High stddev indicates irregular data ingestion or sensor issues.

        Args:
            timeseries_uri: URI containing space and external_id
            time_window_minutes: Window size for rolling calculation (default: 5 min)
            max_stddev_seconds: Maximum allowed stddev in seconds (default: 60s)

        Returns:
            True if stddev exceeds threshold anywhere in the series
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        time_window_minutes = int(literal_to_python(time_window_minutes))
        max_stddev_seconds = float(literal_to_python(max_stddev_seconds))

        instance_id = parse_instance_id_from_uri(timeseries_uri)
        data = fetch_datapoints(instance_id)

        if data.empty or len(data) < 3:
            return Literal(False)

        try:
            time_window = pd.Timedelta(minutes=time_window_minutes)
            stddev_series = rolling_stddev_timedelta(data, time_window=time_window)

            # Check if any stddev exceeds threshold
            exceeds_threshold = (stddev_series > max_stddev_seconds).any()
            return Literal(bool(exceeds_threshold))
        except Exception as e:
            logger.warning(f"rolling_stddev_timedelta failed: {e}")
            return Literal(False)

    wrappers["rolling_stddev_timedelta"] = rolling_stddev_wrapper

    # 4. Datapoint Diff Over Time Period
    # Based on: https://indsl.docs.cognite.com/auto_examples/data_quality/plot_datapoint_diff.html
    @safe_sparql_wrapper(default_value=Literal(False))
    def datapoint_diff_wrapper(
        timeseries_uri: str,
        time_period_hours: int = 24,
        threshold: float = 24.0,
        tolerance_hours: int = 1,
    ) -> Literal:
        """
        Check if difference between datapoints over a time period exceeds threshold.

        Useful for monitoring expected changes (e.g., running hours should
        increase by ~24 in a day).

        Args:
            timeseries_uri: URI containing space and external_id
            time_period_hours: Time period for difference calculation (default: 24h)
            threshold: Maximum allowed difference (default: 24.0)
            tolerance_hours: Tolerance for time period matching (default: 1h)

        Returns:
            True if threshold is breached anywhere in the series
        """
        timeseries_uri = literal_to_python(timeseries_uri)
        time_period_hours = int(literal_to_python(time_period_hours))
        threshold = float(literal_to_python(threshold))
        tolerance_hours = int(literal_to_python(tolerance_hours))

        instance_id = parse_instance_id_from_uri(timeseries_uri)
        data = fetch_datapoints(instance_id)

        if data.empty or len(data) < 2:
            return Literal(False)

        try:
            breach_indicator = datapoint_diff_over_time_period(
                data,
                pd.Timedelta(hours=time_period_hours),
                threshold,
                pd.Timedelta(hours=tolerance_hours),
            )

            has_breach = breach_indicator.sum() > 0
            return Literal(bool(has_breach))
        except Exception as e:
            logger.warning(f"datapoint_diff_over_time_period failed: {e}")
            return Literal(False)

    wrappers["datapoint_diff"] = datapoint_diff_wrapper

    # Try to import additional data quality functions
    # These may have different import paths in different INDSL versions
    try:
        from indsl.data_quality.gaps_identification import gaps_identification_z_scores

        @safe_sparql_wrapper(default_value=Literal(False))
        def gaps_identification_wrapper(
            timeseries_uri: str,
            cutoff: float = 3.0,
        ) -> Literal:
            """
            Identify gaps in time series using z-score method.

            Gaps are detected by analyzing time deltas between consecutive
            data points and flagging those that exceed the z-score cutoff.

            Args:
                timeseries_uri: URI containing space and external_id
                cutoff: Z-score cutoff for gap detection (default: 3.0)

            Returns:
                True if gaps are detected, False otherwise
            """
            timeseries_uri = literal_to_python(timeseries_uri)
            cutoff = float(literal_to_python(cutoff))

            instance_id = parse_instance_id_from_uri(timeseries_uri)
            data = fetch_datapoints(instance_id)

            if data.empty or len(data) < 3:
                return Literal(False)

            try:
                gaps = gaps_identification_z_scores(data, cutoff=cutoff)
                has_gaps = gaps.any() if hasattr(gaps, "any") else bool(gaps.sum() > 0)
                return Literal(bool(has_gaps))
            except Exception as e:
                logger.warning(f"gaps_identification_z_scores failed: {e}")
                return Literal(False)

        wrappers["gaps_identification"] = gaps_identification_wrapper
    except ImportError:
        logger.debug("gaps_identification_z_scores not available in this INDSL version")

    try:
        from indsl.data_quality.low_density_identification import low_density_identification_z_scores

        @safe_sparql_wrapper(default_value=Literal(False))
        def low_density_wrapper(
            timeseries_uri: str,
            cutoff: float = 3.0,
        ) -> Literal:
            """
            Identify low density periods using z-score method.

            Low density periods indicate insufficient data collection.

            Args:
                timeseries_uri: URI containing space and external_id
                cutoff: Z-score cutoff for low density detection (default: 3.0)

            Returns:
                True if low density periods are detected, False otherwise
            """
            timeseries_uri = literal_to_python(timeseries_uri)
            cutoff = float(literal_to_python(cutoff))

            instance_id = parse_instance_id_from_uri(timeseries_uri)
            data = fetch_datapoints(instance_id)

            if data.empty or len(data) < 3:
                return Literal(False)

            try:
                low_density = low_density_identification_z_scores(data, cutoff=cutoff)
                has_low_density = low_density.any() if hasattr(low_density, "any") else bool(low_density.sum() > 0)
                return Literal(bool(has_low_density))
            except Exception as e:
                logger.warning(f"low_density_identification_z_scores failed: {e}")
                return Literal(False)

        wrappers["low_density"] = low_density_wrapper
    except ImportError:
        logger.debug("low_density_identification_z_scores not available in this INDSL version")

    try:
        from indsl.data_quality.out_of_range import out_of_range_iqr

        @safe_sparql_wrapper(default_value=Literal(False))
        def out_of_range_wrapper(
            timeseries_uri: str,
            min_value: float | None = None,
            max_value: float | None = None,
        ) -> Literal:
            """
            Detect out of range outliers in sensor data.

            If min/max not provided, uses IQR method to detect outliers.

            Args:
                timeseries_uri: URI containing space and external_id
                min_value: Minimum expected value (optional)
                max_value: Maximum expected value (optional)

            Returns:
                True if out of range values are detected, False otherwise
            """
            timeseries_uri = literal_to_python(timeseries_uri)

            instance_id = parse_instance_id_from_uri(timeseries_uri)
            data = fetch_datapoints(instance_id)

            if data.empty or len(data) < 3:
                return Literal(False)

            try:
                outliers = out_of_range_iqr(data)
                has_outliers = outliers.any() if hasattr(outliers, "any") else bool(outliers.sum() > 0)
                return Literal(bool(has_outliers))
            except Exception as e:
                logger.warning(f"out_of_range_iqr failed: {e}")
                return Literal(False)

        wrappers["out_of_range"] = out_of_range_wrapper
    except ImportError:
        logger.debug("out_of_range_iqr not available in this INDSL version")

    return wrappers

