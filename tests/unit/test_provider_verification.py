"""Unit tests for provider evidence verification contracts and correlation (Slice E2.2a).

The correlation component is pure: these tests need no database and no provider.
"""

from __future__ import annotations

import ast
import dataclasses
import itertools
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from app.main import app
from app.payments.execution_evidence import ExecutionAttemptRecord, ExecutionEvidenceRecord
from app.payments.provider_verification import (
    SOURCE_CAPABILITIES,
    AttemptBinding,
    AttemptCorrelationAssessment,
    AttemptCorrelationContext,
    CorrelationResult,
    ProviderIdentity,
    ProviderObservation,
    assess_attempt_correlation,
    correlate_observation,
    observation_from_evidence,
)
from app.policy.tool_policy import AGENT_FORBIDDEN_TOOLS, CURRENT_MCP_TOOLS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BINDING = AttemptBinding(
    attempt_id="attempt-1", claim_id="claim-1", approval_id="approval-1", payload_hash="a" * 64
)
OTHER_BINDING = AttemptBinding(
    attempt_id="attempt-2", claim_id="claim-2", approval_id="approval-2", payload_hash="b" * 64
)
CHECKOUT = "ws_CO_191220191020363925"
MERCHANT = "29115-34620561-1"
RECEIPT = "NLJ7RT61SV"
DARAJA_MPESA = ProviderIdentity()


def _context(
    binding: AttemptBinding = BINDING,
    *,
    checkout: frozenset[str] = frozenset({CHECKOUT}),
    merchant: frozenset[str] = frozenset({MERCHANT}),
    identity: ProviderIdentity = DARAJA_MPESA,
) -> AttemptCorrelationContext:
    return AttemptCorrelationContext(
        binding=binding,
        identity=identity,
        checkout_request_ids=checkout,
        merchant_request_ids=merchant,
    )


def _callback(**overrides: Any) -> ProviderObservation:
    values: dict[str, Any] = {
        "source": "provider_callback",
        "result_code": "0",
        "checkout_request_id": CHECKOUT,
        "merchant_request_id": MERCHANT,
        "settlement_id": RECEIPT,
    }
    values.update(overrides)
    return ProviderObservation(**values)


def _initiation(**overrides: Any) -> ProviderObservation:
    values: dict[str, Any] = {
        "source": "provider_initiation_response",
        "result_code": "0",
        "checkout_request_id": CHECKOUT,
        "merchant_request_id": MERCHANT,
        # The existing adapter repeats the CheckoutRequestID as provider_transaction_id.
        "settlement_id": CHECKOUT,
    }
    values.update(overrides)
    return ProviderObservation(**values)


def _status_query(**overrides: Any) -> ProviderObservation:
    values: dict[str, Any] = {
        "source": "provider_status_query",
        "result_code": "0",
        "checkout_request_id": CHECKOUT,
        "settlement_id": CHECKOUT,
    }
    values.update(overrides)
    return ProviderObservation(**values)


def _assert_never_an_outcome(result: CorrelationResult | AttemptCorrelationAssessment) -> None:
    assert result.completion_established is False
    assert result.eligibility.eligible is False
    assert result.eligibility.reasons


# --- Single-observation correlation ----------------------------------------------------


def test_valid_single_attempt_correlation() -> None:
    result = correlate_observation(_callback(), [_context()])

    assert result.status == "correlated"
    assert result.reasons == ("matched_checkout_request_id",)
    assert result.attempt == BINDING
    _assert_never_an_outcome(result)


@pytest.mark.parametrize("checkout", [None, ""])
def test_missing_checkout_request_id_is_uncorrelated(checkout: str | None) -> None:
    result = correlate_observation(_callback(checkout_request_id=checkout), [_context()])

    assert result.status == "uncorrelated"
    assert result.reasons == ("missing_checkout_request_id",)
    assert result.attempt is None
    assert result.eligibility.reasons == ("not_correlated",)


def test_merchant_request_id_alone_never_correlates() -> None:
    observation = _callback(checkout_request_id=None, merchant_request_id=MERCHANT)

    assert correlate_observation(observation, [_context()]).status == "uncorrelated"


