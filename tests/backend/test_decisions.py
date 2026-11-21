"""The Decisions API client and its labelled local stand-in.

No network. The live path is exercised with a fake transport so the request
and response shapes are pinned to what the hackathon guide documents.
"""

from __future__ import annotations

import pytest

from ai.model_ids import DECISION_QUESTION_TYPES, MODEL_IDS
from contracts.common import Money
from contracts.interfaces import AuditInputs, BudgetLine
from backend.decisions import (
    CHECKOUT_QUESTIONS,
    DecisionsClient,
    DecisionsUnavailable,
    run_checkout_gates,
)

pytestmark = pytest.mark.backend


def packet(
    *,
    approved: list[tuple[str, int, int]],
    live: list[tuple[str, int, int]] | None = None,
    fit_status: str = "fits",
    budget: int | None = 120000,
) -> AuditInputs:
    def lines(items):
        return [
            BudgetLine(
                variant_id=v,
                quantity=q,
                unit_price=Money(amount_minor=p, currency="USD"),
            )
            for v, q, p in items
        ]

    approved_lines = lines(approved)
    subtotal = sum(l.unit_price.amount_minor * l.quantity for l in approved_lines)
    return AuditInputs(
        session_id="sess_test",
        scene_revision=7,
        brief_revision=2,
        approved_lines=approved_lines,
        merchandise_subtotal=Money(amount_minor=subtotal, currency="USD"),
        budget=Money(amount_minor=budget, currency="USD") if budget else None,
        hard_constraints=["keep_door_clear"],
        fit_status=fit_status,
        live_cart_lines=lines(live) if live is not None else approved_lines,
        approval_fingerprint="sha256:aaa",
        cart_fingerprint="sha256:bbb",
    )


# ==========================================================================
# The question set, as the guide documents it
# ==========================================================================


def test_the_three_question_types_are_the_documented_ones() -> None:
    assert DECISION_QUESTION_TYPES == ("noul", "choice", "score")


def test_the_yes_no_gates_use_the_noul_type() -> None:
    """noul is the Decisions API yes/no type, verified in the builder guide."""
    assert CHECKOUT_QUESTIONS["purchase_scope_matches"]["type"] == "noul"
    assert CHECKOUT_QUESTIONS["required_constraints_resolved"]["type"] == "noul"


def test_the_routing_gate_is_a_choice_with_the_prd_options() -> None:
    question = CHECKOUT_QUESTIONS["next_action"]
    assert question["type"] == "choice"
    assert set(question["criteria"]) == {
        "ready",
        "refresh_cart",
        "repair_plan",
        "ask_shopper",
        "hold_checkout",
    }


def test_the_decisions_model_id_is_the_verified_one() -> None:
    assert MODEL_IDS["decisions"] == "gemini-decision-1"


def test_the_client_targets_the_v1beta_endpoint() -> None:
    client = DecisionsClient("placeholder-key")
    assert client.endpoint.endswith("/v1beta/decisions")
    assert client.model == "gemini-decision-1"


def test_a_client_with_no_key_is_unavailable() -> None:
    client = DecisionsClient(None)
    assert client.available is False
    with pytest.raises(DecisionsUnavailable):
        client.create([], {})


# ==========================================================================
# The live response shape
# ==========================================================================


class FakeDecisions(DecisionsClient):
    """Returns a canned body in the documented shape, with no network."""

    def __init__(self, body: dict) -> None:
        super().__init__("fake-key")
        self._body = body
        self.calls: list[dict] = []

    def create(self, inputs, questions):  # type: ignore[override]
        self.calls.append({"input": inputs, "questions": questions})
        return self._body


def ready_body() -> dict:
    return {
        "answers": {
            "purchase_scope_matches": {"noul": 0.96, "confidence": 0.94},
            "required_constraints_resolved": {"noul": 0.93, "confidence": 0.91},
            "next_action": {"choice": "ready", "confidence": 0.92},
        }
    }


def test_a_confident_yes_passes() -> None:
    client = FakeDecisions(ready_body())
    answers = run_checkout_gates(packet(approved=[("v1", 1, 42900)]), client=client)
    assert answers.all_passed is True
    assert {a.source for a in answers.answers} == {"live"}


