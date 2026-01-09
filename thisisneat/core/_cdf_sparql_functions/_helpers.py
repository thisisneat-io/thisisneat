"""
Utility functions for CDF SPARQL extensions.

Provides helpers for:
- Parsing instance_id (space + external_id) from URIs
- Fetching time series datapoints (normal, subscription, or backfill mode)
- Caching and error handling
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache, wraps
from typing import TYPE_CHECKING, Any

import pandas as pd
from rdflib import Literal, URIRef

if TYPE_CHECKING:
    from cognite.client import CogniteClient
    from cognite.client.data_classes.data_modeling import NodeId

logger = logging.getLogger(__name__)

# URI patterns for parsing instance_id
# Pattern 1: http://purl.org/cognite/{space}/TimeSeries/{external_id}
# Pattern 2: http://purl.org/cognite/{space}/{view}/{external_id}
# Pattern 3: cdf:{space}/{external_id}
URI_PATTERNS = [
    # http://purl.org/cognite/{space}/TimeSeries/{external_id}
    re.compile(r"http://purl\.org/cognite/([^/]+)/TimeSeries/(.+)$"),
    # http://purl.org/cognite/{space}/{view}/{external_id} (external_id is last segment)
    re.compile(r"http://purl\.org/cognite/([^/]+)/[^/]+/(.+)$"),
    # http://purl.org/cognite/{space}/{external_id}
    re.compile(r"http://purl\.org/cognite/([^/]+)/(.+)$"),
    # Generic: extract space and external_id from last two path segments
    re.compile(r".*/([^/]+)/([^/]+)$"),
]


@dataclass
class DataFetchConfig:
    """
    Configuration for data fetching mode.

    Supports three modes:
    1. Normal mode (default): Fetch data with default time range
    2. Subscription mode: Fetch from Data Point Subscription for incremental updates
    3. Backfill mode: Fetch historic data with custom time range

    Attributes:
        mode: One of "normal", "subscription", or "backfill"
        start: Start time for normal/backfill mode (default: "7d-ago")
        end: End time for normal/backfill mode (default: "now")
        limit: Max datapoints per time series (default: None = unlimited)
        subscription_external_id: External ID of Data Point Subscription (subscription mode)
        subscription_cursor: Cursor for subscription (subscription mode)
        subscription_partitions: Number of partitions to read (subscription mode, default: 1)
    """

    mode: str = "normal"  # "normal", "subscription", or "backfill"
    start: str = "7d-ago"
    end: str = "now"
    limit: int | None = None
    subscription_external_id: str | None = None
    subscription_cursor: str | None = None
    subscription_partitions: int = 1
    # Internal: stores new cursor after subscription fetch
    _new_cursor: str | None = field(default=None, repr=False)
    # Internal: cache of subscription data (external_id -> datapoints)
    _subscription_data: dict[str, pd.Series] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if self.mode == "subscription" and not self.subscription_external_id:
            raise ValueError("subscription_external_id required for subscription mode")

    @classmethod
    def normal(cls, start: str = "7d-ago", end: str = "now", limit: int | None = None) -> "DataFetchConfig":
        """Create config for normal mode with time range."""
        return cls(mode="normal", start=start, end=end, limit=limit)

    @classmethod
    def subscription(
        cls,
        external_id: str,
        cursor: str | None = None,
        partitions: int = 1,
    ) -> "DataFetchConfig":
        """Create config for subscription mode."""
        return cls(
            mode="subscription",
            subscription_external_id=external_id,
            subscription_cursor=cursor,
            subscription_partitions=partitions,
        )

    @classmethod
    def backfill(cls, start: str = "30d-ago", end: str = "now", limit: int | None = None) -> "DataFetchConfig":
        """Create config for backfill mode with custom time range."""
        return cls(mode="backfill", start=start, end=end, limit=limit)


# Global config - set by register_cdf_sparql_functions
_FETCH_CONFIG: DataFetchConfig | None = None


def set_fetch_config(config: DataFetchConfig | None) -> None:
    """Set the global data fetch configuration."""
    global _FETCH_CONFIG
    _FETCH_CONFIG = config
    if config:
        logger.info(f"Data fetch config set: mode={config.mode}")


def get_fetch_config() -> DataFetchConfig:
    """Get the current data fetch configuration (defaults to normal mode)."""
    return _FETCH_CONFIG or DataFetchConfig.normal()


def get_new_cursor() -> str | None:
    """Get the new cursor after subscription fetch (if any)."""
    config = get_fetch_config()
    return config._new_cursor if config else None


def parse_instance_id_from_uri(uri: URIRef | str) -> NodeId:
    """
    Parse instance_id (space + external_id) from a time series URI.

    Supports multiple URI formats commonly used in NEAT:
    - http://purl.org/cognite/{space}/TimeSeries/{external_id}
    - http://purl.org/cognite/{space}/{view}/{external_id}
    - http://purl.org/cognite/{space}/{external_id}

    Args:
        uri: The URI to parse (rdflib URIRef or string)

    Returns:
        NodeId with space and external_id

    Raises:
        ValueError: If URI format is not recognized

    Example:
        >>> from cognite.client.data_classes.data_modeling import NodeId
        >>> node_id = parse_instance_id_from_uri("http://purl.org/cognite/my_space/TimeSeries/ts-001")
        >>> node_id.space
        'my_space'
        >>> node_id.external_id
        'ts-001'
    """
    from cognite.client.data_classes.data_modeling import NodeId

    uri_str = str(uri)

    for pattern in URI_PATTERNS:
        match = pattern.match(uri_str)
        if match:
            space = match.group(1)
            external_id = match.group(2)
            return NodeId(space=space, external_id=external_id)

    # Fallback: try to extract from fragment or last path segment
    if "#" in uri_str:
        fragment = uri_str.split("#")[-1]
        # Assume format: space/external_id or just external_id
        if "/" in fragment:
            parts = fragment.split("/")
            return NodeId(space=parts[0], external_id=parts[1])

    raise ValueError(
        f"Could not parse instance_id from URI: {uri_str}. "
        f"Expected format: http://purl.org/cognite/{{space}}/TimeSeries/{{external_id}}"
    )


def verify_timeseries_exists(
    client: CogniteClient,
    instance_id: NodeId,
) -> bool:
    """
    Verify that an instance_id corresponds to a valid time series in CDF.

    This implements Option B from the architecture: explicit type checking
    to ensure only actual time series are validated.

    Args:
        client: CogniteClient instance
        instance_id: NodeId with space and external_id

    Returns:
        True if the instance_id corresponds to a valid time series, False otherwise

    Example:
        >>> from cognite.client.data_classes.data_modeling import NodeId
        >>> instance_id = NodeId(space="timeseries", external_id="ts-001")
        >>> verify_timeseries_exists(client, instance_id)
        True
    """
    try:
        ts = client.time_series.retrieve(instance_id=instance_id)
        return ts is not None
    except Exception as e:
        logger.debug(f"Time series verification failed for {instance_id}: {e}")
        return False


def get_timeseries_datapoints(
    client: CogniteClient,
    instance_id: NodeId,
    start: str = "30d-ago",
    end: str = "now",
    limit: int | None = None,
    verify_exists: bool = False,
) -> pd.Series:
    """
    Retrieve time series datapoints as a pandas Series.

    Uses instance_id (space + external_id) for time series identification,
    which is the recommended approach for DMS-integrated time series.

    Args:
        client: CogniteClient instance
        instance_id: NodeId with space and external_id
        start: Start time (default: 30 days ago)
        end: End time (default: now)
        limit: Maximum number of datapoints to retrieve
        verify_exists: If True, verify time series exists before fetching (default: False)

    Returns:
        pandas Series with timestamps as index and values as data.
        Returns empty Series if no data found or time series doesn't exist.

    Example:
        >>> from cognite.client.data_classes.data_modeling import NodeId
        >>> instance_id = NodeId(space="my_space", external_id="ts-001")
        >>> data = get_timeseries_datapoints(client, instance_id, start="7d-ago")
        >>> len(data)
        168  # hourly data for 7 days
    """
    try:
        # Optional: verify time series exists first
        if verify_exists and not verify_timeseries_exists(client, instance_id):
            logger.debug(f"Time series {instance_id} does not exist")
            return pd.Series(dtype=float)

        result = client.time_series.data.retrieve(
            instance_id=instance_id,
            start=start,
            end=end,
            limit=limit,
        )

        if result and len(result) > 0:
            df = result.to_pandas()
            if not df.empty:
                # Return first column as Series (there's only one time series)
                return df.iloc[:, 0]

        return pd.Series(dtype=float)

    except Exception as e:
        logger.warning(f"Could not retrieve datapoints for {instance_id}: {e}")
        return pd.Series(dtype=float)


def fetch_subscription_data(
    client: CogniteClient,
    config: DataFetchConfig,
) -> dict[str, pd.Series]:
    """
    Fetch datapoints from a Data Point Subscription.

    Retrieves all changes since the cursor and caches them by external_id.
    Updates config._new_cursor with the cursor for next iteration.

    Args:
        client: CogniteClient instance
        config: DataFetchConfig with subscription settings

    Returns:
        Dict mapping external_id to pandas Series of datapoints
    """
    if config.mode != "subscription" or not config.subscription_external_id:
        return {}

    try:
        logger.info(
            f"Fetching from subscription '{config.subscription_external_id}' "
            f"with cursor: {config.subscription_cursor[:20] if config.subscription_cursor else 'None'}..."
        )

        # Fetch subscription updates
        result = client.time_series.subscriptions.iterate_data(
            external_id=config.subscription_external_id,
            cursor=config.subscription_cursor,
            partitions=list(range(config.subscription_partitions)),
        )

        data_by_ts: dict[str, list[tuple[int, float]]] = {}
        new_cursor = config.subscription_cursor

        for batch in result:
            # Update cursor from batch
            if hasattr(batch, "cursor") and batch.cursor:
                new_cursor = batch.cursor

            # Process updates
            if hasattr(batch, "updates"):
                for update in batch.updates:
                    # Get time series identifier
                    ts_id = None
                    if hasattr(update, "instance_id") and update.instance_id:
                        ts_id = f"{update.instance_id.space}/{update.instance_id.external_id}"
                    elif hasattr(update, "external_id") and update.external_id:
                        ts_id = update.external_id

                    if ts_id and hasattr(update, "upserts"):
                        if ts_id not in data_by_ts:
                            data_by_ts[ts_id] = []

                        for dp in update.upserts:
                            if hasattr(dp, "timestamp") and hasattr(dp, "value"):
                                data_by_ts[ts_id].append((dp.timestamp, dp.value))

        # Convert to pandas Series
        result_data: dict[str, pd.Series] = {}
        for ts_id, datapoints in data_by_ts.items():
            if datapoints:
                timestamps, values = zip(*sorted(datapoints), strict=True)
                result_data[ts_id] = pd.Series(
                    values,
                    index=pd.to_datetime(timestamps, unit="ms"),
                    dtype=float,
                )

        # Store new cursor
        config._new_cursor = new_cursor
        config._subscription_data = result_data

        logger.info(f"Subscription fetch complete: {len(result_data)} time series with updates")
        return result_data

    except Exception as e:
        logger.warning(f"Failed to fetch subscription data: {e}")
        return {}


def safe_sparql_wrapper(default_value: Any = None) -> Callable:
    """
    Decorator for safe SPARQL function execution with error handling.

    Catches exceptions and returns a default value instead of propagating
    errors to the SPARQL engine. Logs errors for debugging.

    Args:
        default_value: Value to return on error. If callable, it will be called.

    Returns:
        Decorator function

    Example:
        @safe_sparql_wrapper(default_value=Literal(False))
        def my_function(uri):
            # ... implementation that might raise
            return Literal(True)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(f"SPARQL function {func.__name__} error: {e}", exc_info=True)
                if callable(default_value):
                    return default_value()
                return default_value

        return wrapper

    return decorator


