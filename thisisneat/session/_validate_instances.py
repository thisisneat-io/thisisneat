"""
Validate instances with SHACL including auto-loading of referenced instances.

Supports custom SPARQL functions for CDF SDK and INDSL data quality checks:
- cdf_sdk: namespace for Cognite SDK functions (datapoints, aggregates)
- cdf_indsl: namespace for INDSL data quality functions (optional)

Reference:
- INDSL Documentation: https://indsl.docs.cognite.com/
- Data Quality Examples: https://indsl.docs.cognite.com/auto_examples/data_quality/index.html
"""

import logging
from collections.abc import Iterator
from typing import Any

from cognite.client import data_modeling as dm
from rdflib import Graph, Namespace
from rdflib.namespace import RDF, SH

from thisisneat.core._cdf_sparql_functions import (
    get_new_cursor,
    register_cdf_sparql_functions,
)
from thisisneat.core._client import NeatClient
from thisisneat.core._instances.extractors._raw import RAWExtractor

from ._state import SessionState
from .exceptions import NeatSessionError, session_class_wrapper

logger = logging.getLogger(__name__)


class SchemaIssue:
    """Represents a data model schema inconsistency detected during validation."""

    def __init__(
        self,
        issue_type: str,
        severity: str,
        message: str,
        view_id: str | None = None,
        property_name: str | None = None,
        source_view_id: str | None = None,
        error_code: int | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.issue_type = issue_type  # e.g., "missing_property", "type_mismatch", "permission_denied"
        self.severity = severity  # "Warning", "Error", "Info"
        self.message = message
        self.view_id = view_id  # e.g., "sp_rmdm_dm/FailureNotification/v8.11"
        self.property_name = property_name  # e.g., "failureMode"
        self.source_view_id = source_view_id  # e.g., "sp_rmdm_dm/FailureMode/v8.11"
        self.error_code = error_code  # CDF API error code if applicable
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "message": self.message,
            "view_id": self.view_id,
            "property_name": self.property_name,
            "source_view_id": self.source_view_id,
            "error_code": self.error_code,
            "details": self.details,
        }


class SHACLValidationResult:
    """Result from SHACL validation. Backwards compatible - unpacks as 3 values.

    Can be used as:
        # Old style (3 values) - BACKWARDS COMPATIBLE
        conforms, report_graph, report_text = result

        # Access new cursor via attribute
        new_cursor = result.new_cursor

        # Named attributes
        result.conforms
        result.report_graph
        result.report_text
        result.new_cursor
        result.schema_issues  # NEW: List of SchemaIssue objects
    """

    def __init__(
        self,
        conforms: bool,
        report_graph: str,
        report_text: str,
        new_cursor: str | None = None,
        schema_issues: list[SchemaIssue] | None = None,
    ):
        self.conforms = conforms
        self.report_graph = report_graph
        self.report_text = report_text
        self.new_cursor = new_cursor
        self.schema_issues = schema_issues or []

    def __iter__(self) -> Iterator:
        """Allow unpacking as 3-tuple for backwards compatibility."""
        return iter((self.conforms, self.report_graph, self.report_text))

    def __len__(self) -> int:
        """Support len() - returns 3 for backwards compatible unpacking."""
        return 3

    def __getitem__(self, index: int) -> Any:
        """Support indexing (0-2 for tuple, new_cursor via attribute)."""
        return (self.conforms, self.report_graph, self.report_text)[index]