@pytest.mark.parametrize(
    "checkout",
    ["ws_CO_000000000000000000", f" {CHECKOUT}", f"{CHECKOUT} ", CHECKOUT.lower()],
    ids=["different", "leading-space", "trailing-space", "re-cased"],
)
def test_incorrect_checkout_request_id_is_not_corrected(checkout: str) -> None:
    result = correlate_observation(_callback(checkout_request_id=checkout), [_context()])

    assert result.status == "uncorrelated"
    assert result.reasons == ("no_matching_attempt",)


def test_merchant_request_id_mismatch_conflicts() -> None:
    result = correlate_observation(_callback(merchant_request_id="other-merchant"), [_context()])

    assert result.status == "conflicting"
    assert result.reasons == ("merchant_request_id_mismatch",)
    assert result.attempt == BINDING
    _assert_never_an_outcome(result)


def test_missing_merchant_request_id_still_correlates_on_checkout() -> None:
    result = correlate_observation(_callback(merchant_request_id=None), [_context()])

    assert result.status == "correlated"


def test_ambiguous_match_is_refused() -> None:
    result = correlate_observation(_callback(), [_context(), _context(OTHER_BINDING)])

    assert result.status == "ambiguous"
    assert result.reasons == ("multiple_matching_attempts",)
    assert result.attempt is None


@pytest.mark.parametrize(
    "binding",
    [
        OTHER_BINDING,
        dataclasses.replace(BINDING, approval_id="approval-2"),
        dataclasses.replace(BINDING, claim_id="claim-2"),
        dataclasses.replace(BINDING, payload_hash="c" * 64),
    ],
    ids=["other-attempt", "other-approval", "other-claim", "other-payload-hash"],
)
def test_wrong_approval_or_claim_binding_conflicts(binding: AttemptBinding) -> None:
    result = correlate_observation(_callback(binding=binding), [_context()])

    assert result.status == "conflicting"
    assert result.reasons == ("binding_mismatch",)


def test_provider_or_rail_mismatch_is_uncorrelated() -> None:
    for identity in (ProviderIdentity(provider="other"), ProviderIdentity(rail="card")):
        result = correlate_observation(_callback(identity=identity), [_context()])
        assert result.status == "uncorrelated"
        assert result.reasons == ("provider_or_rail_mismatch",)


def test_identifier_reused_across_approvals_is_ambiguous_not_guessed() -> None:
    # There is no tenant model; two approvals sharing a request identifier must never
    # be resolved by picking one.
    contexts = [_context(), _context(OTHER_BINDING)]

    for observation in (_callback(), _initiation(), _status_query()):
        assert correlate_observation(observation, contexts).status == "ambiguous"


def test_attempt_without_owner_initiation_identifiers_is_unresolved() -> None:
    context = _context(checkout=frozenset(), merchant=frozenset())

    result = correlate_observation(_callback(), [context])

    assert result.status == "uncorrelated"
    assert result.reasons == ("attempt_initiation_identifiers_unknown",)


def test_attempt_with_conflicting_initiation_identifiers_conflicts() -> None:
    context = _context(checkout=frozenset({CHECKOUT, "ws_CO_second"}))

    result = correlate_observation(_callback(), [context])

    assert result.status == "conflicting"
    assert "attempt_initiation_identifiers_conflict" in result.reasons


@pytest.mark.parametrize("make", [_initiation, _status_query], ids=["initiation", "status-query"])
def test_non_callback_source_cannot_carry_a_settlement_identifier(make: Any) -> None:
    assert correlate_observation(make(), [_context()]).status == "correlated"  # alias only

    result = correlate_observation(make(settlement_id=RECEIPT), [_context()])

    assert result.status == "conflicting"
    assert result.reasons == ("non_settlement_source_carries_settlement_identifier",)


def test_unsupported_source_is_refused() -> None:
    with pytest.raises(ValueError, match="Unsupported observation source"):
        ProviderObservation(source="execution_worker")  # type: ignore[arg-type]


# --- Trust boundaries --------------------------------------------------------------------


def test_no_source_is_authoritative_for_completion() -> None:
    assert set(SOURCE_CAPABILITIES) == {
        "provider_initiation_response",
        "provider_callback",
        "provider_status_query",
    }
    for capability in SOURCE_CAPABILITIES.values():
        assert capability.authoritative_for_completion is False
        assert "no_authoritative_verification_mechanism" in capability.ineligibility


def test_unverified_callback_is_correlated_but_not_eligible() -> None:
    result = correlate_observation(_callback(), [_context()])

    assert result.source_capability.authentication == "sender_authenticated_not_provider"
    assert result.source_capability.establishes == "reported_result_unauthenticated"
    assert "source_not_provider_authenticated" in result.eligibility.reasons
    _assert_never_an_outcome(result)


