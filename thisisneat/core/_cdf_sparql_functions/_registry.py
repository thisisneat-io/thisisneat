"""
Registry for CDF SPARQL custom functions.

Registers cdf_sdk: and cdf_indsl: namespace functions with rdflib's
SPARQL engine for use in SHACL validation constraints.

Supports three data fetching modes:
1. Normal mode (default): Fetch data with default time range (7d-ago to now)
2. Subscription mode: Fetch from Data Point Subscription for incremental updates
3. Backfill mode: Fetch historic data with custom time range

Usage:
    from thisisneat.core._cdf_sparql_functions import register_cdf_sparql_functions

    # Normal mode (default)
    register_cdf_sparql_functions(client, data_graph)

    # Subscription mode (incremental)
    register_cdf_sparql_functions(
        client,
        data_graph,
        subscription_external_id="my_subscription",
        subscription_cursor="abc123...",
    )

    # Backfill mode (historic data)
    register_cdf_sparql_functions(
        client,
        data_graph,
        backfill_start="30d-ago",
        backfill_end="now",
    )

    # Then run pyshacl
    pyshacl.validate(...)

    # After validation, get new cursor (subscription mode)
    from thisisneat.core._cdf_sparql_functions import get_new_cursor
    new_cursor = get_new_cursor()
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from rdflib import Graph, Namespace, URIRef

if TYPE_CHECKING:
    from cognite.client import CogniteClient

logger = logging.getLogger(__name__)

# Namespace definitions
CDF_SDK_NS = Namespace("https://cognite.com/cdf/sdk/")
CDF_INDSL_NS = Namespace("https://cognite.com/cdf/indsl/")

# Track registered functions to avoid re-registration
_REGISTERED = False


def _safe_register_custom_function(uri: URIRef, func: Callable) -> bool:
    """
    Safely register a custom SPARQL function, handling already-registered cases.

    rdflib's register_custom_function raises ValueError if already registered.
    This wrapper catches that error gracefully.

    Args:
        uri: The function URI
        func: The function to register

    Returns:
        True if registered successfully, False if already registered
    """
    from rdflib.plugins.sparql.operators import register_custom_function

    try:
        register_custom_function(uri, func)
        return True
    except ValueError:
        # Already registered - that's fine, the existing registration works
        return False


def register_cdf_sparql_functions(
    client: CogniteClient,
    graph: Graph | None = None,
    force: bool = False,
    # Subscription mode parameters
    subscription_external_id: str | None = None,
    subscription_cursor: str | None = None,
    subscription_partitions: int = 1,
    # Backfill mode parameters
    backfill_start: str | None = None,
    backfill_end: str | None = None,
    # Common parameters
    limit: int | None = None,
) -> dict[str, list[str]]:
    """
    Register CDF SDK and INDSL functions with rdflib's SPARQL engine.

    This enables SHACL rules to use cdf_sdk: and cdf_indsl: prefixed
    functions in sh:sparql constraints.

    Supports three modes:

    **Normal mode (default):**
        Fetches last 7 days of data from Time Series API.
        ```python
        register_cdf_sparql_functions(client, graph)
        ```

    **Subscription mode:**
        Fetches changes from a Data Point Subscription since the cursor.
        Use for incremental validation.
        ```python
        register_cdf_sparql_functions(
            client, graph,
            subscription_external_id="my_subscription",
            subscription_cursor="abc123...",  # From previous run
        )
        # After validation:
        new_cursor = get_new_cursor()
        ```

    **Backfill mode:**
        Fetches historic data with custom time range.
        Use for initial validation of new time series.
        ```python
        register_cdf_sparql_functions(
            client, graph,
            backfill_start="30d-ago",
            backfill_end="now",
        )
        ```

    Args:
        client: CogniteClient for CDF operations (from session state)
        graph: Optional rdflib Graph to bind namespaces to
        force: Force re-registration even if already registered
        subscription_external_id: External ID of Data Point Subscription (enables subscription mode)
        subscription_cursor: Cursor from previous subscription fetch
        subscription_partitions: Number of partitions to read (default: 1)
        backfill_start: Start time for backfill mode (e.g., "30d-ago")
        backfill_end: End time for backfill mode (e.g., "now")
        limit: Maximum datapoints per time series (optional)

    Returns:
        Dict with 'cdf_sdk' and 'cdf_indsl' keys listing registered function names
    """
    global _REGISTERED

    from ._helpers import DataFetchConfig, set_fetch_config

    if _REGISTERED and not force:
        logger.debug("CDF SPARQL functions already registered, skipping")
        return {"cdf_sdk": [], "cdf_indsl": []}

    # Determine fetch mode and create config
    if subscription_external_id:
        # Subscription mode
        config = DataFetchConfig.subscription(
            external_id=subscription_external_id,
            cursor=subscription_cursor,
            partitions=subscription_partitions,
        )
        logger.info(f"CDF SPARQL functions: subscription mode (external_id={subscription_external_id})")
    elif backfill_start:
        # Backfill mode
        config = DataFetchConfig.backfill(
            start=backfill_start,
            end=backfill_end or "now",
            limit=limit,
        )
        logger.info(f"CDF SPARQL functions: backfill mode ({backfill_start} to {backfill_end or 'now'})")
    else:
        # Normal mode
        config = DataFetchConfig.normal(
            start="7d-ago",
            end="now",
            limit=limit,
        )
        logger.info("CDF SPARQL functions: normal mode (7d-ago to now)")

    # Set global config for SPARQL functions to use
    set_fetch_config(config)

    from ._indsl_wrappers import create_indsl_wrappers
    from ._sdk_wrappers import create_sdk_wrappers

    registered: dict[str, list[str]] = {"cdf_sdk": [], "cdf_indsl": []}

    # Bind namespaces to graph if provided
    if graph is not None:
        graph.bind("cdf_sdk", CDF_SDK_NS)
        graph.bind("cdf_indsl", CDF_INDSL_NS)

    # Register CDF SDK functions
    sdk_wrappers = create_sdk_wrappers(client)
    for name, func in sdk_wrappers.items():
        uri = CDF_SDK_NS[name]
        _safe_register_custom_function(uri, func)
        registered["cdf_sdk"].append(name)
        logger.debug(f"Registered SPARQL function: cdf_sdk:{name}")

    # Register INDSL functions (if available)
    indsl_wrappers = create_indsl_wrappers(client)
    for name, func in indsl_wrappers.items():
        uri = CDF_INDSL_NS[name]
        _safe_register_custom_function(uri, func)
        registered["cdf_indsl"].append(name)
        logger.debug(f"Registered SPARQL function: cdf_indsl:{name}")

    if not indsl_wrappers:
        logger.info("INDSL functions not registered (INDSL not installed). Install with: pip install indsl")

    _REGISTERED = True

    logger.info(
        f"Registered CDF SPARQL functions: {len(registered['cdf_sdk'])} SDK, {len(registered['cdf_indsl'])} INDSL"
    )

    return registered


def unregister_cdf_sparql_functions() -> None:
    """
    Unregister CDF SPARQL functions.

    Primarily useful for testing to ensure clean state between tests.
    This removes the functions from rdflib's CUSTOM_EVALS registry.
    """
    global _REGISTERED

    from rdflib.plugins.sparql import CUSTOM_EVALS

    from ._helpers import set_fetch_config

    # Get all function URIs that we registered
    sdk_functions = [
        "datapoints_aggregate",
        "datapoints_count",
        "datapoints_latest",
        "timeseries_exists",
        "datapoints_average",
        "datapoints_min",
        "datapoints_max",
    ]
    indsl_functions = [
        "extreme_outliers",
        "value_decrease_check",
        "rolling_stddev_timedelta",
        "datapoint_diff",
        "gaps_identification",
        "low_density",
        "out_of_range",
    ]

    # Remove SDK functions
    for name in sdk_functions:
        uri_str = str(CDF_SDK_NS[name])
        if uri_str in CUSTOM_EVALS:
            del CUSTOM_EVALS[uri_str]

    # Remove INDSL functions
    for name in indsl_functions:
        uri_str = str(CDF_INDSL_NS[name])
        if uri_str in CUSTOM_EVALS:
            del CUSTOM_EVALS[uri_str]

    # Clear global config
    set_fetch_config(None)

    _REGISTERED = False
    logger.debug("CDF SPARQL functions unregistered")


def get_registered_functions() -> dict[str, list[str]]:
    """
    Get list of available CDF SPARQL functions.

    Returns:
        Dict with 'cdf_sdk' and 'cdf_indsl' keys listing available function names

    Example:
        >>> funcs = get_registered_functions()
        >>> funcs['cdf_sdk']
        ['datapoints_aggregate', 'datapoints_count', 'datapoints_latest', ...]
        >>> funcs['cdf_indsl']
        ['extreme_outliers', 'value_decrease_check', ...]
    """
    from ._indsl_wrappers import is_indsl_available

    sdk_functions = [
        "datapoints_aggregate",
        "datapoints_count",
        "datapoints_latest",
        "timeseries_exists",
        "datapoints_average",
        "datapoints_min",
        "datapoints_max",
    ]

    indsl_functions = []
    if is_indsl_available():
        indsl_functions = [
            "extreme_outliers",
            "value_decrease_check",
            "rolling_stddev_timedelta",
            "datapoint_diff",
            "gaps_identification",
            "low_density",
            "out_of_range",
        ]

    return {
        "cdf_sdk": sdk_functions,
        "cdf_indsl": indsl_functions,
    }