@session_class_wrapper
class ValidateInstancesAPI:
    """API for validating instances with SHACL rules including auto-loading."""

    def __init__(self, state: SessionState) -> None:
        self._state = state

    def with_shacl(
        self,
        instances: list[dict[str, Any]],
        shacl_rules: str,
        datamodel_space: str,
        datamodel_external_id: str,
        datamodel_version: str,
        auto_load_depth: int = 2,
        max_auto_load_instances: int = 10000,
        max_reverse_relations_per_query: int = 1000,
        verbose: bool = True,
        default_view_space: str | None = None,
        default_view_name: str | None = None,
        # Data fetching mode parameters (for CDF SPARQL functions)
        subscription_external_id: str | None = None,
        subscription_cursor: str | None = None,
        subscription_partitions: int = 1,
        backfill_start: str | None = None,
        backfill_end: str | None = None,
    ) -> SHACLValidationResult:
        """
        Validate instances with SHACL rules, automatically loading referenced instances.

        Supports custom SPARQL functions for accessing CDF data and performing
        data quality analysis directly from SHACL rules:

        - cdf_sdk: namespace - Cognite SDK functions (datapoints, aggregates)
        - cdf_indsl: namespace - INDSL data quality functions (requires INDSL)

        Args:
            instances: Simple list of DMS instance dicts, e.g.:
                [
                    {
                        "externalId": "asset-001",
                        "space": "my_space",
                        "properties": {...}
                    },
                    ...
                ]
                For instances without properties, provide default_view_space and
                default_view_name to ensure type triples are created.
            shacl_rules: SHACL rules as Turtle string
            datamodel_space: Space of the data model
            datamodel_external_id: External ID of the data model
            datamodel_version: Version of the data model
            auto_load_depth: Maximum depth for auto-loading referenced instances (default: 2)
            max_auto_load_instances: Maximum total instances to auto-load across all depths (default: 10000).
                Prevents excessive memory usage and API calls. Set to -1 for unlimited.
            max_reverse_relations_per_query: Maximum reverse relation instances to load per query (default: 1000).
                Prevents unbounded queries on highly connected entities. Set to -1 for unlimited (not recommended).
            verbose: Print progress messages (default: True)
            default_view_space: Default view space for instances without properties
            default_view_name: Default view name for instances without properties
            subscription_external_id: External ID of Data Point Subscription for incremental mode
            subscription_cursor: Cursor from previous subscription fetch
            subscription_partitions: Number of subscription partitions to read (default: 1)
            backfill_start: Start time for backfill mode (e.g., "30d-ago")
            backfill_end: End time for backfill mode (e.g., "now")

        Returns:
            SHACLValidationResult - backwards compatible, unpacks as 3 values:
                conforms, report_graph, report_text = result
                new_cursor = result.new_cursor  # Access cursor via attribute
            - conforms: True if all instances pass validation
            - report_graph: SHACL validation report as Turtle string
            - report_text: Human-readable validation report
            - new_cursor: New subscription cursor (if subscription mode) or None

        Example:
            ```python
            neat = NeatSession(client)

            # Simple list of instance dicts
            instances = [
                {
                    "externalId": "asset-001",
                    "space": "my_space",
                    "properties": {
                        "my_space": {
                            "AssetView/v1": {
                                "name": "Asset Name"
                            }
                        }
                    }
                }
            ]

            # SHACL rules with CDF SPARQL functions
            shacl_rules = '''
                @prefix sh: <http://www.w3.org/ns/shacl#> .
                @prefix cdf_sdk: <https://cognite.com/cdf/sdk/> .
                @prefix ex: <http://example.org/> .

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
            '''

            conforms, report_graph, report_text = neat.validate_instances.with_shacl(
                instances=instances,
                shacl_rules=shacl_rules,
                datamodel_space="my_space",
                datamodel_external_id="MyModel",
                datamodel_version="v1"
            )
            ```

        Available SPARQL Functions:

        cdf_sdk: (always available)
            - datapoints_aggregate(uri, aggregate, granularity, start, end)
            - datapoints_count(uri, start, end)
            - datapoints_latest(uri)
            - datapoints_average(uri, start, end)
            - datapoints_min(uri, start, end)
            - datapoints_max(uri, start, end)
            - timeseries_exists(uri)

        cdf_indsl: (requires INDSL: pip install indsl)
            - extreme_outliers(uri)
            - value_decrease_check(uri, threshold)
            - rolling_stddev_timedelta(uri, window_min, max_stddev)
            - datapoint_diff(uri, period_h, threshold, tolerance_h)
            - gaps_identification(uri, cutoff)
            - low_density(uri, cutoff)
            - out_of_range(uri)
        """
        self._state._raise_exception_if_condition_not_met("Validate instances with SHACL", client_required=True)

        client = self._state.client
        if not isinstance(client, NeatClient):
            raise NeatSessionError("Client must be a NeatClient")

        if verbose:
            print(f"Validating {len(instances)} instances with SHACL...")

        # 1. Parse SHACL rules
        shacl_graph = Graph()
        shacl_graph.parse(data=shacl_rules, format="turtle")

        if verbose:
            print(f"  Parsed {len(shacl_graph)} SHACL triples")

        # 2. Analyze SHACL to find sh:node references
        reference_map = self._analyze_shacl_references(shacl_graph, verbose=verbose)

        # 3. Convert instances to RDF
        data_graph = Graph()
        namespace = Namespace(f"http://purl.org/cognite/{datamodel_space}/{datamodel_external_id}/")
        data_graph.bind(datamodel_space, namespace)

        for instance in instances:
            self._add_instance_to_graph(
                data_graph, instance, namespace, datamodel_space, default_view_space, default_view_name
            )

        if verbose:
            print(f"  Converted instances to {len(data_graph)} RDF triples")
            print("\n  --- INPUT INSTANCES ---")
            for instance in instances:
                ext_id = instance.get("externalId", "unknown")
                print(f"    {ext_id}:")
                for _space_key, views in instance.get("properties", {}).items():
                    for view_key, props in views.items():
                        print(f"      View: {view_key}")
                        for prop_name, prop_value in props.items():
                            val_str = str(prop_value)
                            if len(val_str) > 100:
                                val_str = val_str[:100] + "..."
                            print(f"        {prop_name}: {val_str}")

        # 4. Auto-load referenced instances if needed
        schema_issues: list[SchemaIssue] = []
        if reference_map and auto_load_depth > 0:
            loaded_count, schema_issues = self._auto_load_references(
                data_graph,
                instances,
                reference_map,
                client,
                datamodel_space,
                datamodel_external_id,
                datamodel_version,
                namespace,
                max_depth=auto_load_depth,
                max_instances=max_auto_load_instances,
                max_reverse_relations_per_query=max_reverse_relations_per_query,
                verbose=verbose,
            )
            if verbose:
                print(f"  Auto-loaded {loaded_count} referenced instances")
                if schema_issues:
                    print(f"  Found {len(schema_issues)} schema inconsistencies")

        # 5. Register CDF SPARQL functions (always enabled)
        if verbose:
            mode = "subscription" if subscription_external_id else "backfill" if backfill_start else "normal"
            print(f"  Registering CDF SPARQL functions (mode: {mode})...")

        registered = register_cdf_sparql_functions(
            client,
            data_graph,
            subscription_external_id=subscription_external_id,
            subscription_cursor=subscription_cursor,
            subscription_partitions=subscription_partitions,
            backfill_start=backfill_start,
            backfill_end=backfill_end,
            force=True,  # Always re-register to ensure fresh state with new time window
        )

        if verbose:
            sdk_funcs = registered.get("cdf_sdk", [])
            indsl_funcs = registered.get("cdf_indsl", [])
            print(f"    cdf_sdk: {len(sdk_funcs)} functions ({', '.join(sdk_funcs[:3])}...)")
            if indsl_funcs:
                print(f"    cdf_indsl: {len(indsl_funcs)} functions ({', '.join(indsl_funcs[:3])}...)")
            else:
                print("    cdf_indsl: not available (install INDSL: pip install indsl)")

        # 6. Validate with pyshacl
        if verbose:
            print("  Running SHACL validation...")

        import pyshacl

        conforms, report_graph, report_text = pyshacl.validate(
            data_graph=data_graph,
            shacl_graph=shacl_graph,
            inference="none",
            abort_on_first=False,
            debug=False,
        )

        if verbose:
            print(f"  Validation {'PASSED' if conforms else 'FAILED'}")

        # Get new cursor if subscription mode was used
        new_cursor = get_new_cursor()
        if verbose and new_cursor:
            print(f"  New subscription cursor available (length: {len(new_cursor)})")

        report_str = report_graph.decode("utf-8") if isinstance(report_graph, bytes) else report_graph
        return SHACLValidationResult(conforms, report_str, report_text, new_cursor, schema_issues)

    def with_shacl_raw(
        self,
        db_name: str,
        table_name: str,
        shacl_rules: str,
        table_type: str | None = None,
        foreign_keys: list[str] | None = None,
        verbose: bool = True,
        # Processing modes (mutually exclusive per CDF API)
        cursor: str | None = None,
        min_last_updated_time: int | None = None,
        max_last_updated_time: int | None = None,
        limit: int | None = None,
        # Advanced options
        unpack_json: bool = False,
        str_to_ideal_type: bool = False,
    ) -> SHACLValidationResult:
        """
        Validate RAW table rows against SHACL rules.

        This method converts RAW table rows to RDF triples and validates them using SHACL.
        Supports two processing modes:
        - **Cursor mode**: For partitioned historic processing (getCursors API)
        - **Timestamp mode**: For incremental validation (minLastUpdatedTime parameter)

        Note: Cursor and timestamp modes are mutually exclusive per CDF API.
        When cursor is specified, timestamp parameters are ignored.

        Args:
            db_name: RAW database name
            table_name: RAW table name
            shacl_rules: SHACL rules as Turtle string
            table_type: Custom type URI suffix (defaults to table_name)
            foreign_keys: Column names that represent foreign keys to other RAW rows
            verbose: Print progress messages (default: True)
            cursor: Cursor for partitioned historic processing (from getCursors API)
            min_last_updated_time: Only validate rows updated after this timestamp in milliseconds (incremental mode)
            max_last_updated_time: Only validate rows updated before this timestamp in milliseconds (incremental mode)
            limit: Maximum rows to validate
            unpack_json: Parse JSON strings in columns (default: False)
            str_to_ideal_type: Convert string values to appropriate types (default: False)

        Returns:
            SHACLValidationResult - backwards compatible, unpacks as 3 values:
                conforms, report_graph, report_text = result
            - conforms: True if all rows pass validation
            - report_graph: SHACL validation report as Turtle string
            - report_text: Human-readable validation report

        Example:
            # Incremental mode (validate rows updated since last run)
            result = neat.validate_instances.with_shacl_raw(
                db_name="iot",
                table_name="sensors",
                shacl_rules=shacl_rules_turtle,
                min_last_updated_time=last_timestamp,
                limit=10000,
            )

            # Historic/cursor mode (validate partition of historic data)
            result = neat.validate_instances.with_shacl_raw(
                db_name="iot",
                table_name="sensors",
                shacl_rules=shacl_rules_turtle,
                cursor=cursor_from_getCursors_api,
            )
        """
        if not self._state.client:
            raise NeatSessionError("Cannot validate RAW: no CogniteClient configured")

        # 1. Extract RAW rows to RDF graph
        if verbose:
            mode = "cursor" if cursor else "timestamp" if min_last_updated_time else "full"
            print(f"  Extracting RAW rows from {db_name}.{table_name} (mode: {mode})...")

        from thisisneat.core._constants import get_raw_namespace

        extractor = RAWExtractor(
            client=self._state.client,
            db_name=db_name,
            table_name=table_name,
            table_type=table_type,
            foreign_keys=foreign_keys,
            namespace=Namespace(get_raw_namespace(db_name, table_name)),
            cursor=cursor,
            min_last_updated_time=min_last_updated_time,
            max_last_updated_time=max_last_updated_time,
            limit=limit,
            unpack_json=unpack_json,
            str_to_ideal_type=str_to_ideal_type,
        )

        data_graph = Graph()
        for triple in extractor.extract():
            data_graph.add(triple)

        # Count unique subjects (rows) in the graph
        row_count = len(set(s for s, _, _ in data_graph))

        if verbose:
            print(f"  Loaded {len(data_graph)} triples from {row_count} rows")

        # 2. Parse SHACL rules
        if verbose:
            print("  Parsing SHACL rules...")

        shacl_graph = Graph()
        shacl_graph.parse(data=shacl_rules, format="turtle")

        # 3. Validate with pyshacl
        if verbose:
            print("  Running SHACL validation...")

        import pyshacl

        conforms, report_graph, report_text = pyshacl.validate(
            data_graph=data_graph,
            shacl_graph=shacl_graph,
            inference="none",
            abort_on_first=False,
            debug=False,
        )

        if verbose:
            print(f"  Validation {'PASSED' if conforms else 'FAILED'}")

        report_str = report_graph.decode("utf-8") if isinstance(report_graph, bytes) else report_graph
        return SHACLValidationResult(conforms, report_str, report_text)

    def generate_shacl_template_for_raw(
        self,
        db_name: str,
        table_name: str,
        sample_size: int = 1000,
        required_columns: list[str] | None = None,
        verbose: bool = True,
    ) -> str:
        """
        Generate SHACL template by analyzing RAW table schema.

        Samples first N rows to discover:
        - Column names (all columns found in sample)
        - Column types (inferred from values)
        - Required columns (specified or inferred from presence)

        This provides a starting point for SHACL rules that can be customized with
        additional constraints like value ranges, patterns, or cross-column validations.

        Args:
            db_name: RAW database name
            table_name: RAW table name
            sample_size: Number of rows to analyze for schema discovery (default: 1000)
            required_columns: Columns that must be present (if None, all found columns are optional)
            verbose: Print progress messages (default: True)

        Returns:
            SHACL rules as Turtle string with column presence/type constraints

        Example:
            neat = NeatSession(client)
            shacl_template = neat.validate_instances.generate_shacl_template_for_raw(
                db_name="iot",
                table_name="sensors",
                sample_size=1000,
                required_columns=["device_id", "timestamp"],
                verbose=True,
            )

            # Save to file for editing
            with open("iot_sensors_shacl_template.ttl", "w") as f:
                f.write(shacl_template)
        """
        if not self._state.client:
            raise NeatSessionError("Cannot generate SHACL template: no CogniteClient configured")

        # 1. Fetch sample rows
        if verbose:
            print(f"  Fetching {sample_size} sample rows from {db_name}.{table_name}...")

        try:
            rows = list(
                self._state.client.raw.rows.list(
                    db_name=db_name,
                    table_name=table_name,
                    limit=sample_size,
                )
            )
        except AttributeError:
            # Fallback to iterator if list() not available
            rows = []
            for row in self._state.client.raw.rows(db_name, table_name, limit=sample_size):
                rows.append(row)
                if len(rows) >= sample_size:
                    break

        if not rows:
            raise NeatSessionError(f"No rows found in {db_name}.{table_name}")

        if verbose:
            print(f"  Analyzing schema from {len(rows)} rows...")

        # 2. Analyze schema: collect all column names and infer types
        schema = _analyze_raw_schema(rows)

        if verbose:
            print(f"  Found {len(schema)} columns")
            for col_name, col_schema in list(schema.items())[:5]:
                presence = (col_schema["present_in"] / len(rows)) * 100
                print(f"    - {col_name}: {col_schema['type']} (present in {presence:.1f}% of rows)")
            if len(schema) > 5:
                print(f"    ... and {len(schema) - 5} more")

        # 3. Generate SHACL rules
        if verbose:
            print("  Generating SHACL template...")

        shacl_template = _generate_shacl_from_schema(
            db_name=db_name,
            table_name=table_name,
            schema=schema,
            required_columns=required_columns,
        )

        if verbose:
            print("  SHACL template generated successfully!")

        return shacl_template

    def _analyze_shacl_references(self, shacl_graph: Graph, verbose: bool = False) -> dict[str, list[dict]]:
        """
        Analyze SHACL rules to find sh:node references that require auto-loading.

        Returns:
            Dict mapping target class URIs to list of reference info dicts
        """
        reference_map = {}

        # Find all node shapes
        for shape in shacl_graph.subjects(predicate=RDF.type, object=SH.NodeShape):
            # Get target class
            target_class = shacl_graph.value(shape, SH.targetClass)
            if not target_class:
                continue

            references = []

            # Find property shapes with sh:node constraints
            for prop_shape in shacl_graph.objects(shape, SH.property):
                path = shacl_graph.value(prop_shape, SH.path)
                referenced_shape = shacl_graph.value(prop_shape, SH.node)

                if referenced_shape:
                    # Find the target class of the referenced shape
                    ref_target_class = shacl_graph.value(referenced_shape, SH.targetClass)

                    if ref_target_class:
                        references.append(
                            {
                                "property_path": str(path),
                                "target_shape": str(referenced_shape),
                                "target_class": str(ref_target_class),
                            }
                        )

            if references:
                reference_map[str(target_class)] = references
                if verbose:
                    print(f"  Found {len(references)} reference(s) for {target_class}")

        return reference_map

    def _add_instance_to_graph(
        self,
        graph: Graph,
        instance: dict[str, Any],
        default_namespace: Namespace,
        datamodel_space: str,
        default_view_space: str | None = None,
        default_view_name: str | None = None,
    ) -> None:
        """Convert a DMS instance dict to RDF triples.

        URI scheme:
        - Subject: Based on instance space + externalId (so references match)
        - Type: Based on view space + view name
        - Predicates: Based on view space + view name
        - Reference objects: Based on reference space + externalId

        If the instance has no properties but default_view_space and default_view_name
        are provided, a type triple will still be added using those defaults.
        """
        external_id = instance.get("externalId")
        if not external_id:
            return

        # Get instance space
        instance_space = instance.get("space", datamodel_space)

        # Create subject URI based on space + externalId
        instance_ns = Namespace(f"http://purl.org/cognite/{instance_space}/")
        subject = instance_ns[external_id]

        # Add type and properties based on each view
        properties = instance.get("properties", {})

        # If no properties but default view provided, add type triple
        if not properties and default_view_space and default_view_name:
            view_ns = Namespace(f"http://purl.org/cognite/{default_view_space}/{default_view_name}/")
            graph.add((subject, RDF.type, view_ns[default_view_name]))
            return

        for view_space, views in properties.items():
            for view_version, props in views.items():
                # Extract view name from "ViewName/version" format
                view_name = view_version.split("/")[0] if "/" in view_version else view_version

                # Create namespace for this view (for type and predicates)
                view_ns = Namespace(f"http://purl.org/cognite/{view_space}/{view_name}/")

                # Add type
                graph.add((subject, RDF.type, view_ns[view_name]))

                # Add properties
                for prop_name, prop_value in props.items():
                    predicate = view_ns[prop_name]

                    # Handle different value types
                    if isinstance(prop_value, dict) and "externalId" in prop_value:
                        # Direct relation - use referenced instance's space for the object URI
                        ref_space = prop_value.get("space", datamodel_space)
                        ref_ext_id = prop_value["externalId"]
                        ref_ns = Namespace(f"http://purl.org/cognite/{ref_space}/")
                        obj = ref_ns[ref_ext_id]
                        graph.add((subject, predicate, obj))
                    elif isinstance(prop_value, list):
                        # List of values
                        for item in prop_value:
                            if isinstance(item, dict) and "externalId" in item:
                                ref_space = item.get("space", datamodel_space)
                                ref_ext_id = item["externalId"]
                                ref_ns = Namespace(f"http://purl.org/cognite/{ref_space}/")
                                obj = ref_ns[ref_ext_id]
                                graph.add((subject, predicate, obj))
                            else:
                                from rdflib import Literal

                                graph.add((subject, predicate, Literal(item)))
                    else:
                        # Literal value
                        from rdflib import Literal

                        graph.add((subject, predicate, Literal(prop_value)))

    def _auto_load_references(
        self,
        data_graph: Graph,
        instances: list[dict],
        reference_map: dict,
        client: NeatClient,
        datamodel_space: str,
        datamodel_external_id: str,
        datamodel_version: str,
        namespace: Namespace,
        max_depth: int = 2,
        max_instances: int = 10000,
        max_reverse_relations_per_query: int = 1000,
        verbose: bool = False,
        schema_issues: list[SchemaIssue] | None = None,
    ) -> tuple[int, list[SchemaIssue]]:
        """
        Auto-load referenced instances from DMS based on sh:node constraints.
        Supports recursive loading up to max_depth levels.

        Args:
            max_instances: Maximum total instances to auto-load. Set to -1 for unlimited.
            max_reverse_relations_per_query: Max reverse relations per query. Set to -1 for unlimited.
            schema_issues: List to collect schema inconsistencies found during auto-loading

        Returns:
            Tuple of (count of instances loaded, list of schema issues)
        """
        if schema_issues is None:
            schema_issues = []
        import time

        start_time = time.time()

        total_loaded = 0
        loaded_ids = {inst.get("externalId") for inst in instances}

        # Performance metrics
        metrics = {
            "api_calls": 0,
            "views_retrieved": 0,
            "reverse_queries": 0,
            "forward_refs": 0,
        }

        # Cache of view -> property mappings
        view_property_cache: dict[str, tuple[dict[str, dm.ViewId], dict[str, tuple[dm.ViewId, str]]]] = {}

        # Cache of retrieved view objects to avoid repeated API calls
        view_objects_cache: dict[dm.ViewId, dm.View] = {}

        # Track reverse relation chains to detect cycles
        reverse_relation_chains: set[tuple[str, str]] = set()

        # Current instances to scan for references
        current_instances = list(instances)

        for depth in range(1, max_depth + 1):
            # Check if we've hit the max instances limit
            if max_instances > 0 and total_loaded >= max_instances:
                if verbose:
                    print(f"\n    Hit max auto-load limit of {max_instances} instances at depth {depth}")
                break

            if verbose:
                print(f"\n    --- Auto-load depth {depth}/{max_depth} ---")

            # Collect all references from current instances
            to_load: dict[tuple[str, str], dict] = {}
            # Collect reverse relation queries:
            # {(view_id, through_property, prop_name): (property_name, [target_instance_ids])}
            reverse_queries: dict[tuple[dm.ViewId, str, str], tuple[str, list[tuple[str, str]]]] = {}
            # Track reverse relation triples to add: [(source_instance, property_name, target_instance)]
            reverse_relation_triples: list[tuple[tuple[str, str], str, tuple[str, str]]] = []

            # OPTIMIZATION: Collect all view IDs needed at this depth for batch retrieval
            view_ids_needed: set[dm.ViewId] = set()
            for instance in current_instances:
                properties = instance.get("properties", {})
                for space_key, views in properties.items():
                    for view_version, _props in views.items():
                        cache_key = f"{space_key}/{view_version}"
                        if cache_key not in view_property_cache:
                            # Parse view_version to get ViewId
                            parts = view_version.split("/")
                            if len(parts) == 2:
                                view_name, version = parts
                            else:
                                view_name = view_version
                                version = "v1"
                            view_id = dm.ViewId(space_key, view_name, version)
                            if view_id not in view_objects_cache:
                                view_ids_needed.add(view_id)

            # Batch retrieve all needed views at once
            if view_ids_needed:
                if verbose:
                    print(f"    Batch retrieving {len(view_ids_needed)} view definitions...")
                try:
                    retrieved_views = client.data_modeling.views.retrieve(list(view_ids_needed))
                    metrics["api_calls"] += 1
                    if retrieved_views:
                        for view in retrieved_views:
                            view_objects_cache[view.as_id()] = view
                            metrics["views_retrieved"] += 1
                except Exception as e:
                    logger.warning(f"Could not batch retrieve views: {e}")
                    if verbose:
                        print("    Warning: Batch view retrieval failed, falling back to individual retrieval")

            # Now process instances with cached views
            for instance in current_instances:
                instance_space = instance.get("space", datamodel_space)
                instance_ext_id = instance.get("externalId")

                properties = instance.get("properties", {})
                for space_key, views in properties.items():
                    for view_version, props in views.items():
                        # Get or build property->view mapping for this view
                        cache_key = f"{space_key}/{view_version}"
                        if cache_key not in view_property_cache:
                            view_property_cache[cache_key] = self._get_view_property_mappings(
                                client, space_key, view_version, view_objects_cache, verbose
                            )
                        prop_to_view, reverse_relations = view_property_cache[cache_key]

                        # Collect forward relations
                        for prop_name, prop_value in props.items():
                            # Check if this property has references
                            if isinstance(prop_value, dict) and "externalId" in prop_value:
                                ref_space = prop_value.get("space", datamodel_space)
                                ref_ext_id = prop_value["externalId"]
                                if ref_ext_id not in loaded_ids:
                                    to_load[(ref_space, ref_ext_id)] = {
                                        "space": ref_space,
                                        "externalId": ref_ext_id,
                                        "property": prop_name,
                                        "target_view": prop_to_view.get(prop_name),
                                        "source_instance": instance.get("externalId"),
                                    }
                                    metrics["forward_refs"] += 1
                            elif isinstance(prop_value, list):
                                for item in prop_value:
                                    if isinstance(item, dict) and "externalId" in item:
                                        ref_space = item.get("space", datamodel_space)
                                        ref_ext_id = item["externalId"]
                                        if ref_ext_id not in loaded_ids:
                                            to_load[(ref_space, ref_ext_id)] = {
                                                "space": ref_space,
                                                "externalId": ref_ext_id,
                                                "property": prop_name,
                                                "target_view": prop_to_view.get(prop_name),
                                                "source_instance": instance.get("externalId"),
                                            }

                        # Collect reverse relation queries
                        for rev_prop_name, (
                            source_view,
                            through_prop,
                            is_list_property,
                            container_reference,
                        ) in reverse_relations.items():
                            # Cycle detection: Check if this reverse relation chain was already processed
                            chain_key = (
                                f"{space_key}/{view_version}",
                                f"{source_view.space}/{source_view.external_id}/{source_view.version}",
                            )
                            if chain_key in reverse_relation_chains:
                                if verbose:
                                    print(f"        Skipping reverse relation {rev_prop_name} - cycle detected")
                                continue

                            if verbose:
                                print(
                                    f"        Collecting reverse query for {rev_prop_name}: "
                                    f"{source_view.external_id}.{through_prop}"
                                )
                            # Include is_list_property flag and container_reference in the query key
                            query_key = (
                                source_view,
                                through_prop,
                                rev_prop_name,
                                is_list_property,
                                container_reference,
                            )
                            if query_key not in reverse_queries:
                                reverse_queries[query_key] = (rev_prop_name, [])
                            # Unpack, append, repack (tuples are immutable)
                            prop_name, instance_list = reverse_queries[query_key]
                            instance_list.append((instance_space, instance_ext_id))
                            reverse_queries[query_key] = (prop_name, instance_list)
                            # Mark this chain as processed
                            reverse_relation_chains.add(chain_key)

            # Query for reverse relation instances
            if reverse_queries and verbose:
                print(f"    Querying {len(reverse_queries)} reverse relation types")

            for (
                source_view,
                through_prop,
                _,
                is_list_property,
                container_reference,
            ), (rev_prop_name, target_instances) in reverse_queries.items():
                if verbose:
                    list_marker = " (list)" if is_list_property else ""
                    print(
                        f"      Reverse: {source_view.external_id}.{through_prop}{list_marker} -> "
                        f"{len(target_instances)} instances"
                    )

                try:
                    # Build filter to find instances that reference any of our target instances
                    # Filter: source_view instances where through_prop points to any of target_instances
                    from cognite.client.data_classes import filters as dms_filters
                    from cognite.client.exceptions import CogniteAPIError

                    # Determine property reference for filter
                    # For MappedProperty, use container reference; otherwise use view reference
                    if container_reference:
                        # MappedProperty: use container space, container id, container property
                        property_ref = [container_reference[0], container_reference[1], container_reference[2]]
                    else:
                        # Direct property: use view space, view id, property name
                        property_ref = [source_view.space, source_view.external_id, through_prop]

                    # Create filter based on property type
                    if is_list_property:
                        # For list properties, use In filter (checking if the target is in the list)
                        # Build a list of all target instances to check
                        target_values = [
                            {"space": target_space, "externalId": target_ext_id}
                            for target_space, target_ext_id in target_instances
                        ]
                        # Use In filter: matches if any target is in the list property
                        filter_expr = dms_filters.In(property_ref, target_values)
                    else:
                        # For scalar properties, use Equals filter with OR for multiple targets
                        or_filters = []
                        for target_space, target_ext_id in target_instances:
                            filter_value = {"space": target_space, "externalId": target_ext_id}
                            or_filters.append(dms_filters.Equals(property_ref, filter_value))

                        # Combine with OR if multiple targets
                        if len(or_filters) == 1:
                            filter_expr = or_filters[0]
                        else:
                            filter_expr = dms_filters.Or(*or_filters)

                    # Query instances with limit to prevent unbounded loading
                    query_limit = max_reverse_relations_per_query if max_reverse_relations_per_query > 0 else -1
                    result = client.data_modeling.instances.list(
                        instance_type="node", sources=[source_view], filter=filter_expr, limit=query_limit
                    )
                    metrics["api_calls"] += 1
                    metrics["reverse_queries"] += 1

                    # Warn if we hit the limit
                    if max_reverse_relations_per_query > 0 and len(result.data) >= max_reverse_relations_per_query:
                        if verbose:
                            print(
                                f"        WARNING: Hit max reverse relations limit "
                                f"({max_reverse_relations_per_query}). "
                                f"Some instances may be missing. Consider increasing "
                                f"max_reverse_relations_per_query."
                            )

                    # Add to to_load and track reverse triples
                    # Each loaded instance connects back to ALL the target instances we queried for
                    for node in result.data:
                        node_space = node.space
                        node_ext_id = node.external_id

                        # Check which target instance(s) this node points to via through_prop
                        node_dict = node.dump(camel_case=True)
                        node_props = node_dict.get("properties", {})
                        for _prop_space, views in node_props.items():
                            for _view_key, props in views.items():
                                through_value = props.get(through_prop)
                                if through_value:
                                    # Handle single value or list
                                    targets_to_check = (
                                        [through_value] if not isinstance(through_value, list) else through_value
                                    )
                                    for target_val in targets_to_check:
                                        if isinstance(target_val, dict) and "externalId" in target_val:
                                            target_space = target_val.get("space", datamodel_space)
                                            target_ext_id = target_val["externalId"]
                                            # Check if this target is one we're collecting for
                                            if (target_space, target_ext_id) in target_instances:
                                                # Record reverse relation triple with correct property name
                                                reverse_relation_triples.append(
                                                    (
                                                        (target_space, target_ext_id),  # Original instance
                                                        rev_prop_name,  # Property name (e.g., "mcPackagesRel")
                                                        (node_space, node_ext_id),  # Reverse instance
                                                    )
                                                )

                        if node_ext_id not in loaded_ids:
                            to_load[(node_space, node_ext_id)] = {
                                "space": node_space,
                                "externalId": node_ext_id,
                                "property": through_prop,
                                "target_view": source_view,
                                "source_instance": "reverse_relation",
                            }

                    if verbose and len(result.data) > 0:
                        print(f"        Found {len(result.data)} instances")

                except CogniteAPIError as api_err:
                    # Create schema issue for all API errors during reverse relation loading
                    view_id_str = f"{source_view.space}/{source_view.external_id}/{source_view.version}"

                    if api_err.code == 403:
                        logger.error(
                            f"Permission denied for reverse relation query on {source_view.external_id}: {api_err}"
                        )
                        schema_issues.append(
                            SchemaIssue(
                                issue_type="permission_denied",
                                severity="Error",
                                message=f"Permission denied for property '{through_prop}' in {source_view.external_id}",
                                view_id=view_id_str,
                                property_name=through_prop,
                                error_code=api_err.code,
                                details={"error_message": str(api_err)},
                            )
                        )
                        if verbose:
                            print(
                                f"        ERROR: Permission denied - check access rights for view "
                                f"{source_view.external_id}"
                            )
                    elif api_err.code in (408, 429, 503, 504):
                        logger.warning(f"Temporary API error for reverse relation query: {api_err}")
                        # Don't create schema issue for temporary errors
                        if verbose:
                            print(
                                f"        WARNING: Temporary API error (code {api_err.code}) - "
                                f"some instances may be missing"
                            )
                    elif api_err.code == 400 and "do not exist" in str(api_err):
                        # Property doesn't exist in the container/view - create schema issue
                        logger.warning(f"Property {through_prop} not found in {source_view.external_id}: {api_err}")
                        schema_issues.append(
                            SchemaIssue(
                                issue_type="missing_property",
                                severity="Warning",
                                message=f"Property '{through_prop}' does not exist in {source_view.external_id}",
                                view_id=view_id_str,
                                property_name=through_prop,
                                error_code=api_err.code,
                                details={
                                    "error_message": str(api_err),
                                    "reverse_relation": rev_prop_name,
                                },
                            )
                        )
                        if verbose:
                            print(
                                f"        WARNING: Property '{through_prop}' does not exist in "
                                f"{source_view.external_id} - skipping reverse relation"
                            )
                    elif api_err.code == 400 and "is a list" in str(api_err):
                        # Property type issue (should have been caught earlier, but safety net)
                        logger.error(f"Property type error in reverse relation query: {api_err}")
                        schema_issues.append(
                            SchemaIssue(
                                issue_type="property_type_mismatch",
                                severity="Error",
                                message=(
                                    f"Property '{through_prop}' in {source_view.external_id} "
                                    f"has incompatible type for reverse relation"
                                ),
                                view_id=view_id_str,
                                property_name=through_prop,
                                error_code=api_err.code,
                                details={
                                    "error_message": str(api_err),
                                    "reverse_relation": rev_prop_name,
                                },
                            )
                        )
                        if verbose:
                            print(f"        ERROR: Property type issue: {api_err}")
                    else:
                        logger.error(f"API error in reverse relation query: {api_err}")
                        schema_issues.append(
                            SchemaIssue(
                                issue_type="api_error",
                                severity="Error",
                                message=(
                                    f"API error querying reverse relation '{through_prop}' in {source_view.external_id}"
                                ),
                                view_id=view_id_str,
                                property_name=through_prop,
                                error_code=api_err.code,
                                details={"error_message": str(api_err)},
                            )
                        )
                        if verbose:
                            print(f"        ERROR: API error (code {api_err.code}): {api_err}")
                except Exception as e:
                    logger.error(f"Unexpected error in reverse relation query: {e}", exc_info=True)
                    view_id_str = f"{source_view.space}/{source_view.external_id}/{source_view.version}"
                    schema_issues.append(
                        SchemaIssue(
                            issue_type="unexpected_error",
                            severity="Error",
                            message=(
                                f"Unexpected error querying reverse relation '{through_prop}' "
                                f"in {source_view.external_id}"
                            ),
                            view_id=view_id_str,
                            property_name=through_prop,
                            details={"error_message": str(e), "error_type": type(e).__name__},
                        )
                    )
                    if verbose:
                        print(f"        ERROR: Unexpected error: {e}")

            if not to_load:
                if verbose:
                    print(f"    No more references to load at depth {depth}")
                break

            if verbose:
                print(f"    Found {len(to_load)} references to load")
                # Group by target view for display
                by_view: dict[str, int] = {}
                for ref_info in to_load.values():
                    v = ref_info.get("target_view")
                    view_name = f"{v.external_id}/{v.version}" if v else "NO VIEW"
                    by_view[view_name] = by_view.get(view_name, 0) + 1
                for view_name, count in by_view.items():
                    print(f"      {view_name}: {count} instances")

            # Load instances, grouped by target view
            newly_loaded = self._load_instances_by_view(
                client, to_load, data_graph, namespace, datamodel_space, loaded_ids, verbose
            )

            # Add reverse relation triples to the graph
            if reverse_relation_triples:
                if verbose:
                    print(f"    Adding {len(reverse_relation_triples)} reverse relation triples to graph")

                # OPTIMIZATION: Build instance_to_view mapping ONCE before loop
                # Track which view each source instance belongs to
                instance_to_view: dict[tuple[str, str], tuple[str, str]] = {}
                all_instances = list(instances) + newly_loaded  # Use newly_loaded instead of current_instances
                for inst in all_instances:
                    inst_space = inst.get("space", datamodel_space)
                    inst_ext_id = inst.get("externalId")
                    # Get the view from properties
                    props = inst.get("properties", {})
                    for view_space, views in props.items():
                        for view_version, _ in views.items():
                            view_name = view_version.split("/")[0] if "/" in view_version else view_version
                            instance_to_view[(inst_space, inst_ext_id)] = (view_space, view_name)
                            break  # Use first view found
                        break

                # Now efficiently add all triples using the pre-built mapping
                for (source_space, source_ext_id), prop_name, (target_space, target_ext_id) in reverse_relation_triples:
                    # Create URIs for source and target
                    source_ns = Namespace(f"http://purl.org/cognite/{source_space}/")
                    target_ns = Namespace(f"http://purl.org/cognite/{target_space}/")
                    source_uri = source_ns[source_ext_id]
                    target_uri = target_ns[target_ext_id]

                    # Create predicate URI using the source instance's view namespace
                    view_space, view_name = instance_to_view.get(
                        (source_space, source_ext_id), (datamodel_space, "UnknownView")
                    )
                    pred_ns = Namespace(f"http://purl.org/cognite/{view_space}/{view_name}/")
                    predicate_uri = pred_ns[prop_name]

                    # Add triple to graph
                    data_graph.add((source_uri, predicate_uri, target_uri))

                    if verbose:
                        print(f"      {source_ext_id}.{prop_name} -> {target_ext_id}")
                        print(f"        Subject: {source_uri}")
                        print(f"        Predicate: {predicate_uri}")
                        print(f"        Object: {target_uri}")

            total_loaded += len(newly_loaded)
            current_instances = newly_loaded

            if verbose:
                print(f"    Loaded {len(newly_loaded)} instances at depth {depth}")

        # Log performance metrics
        elapsed_time = time.time() - start_time
        if verbose:
            print("\n  --- Auto-loading Performance Metrics ---")
            print(f"    Total instances loaded: {total_loaded}")
            print(f"    Total API calls: {metrics['api_calls']}")
            print(f"    Views retrieved: {metrics['views_retrieved']}")
            print(f"    Reverse relation queries: {metrics['reverse_queries']}")
            print(f"    Forward references found: {metrics['forward_refs']}")
            print(f"    Schema issues found: {len(schema_issues)}")
            print(f"    Time elapsed: {elapsed_time:.2f}s")

        logger.info(
            f"Auto-loading completed: {total_loaded} instances, {metrics['api_calls']} API calls, "
            f"{len(schema_issues)} schema issues, {elapsed_time:.2f}s"
        )

        return total_loaded, schema_issues

    def _get_view_property_mappings(
        self,
        client: NeatClient,
        space: str,
        view_version: str,
        view_objects_cache: dict[dm.ViewId, dm.View],
        verbose: bool = False,
    ) -> tuple[dict[str, dm.ViewId], dict[str, tuple[dm.ViewId, str, bool, tuple[str, str, str] | None]]]:
        """
        Get property -> target view mappings for a view.

        Args:
            view_objects_cache: Pre-fetched view objects to avoid repeated API calls

        Returns:
            Tuple of (forward_mappings, reverse_mappings) where:
            - forward_mappings: dict[property_name, target_view_id]
            - reverse_mappings: dict[property_name, (source_view_id, through_property,
              is_list_property, container_reference)] where container_reference is
              (container_space, container_id, container_property) for MappedProperty, or None
        """
        property_to_target_view: dict[str, dm.ViewId] = {}
        reverse_relations: dict[str, tuple[dm.ViewId, str, bool, tuple[str, str, str] | None]] = {}

        try:
            # Parse view_version (e.g., "YourOrgAsset/v1")
            parts = view_version.split("/")
            if len(parts) == 2:
                view_name, version = parts
            else:
                view_name = view_version
                version = "v1"

            view_id = dm.ViewId(space, view_name, version)

            # Try to use cached view first
            if view_id in view_objects_cache:
                full_view = [view_objects_cache[view_id]]
                if verbose:
                    print(f"      Using cached view: {view_id.space}/{view_id.external_id}/{view_id.version}")
            else:
                # Fall back to individual retrieval if not in cache
                if verbose:
                    print(f"      Retrieving view: {view_id.space}/{view_id.external_id}/{view_id.version}")
                full_view = client.data_modeling.views.retrieve(view_id)
                if full_view:
                    # Cache for future use
                    for v in full_view:
                        view_objects_cache[v.as_id()] = v

            if full_view:
                for v in full_view:
                    for prop_name, prop_def in v.properties.items():
                        # Reverse direct relations (check FIRST before generic source check)
                        if isinstance(prop_def, dm.MultiReverseDirectRelation):
                            # Check if the through property is a list in the source view
                            is_list_property = False
                            container_reference = None  # Will store (space, external_id, property) for MappedProperty
                            try:
                                source_view_obj = view_objects_cache.get(prop_def.source)
                                if not source_view_obj:
                                    # Fetch source view to check property type
                                    source_views = client.data_modeling.views.retrieve(prop_def.source)
                                    if source_views:
                                        source_view_obj = source_views[0]
                                        view_objects_cache[prop_def.source] = source_view_obj

                                if source_view_obj and prop_def.through.property in source_view_obj.properties:
                                    through_prop_def = source_view_obj.properties[prop_def.through.property]
                                    # Check if it's a list type (has isList attribute or is MultiEdgeConnection)
                                    is_list_property = getattr(through_prop_def, "is_list", False) or (
                                        isinstance(through_prop_def, (dm.MappedProperty,))
                                        and getattr(through_prop_def.type, "is_list", False)
                                    )

                                    # For MappedProperty, extract container reference for filter construction
                                    if isinstance(through_prop_def, dm.MappedProperty):
                                        if hasattr(through_prop_def, "container") and hasattr(
                                            through_prop_def, "container_property_identifier"
                                        ):
                                            container_reference = (
                                                through_prop_def.container.space,
                                                through_prop_def.container.external_id,
                                                through_prop_def.container_property_identifier,
                                            )
                            except Exception as e:
                                if verbose:
                                    print(
                                        f"        Warning: Could not check property type for "
                                        f"{prop_def.through.property}: {e}"
                                    )

                            # Store with list property flag and container reference (if MappedProperty)
                            reverse_relations[prop_name] = (
                                prop_def.source,
                                prop_def.through.property,
                                is_list_property,
                                container_reference,
                            )
                            if verbose:
                                list_marker = " (list)" if is_list_property else ""
                                print(
                                    f"        {prop_name} <- {prop_def.source.external_id}/"
                                    f"{prop_def.source.version} (through {prop_def.through.property}{list_marker})"
                                )
                        # Forward direct relations
                        elif hasattr(prop_def, "source") and prop_def.source:
                            property_to_target_view[prop_name] = prop_def.source
                            if verbose:
                                print(f"        {prop_name} -> {prop_def.source.external_id}/{prop_def.source.version}")
        except Exception as e:
            if verbose:
                print(f"      Warning: Could not fetch view {space}/{view_version}: {e}")

        return property_to_target_view, reverse_relations

    def _load_instances_by_view(
        self,
        client: NeatClient,
        to_load: dict[tuple[str, str], dict],
        data_graph: Graph,
        namespace: Namespace,
        datamodel_space: str,
        loaded_ids: set,
        verbose: bool = False,
    ) -> list[dict]:
        """Load instances from DMS, grouped by their target view."""
        newly_loaded = []

        # Group by target view
        refs_by_view: dict[dm.ViewId | None, list[tuple[str, str]]] = {}
        for (space, ext_id), ref_info in to_load.items():
            target_view = ref_info.get("target_view")
            if target_view not in refs_by_view:
                refs_by_view[target_view] = []
            refs_by_view[target_view].append((space, ext_id))

        try:
            for target_view, node_ids in refs_by_view.items():
                if verbose:
                    if target_view:
                        view_ref = f"{target_view.external_id}/{target_view.version}"
                        print(f"      Loading {len(node_ids)} nodes using view '{view_ref}'")
                    else:
                        print(f"      Loading {len(node_ids)} nodes (no specific view)")

                batch_size = 100
                for i in range(0, len(node_ids), batch_size):
                    batch = node_ids[i : i + batch_size]

                    result = client.data_modeling.instances.retrieve(
                        nodes=batch, sources=[target_view] if target_view else None
                    )

                    for node in result.nodes:
                        loaded_ids.add(node.external_id)
                        instance_dict = node.dump()
                        newly_loaded.append(instance_dict)

                        # Log all property values
                        if verbose:
                            print(f"          --- {node.external_id} ---")
                            props_found = False
                            for _space_key, views in instance_dict.get("properties", {}).items():
                                for view_key, props in views.items():
                                    print(f"            View: {view_key}")
                                    for prop_name, prop_value in props.items():
                                        props_found = True
                                        val_str = str(prop_value)
                                        if len(val_str) > 100:
                                            val_str = val_str[:100] + "..."
                                        print(f"              {prop_name}: {val_str}")
                            if not props_found:
                                print("            NO PROPERTIES (view mismatch?)")

                        self._add_instance_to_graph(data_graph, instance_dict, namespace, datamodel_space)

        except Exception as e:
            if verbose:
                print(f"    Warning: Could not load some references: {e}")
                import traceback

                traceback.print_exc()

        return newly_loaded


