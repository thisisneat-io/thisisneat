from typing import Any

import pandas as pd
import pytest

from tests.config import DOC_RULES
from tests.data import SchemaData
from thisisneat.core._client.data_classes.schema import DMSSchema
from thisisneat.core._data_model.importers import ExcelImporter
from thisisneat.core._data_model.models import (
    ConceptualDataModel,
    PhysicalDataModel,
)
from thisisneat.core._data_model.models.physical import UnverifiedPhysicalDataModel
from thisisneat.core._utils.spreadsheet import read_individual_sheet


@pytest.fixture(autouse=True)
def cleanup_cdf_sparql_functions():
    """
    Cleanup CDF SPARQL functions after each test to prevent pollution.

    The CDF SPARQL functions are registered globally in rdflib's CUSTOM_EVALS,
    which can interfere with unrelated tests that use SPARQL queries.
    """
    yield
    # Cleanup after test
    try:
        from thisisneat.core._cdf_sparql_functions import unregister_cdf_sparql_functions

        unregister_cdf_sparql_functions()
    except ImportError:
        pass  # Module not available, nothing to cleanup


@pytest.fixture(scope="session")
def alice_spreadsheet() -> dict[str, dict[str, Any]]:
    filepath = DOC_RULES / "cdf-dms-architect-alice.xlsx"
    excel_file = pd.ExcelFile(filepath)
    return {
        "Metadata": dict(pd.read_excel(excel_file, "Metadata", header=None).values),
        "Properties": read_individual_sheet(excel_file, "Properties", False, ["View Property"]),
        "Views": read_individual_sheet(excel_file, "Views", False, ["View"]),
        "Containers": read_individual_sheet(excel_file, "Containers", False, ["Container"]),
    }


@pytest.fixture(scope="session")
def alice_rules(alice_spreadsheet: dict[str, dict[str, Any]]) -> PhysicalDataModel:
    return UnverifiedPhysicalDataModel.load(alice_spreadsheet).as_verified_data_model()


@pytest.fixture(scope="session")
def david_rules() -> ConceptualDataModel:
    return (
        ExcelImporter(DOC_RULES / "information-architect-david.xlsx")
        .to_data_model()
        .unverified_data_model.as_verified_data_model()
    )


@pytest.fixture(scope="session")
def david_spreadsheet(david_rules) -> dict[str, dict[str, Any]]:
    return david_rules.dump()


@pytest.fixture(scope="session")
def svein_harald_dms_rules() -> PhysicalDataModel:
    return (
        ExcelImporter(DOC_RULES / "dms-addition-svein-harald.xlsx")
        .to_data_model()
        .unverified_data_model.as_verified_data_model()
    )


@pytest.fixture(scope="session")
def olav_rebuild_dms_rules() -> PhysicalDataModel:
    return (
        ExcelImporter(DOC_RULES / "dms-rebuild-olav.xlsx")
        .to_data_model()
        .unverified_data_model.as_verified_data_model()
    )


@pytest.fixture(scope="session")
def cognite_core_schema() -> DMSSchema:
    return DMSSchema.from_zip(SchemaData.NonNeatFormats.cognite_core_v1_zip)
