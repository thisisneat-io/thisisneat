import urllib.parse
from collections.abc import Iterable, Set
from typing import Any, cast

from cognite.client.data_classes import Row, RowList
from cognite.client.utils.useful_types import SequenceNotStr
from rdflib import RDF, Namespace, URIRef

from thisisneat.core._client import NeatClient
from thisisneat.core._constants import DEFAULT_RAW_URI
from thisisneat.core._shared import Triple

from ._base import BaseExtractor
from ._dict import DEFAULT_EMPTY_VALUES, DictExtractor


class RAWExtractor(BaseExtractor):
    def __init__(
        self,
        client: NeatClient,
        db_name: str,
        table_name: str,
        table_type: str | None = None,
        foreign_keys: str | SequenceNotStr[str] | None = None,
        namespace: Namespace | None = None,
        empty_values: Set[str] = DEFAULT_EMPTY_VALUES,
        str_to_ideal_type: bool = False,
        unpack_json: bool = False,
        # Processing modes (mutually exclusive per CDF API)
        cursor: str | None = None,
        min_last_updated_time: int | None = None,
        max_last_updated_time: int | None = None,
        limit: int | None = None,
    ) -> None:
        self.client = client
        self.db_name = db_name
        self.table_name = table_name
        self.table_type = table_type
        self.foreign_keys = {foreign_keys} if isinstance(foreign_keys, str) else set(foreign_keys or [])
        self.namespace = namespace or Namespace(DEFAULT_RAW_URI)
        self.empty_values = empty_values
        self.str_to_ideal_type = str_to_ideal_type
        self.unpack_json = unpack_json
        self.cursor = cursor
        self.min_last_updated_time = min_last_updated_time
        self.max_last_updated_time = max_last_updated_time
        self.limit = limit

    @property
    def _rdf_type(self) -> URIRef:
        return self.namespace[urllib.parse.quote(self.table_type or self.table_name)]

    def extract(self) -> Iterable[Triple]:
        """
        Extract rows using either cursor (historic) or timestamp (incremental) mode.

        Cursor mode: Used for partitioned historic processing (getCursors API)
        Timestamp mode: Used for incremental processing (minLastUpdatedTime parameter)

        Note: cursor and timestamp modes are mutually exclusive per CDF API.
        When cursor is specified, timestamp parameters are ignored.
        """
        if self.cursor or self.min_last_updated_time or self.max_last_updated_time or self.limit:
            # Use new filtering mode (cursor or timestamp)
            rows = self._fetch_filtered_rows()
        else:
            # Legacy mode: use existing partitioned fetch
            rows = self.client.raw.rows(self.db_name, self.table_name, partitions=10, chunk_size=None)

        for row in rows:
            if isinstance(row, Row):
                yield from self._row2triples(row)
            elif isinstance(row, RowList):
                # Bug in SDK returning row list with chunk_size= None
                for item in row:
                    yield from self._row2triples(item)

    def _fetch_filtered_rows(self) -> Iterable[Row | RowList]:
        """
        Fetch rows with cursor or timestamp filtering using REST API.

        SDK's raw.rows.list() doesn't expose cursor parameter, so we use REST API directly.
        Handles pagination by following nextCursor until all rows are fetched.

        API endpoints:
        - GET /api/v1/projects/{project}/raw/dbs/{db}/tables/{table}/rows
        """
        url_path = (
            f"/api/v1/projects/{self.client._config.project}/raw/dbs/{self.db_name}/tables/{self.table_name}/rows"
        )

        # Initial parameters
        params = {}
        if self.cursor:
            # Cursor mode: Historic/partitioned processing
            # Note: timestamp params are ignored when cursor is specified (per CDF API docs)
            params["cursor"] = self.cursor
        else:
            # Timestamp mode: Incremental processing
            if self.min_last_updated_time:
                params["minLastUpdatedTime"] = self.min_last_updated_time
            if self.max_last_updated_time:
                params["maxLastUpdatedTime"] = self.max_last_updated_time

        if self.limit:
            params["limit"] = self.limit

        # Paginate through results using nextCursor
        rows_fetched = 0
        while True:
            # Use client's GET method (similar to how SDK makes API calls internally)
            response = self.client.get(url_path, params=params)

            # Handle both Response objects (real API) and dicts (mocked)
            if isinstance(response, dict):
                res = response
            else:
                res = response.json()

            # Convert API response to Row objects and yield them
            items = res.get("items", [])
            for row_data in items:
                # Check limit if specified
                if self.limit and rows_fetched >= self.limit:
                    return

                yield Row(
                    key=row_data["key"],
                    columns=row_data["columns"],
                    last_updated_time=row_data.get("lastUpdatedTime"),
                )
                rows_fetched += 1

            # Check for next page
            next_cursor = res.get("nextCursor")
            if not next_cursor:
                break

            # Update cursor for next iteration
            params["cursor"] = next_cursor

    def _row2triples(self, row: Row) -> Iterable[Triple]:
        # The row is always set. It is just the PySDK that have it as str | None
        key, data = cast(tuple[str, dict[str, Any]], (row.key, row.columns))
        identifier = self.namespace[urllib.parse.quote(key)]
        yield identifier, RDF.type, self._rdf_type

        yield from DictExtractor(
            identifier,
            data,
            self.namespace,
            self.foreign_keys,
            self.empty_values,
            self.str_to_ideal_type,
            self.unpack_json,
        ).extract()