def test_initiation_acknowledgment_is_not_completion() -> None:
    result = correlate_observation(_initiation(result_code="0"), [_context()])
    assessment = assess_attempt_correlation(_context(), [_initiation(result_code="0")])

    assert result.status == "correlated"
    assert result.source_capability.establishes == "request_acknowledgment"
    assert "source_establishes_acknowledgment_only" in result.eligibility.reasons
    assert assessment.status == "correlated"
    assert assessment.settlement_ids == frozenset()
    _assert_never_an_outcome(result)
    _assert_never_an_outcome(assessment)


def test_status_query_response_is_query_acceptance_only() -> None:
    result = correlate_observation(_status_query(), [_context()])

    assert result.source_capability.establishes == "query_acceptance_only"
    assert "status_query_result_not_ingested" in result.eligibility.reasons
    _assert_never_an_outcome(result)


def test_results_expose_no_payment_outcome_field() -> None:
    for model in (CorrelationResult, AttemptCorrelationAssessment):
        names = {item.name for item in dataclasses.fields(model)}
        assert not names & {"success", "succeeded", "paid", "verified", "payment_status"}


# --- Attempt-level assessment ------------------------------------------------------------


def test_duplicate_observations_collapse() -> None:
    assessment = assess_attempt_correlation(_context(), [_callback()] * 3 + [_initiation()] * 2)

    assert assessment.status == "correlated"
    assert assessment.distinct_observations == 2
    assert assessment.duplicate_observations == 3
    assert assessment.settlement_ids == frozenset({RECEIPT})
    assert assessment.sources == frozenset({"provider_callback", "provider_initiation_response"})
    _assert_never_an_outcome(assessment)


MISBINDINGS = [
    OTHER_BINDING,
    dataclasses.replace(BINDING, attempt_id="attempt-2"),
    dataclasses.replace(BINDING, claim_id="claim-2"),
    dataclasses.replace(BINDING, approval_id="approval-2"),
    dataclasses.replace(BINDING, payload_hash="c" * 64),
]
MISBINDING_IDS = ["other-attempt", "attempt-id", "claim-id", "approval-id", "payload-hash"]


@pytest.mark.parametrize("misbound", MISBINDINGS, ids=MISBINDING_IDS)
def test_misbound_duplicate_conflicts_in_either_order(misbound: AttemptBinding) -> None:
    correct = _callback(binding=BINDING)
    wrong = _callback(binding=misbound)

    assert correct.identity_key != wrong.identity_key
    for order in ([correct, wrong], [wrong, correct]):
        assessment = assess_attempt_correlation(_context(), order)
        assert assessment.status == "conflicting", order
        assert "binding_mismatch" in assessment.reasons
        assert assessment.distinct_observations == 2
        assert assessment.duplicate_observations == 0
        _assert_never_an_outcome(assessment)


def test_identical_bound_observations_still_collapse() -> None:
    bound = [_callback(binding=BINDING), _callback(binding=BINDING), _callback(binding=BINDING)]

    assessment = assess_attempt_correlation(_context(), bound)

    assert assessment.status == "correlated"
    assert assessment.distinct_observations == 1
    assert assessment.duplicate_observations == 2


def test_binding_aware_assessment_is_independent_of_arrival_order() -> None:
    observations = [
        _initiation(binding=BINDING),
        _callback(binding=BINDING),
        _callback(binding=BINDING),
        _callback(binding=dataclasses.replace(BINDING, claim_id="claim-2")),
    ]

    results = {
        assess_attempt_correlation(_context(), list(order))
        for order in itertools.permutations(observations)
    }

    assert len(results) == 1
    result = next(iter(results))
    assert result.status == "conflicting"
    assert result.reasons == ("binding_mismatch",)
    assert (result.distinct_observations, result.duplicate_observations) == (3, 1)


