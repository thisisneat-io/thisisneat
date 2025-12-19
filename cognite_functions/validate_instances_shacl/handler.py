"""
Cognite Function for SHACL validation of instances with auto-loading of references.

This function validates incoming DMS instances against SHACL rules,
automatically loading referenced instances as needed based on sh:node constraints.

Can optionally post validation results to CDF Records API for data quality tracking.
"""

from typing import Any


def _load_shacl_from_file(client, external_id: str) -> str:
    """
    Load SHACL rules from a CDF File by external ID.
    
    Args:
        client: Cognite client instance
        external_id: External ID of the file containing SHACL rules
    
    Returns:
        SHACL rules as a string
    """
    file_bytes = client.files.download_bytes(external_id=external_id)
    return file_bytes.decode('utf-8')


def _post_validation_results_to_records(
    client,
    all_instances: list[dict],
    violations: list[dict],
    job_run_id: str,
    stream_id: str,
    rule_set_id: str,
    rule_set_version: str,
    namespace_base: str,
    data_domain_external_id: str = None,
    records_space: str = "dataQuality",
    records_container: str = "DataQualityValidationRecord"
) -> tuple[int, list[dict]]:
    """
    Post SHACL validation results to CDF Records API.
    
    Creates one record per validated instance:
    - Instances with violations get failedConstraints populated
    - Instances without violations get empty failedConstraints (passed)
    
    Returns:
        Tuple of (posted_count, errors)
    """
    # Group violations by focus node
    violations_by_instance = {}
    for violation in violations:
        focus_node = violation.get('focusNode', 'unknown')
        if focus_node not in violations_by_instance:
            violations_by_instance[focus_node] = []
        violations_by_instance[focus_node].append(violation)
    
    # Build a mapping of all instances to their violations (or empty list if passed)
    instances_to_post = {}
    for inst in all_instances:
        inst_space = inst.get('space', 'unknown')
        inst_external_id = inst.get('externalId', 'unknown')
        # Build the focus node URI as used in SHACL validation
        focus_node = f"{namespace_base}{inst_space}/{inst_external_id}"
        instances_to_post[focus_node] = {
            'space': inst_space,
            'externalId': inst_external_id,
            'violations': violations_by_instance.get(focus_node, [])
        }
    
    passed_count = sum(1 for i in instances_to_post.values() if not i['violations'])
    failed_count = sum(1 for i in instances_to_post.values() if i['violations'])
    
    print(f"Posting results for {len(instances_to_post)} instances to Records API...")
    print(f"  - Passed: {passed_count}")
    print(f"  - Failed: {failed_count}")
    
    # Enable alpha version for Records API
    original_headers = client.config.headers.copy()
    client.config.headers["cdf-version"] = "alpha"
    
    posted_count = 0
    errors = []
    
    try:
        for focus_node, inst_data in instances_to_post.items():
            external_id = inst_data['externalId']
            instance_space = inst_data['space']
            instance_violations = inst_data['violations']
            
            # Build failed constraints list (empty for passed instances)
            failed_constraints = []
            source_shapes = set()
            constraint_details = []
            severities = []  # Collect severities to determine worst
            
            for v in instance_violations:
                constraint_component = v.get('sourceConstraintComponent', 'Unknown')
                failed_constraints.append(constraint_component)
                
                if v.get('sourceShape'):
                    source_shapes.add(v['sourceShape'])
                
                # Include violation details (excluding fields that have their own top-level properties)
                raw_severity = v.get('resultSeverity', 'Violation')
                severity = str(raw_severity).split('#')[-1] if '#' in str(raw_severity) else str(raw_severity)
                severities.append(severity)
                
                detail = {
                    "sourceConstraintComponent": constraint_component,
                    "resultMessage": v.get('resultMessage', 'No message'),
                    "resultSeverity": severity,
                    "resultPath": v.get('resultPath'),
                    "value": v.get('value'),
                    "sourceConstraint": v.get('sourceConstraint'),
                }
                # Remove None values to keep JSON clean
                detail = {k: v for k, v in detail.items() if v is not None}
                
                constraint_details.append(detail)
            
            # Create record for this instance
            record_external_id = f"dq_{rule_set_id}_{external_id}_{job_run_id}"
            
            record = {
                "items": [{
                    "space": records_space,
                    "externalId": record_external_id,
                    "sources": [{
                        "source": {
                            "type": "container",
                            "space": records_space,
                            "externalId": records_container
                        },
                        "properties": {
                            "ruleSetId": rule_set_id,
                            "ruleSetVersion": rule_set_version,
                            "jobRunId": job_run_id,
                            "passedValidation": len(failed_constraints) == 0,  # bool: True = passed, False = failed
                            "resultSeverity": severities,  # list of text: ["Violation", "Warning", ...]
                            "failedConstraints": failed_constraints,  # Empty array = passed, non-empty = failed
                            "focusNode": focus_node,
                            "focusNodeInstance": {
                                "space": instance_space,
                                "externalId": external_id
                            },
                            "validationReport": {
                                "violationCount": len(instance_violations),
                                "violations": constraint_details,
                                "summary": f"{len(instance_violations)} violation(s)" if instance_violations else "Passed all constraints"
                            }
                        }
                    }]
                }]
            }
            
            # Add optional fields
            if source_shapes:
                record["items"][0]["sources"][0]["properties"]["sourceShape"] = list(source_shapes)
            
            if data_domain_external_id:
                record["items"][0]["sources"][0]["properties"]["dataDomainExternalId"] = data_domain_external_id
            
            # Post to Records API
            try:
                res = client.post(
                    f"/api/v1/projects/{client.config.project}/streams/{stream_id}/records",
                    json=record
                )
                posted_count += 1
                status = "FAILED" if failed_constraints else "PASSED"
                print(f"  Posted [{status}] record for {external_id}")
            except Exception as e:
                errors.append({"instance": external_id, "error": str(e)})
                print(f"  ERROR posting record for {external_id}: {e}")
    
    finally:
        # Restore original headers
        client.config.headers = original_headers
    
    print(f"\nPosted {posted_count}/{len(instances_to_post)} records")
    if errors:
        print(f"Errors: {len(errors)}")
        for err in errors[:5]:
            print(f"  - {err['instance']}: {err['error']}")
    
    return posted_count, errors


