"""Tests for CDF SPARQL function registry."""

from unittest.mock import MagicMock, patch

import pytest
from rdflib import Graph

from thisisneat.core._cdf_sparql_functions._registry import (
    CDF_INDSL_NS,
    CDF_SDK_NS,
    get_registered_functions,
    register_cdf_sparql_functions,
    unregister_cdf_sparql_functions,
)


class TestNamespaces:
    """Tests for namespace constants."""

    def test_sdk_namespace(self):
        """Test CDF SDK namespace URI."""
        assert str(CDF_SDK_NS) == "https://cognite.com/cdf/sdk/"

    def test_indsl_namespace(self):
        """Test CDF INDSL namespace URI."""
        assert str(CDF_INDSL_NS) == "https://cognite.com/cdf/indsl/"

    def test_namespace_function_uri(self):
        """Test creating function URIs from namespace."""
        func_uri = CDF_SDK_NS["datapoints_aggregate"]
        assert str(func_uri) == "https://cognite.com/cdf/sdk/datapoints_aggregate"


class TestRegisterCdfSparqlFunctions:
    """Tests for register_cdf_sparql_functions."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock CogniteClient."""
        return MagicMock()

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset registry state before and after each test."""
        unregister_cdf_sparql_functions()
        yield
        unregister_cdf_sparql_functions()

    def test_registers_sdk_functions(self, mock_client):
        """Test that SDK functions are registered."""
        registered = register_cdf_sparql_functions(mock_client, force=True)

        assert "cdf_sdk" in registered
        assert len(registered["cdf_sdk"]) > 0
        assert "datapoints_aggregate" in registered["cdf_sdk"]
        assert "datapoints_count" in registered["cdf_sdk"]
        assert "timeseries_exists" in registered["cdf_sdk"]

    def test_binds_namespaces_to_graph(self, mock_client):
        """Test that namespaces are bound to graph."""
        graph = Graph()
        register_cdf_sparql_functions(mock_client, graph, force=True)

        # Check namespace bindings
        namespaces = dict(graph.namespaces())
        assert "cdf_sdk" in namespaces
        assert "cdf_indsl" in namespaces

    def test_returns_empty_on_duplicate_registration(self, mock_client):
        """Test that duplicate registration returns empty lists."""
        # First registration
        first_result = register_cdf_sparql_functions(mock_client, force=True)
        assert len(first_result["cdf_sdk"]) > 0

        # Second registration without force
        second_result = register_cdf_sparql_functions(mock_client, force=False)
        assert second_result == {"cdf_sdk": [], "cdf_indsl": []}

    def test_force_reregistration(self, mock_client):
        """Test that force=True allows re-registration."""
        # First registration
        first_result = register_cdf_sparql_functions(mock_client, force=True)
        assert len(first_result["cdf_sdk"]) > 0

        # Second registration with force
        second_result = register_cdf_sparql_functions(mock_client, force=True)
        assert len(second_result["cdf_sdk"]) > 0


class TestGetRegisteredFunctions:
    """Tests for get_registered_functions."""

    def test_returns_sdk_functions(self):
        """Test that SDK functions are listed."""
        funcs = get_registered_functions()

        assert "cdf_sdk" in funcs
        assert "datapoints_aggregate" in funcs["cdf_sdk"]
        assert "datapoints_count" in funcs["cdf_sdk"]
        assert "datapoints_latest" in funcs["cdf_sdk"]
        assert "timeseries_exists" in funcs["cdf_sdk"]

    def test_indsl_functions_depend_on_availability(self):
        """Test that INDSL functions depend on package availability."""
        funcs = get_registered_functions()

        # cdf_indsl key should always exist
        assert "cdf_indsl" in funcs

        # Content depends on whether INDSL is installed
        # (we don't know if it's installed in test environment)


class TestUnregisterCdfSparqlFunctions:
    """Tests for unregister_cdf_sparql_functions."""

    @pytest.fixture
    def mock_client(self):
        """Create a mock CogniteClient."""
        return MagicMock()

    def test_unregister_allows_reregistration(self, mock_client):
        """Test that unregister allows re-registration without force."""
        # Register first
        register_cdf_sparql_functions(mock_client, force=True)

        # Unregister
        unregister_cdf_sparql_functions()

        # Should be able to register again without force
        result = register_cdf_sparql_functions(mock_client, force=False)
        assert len(result["cdf_sdk"]) > 0