@pytest.mark.parametrize("checkout", [None, ""], ids=["none", "empty"])
@pytest.mark.parametrize("misbound", MISBINDINGS, ids=MISBINDING_IDS)
def test_misbound_observation_without_checkout_id_conflicts(
    misbound: AttemptBinding, checkout: str | None
) -> None:
    observation = _callback(binding=misbound, checkout_request_id=checkout)

    single = correlate_observation(observation, [_context()])
    assert single.status == "conflicting"
    assert single.reasons == ("binding_mismatch",)
    _assert_never_an_outcome(single)

    valid = _callback(binding=BINDING)
    for order in ([valid, observation], [observation, valid]):
        assessment = assess_attempt_correlation(_context(), order)
        assert assessment.status == "conflicting", order
        assert "binding_mismatch" in assessment.reasons
        _assert_never_an_outcome(assessment)


@pytest.mark.parametrize("checkout", [None, ""], ids=["none", "empty"])
def test_correctly_bound_observation_without_checkout_id_stays_unresolved(
    checkout: str | None,
) -> None:
    observation = _callback(binding=BINDING, checkout_request_id=checkout)

    assert correlate_observation(observation, [_context()]).reasons == (
        "missing_checkout_request_id",
    )
    assessment = assess_attempt_correlation(_context(), [observation, observation])
    assert assessment.status == "unresolved"
    assert assessment.reasons == ("missing_checkout_request_id",)
    assert assessment.duplicate_observations == 1


def test_binding_first_assessment_is_independent_of_arrival_order() -> None:
    observations = [
        _callback(binding=BINDING),
        _callback(binding=BINDING),
        _callback(binding=BINDING, checkout_request_id=None),
        _callback(binding=OTHER_BINDING, checkout_request_id=None),
    ]

    results = {
        assess_attempt_correlation(_context(), list(order))
        for order in itertools.permutations(observations)
    }

    assert len(results) == 1
    result = next(iter(results))
    assert result.status == "conflicting"
    assert "binding_mismatch" in result.reasons
    assert (result.distinct_observations, result.duplicate_observations) == (3, 1)


def test_ambiguity_is_preserved_without_a_unique_expected_attempt() -> None:
    contexts = [_context(), _context(OTHER_BINDING)]

    assert correlate_observation(_callback(), contexts).status == "ambiguous"
    assert correlate_observation(_callback(binding=BINDING), contexts).status == "ambiguous"


def test_bound_initiation_rejection_without_checkout_id_conflicts_in_any_order() -> None:
    observations = [
        _initiation(binding=BINDING, result_code="0"),
        _callback(binding=BINDING),
        _initiation(binding=BINDING, result_code="1", checkout_request_id=None, settlement_id=None),
    ]

    results = {
        assess_attempt_correlation(_context(), list(order))
        for order in itertools.permutations(observations)
    }

    assert len(results) == 1
    result = next(iter(results))
    assert result.status == "conflicting"
    assert "conflicting_initiation_result_codes" in result.reasons
    _assert_never_an_outcome(result)


def test_bound_initiation_rejection_cannot_be_hidden_by_a_correlated_callback() -> None:
    rejection = _initiation(
        binding=BINDING, result_code="1", checkout_request_id=None, settlement_id=None
    )

    assessment = assess_attempt_correlation(_context(), [_callback(binding=BINDING), rejection])

    assert assessment.status == "conflicting"
    assert "callback_after_rejected_initiation" in assessment.reasons


@pytest.mark.parametrize("checkout", [None, ""], ids=["none", "empty"])
def test_bound_callback_without_checkout_id_contradicting_result_conflicts(
    checkout: str | None,
) -> None:
    contradiction = _callback(
        binding=BINDING, result_code="1032", settlement_id=None, checkout_request_id=checkout
    )

    for order in (
        [_callback(binding=BINDING), contradiction],
        [contradiction, _callback(binding=BINDING)],
    ):
        assessment = assess_attempt_correlation(_context(), order)
        assert assessment.status == "conflicting"
        assert "conflicting_callback_result_codes" in assessment.reasons


def test_bound_callback_without_checkout_id_with_other_receipt_conflicts() -> None:
    other = _callback(binding=BINDING, checkout_request_id=None, settlement_id="OTHERRCPT")

    assessment = assess_attempt_correlation(_context(), [_callback(binding=BINDING), other])

    assert assessment.status == "conflicting"
    assert "conflicting_settlement_identifiers" in assessment.reasons


