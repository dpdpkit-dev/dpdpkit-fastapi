"""FastAPI adapter for dpdpkit: router, SQLAlchemy repository, migrations, scheduler, admin mount.

dpdpkit helps you implement DPDP obligations; it does not provide legal advice or guarantee compliance.
"""

from __future__ import annotations

from .app import ActivityMiddleware, DPDPKit, require_consent
from .http import ConsentRequiredHTTP
from .repository import SqlAlchemyRepository, make_engine

__version__ = "0.1.0.dev0"

__all__ = [
    "ActivityMiddleware",
    "ConsentRequiredHTTP",
    "DPDPKit",
    "SqlAlchemyRepository",
    "__version__",
    "make_engine",
    "require_consent",
]
