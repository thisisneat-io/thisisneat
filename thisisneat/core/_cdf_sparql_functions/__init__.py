"""
CDF SPARQL Functions for SHACL Validation.

This package provides custom SPARQL functions that can be used in SHACL
validation rules to access CDF data and perform data quality analysis.

Two namespaces are supported:
- cdf_sdk: Functions wrapping the Cognite Python SDK (datapoints, aggregates, etc.)
- cdf_indsl: Functions wrapping INDSL data quality algorithms (optional)

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
    from thisisneat.core._cdf_sparql_functions import register_cdf_sparql_functions
    import pyshacl

    # Register functions before validation
    register_cdf_sparql_functions(client, data_graph)

    # Run validation
    conforms, report, text = pyshacl.validate(
        data_graph=data_graph,
        shacl_graph=shacl_graph,
    )
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
    - low_density(uri, cutoff) - Identify low density periods
    - out_of_range(uri) - Detect out of range values
"""
from ._helpers import (
    create_datapoints_fetcher,
    get_timeseries_datapoints,
    literal_to_python,
    parse_instance_id_from_uri,
    safe_sparql_wrapper,
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
    # Main registration function
    "register_cdf_sparql_functions",
    "unregister_cdf_sparql_functions",
    "get_registered_functions",
    # Namespace constants
    "CDF_SDK_NS",
    "CDF_INDSL_NS",
    # Wrapper factories
    "create_sdk_wrappers",
    "create_indsl_wrappers",
    # INDSL availability
    "is_indsl_available",
    "get_indsl_import_error",
    # Helpers
    "parse_instance_id_from_uri",
    "get_timeseries_datapoints",
    "verify_timeseries_exists",
    "create_datapoints_fetcher",
    "literal_to_python",
    "safe_sparql_wrapper",
]

