from unittest.mock import Mock

from cognite.client.data_classes import Row
from rdflib import RDF, Literal

from thisisneat.core._client.testing import monkeypatch_neat_client
from thisisneat.core._instances.extractors import RAWExtractor


class TestRAWExtractor:
    def test_extract_triples(self) -> None:
        with monkeypatch_neat_client() as client:
            client.raw.rows.return_value = [
                Row(key="key1", columns={"column1": "value1", "column2": "value2"}),
                Row(key="key2", columns={"column1": "value3", "column2": "value4"}),
            ]
        extractor = RAWExtractor(client, "my_db", "my_table")
        ns = extractor.namespace

        triples = set(extractor.extract())

        assert triples == {
            (ns["key1"], RDF.type, ns["my_table"]),
            (ns["key1"], ns["column1"], Literal("value1")),
            (ns["key1"], ns["column2"], Literal("value2")),
            (ns["key2"], RDF.type, ns["my_table"]),
            (ns["key2"], ns["column1"], Literal("value3")),
            (ns["key2"], ns["column2"], Literal("value4")),
        }

    def test_extract_with_cursor(self) -> None:
        """Test extraction using cursor for historic/partitioned processing."""
        with monkeypatch_neat_client() as client:
            # Mock _config.project attribute
            mock_config = Mock()
            mock_config.project = "test_project"
            client._config = mock_config

            # Mock REST API response
            client.get = Mock(
                return_value={
                    "items": [
                        {"key": "key1", "columns": {"col1": "val1"}, "lastUpdatedTime": 1000},
                        {"key": "key2", "columns": {"col1": "val2"}, "lastUpdatedTime": 2000},
                    ],
                    "nextCursor": None,  # No more pages
                }
            )

            extractor = RAWExtractor(client, "my_db", "my_table", cursor="test_cursor_123")
            ns = extractor.namespace

            triples = set(extractor.extract())

        assert triples == {
            (ns["key1"], RDF.type, ns["my_table"]),
            (ns["key1"], ns["col1"], Literal("val1")),
            (ns["key2"], RDF.type, ns["my_table"]),
            (ns["key2"], ns["col1"], Literal("val2")),
        }

        # Verify REST API was called with cursor
        client.get.assert_called_once()
        call_args = client.get.call_args
        assert "cursor" in call_args[1]["params"]
        assert call_args[1]["params"]["cursor"] == "test_cursor_123"

    def test_extract_with_timestamp_filtering(self) -> None:
        """Test extraction with timestamp-based incremental processing."""
        with monkeypatch_neat_client() as client:
            # Mock _config.project attribute
            mock_config = Mock()
            mock_config.project = "test_project"
            client._config = mock_config

            # Mock REST API response
            client.get = Mock(
                return_value={
                    "items": [
                        {"key": "key1", "columns": {"col1": "val1"}, "lastUpdatedTime": 5000},
                    ],
                    "nextCursor": None,
                }
            )

            extractor = RAWExtractor(
                client,
                "my_db",
                "my_table",
                min_last_updated_time=1000,
                max_last_updated_time=10000,
            )
            ns = extractor.namespace

            triples = set(extractor.extract())

        assert (ns["key1"], ns["col1"], Literal("val1")) in triples

        # Verify REST API was called with timestamps
        client.get.assert_called_once()
        call_args = client.get.call_args
        assert call_args[1]["params"]["minLastUpdatedTime"] == 1000
        assert call_args[1]["params"]["maxLastUpdatedTime"] == 10000

    def test_extract_with_pagination(self) -> None:
        """Test extraction handles pagination with nextCursor."""
        with monkeypatch_neat_client() as client:
            # Mock _config.project attribute
            mock_config = Mock()
            mock_config.project = "test_project"
            client._config = mock_config

            # Mock REST API with multiple pages
            client.get = Mock(
                side_effect=[
                    {
                        "items": [
                            {"key": "key1", "columns": {"col1": "val1"}, "lastUpdatedTime": 1000},
                        ],
                        "nextCursor": "cursor_page2",
                    },
                    {
                        "items": [
                            {"key": "key2", "columns": {"col1": "val2"}, "lastUpdatedTime": 2000},
                        ],
                        "nextCursor": None,  # Last page
                    },
                ]
            )

            extractor = RAWExtractor(client, "my_db", "my_table", cursor="initial_cursor", limit=2)
            ns = extractor.namespace

            triples = set(extractor.extract())

        # Should have rows from both pages
        assert (ns["key1"], ns["col1"], Literal("val1")) in triples
        assert (ns["key2"], ns["col1"], Literal("val2")) in triples

        # Verify pagination happened
        assert client.get.call_count == 2

    def test_extract_with_limit(self) -> None:
        """Test extraction respects limit parameter."""
        with monkeypatch_neat_client() as client:
            # Mock _config.project attribute
            mock_config = Mock()
            mock_config.project = "test_project"
            client._config = mock_config

            # Mock REST API with more rows than limit
            client.get = Mock(
                return_value={
                    "items": [
                        {"key": f"key{i}", "columns": {"col1": f"val{i}"}, "lastUpdatedTime": i * 1000}
                        for i in range(100)
                    ],
                    "nextCursor": "more_data",
                }
            )

            extractor = RAWExtractor(client, "my_db", "my_table", limit=10)

            triples = list(extractor.extract())

        # Should only extract 10 rows (each row generates 2 triples: type + property)
        # 10 rows * 2 triples per row = 20 triples
        assert len(triples) == 20
