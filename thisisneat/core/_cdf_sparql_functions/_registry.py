"""
Registry for CDF SPARQL custom functions.

Registers cdf_sdk: and cdf_indsl: namespace functions with rdflib's
SPARQL engine for use in SHACL validation constraints.

Usage:
    from thisisneat.core._cdf_sparql_functions import register_cdf_sparql_functions

    # In validation code:
    register_cdf_sparql_functions(client, data_graph)

    # Then run pyshacl
    pyshacl.validate(...)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

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
    This wrapper catches that error and optionally replaces the function.

    Args:
        uri: The function URI
        func: The function to register

    Returns:
        True if registered successfully, False if already registered
    """
    from rdflib.plugins.sparql import CUSTOM_EVALS

    # Check if already registered and replace if so
    uri_str = str(uri)
    if uri_str in CUSTOM_EVALS:
        CUSTOM_EVALS[uri_str] = func
        return True

    # Try to register normally
    try:
        from rdflib.plugins.sparql.operators import register_custom_function

        register_custom_function(uri, func)
        return True
    except ValueError:
        # Already registered by another call, just update
        CUSTOM_EVALS[uri_str] = func
        return True


def register_cdf_sparql_functions(
    client: "CogniteClient",
    graph: Graph | None = None,
    force: bool = False,
) -> dict[str, list[str]]:
    """
    Register CDF SDK and INDSL functions with rdflib's SPARQL engine.

    This enables SHACL rules to use cdf_sdk: and cdf_indsl: prefixed
    functions in sh:sparql constraints.

    Args:
        client: CogniteClient for CDF operations (from session state)
        graph: Optional rdflib Graph to bind namespaces to
        force: Force re-registration even if already registered

    Returns:
        Dict with 'cdf_sdk' and 'cdf_indsl' keys listing registered function names

    Example:
        ```python
        from thisisneat.core._cdf_sparql_functions import register_cdf_sparql_functions

        # Register functions using session client
        registered = register_cdf_sparql_functions(neat._state.client, data_graph)

        # Now SHACL rules can use:
        # cdf_sdk:datapoints_aggregate(?this, "count", "1h", "7d-ago", "now")
        # cdf_indsl:extreme_outliers(?this)
        ```
    """
    global _REGISTERED

    if _REGISTERED and not force:
        logger.debug("CDF SPARQL functions already registered, skipping")
        return {"cdf_sdk": [], "cdf_indsl": []}

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
        logger.info(
            "INDSL functions not registered (INDSL not installed). "
            "Install with: pip install indsl"
        )

    _REGISTERED = True

    logger.info(
        f"Registered CDF SPARQL functions: "
        f"{len(registered['cdf_sdk'])} SDK, {len(registered['cdf_indsl'])} INDSL"
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