# Helper functions for SHACL template generation


def _analyze_raw_schema(rows: list) -> dict:
    """
    Analyze RAW rows to discover column schema.

    Args:
        rows: List of Row objects from CDF RAW API

    Returns:
        Dict mapping column names to schema info:
        {
            column_name: {
                "type": "string" | "int" | "float" | "boolean",
                "present_in": int,  # Number of rows with this column
                "nullable": bool,   # True if any row has None/null value
            }
        }
    """
    from collections import defaultdict

    column_stats = defaultdict(
        lambda: {
            "types": set(),
            "present_in": 0,
            "nullable": False,
        }
    )

    for row in rows:
        columns = row.columns if hasattr(row, "columns") else row
        for col_name, value in columns.items():
            stats = column_stats[col_name]
            stats["present_in"] += 1

            if value is None:
                stats["nullable"] = True
            else:
                # Infer type
                if isinstance(value, bool):
                    stats["types"].add("boolean")
                elif isinstance(value, int):
                    stats["types"].add("int")
                elif isinstance(value, float):
                    stats["types"].add("float")
                else:
                    stats["types"].add("string")

    # Consolidate types (prefer most specific)
    schema = {}
    for col_name, stats in column_stats.items():
        # Choose most restrictive type if multiple found
        types = stats["types"]
        if "string" in types:
            col_type = "string"  # Fallback to string if mixed with other types
        elif "float" in types:
            col_type = "float"
        elif "int" in types:
            col_type = "int"
        elif "boolean" in types:
            col_type = "boolean"
        else:
            col_type = "string"

        schema[col_name] = {
            "type": col_type,
            "present_in": stats["present_in"],
            "nullable": stats["nullable"],
        }

    return schema


