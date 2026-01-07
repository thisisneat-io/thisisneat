"""Tests for CDF SPARQL helper functions."""

import pytest
from rdflib import Literal, URIRef

from thisisneat.core._cdf_sparql_functions._helpers import (
    literal_to_python,
    parse_instance_id_from_uri,
    safe_sparql_wrapper,
)


class TestParseInstanceIdFromUri:
    """Tests for parse_instance_id_from_uri function."""

    def test_parse_standard_uri_with_timeseries(self):
        """Test parsing standard NEAT URI format with TimeSeries."""
        uri = "http://purl.org/cognite/my_space/TimeSeries/ts-001"
        result = parse_instance_id_from_uri(uri)

        assert result.space == "my_space"
        assert result.external_id == "ts-001"

    def test_parse_uri_with_view(self):
        """Test parsing URI with view name."""
        uri = "http://purl.org/cognite/my_space/MyView/asset-001"
        result = parse_instance_id_from_uri(uri)

        assert result.space == "my_space"
        assert result.external_id == "asset-001"

    def test_parse_simple_two_segment_uri(self):
        """Test parsing simple two-segment URI."""
        uri = "http://purl.org/cognite/test_space/external-id-123"
        result = parse_instance_id_from_uri(uri)

        assert result.space == "test_space"
        assert result.external_id == "external-id-123"

    def test_parse_uri_ref(self):
        """Test parsing rdflib URIRef."""
        uri = URIRef("http://purl.org/cognite/space1/TimeSeries/ts-abc")
        result = parse_instance_id_from_uri(uri)

        assert result.space == "space1"
        assert result.external_id == "ts-abc"

    def test_parse_uri_with_special_characters(self):
        """Test parsing URI with special characters in external_id."""
        uri = "http://purl.org/cognite/my_space/TimeSeries/ts-001-test_value"
        result = parse_instance_id_from_uri(uri)

        assert result.space == "my_space"
        assert result.external_id == "ts-001-test_value"

    def test_parse_invalid_uri_raises_error(self):
        """Test that invalid URI raises ValueError."""
        with pytest.raises(ValueError, match="Could not parse instance_id"):
            parse_instance_id_from_uri("invalid-uri")

    def test_parse_generic_uri_fallback(self):
        """Test generic URI parsing as fallback."""
        uri = "http://example.org/data/space_name/external_id_value"
        result = parse_instance_id_from_uri(uri)

        # Should extract last two segments
        assert result.space == "space_name"
        assert result.external_id == "external_id_value"


class TestLiteralToPython:
    """Tests for literal_to_python conversion function."""

    def test_convert_string_literal(self):
        """Test converting string Literal."""
        lit = Literal("hello")
        result = literal_to_python(lit)
        assert result == "hello"
        assert isinstance(result, str)

    def test_convert_integer_literal(self):
        """Test converting integer Literal."""
        lit = Literal(42)
        result = literal_to_python(lit)
        assert result == 42
        assert isinstance(result, int)

    def test_convert_float_literal(self):
        """Test converting float Literal."""
        lit = Literal(3.14)
        result = literal_to_python(lit)
        assert result == 3.14
        assert isinstance(result, float)

    def test_convert_boolean_literal(self):
        """Test converting boolean Literal."""
        lit = Literal(True)
        result = literal_to_python(lit)
        assert result is True

    def test_convert_uri_ref(self):
        """Test converting URIRef to string."""
        uri = URIRef("http://example.org/resource")
        result = literal_to_python(uri)
        assert result == "http://example.org/resource"
        assert isinstance(result, str)

    def test_passthrough_native_types(self):
        """Test that native Python types pass through unchanged."""
        assert literal_to_python("string") == "string"
        assert literal_to_python(123) == 123
        assert literal_to_python(4.5) == 4.5
        assert literal_to_python(True) is True


class TestSafeSparqlWrapper:
    """Tests for safe_sparql_wrapper decorator."""

    def test_wrapper_returns_result_on_success(self):
        """Test that wrapper returns function result on success."""

        @safe_sparql_wrapper(default_value=Literal(False))
        def success_func():
            return Literal(True)

        result = success_func()
        assert result == Literal(True)

    def test_wrapper_returns_default_on_exception(self):
        """Test that wrapper returns default value on exception."""

        @safe_sparql_wrapper(default_value=Literal(False))
        def failing_func():
            raise ValueError("Test error")

        result = failing_func()
        assert result == Literal(False)

    def test_wrapper_with_callable_default(self):
        """Test that wrapper calls callable default on exception."""

        @safe_sparql_wrapper(default_value=lambda: Literal(0))
        def failing_func():
            raise RuntimeError("Test error")

        result = failing_func()
        assert result == Literal(0)

    def test_wrapper_preserves_function_name(self):
        """Test that wrapper preserves function metadata."""

        @safe_sparql_wrapper(default_value=None)
        def my_function():
            """My docstring."""
            return True

        assert my_function.__name__ == "my_function"
        assert "My docstring" in (my_function.__doc__ or "")

    def test_wrapper_passes_arguments(self):
        """Test that wrapper correctly passes arguments."""

        @safe_sparql_wrapper(default_value=Literal(0))
        def add_func(a, b):
            return Literal(a + b)

        result = add_func(3, 4)
        assert result == Literal(7)

    def test_wrapper_passes_kwargs(self):
        """Test that wrapper correctly passes keyword arguments."""

        @safe_sparql_wrapper(default_value=Literal("default"))
        def kwargs_func(value, prefix="pre_"):
            return Literal(f"{prefix}{value}")

        result = kwargs_func("test", prefix="my_")
        assert result == Literal("my_test")

