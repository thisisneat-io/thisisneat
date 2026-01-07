"""Tests for the validate_instances module with SHACL validation."""

import pytest
from unittest.mock import MagicMock, patch
from rdflib import Graph, Namespace, Literal
from rdflib.namespace import RDF

from thisisneat.session._validate_instances import ValidateInstancesAPI
from thisisneat.session._state import SessionState


class TestValidateInstancesAPI:
    """Tests for ValidateInstancesAPI."""
    
    @pytest.fixture
    def mock_state(self):
        """Create a mock session state with a mock client."""
        state = MagicMock(spec=SessionState)
        state.client = MagicMock()
        state._raise_exception_if_condition_not_met = MagicMock()
        return state
    
    @pytest.fixture
    def api(self, mock_state):
        """Create a ValidateInstancesAPI instance."""
        return ValidateInstancesAPI(mock_state)
    
    @pytest.fixture
    def simple_shacl_rules(self):
        """Simple SHACL rules for testing."""
        return """
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix ex: <http://example.org/> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            
            ex:AssetShape a sh:NodeShape ;
                sh:targetClass ex:Asset ;
                sh:property [
                    sh:path ex:name ;
                    sh:minCount 1 ;
                    sh:datatype xsd:string ;
                ] .
        """
    
    @pytest.fixture
    def simple_instances(self):
        """Simple instances for testing."""
        return [
            {
                "externalId": "asset-001",
                "space": "test_space",
                "properties": {
                    "test_space": {
                        "Asset/v1": {
                            "name": "Test Asset"
                        }
                    }
                }
            }
        ]
    
    def test_analyze_shacl_references_finds_sh_node(self, api):
        """Test that _analyze_shacl_references finds sh:node constraints."""
        shacl_rules = """
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix ex: <http://example.org/> .
            
            ex:OrderShape a sh:NodeShape ;
                sh:targetClass ex:Order ;
                sh:property [
                    sh:path ex:asset ;
                    sh:node ex:AssetShape ;
                ] .
            
            ex:AssetShape a sh:NodeShape ;
                sh:targetClass ex:Asset ;
                sh:property [
                    sh:path ex:name ;
                    sh:minCount 1 ;
                ] .
        """
        shacl_graph = Graph()
        shacl_graph.parse(data=shacl_rules, format='turtle')
        
        reference_map = api._analyze_shacl_references(shacl_graph, verbose=False)
        
        # Should find the Order -> Asset reference via sh:node
        assert len(reference_map) == 1
        assert "http://example.org/Order" in reference_map
        refs = reference_map["http://example.org/Order"]
        assert len(refs) == 1
        assert refs[0]["target_class"] == "http://example.org/Asset"
    
    def test_analyze_shacl_references_empty_for_no_sh_node(self, api):
        """Test that _analyze_shacl_references returns empty when no sh:node constraints."""
        shacl_rules = """
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix ex: <http://example.org/> .
            
            ex:AssetShape a sh:NodeShape ;
                sh:targetClass ex:Asset ;
                sh:property [
                    sh:path ex:name ;
                    sh:minCount 1 ;
                ] .
        """
        shacl_graph = Graph()
        shacl_graph.parse(data=shacl_rules, format='turtle')
        
        reference_map = api._analyze_shacl_references(shacl_graph, verbose=False)
        
        assert len(reference_map) == 0
    
    def test_add_instance_to_graph_creates_triples(self, api):
        """Test that _add_instance_to_graph correctly converts instances to RDF."""
        graph = Graph()
        namespace = Namespace("http://purl.org/cognite/test_space/TestModel/")
        
        instance = {
            "externalId": "asset-001",
            "space": "test_space",
            "properties": {
                "test_space": {
                    "Asset/v1": {
                        "name": "Test Asset",
                        "value": 42
                    }
                }
            }
        }
        
        api._add_instance_to_graph(graph, instance, namespace, "test_space")
        
        # Check that triples were created
        assert len(graph) >= 2  # At least type and properties
        
        # Check subject URI uses instance space
        instance_ns = Namespace("http://purl.org/cognite/test_space/")
        subject = instance_ns["asset-001"]
        
        # Check type triple exists
        view_ns = Namespace("http://purl.org/cognite/test_space/Asset/")
        assert (subject, RDF.type, view_ns["Asset"]) in graph
        
        # Check property triples exist
        assert (subject, view_ns["name"], Literal("Test Asset")) in graph
        assert (subject, view_ns["value"], Literal(42)) in graph
    
    def test_add_instance_to_graph_handles_direct_relations(self, api):
        """Test that _add_instance_to_graph handles direct relation references."""
        graph = Graph()
        namespace = Namespace("http://purl.org/cognite/test_space/TestModel/")
        
        instance = {
            "externalId": "order-001",
            "space": "test_space",
            "properties": {
                "test_space": {
                    "Order/v1": {
                        "asset": {
                            "externalId": "asset-001",
                            "space": "test_space"
                        }
                    }
                }
            }
        }
        
        api._add_instance_to_graph(graph, instance, namespace, "test_space")
        
        # Check that reference was created correctly
        order_ns = Namespace("http://purl.org/cognite/test_space/")
        asset_ns = Namespace("http://purl.org/cognite/test_space/")
        view_ns = Namespace("http://purl.org/cognite/test_space/Order/")
        
        order_subject = order_ns["order-001"]
        asset_object = asset_ns["asset-001"]
        
        assert (order_subject, view_ns["asset"], asset_object) in graph
    
    def test_add_instance_to_graph_handles_list_of_relations(self, api):
        """Test that _add_instance_to_graph handles lists of references."""
        graph = Graph()
        namespace = Namespace("http://purl.org/cognite/test_space/TestModel/")
        
        instance = {
            "externalId": "order-001",
            "space": "test_space",
            "properties": {
                "test_space": {
                    "Order/v1": {
                        "assets": [
                            {"externalId": "asset-001", "space": "test_space"},
                            {"externalId": "asset-002", "space": "test_space"}
                        ]
                    }
                }
            }
        }
        
        api._add_instance_to_graph(graph, instance, namespace, "test_space")
        
        order_ns = Namespace("http://purl.org/cognite/test_space/")
        asset_ns = Namespace("http://purl.org/cognite/test_space/")
        view_ns = Namespace("http://purl.org/cognite/test_space/Order/")
        
        order_subject = order_ns["order-001"]
        asset1 = asset_ns["asset-001"]
        asset2 = asset_ns["asset-002"]
        
        assert (order_subject, view_ns["assets"], asset1) in graph
        assert (order_subject, view_ns["assets"], asset2) in graph
    
    def test_add_instance_to_graph_handles_list_of_literals(self, api):
        """Test that _add_instance_to_graph handles lists of literal values."""
        graph = Graph()
        namespace = Namespace("http://purl.org/cognite/test_space/TestModel/")
        
        instance = {
            "externalId": "asset-001",
            "space": "test_space",
            "properties": {
                "test_space": {
                    "Asset/v1": {
                        "tags": ["tag1", "tag2", "tag3"]
                    }
                }
            }
        }
        
        api._add_instance_to_graph(graph, instance, namespace, "test_space")
        
        instance_ns = Namespace("http://purl.org/cognite/test_space/")
        view_ns = Namespace("http://purl.org/cognite/test_space/Asset/")
        
        subject = instance_ns["asset-001"]
        
        assert (subject, view_ns["tags"], Literal("tag1")) in graph
        assert (subject, view_ns["tags"], Literal("tag2")) in graph
        assert (subject, view_ns["tags"], Literal("tag3")) in graph
    
    def test_add_instance_to_graph_skips_empty_external_id(self, api):
        """Test that instances without externalId are skipped."""
        graph = Graph()
        namespace = Namespace("http://purl.org/cognite/test_space/TestModel/")
        
        instance = {
            "space": "test_space",
            "properties": {}
        }
        
        api._add_instance_to_graph(graph, instance, namespace, "test_space")
        
        assert len(graph) == 0