def test_missing_id_evidence_never_establishes_correlation_or_sources() -> None:
    alone = assess_attempt_correlation(
        _context(),
        [
            _callback(binding=BINDING, checkout_request_id=None),
            _initiation(binding=BINDING, checkout_request_id=None, settlement_id=None),
        ],
    )
    with_callback = assess_attempt_correlation(
        _context(),
        [
            _callback(binding=BINDING),
            _initiation(binding=BINDING, checkout_request_id=None, settlement_id=None),
        ],
    )

    assert alone.status == "unresolved"
    assert alone.sources == frozenset()
    assert with_callback.status == "correlated"
    assert with_callback.sources == frozenset({"provider_callback"})


def test_misbound_contradiction_does_not_feed_consistency_checks() -> None:
    misbound = _callback(
        binding=OTHER_BINDING,
        result_code="1032",
        settlement_id="OTHERRCPT",
        checkout_request_id=None,
    )

    assessment = assess_attempt_correlation(_context(), [_callback(binding=BINDING), misbound])

    assert assessment.status == "conflicting"
    assert assessment.reasons == ("binding_mismatch",)
    assert assessment.settlement_ids == frozenset({RECEIPT})


@pytest.mark.parametrize("checkout", [None, ""], ids=["none", "empty"])
def test_bound_missing_checkout_with_conflicting_merchant_id_conflicts(
    checkout: str | None,
) -> None:
    wrong = _callback(binding=BINDING, checkout_request_id=checkout, merchant_request_id="mr-x")

    alone = assess_attempt_correlation(_context(), [wrong])
    assert alone.status == "conflicting"
    assert "merchant_request_id_mismatch" in alone.reasons
    for order in ([_callback(binding=BINDING), wrong], [wrong, _callback(binding=BINDING)]):
        assessment = assess_attempt_correlation(_context(), order)
        assert assessment.status == "conflicting"
        assert "merchant_request_id_mismatch" in assessment.reasons


@pytest.mark.parametrize("merchant", [MERCHANT, None], ids=["matching", "absent"])
def test_bound_missing_checkout_without_merchant_conflict_stays_unresolved(
    merchant: str | None,
) -> None:
    observation = _callback(binding=BINDING, checkout_request_id=None, merchant_request_id=merchant)

    assessment = assess_attempt_correlation(_context(), [observation])

    assert assessment.status == "unresolved"
    assert assessment.reasons == ("missing_checkout_request_id",)
    assert assessment.sources == frozenset()


@pytest.mark.parametrize("make", [_initiation, _status_query], ids=["initiation", "status-query"])
def test_bound_non_callback_without_checkout_carrying_receipt_conflicts(make: Any) -> None:
    improper = make(binding=BINDING, checkout_request_id=None, settlement_id=RECEIPT)
    alias = make(binding=BINDING, checkout_request_id=None, settlement_id=CHECKOUT)

    assessment = assess_attempt_correlation(_context(), [_callback(binding=BINDING), improper])
    assert assessment.status == "conflicting"
    assert "non_settlement_source_carries_settlement_identifier" in assessment.reasons

    accepted = assess_attempt_correlation(_context(), [_callback(binding=BINDING), alias])
    assert accepted.status == "correlated"
    assert accepted.reasons == ("missing_checkout_request_id",)


def test_bound_observations_with_unknown_attempt_identifiers_are_checked() -> None:
    context = _context(checkout=frozenset(), merchant=frozenset())
    cases = {
        "conflicting_callback_result_codes": [
            _callback(binding=BINDING),
            _callback(binding=BINDING, result_code="1032", settlement_id=None),
        ],
        "conflicting_initiation_result_codes": [
            _initiation(binding=BINDING, result_code="0"),
            _initiation(binding=BINDING, result_code="1"),
        ],
        "conflicting_settlement_identifiers": [
            _callback(binding=BINDING),
            _callback(binding=BINDING, settlement_id="OTHERRCPT"),
        ],
        "callback_after_rejected_initiation": [
            _initiation(binding=BINDING, result_code="1"),
            _callback(binding=BINDING),
        ],
    }
    for reason, observations in cases.items():
        assessment = assess_attempt_correlation(context, observations)
        assert assessment.status == "conflicting", reason
        assert reason in assessment.reasons
        assert assessment.sources == frozenset()

    consistent = assess_attempt_correlation(
        context, [_callback(binding=BINDING), _initiation(binding=BINDING)]
    )
    assert consistent.status == "unresolved"
    assert consistent.reasons == ("attempt_initiation_identifiers_unknown",)
    assert consistent.sources == frozenset()
    _assert_never_an_outcome(consistent)