def handle(data: dict[str, Any], client) -> dict[str, Any]:
    """
    Validate instances with SHACL rules including auto-loading of referenced instances.
    
    This handler extracts instances from the nested Cognite Functions structure
    and passes a simple list to NEAT for validation.
    
    Args:
        data: Input data containing:
            - instances: Dict with instance items (e.g., {"items": {"n": [...], "e": [...]}})
            - shacl_rules: SHACL rules as Turtle string (use this OR shacl_rules_file_external_id)
            - shacl_rules_file_external_id: External ID of a CDF File containing SHACL rules
            - datamodel_space: Space of the data model
            - datamodel_external_id: External ID of the data model
            - datamodel_version: Version of the data model
            - auto_load_depth: (optional) Maximum depth for auto-loading (default: 2)
            - verbose: (optional) Print progress messages (default: True)
            - post_to_records: (optional) Post results to CDF Records API (default: False)
            - job_run_id: (optional) Unique job run ID for records
            - records_config: (optional) Config for Records API posting:
                - stream_id: Stream ID for records
                - rule_set_id: Rule set identifier
                - rule_set_version: Rule set version
                - data_domain_external_id: (optional) Data domain
                - records_space: (optional) Space for records (default: "dataQuality")
                - records_container: (optional) Container name (default: "DataQualityValidationRecord")
        client: Cognite client instance
    
    Returns:
        Dict with validation results:
            - conforms: Boolean indicating if validation passed
            - violations: List of violation details
            - report_text: Human-readable report
            - instance_count: Number of instances validated
            - records_posted: (optional) Number of records posted if post_to_records=True
    """
    from thisisneat import NeatSession
    import time
    
    print("=" * 80)
    print("SHACL VALIDATION WITH AUTO-LOADING")
    print("=" * 80)
    
    # Extract parameters
    instances_data = data.get("instances", {})
    shacl_rules = data.get("shacl_rules")
    shacl_rules_file_external_id = data.get("shacl_rules_file_external_id")
    datamodel_space = data.get("datamodel_space")
    datamodel_external_id = data.get("datamodel_external_id")
    datamodel_version = data.get("datamodel_version")
    auto_load_depth = data.get("auto_load_depth", 2)
    verbose = data.get("verbose", True)
    
    # Records API parameters
    post_to_records = data.get("post_to_records", False)
    job_run_id = data.get("job_run_id", f"validation_{int(time.time())}")
    records_config = data.get("records_config", {})
    
    # Load SHACL rules from CDF File if external ID is provided
    if shacl_rules_file_external_id and not shacl_rules:
        try:
            print(f"Loading SHACL rules from CDF File: {shacl_rules_file_external_id}")
            shacl_rules = _load_shacl_from_file(client, shacl_rules_file_external_id)
            print(f"  Loaded {len(shacl_rules)} characters from file")
        except Exception as e:
            return {
                "conforms": False,
                "error": f"Failed to load SHACL rules from file '{shacl_rules_file_external_id}': {str(e)}",
                "violations": []
            }
    
    # Validate required parameters
    if not shacl_rules:
        return {
            "conforms": False,
            "error": "Missing required parameter: shacl_rules or shacl_rules_file_external_id",
            "violations": []
        }
    
    if not datamodel_space or not datamodel_external_id or not datamodel_version:
        return {
            "conforms": False,
            "error": "Missing required parameters: datamodel_space, datamodel_external_id, datamodel_version",
            "violations": []
        }
    
    # Extract instances from the nested Cognite Functions structure
    # Input: {"items": {"n": [...], "e": [...]}}
    # Output: Simple list of instance dicts for NEAT
    items = instances_data.get("items", {})
    all_instances = []
    
    if isinstance(items, dict):
        # Collect all instances from all keys (n=nodes, e=edges, etc.)
        for key, value in items.items():
            if isinstance(value, list):
                all_instances.extend(value)
                if verbose:
                    print(f"Found {len(value)} instances in key '{key}'")
    elif isinstance(items, list):
        # Already a simple list
        all_instances = items
    
    # Filter out deleted instances (those with deletedTime set)
    # deletedTime indicates a delete operation from sync/subscriptions
    instances = []
    deleted_count = 0
    for inst in all_instances:
        if inst.get("deletedTime"):
            deleted_count += 1
        else:
            instances.append(inst)
    
    if deleted_count > 0 and verbose:
        print(f"Skipped {deleted_count} deleted instances (have deletedTime)")
    
    if not instances:
        return {
            "conforms": True,
            "message": "No instances to validate",
            "instance_count": 0,
            "violations": []
        }
    
    print(f"\nValidating {len(instances)} instances...")
    print(f"Data model: {datamodel_space}/{datamodel_external_id}/{datamodel_version}")
    print(f"Auto-load depth: {auto_load_depth}")
    
    # Initialize NEAT session
    neat = NeatSession(client, verbose=False)
    
    try:
        # Validate with auto-loading
        conforms, report_graph, report_text = neat.validate_instances.with_shacl(
            instances=instances,
            shacl_rules=shacl_rules,
            datamodel_space=datamodel_space,
            datamodel_external_id=datamodel_external_id,
            datamodel_version=datamodel_version,
            auto_load_depth=auto_load_depth,
            verbose=verbose
        )
        
        # Extract violations from report graph
        violations = []
        if report_graph:
            from rdflib import Namespace
            SH = Namespace("http://www.w3.org/ns/shacl#")
            
            for result in report_graph.subjects(predicate=None, object=SH.ValidationResult):
                violation = {}
                for pred, obj in report_graph.predicate_objects(subject=result):
                    pred_name = str(pred).split("#")[-1]
                    violation[pred_name] = str(obj)
                violations.append(violation)
        
        # Print all violations
        print("\n" + "=" * 80)
        if conforms:
            print("VALIDATION PASSED")
        else:
            print("VALIDATION FAILED")
        print("=" * 80)
        print(f"Instances validated: {len(instances)}")
        print(f"Violations found: {len(violations)}")
        
        if violations:
            print("\n--- ALL VIOLATIONS ---")
            for i, v in enumerate(violations, 1):
                print(f"\n  Violation {i}:")
                print(f"    Focus Node: {v.get('focusNode', 'N/A')}")
                print(f"    Message: {v.get('resultMessage', 'N/A')}")
                print(f"    Severity: {v.get('resultSeverity', 'N/A').split('#')[-1]}")
                print(f"    Source: {v.get('sourceConstraintComponent', 'N/A').split('#')[-1]}")
                if 'value' in v:
                    print(f"    Value: {v.get('value', 'N/A')}")
                if 'resultPath' in v:
                    print(f"    Path: {v.get('resultPath', 'N/A')}")
        
        print("=" * 80)
        
        # Build result
        result = {
            "conforms": conforms,
            "violations": violations,
            "report_text": report_text,
            "instance_count": len(instances),
        }
        
        # Post to Records API if enabled (even for 0 violations - to record that validation passed)
        if post_to_records:
            stream_id = records_config.get("stream_id")
            rule_set_id = records_config.get("rule_set_id")
            rule_set_version = records_config.get("rule_set_version")
            
            if stream_id and rule_set_id and rule_set_version:
                print("\n" + "=" * 80)
                print("POSTING TO RECORDS API")
                print("=" * 80)
                
                # Namespace base used by NEAT for URI generation
                namespace_base = "http://purl.org/cognite/"
                
                posted_count, record_errors = _post_validation_results_to_records(
                    client=client,
                    all_instances=instances,
                    violations=violations,
                    job_run_id=job_run_id,
                    stream_id=stream_id,
                    rule_set_id=rule_set_id,
                    rule_set_version=rule_set_version,
                    namespace_base=namespace_base,
                    data_domain_external_id=records_config.get("data_domain_external_id"),
                    records_space=records_config.get("records_space", "dataQuality"),
                    records_container=records_config.get("records_container", "DataQualityValidationRecord")
                )
                
                result["records_posted"] = posted_count
                result["records_errors"] = record_errors
            else:
                print("\nWARNING: post_to_records=True but missing required records_config (stream_id, rule_set_id, rule_set_version)")
        
        return result
        
    except Exception as e:
        import traceback
        error_msg = f"Validation error: {str(e)}\n{traceback.format_exc()}"
        print(f"ERROR: {error_msg}")
        return {
            "conforms": False,
            "error": error_msg,
            "violations": [],
            "instance_count": len(instances)
        }
