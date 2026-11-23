"""Bootstrap for the standalone lane A demo.

Karishma's backend package, renamed from `backend` so it cannot be confused
with, or shadowed by, the lane C backend at the repository root. Her module
contents are unchanged apart from the import lines, which were repointed at
the vendored copies of her code under ai/karishma/.

Two things happen here, and nothing else.

The repository root goes on sys.path, so `ai.karishma` resolves when uvicorn
is started from this folder.

The demo defaults to its own mocks. This is a demo of her front end, not a
route to anybody's money, so MOCK_AI and her fixture catalogue are switched on
unless the person running it has deliberately set them otherwise. Her
shop.py will not reach Shopify without SHOPIFY_STORE_DOMAIN and
SHOPIFY_STOREFRONT_TOKEN, and nothing here sets those.

Note that her app.py calls load_dotenv() at import time, which is her code and
is left alone. If you have a populated .env at the repository root, run this
demo from a shell where you do not mind it being read.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# setdefault, so an explicit choice in the environment still wins.
os.environ.setdefault("MOCK_AI", "1")
os.environ.setdefault("ETHYNEON_FIXTURE_CATALOG", "1")