def test_misbound_merchant_conflict_is_only_a_binding_mismatch() -> None:
    misbound = _callback(
        binding=OTHER_BINDING, checkout_request_id=None, merchant_request_id="mr-x"
    )

    assessment = assess_attempt_correlation(_context(), [_callback(binding=BINDING), misbound])

    assert assessment.reasons == ("binding_mismatch",)


def test_bound_missing_id_duplicates_collapse() -> None:
    observation = _callback(binding=BINDING, checkout_request_id=None)

    assessment = assess_attempt_correlation(_context(), [observation] * 3)

    assert (assessment.distinct_observations, assessment.duplicate_observations) == (1, 2)


def test_comprehensive_consistency_is_independent_of_arrival_order() -> None:
    observations = [
        _initiation(binding=BINDING),
        _callback(binding=BINDING),
        _callback(binding=BINDING, checkout_request_id=None, merchant_request_id="mr-x"),
        _status_query(binding=BINDING, checkout_request_id=None, settlement_id=RECEIPT),
        _callback(binding=OTHER_BINDING, checkout_request_id=None),
    ]

    results = {
        assess_attempt_correlation(_context(), list(order))
        for order in itertools.permutations(observations)
    }

    assert len(results) == 1
    result = next(iter(results))
    assert result.status == "conflicting"
    assert {
        "binding_mismatch",
        "merchant_request_id_mismatch",
        "non_settlement_source_carries_settlement_identifier",
    } <= set(result.reasons)
    _assert_never_an_outcome(result)


def _both_orders(*observations: ProviderObservation) -> AttemptCorrelationAssessment:
    results = {
        assess_attempt_correlation(_context(), list(order))
        for order in itertools.permutations(observations)
    }
    assert len(results) == 1
    return next(iter(results))


@pytest.mark.parametrize("code", [None, ""], ids=["none", "empty"])
def test_absent_initiation_code_is_not_a_rejection(code: str | None) -> None:
    result = _both_orders(
        _initiation(binding=BINDING, result_code=code), _callback(binding=BINDING)
    )

    assert result.status == "correlated"
    assert result.reasons == ()
    _assert_never_an_outcome(result)


@pytest.mark.parametrize("code", [None, ""], ids=["none", "empty"])
def test_absent_code_does_not_conflict_with_an_explicit_code(code: str | None) -> None:
    initiation = _both_orders(
        _initiation(binding=BINDING, result_code="0"),
        _initiation(binding=BINDING, result_code=code, settlement_id=None),
    )
    callback = _both_orders(
        _callback(binding=BINDING, result_code="0"),
        _callback(binding=BINDING, result_code=code),
    )

    assert initiation.status == "correlated"
    assert callback.status == "correlated"


def test_absent_code_beside_a_real_rejection_still_reports_the_rejection() -> None:
    result = _both_orders(
        _initiation(binding=BINDING, result_code="1"),
        _initiation(binding=BINDING, result_code=None, settlement_id=None),
        _callback(binding=BINDING),
    )

    assert result.status == "conflicting"
    assert "callback_after_rejected_initiation" in result.reasons
    assert "conflicting_initiation_result_codes" not in result.reasons


def test_distinct_explicit_codes_still_conflict() -> None:
    initiation = _both_orders(
        _initiation(binding=BINDING, result_code="0"),
        _initiation(binding=BINDING, result_code="1", settlement_id=None),
    )
    callback = _both_orders(
        _callback(binding=BINDING, result_code="0"),
        _callback(binding=BINDING, result_code="1032", settlement_id=None),
    )

    assert "conflicting_initiation_result_codes" in initiation.reasons
    assert "conflicting_callback_result_codes" in callback.reasons


@pytest.mark.parametrize(
    "identity",
    [ProviderIdentity(provider="other"), ProviderIdentity(rail="card")],
    ids=["provider", "rail"],
)
def test_bound_identity_mismatch_without_checkout_id_conflicts(identity: ProviderIdentity) -> None:
    mismatched = _callback(binding=BINDING, checkout_request_id=None, identity=identity)

    single = correlate_observation(mismatched, [_context()])
    result = _both_orders(_callback(binding=BINDING), mismatched)

    assert single.reasons == ("provider_or_rail_mismatch",)
    assert single.status != "correlated"
    assert result.status == "conflicting"
    assert "provider_or_rail_mismatch" in result.reasons
    assert result.sources == frozenset({"provider_callback"})


