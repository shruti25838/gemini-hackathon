"""Bootstrap for the standalone lane A demo.

Karishma's backend package, renamed from `backend` so it cannot be confused
with, or shadowed by, the lane C backend at the repository root. Her module
contents are unchanged apart from the import lines, which were repointed at
the vendored copies of her code under ai/karishma/.

Two things happen here, and nothing else.

The repository root goes on sys.path, so `ai.karishma` resolves when uvicorn
is started from this folder.

The demo is kept off the live store. That needs more than switching her
fixture catalogue on, and the reason is worth writing down.

Her app.py calls load_dotenv() at import time. That is her code and is left
alone, but with no argument it searches upwards from the working directory
and finds the repository root's .env, so the real Shopify credentials land in
the environment whatever this file asks for. Her shop.py then checks live()
*before* fixture_mode(), so a configured store wins over the fixture
catalogue. Started naively, this demo talked to the real development store
and reported shopify_live: true.

python-dotenv does not override a variable that is already set, so blanking
the two Shopify variables here, before her import runs, is what actually
holds. An operator who wants the live store can say so explicitly with
LANE_A_DEMO_LIVE_SHOPIFY=1.
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

# Not setdefault. These have to be present and empty before load_dotenv runs,
# or the repository root's .env fills them in and her shop.py goes live.
if os.environ.get("LANE_A_DEMO_LIVE_SHOPIFY") != "1":
    os.environ["SHOPIFY_STORE_DOMAIN"] = ""
    os.environ["SHOPIFY_STOREFRONT_TOKEN"] = ""
