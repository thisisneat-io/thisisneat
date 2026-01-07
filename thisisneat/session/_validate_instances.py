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
from typing import Any

from cognite.client import data_modeling as dm
from rdflib import Graph, Namespace
from rdflib.namespace import RDF, SH

from thisisneat.core._cdf_sparql_functions import (
    get_registered_functions,
    is_indsl_available,
    register_cdf_sparql_functions,
)
from thisisneat.core._client import NeatClient
from thisisneat.core._issues import IssueList

from ._state import SessionState
from .exceptions import NeatSessionError, session_class_wrapper

logger = logging.getLogger(__name__)


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
        enable_cdf_functions: bool = True,
        verbose: bool = True,
    ) -> tuple[bool, str, str]:
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
            shacl_rules: SHACL rules as Turtle string
            datamodel_space: Space of the data model
            datamodel_external_id: External ID of the data model
            datamodel_version: Version of the data model
            auto_load_depth: Maximum depth for auto-loading referenced instances (default: 2)
            enable_cdf_functions: Enable cdf_sdk: and cdf_indsl: SPARQL functions (default: True)
            verbose: Print progress messages (default: True)

        Returns:
            Tuple of (conforms, report_graph, report_text)

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
                datamodel_version="v1",
                enable_cdf_functions=True  # Enable cdf_sdk: and cdf_indsl: functions
            )
            ```

        Available SPARQL Functions (when enable_cdf_functions=True):

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
        self._state._raise_exception_if_condition_not_met(
            "Validate instances with SHACL",
            client_required=True
        )
        
        client = self._state.client
        if not isinstance(client, NeatClient):
            raise NeatSessionError("Client must be a NeatClient")
        
        if verbose:
            print(f"Validating {len(instances)} instances with SHACL...")
        
        # 1. Parse SHACL rules
        shacl_graph = Graph()
        shacl_graph.parse(data=shacl_rules, format='turtle')
        
        if verbose:
            print(f"  Parsed {len(shacl_graph)} SHACL triples")
        
        # 2. Analyze SHACL to find sh:node references
        reference_map = self._analyze_shacl_references(shacl_graph, verbose=verbose)
        
        # 3. Convert instances to RDF
        data_graph = Graph()
        namespace = Namespace(f"http://purl.org/cognite/{datamodel_space}/{datamodel_external_id}/")
        data_graph.bind(datamodel_space, namespace)
        
        for instance in instances:
            self._add_instance_to_graph(data_graph, instance, namespace, datamodel_space)
        
        if verbose:
            print(f"  Converted instances to {len(data_graph)} RDF triples")
            print(f"\n  --- INPUT INSTANCES ---")
            for instance in instances:
                ext_id = instance.get('externalId', 'unknown')
                print(f"    {ext_id}:")
                for space_key, views in instance.get('properties', {}).items():
                    for view_key, props in views.items():
                        print(f"      View: {view_key}")
                        for prop_name, prop_value in props.items():
                            val_str = str(prop_value)
                            if len(val_str) > 100:
                                val_str = val_str[:100] + "..."
                            print(f"        {prop_name}: {val_str}")
        
        # 4. Auto-load referenced instances if needed
        if reference_map and auto_load_depth > 0:
            loaded_count = self._auto_load_references(
                data_graph,
                instances,
                reference_map,
                client,
                datamodel_space,
                datamodel_external_id,
                datamodel_version,
                namespace,
                max_depth=auto_load_depth,
                verbose=verbose,
            )
            if verbose:
                print(f"  Auto-loaded {loaded_count} referenced instances")

        # 5. Register CDF SPARQL functions if enabled
        if enable_cdf_functions:
            if verbose:
                print("  Registering CDF SPARQL functions...")

            registered = register_cdf_sparql_functions(client, data_graph)

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

        # Use advanced=True to enable SPARQL-based constraints and custom functions
        conforms, report_graph, report_text = pyshacl.validate(
            data_graph=data_graph,
            shacl_graph=shacl_graph,
            inference="none",
            advanced=enable_cdf_functions,  # Enable custom SPARQL functions
            abort_on_first=False,
            debug=False,
        )

        if verbose:
            print(f"  Validation {'PASSED' if conforms else 'FAILED'}")

        return (
            conforms,
            report_graph.decode("utf-8") if isinstance(report_graph, bytes) else report_graph,
            report_text,
        )
    
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
                        references.append({
                            'property_path': str(path),
                            'target_shape': str(referenced_shape),
                            'target_class': str(ref_target_class)
                        })
            
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
        datamodel_space: str
    ) -> None:
        """Convert a DMS instance dict to RDF triples.
        
        URI scheme:
        - Subject: Based on instance space + externalId (so references match)
        - Type: Based on view space + view name
        - Predicates: Based on view space + view name
        - Reference objects: Based on reference space + externalId
        """
        external_id = instance.get('externalId')
        if not external_id:
            return
        
        # Get instance space
        instance_space = instance.get('space', datamodel_space)
        
        # Create subject URI based on space + externalId
        instance_ns = Namespace(f"http://purl.org/cognite/{instance_space}/")
        subject = instance_ns[external_id]
        
        # Add type and properties based on each view
        properties = instance.get('properties', {})
        for view_space, views in properties.items():
            for view_version, props in views.items():
                # Extract view name from "ViewName/version" format
                view_name = view_version.split('/')[0] if '/' in view_version else view_version
                
                # Create namespace for this view (for type and predicates)
                view_ns = Namespace(f"http://purl.org/cognite/{view_space}/{view_name}/")
                
                # Add type
                graph.add((subject, RDF.type, view_ns[view_name]))
                
                # Add properties
                for prop_name, prop_value in props.items():
                    predicate = view_ns[prop_name]
                    
                    # Handle different value types
                    if isinstance(prop_value, dict) and 'externalId' in prop_value:
                        # Direct relation - use referenced instance's space for the object URI
                        ref_space = prop_value.get('space', datamodel_space)
                        ref_ext_id = prop_value['externalId']
                        ref_ns = Namespace(f"http://purl.org/cognite/{ref_space}/")
                        obj = ref_ns[ref_ext_id]
                        graph.add((subject, predicate, obj))
                    elif isinstance(prop_value, list):
                        # List of values
                        for item in prop_value:
                            if isinstance(item, dict) and 'externalId' in item:
                                ref_space = item.get('space', datamodel_space)
                                ref_ext_id = item['externalId']
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
        verbose: bool = False
    ) -> int:
        """
        Auto-load referenced instances from DMS based on sh:node constraints.
        Supports recursive loading up to max_depth levels.
        
        Returns:
            Count of instances loaded
        """
        total_loaded = 0
        loaded_ids = {inst.get('externalId') for inst in instances}
        
        # Cache of view -> property mappings
        view_property_cache: dict[str, dict[str, dm.ViewId]] = {}
        
        # Current instances to scan for references
        current_instances = list(instances)
        
        for depth in range(1, max_depth + 1):
            if verbose:
                print(f"\n    --- Auto-load depth {depth}/{max_depth} ---")
            
            # Collect all references from current instances
            to_load: dict[tuple[str, str], dict] = {}
            
            for instance in current_instances:
                properties = instance.get('properties', {})
                for space_key, views in properties.items():
                    for view_version, props in views.items():
                        # Get or build property->view mapping for this view
                        cache_key = f"{space_key}/{view_version}"
                        if cache_key not in view_property_cache:
                            view_property_cache[cache_key] = self._get_view_property_mappings(
                                client, space_key, view_version, verbose
                            )
                        prop_to_view = view_property_cache[cache_key]
                        
                        for prop_name, prop_value in props.items():
                            # Check if this property has references
                            if isinstance(prop_value, dict) and 'externalId' in prop_value:
                                ref_space = prop_value.get('space', datamodel_space)
                                ref_ext_id = prop_value['externalId']
                                if ref_ext_id not in loaded_ids:
                                    to_load[(ref_space, ref_ext_id)] = {
                                        'space': ref_space,
                                        'externalId': ref_ext_id,
                                        'property': prop_name,
                                        'target_view': prop_to_view.get(prop_name),
                                        'source_instance': instance.get('externalId')
                                    }
                            elif isinstance(prop_value, list):
                                for item in prop_value:
                                    if isinstance(item, dict) and 'externalId' in item:
                                        ref_space = item.get('space', datamodel_space)
                                        ref_ext_id = item['externalId']
                                        if ref_ext_id not in loaded_ids:
                                            to_load[(ref_space, ref_ext_id)] = {
                                                'space': ref_space,
                                                'externalId': ref_ext_id,
                                                'property': prop_name,
                                                'target_view': prop_to_view.get(prop_name),
                                                'source_instance': instance.get('externalId')
                                            }
            
            if not to_load:
                if verbose:
                    print(f"    No more references to load at depth {depth}")
                break
            
            if verbose:
                print(f"    Found {len(to_load)} references to load")
                # Group by target view for display
                by_view: dict[str, int] = {}
                for ref_info in to_load.values():
                    v = ref_info.get('target_view')
                    view_name = f"{v.external_id}/{v.version}" if v else "NO VIEW"
                    by_view[view_name] = by_view.get(view_name, 0) + 1
                for view_name, count in by_view.items():
                    print(f"      {view_name}: {count} instances")
            
            # Load instances, grouped by target view
            newly_loaded = self._load_instances_by_view(
                client, to_load, data_graph, namespace, datamodel_space, loaded_ids, verbose
            )
            
            total_loaded += len(newly_loaded)
            current_instances = newly_loaded
            
            if verbose:
                print(f"    Loaded {len(newly_loaded)} instances at depth {depth}")
        
        return total_loaded
    
    def _get_view_property_mappings(
        self,
        client: NeatClient,
        space: str,
        view_version: str,
        verbose: bool = False
    ) -> dict[str, dm.ViewId]:
        """Get property -> target view mappings for a view."""
        property_to_target_view: dict[str, dm.ViewId] = {}
        
        try:
            # Parse view_version (e.g., "YourOrgAsset/v1")
            parts = view_version.split('/')
            if len(parts) == 2:
                view_name, version = parts
            else:
                view_name = view_version
                version = "v1"
            
            view_id = dm.ViewId(space, view_name, version)
            if verbose:
                print(f"      Looking up view: {view_id.space}/{view_id.external_id}/{view_id.version}")
            
            full_view = client.data_modeling.views.retrieve(view_id)
            if full_view:
                for v in full_view:
                    for prop_name, prop_def in v.properties.items():
                        if hasattr(prop_def, 'source') and prop_def.source:
                            property_to_target_view[prop_name] = prop_def.source
                            if verbose:
                                print(f"        {prop_name} -> {prop_def.source.external_id}/{prop_def.source.version}")
        except Exception as e:
            if verbose:
                print(f"      Warning: Could not fetch view {space}/{view_version}: {e}")
        
        return property_to_target_view
    
    def _load_instances_by_view(
        self,
        client: NeatClient,
        to_load: dict[tuple[str, str], dict],
        data_graph: Graph,
        namespace: Namespace,
        datamodel_space: str,
        loaded_ids: set,
        verbose: bool = False
    ) -> list[dict]:
        """Load instances from DMS, grouped by their target view."""
        newly_loaded = []
        
        # Group by target view
        refs_by_view: dict[dm.ViewId | None, list[tuple[str, str]]] = {}
        for (space, ext_id), ref_info in to_load.items():
            target_view = ref_info.get('target_view')
            if target_view not in refs_by_view:
                refs_by_view[target_view] = []
            refs_by_view[target_view].append((space, ext_id))
        
        try:
            for target_view, node_ids in refs_by_view.items():
                if verbose:
                    if target_view:
                        print(f"      Loading {len(node_ids)} nodes using view '{target_view.external_id}/{target_view.version}'")
                    else:
                        print(f"      Loading {len(node_ids)} nodes (no specific view)")
                
                batch_size = 100
                for i in range(0, len(node_ids), batch_size):
                    batch = node_ids[i:i + batch_size]
                    
                    result = client.data_modeling.instances.retrieve(
                        nodes=batch,
                        sources=[target_view] if target_view else None
                    )
                    
                    for node in result.nodes:
                        loaded_ids.add(node.external_id)
                        instance_dict = node.dump()
                        newly_loaded.append(instance_dict)
                        
                        # Log all property values
                        if verbose:
                            print(f"          --- {node.external_id} ---")
                            props_found = False
                            for space_key, views in instance_dict.get('properties', {}).items():
                                for view_key, props in views.items():
                                    print(f"            View: {view_key}")
                                    for prop_name, prop_value in props.items():
                                        props_found = True
                                        val_str = str(prop_value)
                                        if len(val_str) > 100:
                                            val_str = val_str[:100] + "..."
                                        print(f"              {prop_name}: {val_str}")
                            if not props_found:
                                print(f"            NO PROPERTIES (view mismatch?)")
                        
                        self._add_instance_to_graph(data_graph, instance_dict, namespace, datamodel_space)
        
        except Exception as e:
            if verbose:
                print(f"    Warning: Could not load some references: {e}")
                import traceback
                traceback.print_exc()
        
        return newly_loaded