def test_identity_mismatch_without_checkout_id_contributes_no_result_codes() -> None:
    mismatched = _callback(
        binding=BINDING,
        checkout_request_id=None,
        identity=ProviderIdentity(provider="other"),
        result_code="1032",
        settlement_id="OTHERRCPT",
    )

    result = _both_orders(_callback(binding=BINDING), mismatched)

    assert result.reasons == ("provider_or_rail_mismatch",)
    assert result.settlement_ids == frozenset({RECEIPT})


def test_matching_identity_without_checkout_id_stays_unresolved() -> None:
    observation = _callback(binding=BINDING, checkout_request_id=None, identity=DARAJA_MPESA)

    result = assess_attempt_correlation(_context(), [observation])

    assert result.status == "unresolved"
    assert result.reasons == ("missing_checkout_request_id",)


def test_identity_mismatch_with_several_candidates_is_unchanged() -> None:
    contexts = [_context(), _context(OTHER_BINDING)]

    assert correlate_observation(_callback(), contexts).status == "ambiguous"
    mismatched = _callback(identity=ProviderIdentity(provider="other"))
    assert correlate_observation(mismatched, contexts).reasons == ("provider_or_rail_mismatch",)


RECORDED_AT = datetime(2026, 11, 26, tzinfo=UTC)
ATTEMPT_RECORD = ExecutionAttemptRecord(
    attempt_id=BINDING.attempt_id,
    claim_id=BINDING.claim_id,
    approval_id=BINDING.approval_id,
    payload_hash=BINDING.payload_hash,
    provider="daraja",
    rail="mpesa",
    idempotency_key=None,
    recorded_by="worker-1",
    correlation_id=None,
    recorded_at=RECORDED_AT,
)


def _initiation_record(
    sequence: int, *, checkout: str | None, merchant: str | None, code: str = "0"
) -> ExecutionEvidenceRecord:
    return ExecutionEvidenceRecord(
        evidence_id=f"evidence-{sequence}",
        attempt_id=BINDING.attempt_id,
        claim_id=BINDING.claim_id,
        approval_id=BINDING.approval_id,
        payload_hash=BINDING.payload_hash,
        sequence=sequence,
        evidence_type="provider_response_observed",
        evidence_source="provider_initiation_response",
        verification_level="unverified",
        observation_key=f"provider_initiation_response:initiation-{sequence}",
        uncertainty_reason=None,
        result_code=code,
        result_description=None,
        checkout_request_id=checkout,
        merchant_request_id=merchant,
        provider_transaction_id=checkout,
        recorded_at=RECORDED_AT,
    )


def _from_records(*records: ExecutionEvidenceRecord) -> tuple[Any, list[ProviderObservation]]:
    context = AttemptCorrelationContext.from_records(ATTEMPT_RECORD, records)
    observations = [observation_from_evidence(r, identity=DARAJA_MPESA) for r in records]
    return context, observations


def _every_order(
    context: AttemptCorrelationContext, observations: list[ProviderObservation]
) -> AttemptCorrelationAssessment:
    results = {
        assess_attempt_correlation(context, list(order))
        for order in itertools.permutations(observations)
    }
    assert len(results) == 1
    return next(iter(results))


@pytest.mark.parametrize("checkout", [None, ""], ids=["none", "empty"])
def test_conflicting_merchant_ids_without_checkout_conflict(checkout: str | None) -> None:
    context, observations = _from_records(
        _initiation_record(1, checkout=checkout, merchant="mr-a"),
        _initiation_record(2, checkout=checkout, merchant="mr-b"),
    )

    assert context.identifiers_conflict is True
    result = _every_order(context, observations)
    assert result.status == "conflicting"
    assert "attempt_initiation_identifiers_conflict" in result.reasons
    assert result.sources == frozenset()
    _assert_never_an_outcome(result)
    single = correlate_observation(observations[0], [context])
    assert single.status == "conflicting"
    assert single.reasons == ("attempt_initiation_identifiers_conflict",)


def test_conflicting_checkout_ids_conflict_with_or_without_correlation() -> None:
    context, observations = _from_records(
        _initiation_record(1, checkout="ws_CO_a", merchant=MERCHANT),
        _initiation_record(2, checkout="ws_CO_b", merchant=MERCHANT),
    )
    callback = _callback(binding=BINDING, checkout_request_id="ws_CO_a")

    for observed in (observations, [*observations, callback], []):
        result = _every_order(context, observed)
        assert result.status == "conflicting", observed
        assert "attempt_initiation_identifiers_conflict" in result.reasons
        _assert_never_an_outcome(result)


