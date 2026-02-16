#!/usr/bin/env python
"""
Test script for CDF SPARQL functions in SHACL validation.

This script tests the cdf_sdk: and cdf_indsl: SPARQL functions
used in SHACL rules against a real time series in CDF.

Usage:
    cd /path/to/thisisneat
    uv run python scripts/test_cdf_sparql_functions.py

Environment:
    Uses get.env file for CDF credentials
"""
import time

from thisisneat import NeatSession
from thisisneat.core._client import NeatClient
from thisisneat.core._utils.auth import get_cognite_client

# Test configuration
TEST_TIMESERIES_SPACE = "timeseries"
TEST_TIMESERIES_EXTERNAL_ID = "xid_volten_live_power"


def log(msg: str) -> None:
    """Print with immediate flush."""
    print(msg, flush=True)


def get_test_instances() -> list[dict]:
    """Get standard test instances."""
    return [
        {
            "externalId": TEST_TIMESERIES_EXTERNAL_ID,
            "space": TEST_TIMESERIES_SPACE,
            "properties": {
                TEST_TIMESERIES_SPACE: {
                    "CogniteTimeSeries/v1": {
                        "name": "Volten Live Power",
                    }
                }
            },
        }
    ]


def run_shacl_test(
    neat: NeatSession,
    test_name: str,
    shacl_rules: str,
    pass_msg: str = "PASSED",
    fail_msg: str = "FAILED",
) -> bool:
    """Run a single SHACL test with timing."""
    log(f"\n=== {test_name} ===")
    log("  Preparing validation...")
    
    start = time.time()
    
    conforms, _, report_text = neat.validate_instances.with_shacl(
        instances=get_test_instances(),
        shacl_rules=shacl_rules,
        datamodel_space=TEST_TIMESERIES_SPACE,
        datamodel_external_id="CogniteTimeSeries",
        datamodel_version="v1",
        auto_load_depth=0,
        verbose=False,
    )
    
    elapsed = time.time() - start
    log(f"  Completed in {elapsed:.1f}s")
    log(f"  Result: {pass_msg if conforms else fail_msg}")
    
    if not conforms:
        # Show just the message, not full report
        for line in report_text.split("\n"):
            if "Message:" in line:
                log(f"  {line.strip()}")
    
    return conforms


def test_datapoints_count(neat: NeatSession) -> bool:
    """Test cdf_sdk:datapoints_count."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "No data in last 7 days" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_sdk" ; sh:namespace "https://cognite.com/cdf/sdk/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_sdk:datapoints_count($this, \\"7d-ago\\", \\"now\\") AS ?c) FILTER(?c < 1) }}"
            ] .
    """
    return run_shacl_test(neat, "datapoints_count", shacl_rules)


def test_datapoints_average(neat: NeatSession) -> bool:
    """Test cdf_sdk:datapoints_average."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "Average not positive" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_sdk" ; sh:namespace "https://cognite.com/cdf/sdk/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_sdk:datapoints_average($this, \\"7d-ago\\", \\"now\\") AS ?a) FILTER(?a <= 0) }}"
            ] .
    """
    return run_shacl_test(neat, "datapoints_average", shacl_rules)


def test_extreme_outliers(neat: NeatSession) -> bool:
    """Test cdf_indsl:extreme_outliers with parameters."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "Has extreme outliers (alpha=0.05)" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_indsl:extreme_outliers($this, 0.05, 0.167, 3) AS ?o) FILTER(?o = true) }}"
            ] .
    """
    return run_shacl_test(neat, "extreme_outliers(alpha=0.05)", shacl_rules, 
                          "PASSED (no outliers)", "FAILED (has outliers)")


def test_gaps_identification(neat: NeatSession) -> bool:
    """Test cdf_indsl:gaps_identification with cutoff parameter."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "Has data gaps (z-score cutoff=3.0)" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_indsl:gaps_identification($this, 3.0) AS ?g) FILTER(?g = true) }}"
            ] .
    """
    return run_shacl_test(neat, "gaps_identification(cutoff=3.0)", shacl_rules,
                          "PASSED (no significant gaps)", "FAILED (gaps detected)")


def test_low_density(neat: NeatSession) -> bool:
    """Test cdf_indsl:low_density with cutoff parameter."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "Has low density periods (z-score cutoff=3.0)" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_indsl:low_density($this, 3.0) AS ?d) FILTER(?d = true) }}"
            ] .
    """
    return run_shacl_test(neat, "low_density(cutoff=3.0)", shacl_rules,
                          "PASSED (normal density)", "FAILED (low density detected)")


