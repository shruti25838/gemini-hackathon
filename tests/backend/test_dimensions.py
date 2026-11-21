"""The dimension extractor: plain code, conservative, never a guess.

Shop MCP carries no structured dimension field, so these strings are the only
source. A wrong parse becomes a wrong fit claim, so the extractor is required
to return nothing when it is not sure.
"""

from __future__ import annotations

import pytest

from backend.dimensions import dimensions_from_metafields, extract_dimensions

pytestmark = [pytest.mark.backend, pytest.mark.commerce]


# ==========================================================================
# What it should read
# ==========================================================================


def test_a_metric_triple() -> None:
    dims = extract_dimensions("Size: 80 x 40 x 72 cm")
    assert dims is not None
    assert dims.width_m == 0.8
    assert dims.depth_m == 0.4
    assert dims.height_m == 0.72


def test_an_imperial_triple() -> None:
    dims = extract_dimensions('Overall 31.5" x 15.7" x 28.3"')
    assert dims is not None
    assert dims.width_m == pytest.approx(0.8, abs=0.01)
    assert dims.depth_m == pytest.approx(0.399, abs=0.01)


def test_the_real_shop_mcp_option_label() -> None:
    """Exactly the string the live server returned for the Garvee desk."""
    dims = extract_dimensions('31.5"W x 15.7"D')
    assert dims is not None
    assert dims.width_m == pytest.approx(0.8, abs=0.01)
    assert dims.depth_m == pytest.approx(0.399, abs=0.01)
    assert dims.height_m is None


def test_the_real_shop_mcp_description_sentence() -> None:
    html = (
        "<p><strong>Size:</strong> 31.5\" x 15.7\" x 28.3\" (80 x 40 x 72 cm)<br>"
        "<strong>Materials:</strong> Wood &amp; Metal</p>"
    )
    dims = extract_dimensions(html)
    assert dims is not None
    assert dims.width_m == pytest.approx(0.8, abs=0.01)


def test_labelled_single_axes() -> None:
    dims = extract_dimensions("Width: 120 cm. Depth: 60 cm. Height: 74 cm.")
    assert dims is not None
    assert dims.width_m == 1.2
    assert dims.depth_m == 0.6
    assert dims.height_m == 0.74


def test_height_only_gives_a_partial_result() -> None:
    dims = extract_dimensions("Height: 42 cm")
    assert dims is not None
    assert dims.height_m == 0.42
    assert dims.width_m is None
    assert dims.has_footprint is False


def test_millimetres_and_metres_both_work() -> None:
    assert extract_dimensions("1200 x 600 x 740 mm").width_m == 1.2
    assert extract_dimensions("1.2 x 0.6 x 0.74 m").width_m == 1.2


def test_the_times_symbol_is_handled() -> None:
    dims = extract_dimensions("80 × 40 × 72 cm")
    assert dims is not None and dims.width_m == 0.8


def test_html_is_stripped_before_parsing() -> None:
    dims = extract_dimensions("<li><b>80</b> x <b>40</b> x <b>72</b> cm</li>")
    assert dims is not None
    assert dims.width_m == 0.8


def test_sources_are_tried_in_order_and_the_first_hit_wins() -> None:
    """Option labels are more reliable than sales copy, so they go first."""
    dims = extract_dimensions('31.5"W x 15.7"D', "Size: 200 x 100 x 90 cm")
    assert dims.width_m == pytest.approx(0.8, abs=0.01)


def test_a_later_source_is_used_when_the_first_has_nothing() -> None:
    dims = extract_dimensions("Natural", "Size: 80 x 40 x 72 cm")
    assert dims is not None and dims.width_m == 0.8


# ==========================================================================
# What it must refuse
# ==========================================================================


def test_nothing_at_all_returns_none() -> None:
    assert extract_dimensions(None) is None
    assert extract_dimensions("") is None
    assert extract_dimensions("A lovely desk in natural wood") is None


def test_numbers_with_no_unit_are_not_dimensions() -> None:
    """"Fits 2 x 3 people" is not a size, and guessing would be worse than
    returning unknown."""
    assert extract_dimensions("Seats 2 x 3 people comfortably") is None


def test_an_implausibly_large_value_is_rejected() -> None:
    """A 90 metre desk means the parse went wrong."""
    assert extract_dimensions("9000 x 4000 x 7200 cm") is None


def test_an_implausibly_small_value_is_rejected() -> None:
    assert extract_dimensions("1 x 1 x 1 mm") is None


def test_an_unknown_unit_is_rejected() -> None:
    assert extract_dimensions("80 x 40 x 72 furlongs") is None


def test_everything_it_produces_is_labelled_estimated() -> None:
    """Inferred dimensions cap a fit result at tentative, so the label matters."""
    dims = extract_dimensions("Size: 80 x 40 x 72 cm")
    assert dims.evidence_status == "estimated"
    assert dims.raw_text is not None


# ==========================================================================
# Metafields, which are authoritative
# ==========================================================================


def test_plain_number_metafields_are_metres() -> None:
    dims = dimensions_from_metafields("1.2", "0.6", "0.74")
    assert dims is not None
    assert dims.width_m == 1.2
    assert dims.evidence_status == "confirmed"


def test_shopify_dimension_type_metafields_carry_their_unit() -> None:
    dims = dimensions_from_metafields(
        '{"value": 120, "unit": "cm"}',
        '{"value": 60, "unit": "cm"}',
        '{"value": 740, "unit": "mm"}',
    )
    assert dims is not None
    assert dims.width_m == 1.2
    assert dims.depth_m == 0.6
    assert dims.height_m == 0.74


def test_missing_metafields_give_none_rather_than_zero() -> None:
    assert dimensions_from_metafields(None, None, None) is None


def test_a_partial_set_of_metafields_still_returns_what_it_has() -> None:
    dims = dimensions_from_metafields("1.2", None, None)
    assert dims is not None
    assert dims.width_m == 1.2
    assert dims.depth_m is None
    assert dims.has_footprint is False


def test_an_unparseable_metafield_is_dropped_not_guessed() -> None:
    assert dimensions_from_metafields("about a metre", None, None) is None


def test_metafields_are_confirmed_and_inferred_text_is_not() -> None:
    """The distinction that decides whether a fit result is tentative."""
    assert dimensions_from_metafields("1.2", "0.6", "0.74").evidence_status == "confirmed"
    assert extract_dimensions("120 x 60 x 74 cm").evidence_status == "estimated"