def test_context_conflict_with_only_uncorrelated_observations() -> None:
    context = _context(merchant=frozenset({MERCHANT, "mr-other"}))
    unidentified = _callback(binding=BINDING, checkout_request_id=None)
    unbound = _callback(checkout_request_id=None)

    result = _every_order(context, [unidentified, unbound])

    assert result.status == "conflicting"
    assert "attempt_initiation_identifiers_conflict" in result.reasons
    assert result.sources == frozenset()


def test_consistent_initiation_without_checkout_stays_unresolved() -> None:
    context, observations = _from_records(
        _initiation_record(1, checkout=None, merchant=MERCHANT),
        _initiation_record(2, checkout=None, merchant=MERCHANT, code="0"),
    )

    assert context.identifiers_conflict is False
    result = _every_order(context, observations)
    assert result.status == "unresolved"
    assert "attempt_initiation_identifiers_conflict" not in result.reasons
    assert result.sources == frozenset()


def test_repeated_callbacks_are_not_independent_corroboration() -> None:
    assessment = assess_attempt_correlation(_context(), [_callback(), _callback()])

    assert assessment.sources == frozenset({"provider_callback"})
    assert assessment.distinct_observations == 1


def test_assessment_is_independent_of_arrival_order() -> None:
    observations = [
        _initiation(),
        _callback(),
        _status_query(),
        _callback(settlement_id="OTHERRCPT"),
    ]

    results = {
        assess_attempt_correlation(_context(), list(order))
        for order in itertools.permutations(observations)
    }

    assert len(results) == 1
    assert next(iter(results)).status == "conflicting"


def test_conflicting_settlement_identifiers() -> None:
    assessment = assess_attempt_correlation(
        _context(), [_callback(), _callback(settlement_id="OTHERRCPT")]
    )

    assert assessment.status == "conflicting"
    assert "conflicting_settlement_identifiers" in assessment.reasons
    assert assessment.settlement_ids == frozenset({RECEIPT, "OTHERRCPT"})


def test_conflicting_callback_result_codes() -> None:
    assessment = assess_attempt_correlation(
        _context(), [_callback(), _callback(result_code="1032", settlement_id=None)]
    )

    assert assessment.status == "conflicting"
    assert "conflicting_callback_result_codes" in assessment.reasons


def test_callback_after_rejected_initiation_conflicts() -> None:
    assessment = assess_attempt_correlation(_context(), [_initiation(result_code="1"), _callback()])

    assert assessment.status == "conflicting"
    assert "callback_after_rejected_initiation" in assessment.reasons


def test_observation_for_a_different_request_conflicts() -> None:
    assessment = assess_attempt_correlation(
        _context(), [_callback(), _callback(checkout_request_id="ws_CO_other")]
    )

    assert assessment.status == "conflicting"
    assert "observation_for_different_request" in assessment.reasons


def test_no_or_unusable_observations_are_unresolved() -> None:
    empty = assess_attempt_correlation(_context(), [])
    missing = assess_attempt_correlation(_context(), [_callback(checkout_request_id=None)])

    assert empty.status == "unresolved"
    assert empty.reasons == ("no_observations",)
    assert missing.status == "unresolved"
    assert missing.reasons == ("missing_checkout_request_id",)
    for assessment in (empty, missing):
        _assert_never_an_outcome(assessment)


# --- Isolation ---------------------------------------------------------------------------


def test_module_is_pure_and_not_exposed() -> None:
    source = REPOSITORY_ROOT / "app" / "payments" / "provider_verification.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert imported <= {
        "__future__",
        "collections.abc",
        "dataclasses",
        "typing",
        "app.payments.execution_evidence",
    }

    tool_names = {*CURRENT_MCP_TOOLS, *AGENT_FORBIDDEN_TOOLS}
    assert not any("verif" in name or "correlat" in name for name in tool_names)
    assert not any(
        "verif" in getattr(route, "path", "") or "correlat" in getattr(route, "path", "")
        for route in app.routes
    )
    for path in (REPOSITORY_ROOT / "app").rglob("*.py"):
        if path.name == "provider_verification.py":
            continue
        assert "provider_verification" not in path.read_text(encoding="utf-8"), path
