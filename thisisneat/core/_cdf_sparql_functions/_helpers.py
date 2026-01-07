"""
Utility functions for CDF SPARQL extensions.

Provides helpers for:
- Parsing instance_id (space + external_id) from URIs
- Fetching time series datapoints
- Caching and error handling
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
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


def create_datapoints_fetcher(client: CogniteClient) -> Callable[[NodeId], pd.Series]:
    """
    Create a cached datapoints fetcher function.

    Returns a function that retrieves datapoints for a given instance_id,
    with LRU caching to avoid redundant API calls within a validation run.

    Args:
        client: CogniteClient instance

    Returns:
        Callable that takes NodeId and returns pandas Series
    """

    # Use a cache with reasonable size for validation runs
    @lru_cache(maxsize=100)
    def _cached_fetch(space: str, external_id: str) -> pd.Series:
        from cognite.client.data_classes.data_modeling import NodeId

        instance_id = NodeId(space=space, external_id=external_id)
        # Use 7 days and limit to 10000 datapoints for reasonable performance
        return get_timeseries_datapoints(client, instance_id, start="7d-ago", end="now", limit=10000)

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
