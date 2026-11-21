"""Shared literal types (team integration contract: Shared conventions)."""
from typing import Literal

WallId = Literal["wall_n", "wall_e", "wall_s", "wall_w"]
# Evidence status per contract, plus labels the UI must show (preset / inferred / catalog_fact / default)
Source = Literal["estimated", "confirmed", "unknown", "sampled_from_photo", "inferred", "catalog_fact", "default", "preset"]
FitStatus = Literal["fits", "tight", "does_not_fit", "unknown"]
