"""
Integration tests for RAW table SHACL validation API.

These tests verify:
1. Basic SHACL validation of RAW rows
2. SHACL validation with violations detected
3. Value constraint validation (ranges, patterns)
4. Cursor-based historic processing
5. Timestamp-based incremental processing
6. SHACL template generation from RAW table schema
"""

import pytest
from cognite.client import CogniteClient
from cognite.client.data_classes import Row

from thisisneat import NeatSession


class TestRawSHACLValidation:
    """Test SHACL validation API for RAW tables."""

    @pytest.fixture
    def test_raw_table(self, cognite_client: CogniteClient) -> tuple[str, str]:
        """Create a test RAW table with test data."""
        db_name = "neat_test_db"
        table_name = "shacl_validation_test"

        # Ensure database exists
        try:
            cognite_client.raw.databases.create(db_name)
        except Exception:
            pass  # Database already exists

        # Ensure table exists and populate with test data
        try:
            cognite_client.raw.tables.create(db_name, table_name)
        except Exception:
            pass  # Table already exists

        # Insert test rows
        test_rows = [
            Row(
                key="sensor_001",
                columns={
                    "device_id": "sensor_001",
                    "name": "Temperature Sensor 1",
                    "temperature": 22.5,
                    "humidity": 45.0,
                    "status": "active",
                },
            ),
            Row(
                key="sensor_002",
                columns={
                    "device_id": "sensor_002",
                    "name": "Temperature Sensor 2",
                    "temperature": 23.1,
                    "humidity": 48.0,
                    "status": "active",
                },
            ),
            Row(
                key="sensor_invalid",
                columns={
                    # Missing required device_id
                    "name": "Invalid Sensor",
                    "temperature": 150.0,  # Out of valid range
                    "status": "error",
                },
            ),
        ]

        cognite_client.raw.rows.insert(db_name, table_name, test_rows, ensure_parent=True)

        yield db_name, table_name

        # Cleanup
        try:
            cognite_client.raw.tables.delete(db_name, table_name)
        except Exception:
            pass

    def test_validate_raw_passing(self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]) -> None:
        """Test SHACL validation with RAW rows - expects both valid and invalid records."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        # SHACL rules requiring device_id and valid temperature range
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix raw: <http://purl.org/cognite/raw/{db_name}/{table_name}/> .

            raw:DeviceIdRequiredShape a sh:NodeShape ;
                sh:targetClass raw:{table_name} ;
                sh:property [
                    sh:path raw:device_id ;
                    sh:minCount 1 ;
                    sh:message "device_id is required" ;
                ] .

            raw:TemperatureRangeShape a sh:NodeShape ;
                sh:targetClass raw:{table_name} ;
                sh:property [
                    sh:path raw:temperature ;
                    sh:datatype xsd:double ;
                    sh:minInclusive -40.0 ;
                    sh:maxInclusive 100.0 ;
                    sh:message "temperature must be between -40 and 100" ;
                ] .
        """

        # Validate all rows (2 valid, 1 invalid)
        result = neat.validate_instances.with_shacl_raw(
            db_name=db_name,
            table_name=table_name,
            shacl_rules=shacl_rules,
            verbose=False,
        )

        # Validation should fail overall due to sensor_invalid
        assert result.conforms is False, f"Validation should fail due to invalid row. Report: {result.report_text}"

        # Check that expected violations are reported
        report_lower = result.report_text.lower()
        assert "device_id" in report_lower or "mincount" in report_lower, (
            f"Report should mention device_id violation: {result.report_text}"
        )
        assert "temperature" in report_lower or "maxinclusive" in report_lower, (
            f"Report should mention temperature violation: {result.report_text}"
        )

        # Check that sensor_invalid is the focus node in violations
        assert "sensor_invalid" in result.report_text, f"Report should identify sensor_invalid: {result.report_text}"

    def test_validate_raw_failing(self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]) -> None:
        """Test SHACL validation with RAW rows that violate rules."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        # SHACL rules requiring device_id
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix raw: <http://purl.org/cognite/raw/{db_name}/{table_name}/> .

            raw:DeviceIdRequiredShape a sh:NodeShape ;
                sh:targetClass raw:{table_name} ;
                sh:property [
                    sh:path raw:device_id ;
                    sh:minCount 1 ;
                    sh:message "device_id is required" ;
                ] .
        """

        # Validate all rows (including the invalid one)
        result = neat.validate_instances.with_shacl_raw(
            db_name=db_name,
            table_name=table_name,
            shacl_rules=shacl_rules,
            verbose=False,
        )

        assert result.conforms is False, "Validation should fail due to missing device_id"
        assert "device_id" in result.report_text.lower() or "mincount" in result.report_text.lower(), (
            f"Report should mention the device_id violation: {result.report_text}"
        )

    def test_validate_raw_value_constraint(
        self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]
    ) -> None:
        """Test SHACL validation with value constraints (temperature range)."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        # SHACL rules with strict temperature range
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
            @prefix raw: <http://purl.org/cognite/raw/{db_name}/{table_name}/> .

            raw:TemperatureRangeShape a sh:NodeShape ;
                sh:targetClass raw:{table_name} ;
                sh:property [
                    sh:path raw:temperature ;
                    sh:datatype xsd:double ;
                    sh:minInclusive -40.0 ;
                    sh:maxInclusive 100.0 ;
                    sh:message "temperature must be between -40 and 100" ;
                ] .
        """

        # Validate all rows (sensor_invalid has temperature 150.0)
        result = neat.validate_instances.with_shacl_raw(
            db_name=db_name,
            table_name=table_name,
            shacl_rules=shacl_rules,
            verbose=False,
        )

        assert result.conforms is False, "Validation should fail for out-of-range temperature"
        assert "temperature" in result.report_text.lower() or "maxinclusive" in result.report_text.lower()

    def test_validate_raw_with_cursor(self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]) -> None:
        """Test cursor-based validation (historic/partitioned processing)."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        # Get cursors for partitioned processing
        cursors_response = cognite_client.raw.rows.get_cursors(db_name=db_name, table_name=table_name, num_partitions=2)

        # Validate using first cursor
        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix raw: <http://purl.org/cognite/raw/{db_name}/{table_name}/> .

            raw:NameRequiredShape a sh:NodeShape ;
                sh:targetClass raw:{table_name} ;
                sh:property [
                    sh:path raw:name ;
                    sh:minCount 1 ;
                ] .
        """

        result = neat.validate_instances.with_shacl_raw(
            db_name=db_name,
            table_name=table_name,
            shacl_rules=shacl_rules,
            cursor=cursors_response.cursors[0],
            verbose=False,
        )

        # Should validate at least some rows
        assert result.report_graph is not None

    def test_validate_raw_with_timestamp_filter(
        self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]
    ) -> None:
        """Test timestamp-based incremental validation."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        # Get current time
        import time

        current_time = int(time.time() * 1000)
        one_hour_ago = current_time - (3600 * 1000)

        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix raw: <http://purl.org/cognite/raw/{db_name}/{table_name}/> .

            raw:StatusShape a sh:NodeShape ;
                sh:targetClass raw:{table_name} ;
                sh:property [
                    sh:path raw:status ;
                    sh:minCount 1 ;
                ] .
        """

        # Validate rows updated in the last hour
        result = neat.validate_instances.with_shacl_raw(
            db_name=db_name,
            table_name=table_name,
            shacl_rules=shacl_rules,
            min_last_updated_time=one_hour_ago,
            verbose=False,
        )

        # Should complete successfully (even if no rows in time range)
        assert result.report_graph is not None

    def test_generate_shacl_template_for_raw(
        self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]
    ) -> None:
        """Test SHACL template generation from RAW table schema."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        # Generate SHACL template by analyzing table schema
        shacl_template = neat.validate_instances.generate_shacl_template_for_raw(
            db_name=db_name,
            table_name=table_name,
            sample_size=10,
            required_columns=["device_id", "name"],
            verbose=False,
        )

        # Verify template contains expected elements
        assert "@prefix sh:" in shacl_template
        assert "@prefix xsd:" in shacl_template
        assert f"raw_{db_name}_{table_name}" in shacl_template
        assert "device_id" in shacl_template
        assert "name" in shacl_template
        assert "temperature" in shacl_template  # Discovered from data
        assert "sh:minCount 1" in shacl_template  # For required columns
        assert "sh:datatype" in shacl_template  # Type constraints

    def test_validate_raw_with_foreign_keys(
        self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]
    ) -> None:
        """Test validation with foreign key configuration."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix raw: <http://purl.org/cognite/raw/{db_name}/{table_name}/> .

            raw:NameShape a sh:NodeShape ;
                sh:targetClass raw:{table_name} ;
                sh:property [
                    sh:path raw:name ;
                    sh:minCount 1 ;
                ] .
        """

        result = neat.validate_instances.with_shacl_raw(
            db_name=db_name,
            table_name=table_name,
            shacl_rules=shacl_rules,
            foreign_keys=["device_id"],  # Treat device_id as foreign key
            verbose=False,
        )

        assert result.report_graph is not None

    def test_validate_raw_with_custom_table_type(
        self, cognite_client: CogniteClient, test_raw_table: tuple[str, str]
    ) -> None:
        """Test validation with custom table type URI."""
        db_name, table_name = test_raw_table
        neat = NeatSession(client=cognite_client)

        custom_type = "SensorReading"

        shacl_rules = f"""
            @prefix sh: <http://www.w3.org/ns/shacl#> .
            @prefix raw: <http://purl.org/cognite/raw/{db_name}/{table_name}/> .

            raw:SensorShape a sh:NodeShape ;
                sh:targetClass raw:{custom_type} ;
                sh:property [
                    sh:path raw:temperature ;
                    sh:minCount 1 ;
                ] .
        """

        result = neat.validate_instances.with_shacl_raw(
            db_name=db_name,
            table_name=table_name,
            shacl_rules=shacl_rules,
            table_type=custom_type,
            verbose=False,
        )

        assert result.report_graph is not None