def test_value_decrease(neat: NeatSession) -> bool:
    """Test cdf_indsl:value_decrease_check with threshold parameter."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "Value decreases detected (threshold=100)" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_indsl:value_decrease_check($this, 100.0) AS ?d) FILTER(?d = true) }}"
            ] .
    """
    return run_shacl_test(neat, "value_decrease_check(threshold=100)", shacl_rules,
                          "PASSED (no significant decreases)", "FAILED (decreases detected)")


def test_out_of_range(neat: NeatSession) -> bool:
    """Test cdf_indsl:out_of_range (IQR-based outlier detection)."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "Has out of range values (IQR method)" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_indsl:out_of_range($this) AS ?r) FILTER(?r = true) }}"
            ] .
    """
    return run_shacl_test(neat, "out_of_range (IQR)", shacl_rules,
                          "PASSED (all in range)", "FAILED (out of range detected)")


def test_combined_report(neat: NeatSession) -> bool:
    """Test multiple functions in one SHACL rule - shows values in message."""
    shacl_rules = f"""
        @prefix sh: <http://www.w3.org/ns/shacl#> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
        @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
        @prefix cdf_indsl: <https://cognite.com/cdf/indsl/> .
        @prefix ts: <http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/> .

        ts:Shape a sh:NodeShape ;
            sh:targetClass ts:CogniteTimeSeries ;
            sh:sparql [
                a sh:SPARQLConstraint ;
                sh:message "Data Quality Report - Count: {{?count}}, Avg: {{?avg}}, Outliers: {{?outliers}}, Gaps: {{?gaps}}" ;
                sh:prefixes [
                    sh:declare [ sh:prefix "cdf_sdk" ; sh:namespace "https://cognite.com/cdf/sdk/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "cdf_indsl" ; sh:namespace "https://cognite.com/cdf/indsl/"^^xsd:anyURI ] ;
                    sh:declare [ sh:prefix "ts" ; sh:namespace "http://purl.org/cognite/{TEST_TIMESERIES_SPACE}/CogniteTimeSeries/"^^xsd:anyURI ]
                ] ;
                sh:select "SELECT $this ?count ?avg ?outliers ?gaps WHERE {{ $this a ts:CogniteTimeSeries . BIND(cdf_sdk:datapoints_count($this, \\"1d-ago\\", \\"now\\") AS ?count) BIND(cdf_sdk:datapoints_average($this, \\"1d-ago\\", \\"now\\") AS ?avg) BIND(cdf_indsl:extreme_outliers($this, 0.05, 0.167, 3) AS ?outliers) BIND(cdf_indsl:gaps_identification($this, 3.0) AS ?gaps) FILTER(?count > 0) }}"
            ] .
    """
    # This test always "fails" to show the report values
    return run_shacl_test(neat, "Combined Data Quality Report", shacl_rules,
                          "No data", "Report generated")


def main():
    """Run SHACL validation tests."""
    log("=" * 60)
    log("CDF SPARQL Functions - SHACL Validation Tests")
    log("=" * 60)
    log(f"TimeSeries: {TEST_TIMESERIES_SPACE}/{TEST_TIMESERIES_EXTERNAL_ID}")
    
    log("\n[1/9] Connecting to CDF...")
    start_total = time.time()
    client = get_cognite_client("get.env")
    
    log("[2/9] Creating NeatSession...")
    neat = NeatSession(client=NeatClient(client))
    
    results = {}
    
    # SDK tests
    log("\n--- cdf_sdk: functions ---")
    log("[3/9] Testing datapoints_count...")
    results["datapoints_count"] = test_datapoints_count(neat)
    
    log("[4/9] Testing datapoints_average...")
    results["datapoints_average"] = test_datapoints_average(neat)
    
    # INDSL tests
    log("\n--- cdf_indsl: functions (INDSL) ---")
    log("[5/9] Testing extreme_outliers...")
    results["extreme_outliers"] = test_extreme_outliers(neat)
    
    log("[6/9] Testing gaps_identification...")
    results["gaps_identification"] = test_gaps_identification(neat)
    
    log("[7/9] Testing low_density...")
    results["low_density"] = test_low_density(neat)
    
    log("[8/9] Testing value_decrease_check...")
    results["value_decrease"] = test_value_decrease(neat)
    
    log("[9/9] Testing out_of_range...")
    results["out_of_range"] = test_out_of_range(neat)
    
    # Combined report
    log("\n--- Combined Report ---")
    test_combined_report(neat)
    
    # Summary
    total_time = time.time() - start_total
    log("\n" + "=" * 60)
    log("Summary:")
    passed = sum(1 for p in results.values() if p)
    log(f"  {passed}/{len(results)} tests passed")
    for name, test_passed in results.items():
        status = "PASS" if test_passed else "FAIL"
        log(f"    [{status}] {name}")
    log(f"\nTotal time: {total_time:.1f}s")
    log("=" * 60)


if __name__ == "__main__":
    main()