def _generate_shacl_from_schema(
    db_name: str,
    table_name: str,
    schema: dict,
    required_columns: list[str] | None = None,
) -> str:
    """
    Generate SHACL rules from discovered schema.

    Creates rules for:
    - Required columns (sh:minCount 1)
    - Column types (sh:datatype)

    Args:
        db_name: RAW database name
        table_name: RAW table name
        schema: Column schema from analyze_raw_schema()
        required_columns: List of column names that must be present

    Returns:
        SHACL rules as Turtle string
    """
    namespace = f"raw_{db_name}_{table_name}"
    uri_base = f"http://purl.org/cognite/raw/{db_name}/{table_name}/"

    # Build SHACL document
    rules = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        f"@prefix {namespace}: <{uri_base}> .",
        "",
        f"# SHACL template for RAW table: {db_name}.{table_name}",
        f"# Auto-generated from {len(schema)} columns found in sample rows",
        "# Edit this template to add custom constraints (ranges, patterns, etc.)",
        "",
    ]

    # Create shape for each column
    for col_name, col_schema in schema.items():
        is_required = required_columns and col_name in required_columns
        col_presence = (col_schema["present_in"] / len(schema)) * 100 if schema else 0

        # Column presence shape
        shape_name = f"{namespace}:{col_name.replace('-', '_').replace(' ', '_')}Shape"
        rules.extend(
            [
                f"# Column: {col_name}",
                (
                    f"# Type: {col_schema['type']}, Present in: {col_presence:.0f}% of rows, "
                    f"Nullable: {col_schema['nullable']}"
                ),
                f"{shape_name}",
                "    a sh:NodeShape ;",
                f"    sh:targetClass {namespace}:{table_name} ;",
                "    sh:property [",
                f"        sh:path {namespace}:{col_name.replace('-', '_').replace(' ', '_')} ;",
            ]
        )

        # Add minCount if required
        if is_required:
            rules.append("        sh:minCount 1 ;")

        # Add datatype constraint
        xsd_type = {
            "string": "xsd:string",
            "int": "xsd:integer",
            "float": "xsd:double",
            "boolean": "xsd:boolean",
        }.get(col_schema["type"], "xsd:string")

        if not col_schema["nullable"]:
            rules.append(f"        sh:datatype {xsd_type} ;")

        # Add helpful error message
        if is_required and not col_schema["nullable"]:
            # Both required and has type constraint
            rules.append(f'        sh:message "{col_name} is required and must be of type {col_schema["type"]}" ;')
        elif is_required:
            # Only required
            rules.append(f'        sh:message "{col_name} is required" ;')
        elif not col_schema["nullable"]:
            # Only type constraint
            rules.append(f'        sh:message "{col_name} must be of type {col_schema["type"]}" ;')

        rules.extend(
            [
                "    ] .",
                "",
            ]
        )

    return "\n".join(rules)
