"""Fixtures for the lane A adapter tests.

Reuses the backend fixtures, because the adapter is tested against our real
services rather than against doubles.
"""

from tests.backend.conftest import (  # noqa: F401
    _no_ambient_credentials,
    db,
    services,
    session_id,
    settings,
)
