"""
Unit tests for RAW table SHACL template generation.

Tests verify:
1. Schema analysis from RAW rows
2. Type inference (string, int, float, boolean)
3. Required vs optional columns
4. SHACL rule generation
5. Handling of missing/null values
"""

from unittest.mock import Mock

from cognite.client.data_classes import Row

from thisisneat.core._client.testing import monkeypatch_neat_client
from thisisneat.session import NeatSession


class TestGenerateSHACLTemplateForRaw:
    """Test SHACL template generation from RAW table schema."""

    def test_analyze_schema_basic_types(self) -> None:
        """Test schema analysis with basic data types."""
        from thisisneat.session._validate_instances import _analyze_raw_schema as analyze_raw_schema

        rows = [
            Row(
                key="row1",
                columns={
                    "string_col": "value1",
                    "int_col": 42,
                    "float_col": 3.14,
                    "bool_col": True,
                },
            ),
            Row(
                key="row2",
                columns={
                    "string_col": "value2",
                    "int_col": 100,
                    "float_col": 2.71,
                    "bool_col": False,
                },
            ),
        ]

        schema = analyze_raw_schema(rows)

        assert schema["string_col"]["type"] == "string"
        assert schema["int_col"]["type"] == "int"
        assert schema["float_col"]["type"] == "float"
        assert schema["bool_col"]["type"] == "boolean"
        assert schema["string_col"]["present_in"] == 2
        assert schema["string_col"]["nullable"] is False

    def test_analyze_schema_nullable_columns(self) -> None:
        """Test schema analysis detects nullable columns."""
        from thisisneat.session._validate_instances import _analyze_raw_schema as analyze_raw_schema

        rows = [
            Row(key="row1", columns={"col1": "value1", "col2": None}),
            Row(key="row2", columns={"col1": "value2", "col2": "value"}),
        ]

        schema = analyze_raw_schema(rows)

        assert schema["col1"]["nullable"] is False  # Never null
        assert schema["col2"]["nullable"] is True  # Sometimes null

    def test_analyze_schema_sparse_columns(self) -> None:
        """Test schema analysis with columns not present in all rows."""
        from thisisneat.session._validate_instances import _analyze_raw_schema as analyze_raw_schema

        rows = [
            Row(key="row1", columns={"common": "value1", "sparse1": "a"}),
            Row(key="row2", columns={"common": "value2", "sparse2": "b"}),
            Row(key="row3", columns={"common": "value3"}),
        ]

        schema = analyze_raw_schema(rows)

        assert schema["common"]["present_in"] == 3
        assert schema["sparse1"]["present_in"] == 1
        assert schema["sparse2"]["present_in"] == 1

    def test_analyze_schema_mixed_types(self) -> None:
        """Test schema analysis with mixed types (fallback to string)."""
        from thisisneat.session._validate_instances import _analyze_raw_schema as analyze_raw_schema

        rows = [
            Row(key="row1", columns={"mixed_col": 42}),  # int
            Row(key="row2", columns={"mixed_col": "string_value"}),  # string
        ]

        schema = analyze_raw_schema(rows)

        # Should fallback to string when multiple types detected
        assert schema["mixed_col"]["type"] == "string"

    def test_generate_shacl_from_schema(self) -> None:
        """Test SHACL rule generation from discovered schema."""
        from thisisneat.session._validate_instances import _generate_shacl_from_schema as generate_shacl_from_schema

        schema = {
            "device_id": {"type": "string", "present_in": 100, "nullable": False},
            "temperature": {"type": "float", "present_in": 98, "nullable": False},  # Non-nullable for type constraint
            "active": {"type": "boolean", "present_in": 100, "nullable": False},
        }

        shacl_rules = generate_shacl_from_schema(
            db_name="iot",
            table_name="sensors",
            schema=schema,
            required_columns=["device_id"],
        )

        # Verify SHACL structure
        assert "@prefix sh:" in shacl_rules
        assert "@prefix xsd:" in shacl_rules
        assert "raw_iot_sensors" in shacl_rules

        # Verify column shapes
        assert "device_id" in shacl_rules
        assert "temperature" in shacl_rules
        assert "active" in shacl_rules

        # Verify required column has minCount
        assert "sh:minCount 1" in shacl_rules
        assert "device_id is required" in shacl_rules

        # Verify datatypes (note: implementation only adds datatypes for non-nullable columns)
        assert "xsd:string" in shacl_rules
        assert "xsd:boolean" in shacl_rules

    def test_generate_shacl_template_integration(self) -> None:
        """Test complete template generation flow."""
        with monkeypatch_neat_client() as client:
            # Mock raw.rows.list to return sample data
            client.raw.rows.list = Mock(
                return_value=[
                    Row(
                        key="sensor_001",
                        columns={
                            "device_id": "sensor_001",
                            "name": "Temperature Sensor 1",
                            "temperature": 22.5,
                            "humidity": 45.0,
                            "active": True,
                        },
                    ),
                    Row(
                        key="sensor_002",
                        columns={
                            "device_id": "sensor_002",
                            "name": "Temperature Sensor 2",
                            "temperature": 23.1,
                            # humidity missing in this row
                            "active": True,
                        },
                    ),
                ]
            )

            neat = NeatSession(client)

            shacl_template = neat.validate_instances.generate_shacl_template_for_raw(
                db_name="iot",
                table_name="sensors",
                sample_size=10,
                required_columns=["device_id", "name"],
                verbose=False,
            )

        # Verify template structure
        assert "@prefix sh:" in shacl_template
        assert "raw_iot_sensors" in shacl_template

        # Verify all discovered columns
        assert "device_id" in shacl_template
        assert "name" in shacl_template
        assert "temperature" in shacl_template
        assert "humidity" in shacl_template
        assert "active" in shacl_template

        # Verify required columns marked with minCount
        assert shacl_template.count("sh:minCount 1") >= 2  # At least device_id and name

        # Verify type constraints
        assert "xsd:string" in shacl_template
        assert "xsd:double" in shacl_template
        assert "xsd:boolean" in shacl_template

    def test_generate_shacl_template_empty_table(self) -> None:
        """Test template generation handles empty tables gracefully."""
        with monkeypatch_neat_client() as client:
            client.raw.rows.list = Mock(return_value=[])

            neat = NeatSession(client)

            shacl_template = neat.validate_instances.generate_shacl_template_for_raw(
                db_name="empty_db",
                table_name="empty_table",
                sample_size=10,
                verbose=False,
            )

        # Returns None when table is empty (error is logged)
        assert shacl_template is None

    def test_generate_shacl_template_no_required_columns(self) -> None:
        """Test template generation with no required columns specified."""
        with monkeypatch_neat_client() as client:
            client.raw.rows.list = Mock(
                return_value=[
                    Row(key="row1", columns={"col1": "value1", "col2": 42}),
                ]
            )

            neat = NeatSession(client)

            shacl_template = neat.validate_instances.generate_shacl_template_for_raw(
                db_name="test_db",
                table_name="test_table",
                sample_size=10,
                required_columns=None,  # No required columns
                verbose=False,
            )

        # Should not have minCount constraints
        assert "sh:minCount" not in shacl_template
        # But should have type constraints
        assert "sh:datatype" in shacl_template

    def test_generate_shacl_template_custom_sample_size(self) -> None:
        """Test template generation respects sample_size parameter."""
        with monkeypatch_neat_client() as client:
            mock_list = Mock(return_value=[Row(key="row1", columns={"col1": "value1"})])
            client.raw.rows.list = mock_list

            neat = NeatSession(client)

            neat.validate_instances.generate_shacl_template_for_raw(
                db_name="test_db",
                table_name="test_table",
                sample_size=500,
                verbose=False,
            )

        # Verify sample_size was passed to list()
        mock_list.assert_called_once()
        call_kwargs = mock_list.call_args[1]
        assert call_kwargs["limit"] == 500

    def test_analyze_schema_all_null_column(self) -> None:
        """Test schema analysis when column is always null."""
        from thisisneat.session._validate_instances import _analyze_raw_schema as analyze_raw_schema

        rows = [
            Row(key="row1", columns={"always_null": None, "normal": "value1"}),
            Row(key="row2", columns={"always_null": None, "normal": "value2"}),
        ]

        schema = analyze_raw_schema(rows)

        assert schema["always_null"]["nullable"] is True
        assert schema["always_null"]["type"] == "string"  # Default fallback

    def test_generate_shacl_xsd_type_mapping(self) -> None:
        """Test correct XSD type mapping in generated SHACL."""
        from thisisneat.session._validate_instances import _generate_shacl_from_schema as generate_shacl_from_schema

        schema = {
            "string_field": {"type": "string", "present_in": 1, "nullable": False},
            "int_field": {"type": "int", "present_in": 1, "nullable": False},
            "float_field": {"type": "float", "present_in": 1, "nullable": False},
            "bool_field": {"type": "boolean", "present_in": 1, "nullable": False},
        }

        shacl_rules = generate_shacl_from_schema("db", "table", schema, None)

        # Verify XSD type mappings
        assert "xsd:string" in shacl_rules
        assert "xsd:integer" in shacl_rules
        assert "xsd:double" in shacl_rules
        assert "xsd:boolean" in shacl_rules

    def test_generate_shacl_message_customization(self) -> None:
        """Test that generated SHACL includes helpful error messages."""
        from thisisneat.session._validate_instances import _generate_shacl_from_schema as generate_shacl_from_schema

        schema = {
            "required_field": {"type": "string", "present_in": 10, "nullable": False},
        }

        shacl_rules = generate_shacl_from_schema(
            "db",
            "table",
            schema,
            required_columns=["required_field"],
        )

        # Verify error messages are included
        assert "sh:message" in shacl_rules
        assert "required_field is required" in shacl_rules
        assert "must be of type string" in shacl_rules
