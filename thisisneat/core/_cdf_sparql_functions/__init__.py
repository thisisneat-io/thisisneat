"""
CDF SPARQL Functions for SHACL Validation.

This package provides custom SPARQL functions that can be used in SHACL
validation rules to access CDF data and perform data quality analysis.

Two namespaces are supported:
- cdf_sdk: Functions wrapping the Cognite Python SDK (datapoints, aggregates, etc.)
- cdf_indsl: Functions wrapping INDSL data quality algorithms (optional)

Three data fetching modes are supported:
1. Normal mode (default): Fetch data with default time range (7d-ago to now)
2. Subscription mode: Fetch from Data Point Subscription for incremental updates
3. Backfill mode: Fetch historic data with custom time range

Usage in SHACL:
    ```turtle
    @prefix sh: <http://www.w3.org/ns/shacl#> .
    @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
    @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .

    ex:TimeSeriesShape a sh:NodeShape ;
        sh:targetClass ex:TimeSeries ;
        sh:sparql [
            sh:message "Time series must have data in last 7 days" ;
            sh:select \"\"\"
                SELECT ?this WHERE {
                    ?this a ex:TimeSeries .
                    BIND(cdf_sdk:datapoints_count(?this, "7d-ago", "now") AS ?count)
                    FILTER (?count < 1)
                }
            \"\"\" ;
        ] .
    ```

Usage in Python:
    ```python
    from thisisneat.core._cdf_sparql_functions import (
        register_cdf_sparql_functions,
        get_new_cursor,
    )
    import pyshacl

    # Normal mode (default)
    register_cdf_sparql_functions(client, data_graph)

    # Subscription mode (incremental)
    register_cdf_sparql_functions(
        client, data_graph,
        subscription_external_id="my_subscription",
        subscription_cursor="abc123...",  # From previous run
    )

    # Backfill mode (historic data)
    register_cdf_sparql_functions(
        client, data_graph,
        backfill_start="30d-ago",
        backfill_end="now",
    )

    # Run validation
    conforms, report, text = pyshacl.validate(
        data_graph=data_graph,
        shacl_graph=shacl_graph,
    )

    # Get new cursor after subscription mode validation
    new_cursor = get_new_cursor()
    ```

Available Functions:

cdf_sdk: (always available)
    - datapoints_aggregate(uri, aggregate, granularity, start, end)
    - datapoints_count(uri, start, end)
    - datapoints_latest(uri)
    - datapoints_average(uri, start, end)
    - datapoints_min(uri, start, end)
    - datapoints_max(uri, start, end)
    - timeseries_exists(uri)

cdf_indsl: (requires INDSL: pip install indsl)
    - extreme_outliers(uri) - Detect extreme outliers
    - value_decrease_check(uri, threshold) - Check for decreasing values
    - rolling_stddev_timedelta(uri, window_min, max_stddev) - Check ingestion regularity
    - datapoint_diff(uri, period_h, threshold, tolerance_h) - Threshold breach check
    - gaps_identification(uri, cutoff) - Identify gaps in time series
    - low_density(uri) - Identify low density periods
    - out_of_range(uri) - Detect out of range values
"""

from ._helpers import (
    DataFetchConfig,
    create_datapoints_fetcher,
    get_fetch_config,
    get_new_cursor,
    get_timeseries_datapoints,
    literal_to_python,
    parse_instance_id_from_uri,
    safe_sparql_wrapper,
    set_fetch_config,
    verify_timeseries_exists,
)
from ._indsl_wrappers import (
    create_indsl_wrappers,
    get_indsl_import_error,
    is_indsl_available,
)
from ._registry import (
    CDF_INDSL_NS,
    CDF_SDK_NS,
    get_registered_functions,
    register_cdf_sparql_functions,
    unregister_cdf_sparql_functions,
)
from ._sdk_wrappers import create_sdk_wrappers

__all__ = [
    "CDF_INDSL_NS",
    "CDF_SDK_NS",
    "DataFetchConfig",
    "create_datapoints_fetcher",
    "create_indsl_wrappers",
    "create_sdk_wrappers",
    "get_fetch_config",
    "get_indsl_import_error",
    "get_new_cursor",
    "get_registered_functions",
    "get_timeseries_datapoints",
    "is_indsl_available",
    "literal_to_python",
    "parse_instance_id_from_uri",
    "register_cdf_sparql_functions",
    "safe_sparql_wrapper",
    "set_fetch_config",
    "unregister_cdf_sparql_functions",
    "verify_timeseries_exists",
]
