"""Adapter between Karishma's lane A and our lane C.

Her code lives untouched in `ai/karishma/` apart from import lines, which are
listed in docs/LANE_A_ADAPTER.md. Nothing of hers reaches `contracts/`.

Three rules this adapter exists to keep:
  lane A only proposes; only our commit_revision writes
  store, shoppable, dimension_source and availability are re-derived from our
    catalog by variant_id whenever anything crosses the boundary
  our check_fit is the only fit verdict; her solver only proposes placements
"""