class TestCursorManagement:
    """Tests for cursor management functionality."""
    
    def test_cursors_dict_operations(self):
        """Test basic cursor dictionary operations."""
        cursors: dict[str, str] = {}
        
        # Set cursors
        cursors["view1"] = "cursor1"
        cursors["view2"] = "cursor2"
        
        assert len(cursors) == 2
        assert cursors["view1"] == "cursor1"
        
        # Get copy
        cursors_copy = cursors.copy()
        cursors_copy["view3"] = "cursor3"
        
        # Original unaffected
        assert "view3" not in cursors
        assert "view3" in cursors_copy
    
    def test_cursors_json_serialization(self, tmp_path):
        """Test cursor persistence to JSON file."""
        import json
        
        cursors = {"view1": "cursor_value_1", "view2": "cursor_value_2"}
        cursors_file = tmp_path / "cursors.json"
        
        # Save
        with open(cursors_file, "w") as f:
            json.dump(cursors, f, indent=2)
        
        # Load
        with open(cursors_file, "r") as f:
            loaded = json.load(f)
        
        assert loaded == cursors
    
    def test_cursor_key_format(self):
        """Test that cursor keys follow expected format (space/view/version)."""
        cursors = {}
        
        # Keys should be view identifiers
        key = "my_space/MyView/v1"
        cursors[key] = "some_cursor_string"
        
        assert key in cursors
        
        # Parse key
        parts = key.split("/")
        assert len(parts) == 3
        assert parts[0] == "my_space"
        assert parts[1] == "MyView"
        assert parts[2] == "v1"