def create_datapoints_fetcher(
    client: CogniteClient,
    config: DataFetchConfig | None = None,
) -> Callable[[NodeId], pd.Series]:
    """
    Create a datapoints fetcher function that respects the current fetch mode.

    In normal/backfill mode: Fetches from Time Series API with time range
    In subscription mode: Returns data from pre-fetched subscription cache

    Args:
        client: CogniteClient instance
        config: Optional DataFetchConfig (defaults to global config)

    Returns:
        Callable that takes NodeId and returns pandas Series
    """
    fetch_config = config or get_fetch_config()

    # If subscription mode, pre-fetch all subscription data
    if fetch_config.mode == "subscription":
        # Fetch subscription data if not already done
        if not fetch_config._subscription_data:
            fetch_subscription_data(client, fetch_config)

        def fetch_from_subscription(instance_id: NodeId) -> pd.Series:
            """Fetch from subscription cache."""
            # Try both formats: space/external_id and just external_id
            ts_id = f"{instance_id.space}/{instance_id.external_id}"
            if ts_id in fetch_config._subscription_data:
                return fetch_config._subscription_data[ts_id]
            if instance_id.external_id in fetch_config._subscription_data:
                return fetch_config._subscription_data[instance_id.external_id]
            # No updates for this TS in subscription - return empty
            logger.debug(f"No subscription updates for {instance_id}")
            return pd.Series(dtype=float)

        return fetch_from_subscription

    # Normal or backfill mode: use cached API fetcher
    @lru_cache(maxsize=100)
    def _cached_fetch(space: str, external_id: str) -> pd.Series:
        from cognite.client.data_classes.data_modeling import NodeId

        instance_id = NodeId(space=space, external_id=external_id)
        return get_timeseries_datapoints(
            client,
            instance_id,
            start=fetch_config.start,
            end=fetch_config.end,
            limit=fetch_config.limit,
        )

    def fetch(instance_id: NodeId) -> pd.Series:
        return _cached_fetch(instance_id.space, instance_id.external_id)

    # Clear cache method for testing
    fetch.cache_clear = _cached_fetch.cache_clear  # type: ignore[attr-defined]

    return fetch


def literal_to_python(value: Any) -> Any:
    """
    Convert rdflib Literal or URIRef to Python native type.

    SPARQL function arguments may be passed as rdflib types.
    This helper converts them to Python types for SDK/INDSL calls.

    Args:
        value: rdflib Literal, URIRef, or Python native type

    Returns:
        Python native type (str, int, float, bool)
    """
    if isinstance(value, Literal):
        return value.toPython()
    if isinstance(value, URIRef):
        return str(value)
    return value