def test_the_request_carries_the_documented_question_shape() -> None:
    client = FakeDecisions(ready_body())
    run_checkout_gates(packet(approved=[("v1", 1, 42900)]), client=client)
    sent = client.calls[0]["questions"]
    assert sent["purchase_scope_matches"]["type"] == "noul"
    assert "instructions" in sent["purchase_scope_matches"]
    assert sent["next_action"]["type"] == "choice"


def test_a_noul_probability_below_half_reads_as_no() -> None:
    body = ready_body()
    body["answers"]["purchase_scope_matches"] = {"noul": 0.12, "confidence": 0.95}
    answers = run_checkout_gates(
        packet(approved=[("v1", 1, 42900)]), client=FakeDecisions(body)
    )
    scope = next(a for a in answers.answers if a.question == "purchase_scope_matches")
    assert scope.answer == "no"
    assert scope.passed is False
    assert answers.all_passed is False


def test_a_confident_yes_with_low_confidence_does_not_pass() -> None:
    """Confidence is read in code. A high probability is not enough."""
    body = ready_body()
    body["answers"]["purchase_scope_matches"] = {"noul": 0.99, "confidence": 0.31}
    answers = run_checkout_gates(
        packet(approved=[("v1", 1, 42900)]), client=FakeDecisions(body)
    )
    scope = next(a for a in answers.answers if a.question == "purchase_scope_matches")
    assert scope.answer == "yes"
    assert scope.passed is False


def test_a_non_ready_route_does_not_pass() -> None:
    body = ready_body()
    body["answers"]["next_action"] = {"choice": "ask_shopper", "confidence": 0.97}
    answers = run_checkout_gates(
        packet(approved=[("v1", 1, 42900)]), client=FakeDecisions(body)
    )
    assert answers.all_passed is False


def test_a_missing_answer_is_an_outage_not_a_pass() -> None:
    body = {"answers": {"purchase_scope_matches": {"noul": 0.9, "confidence": 0.9}}}
    with pytest.raises(DecisionsUnavailable):
        run_checkout_gates(packet(approved=[("v1", 1, 42900)]), client=FakeDecisions(body))


# ==========================================================================
# The local stand-in
# ==========================================================================


def test_the_stand_in_is_labelled_on_every_answer() -> None:
    answers = run_checkout_gates(packet(approved=[("v1", 1, 42900)]))
    assert {a.source for a in answers.answers} == {"local_stand_in"}


def test_the_stand_in_passes_a_clean_cart() -> None:
    answers = run_checkout_gates(packet(approved=[("v1", 1, 42900)]))
    assert answers.all_passed is True


def test_the_stand_in_refuses_when_the_live_cart_differs() -> None:
    answers = run_checkout_gates(
        packet(approved=[("v1", 1, 42900)], live=[("v2", 1, 42900)])
    )
    assert answers.all_passed is False
    route = next(a for a in answers.answers if a.question == "next_action")
    assert route.answer == "refresh_cart"


def test_the_stand_in_refuses_a_failing_fit() -> None:
    answers = run_checkout_gates(
        packet(approved=[("v1", 1, 42900)], fit_status="does_not_fit")
    )
    assert answers.all_passed is False
    route = next(a for a in answers.answers if a.question == "next_action")
    assert route.answer == "repair_plan"


def test_the_stand_in_refuses_an_unknown_fit() -> None:
    answers = run_checkout_gates(packet(approved=[("v1", 1, 42900)], fit_status="unknown"))
    assert answers.all_passed is False


def test_the_stand_in_refuses_over_budget() -> None:
    answers = run_checkout_gates(packet(approved=[("v1", 5, 42900)], budget=50000))
    assert answers.all_passed is False


def test_the_stand_in_never_claims_full_confidence() -> None:
    """A rule is not a calibrated probability, and must not look like one."""
    answers = run_checkout_gates(packet(approved=[("v1", 1, 42900)]))
    assert all(a.confidence < 1.0 for a in answers.answers)


def test_the_stand_in_can_be_switched_off_entirely() -> None:
    with pytest.raises(DecisionsUnavailable):
        run_checkout_gates(packet(approved=[("v1", 1, 42900)]), use_stand_in=False)
