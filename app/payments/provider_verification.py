"""Provider evidence verification contracts and deterministic correlation (Slice E2.2a).

Pure, internal, non-executing. Nothing here calls Daraja, writes to the database, marks
a payment successful or failed, or is reachable from MCP tools, HTTP routes or the live
payment workflow. Its outputs are *correlation assessments*, never payment outcomes.

Identifiers in the existing Daraja integration (``app/daraja/client.py``,
``app/callbacks``):

* ``CheckoutRequestID`` (primary initiation identifier): returned by the STK Push
  initiation response and repeated in the STK callback. The callback handler and the
  callback replay key both key on it, so it is the primary correlation key here.
* ``MerchantRequestID`` (secondary initiation identifier): returned by initiation and
  repeated in the callback. Used only to cross-check, never to correlate on its own.
* ``MpesaReceiptNumber`` (settlement identifier): appears only in a successful STK
  callback's metadata. E2.1 stores it as ``provider_transaction_id`` on callback
  evidence. The existing adapters also fill ``provider_transaction_id`` with the
  ``CheckoutRequestID`` on initiation and status-query responses; that value is an
  initiation alias, not a settlement identifier, and is treated as such.

What each evidence source actually establishes (see ``SOURCE_CAPABILITIES``):

* Initiation response: an HTTPS response to our OAuth-authenticated request. It shows
  Daraja *acknowledged the request*; it does not establish customer payment.
* STK callback: authenticated at most by the optional ``X-Callback-Secret`` header and
  the callback source verifier (trusted proxy header). Those authenticate the immediate
  sender (for example our reverse proxy), not Safaricom; Daraja STK callbacks carry no
  provider signature in this integration.
* Transaction-status query: the synchronous response only reports query acceptance;
  the result is delivered asynchronously to ``ResultURL``, which no route ingests, and
  the request currently sends the ``CheckoutRequestID`` as ``TransactionID`` (see the
  TODO in ``RealDarajaClient._transaction_status_payload``).

Consequently no source is authoritative for completion, and every verification
eligibility is ``False`` with explicit reasons. There is no tenant model and the STK
callback carries no shortcode, so organization-level correlation is not possible beyond
the durable approval and claim binding.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.payments.execution_evidence import (
    EvidenceSource,
    ExecutionAttemptRecord,
    ExecutionEvidenceRecord,
    ProviderObservationSource,
)

CorrelationStatus = Literal["correlated", "uncorrelated", "ambiguous", "conflicting"]
SourceAuthentication = Literal[
    "tls_response_to_authenticated_request",
    "sender_authenticated_not_provider",
]
SourceEstablishes = Literal[
    "request_acknowledgment",
    "reported_result_unauthenticated",
    "query_acceptance_only",
]
CorrelationReason = Literal[
    "matched_checkout_request_id",
    "missing_checkout_request_id",
    "no_matching_attempt",
    "multiple_matching_attempts",
    "attempt_initiation_identifiers_unknown",
    "attempt_initiation_identifiers_conflict",
    "provider_or_rail_mismatch",
    "merchant_request_id_mismatch",
    "binding_mismatch",
    "non_settlement_source_carries_settlement_identifier",
]
IneligibilityReason = Literal[
    "not_correlated",
    "no_authoritative_verification_mechanism",
    "source_establishes_acknowledgment_only",
    "source_not_provider_authenticated",
    "status_query_result_not_ingested",
]
AttemptCorrelationStatus = Literal["unresolved", "correlated", "conflicting"]

INITIATION_SOURCE: ProviderObservationSource = "provider_initiation_response"
CALLBACK_SOURCE: ProviderObservationSource = "provider_callback"
STATUS_QUERY_SOURCE: ProviderObservationSource = "provider_status_query"
INITIATION_ACCEPTED_CODE = "0"
"""Daraja STK Push ``ResponseCode`` for an accepted request (acknowledgment only)."""


@dataclass(frozen=True)
class ProviderIdentity:
    """Payment provider and rail, matching the E2.1 attempt columns."""

    provider: str = "daraja"
    rail: str = "mpesa"


@dataclass(frozen=True)
class AttemptBinding:
    """The durable identity an observation must belong to (E1 claim + E2.1 attempt)."""

    attempt_id: str
    claim_id: str
    approval_id: str
    payload_hash: str


@dataclass(frozen=True)
class SourceCapability:
    """What an evidence source can and cannot establish in the current integration."""

    authentication: SourceAuthentication
    establishes: SourceEstablishes
    authoritative_for_completion: Literal[False]
    ineligibility: tuple[IneligibilityReason, ...]


SOURCE_CAPABILITIES: dict[ProviderObservationSource, SourceCapability] = {
    INITIATION_SOURCE: SourceCapability(
        authentication="tls_response_to_authenticated_request",
        establishes="request_acknowledgment",
        authoritative_for_completion=False,
        ineligibility=(
            "no_authoritative_verification_mechanism",
            "source_establishes_acknowledgment_only",
        ),
    ),
    CALLBACK_SOURCE: SourceCapability(
        authentication="sender_authenticated_not_provider",
        establishes="reported_result_unauthenticated",
        authoritative_for_completion=False,
        ineligibility=(
            "no_authoritative_verification_mechanism",
            "source_not_provider_authenticated",
        ),
    ),
    STATUS_QUERY_SOURCE: SourceCapability(
        authentication="tls_response_to_authenticated_request",
        establishes="query_acceptance_only",
        authoritative_for_completion=False,
        ineligibility=(
            "no_authoritative_verification_mechanism",
            "status_query_result_not_ingested",
        ),
    ),
}


@dataclass(frozen=True)
class ProviderObservation:
    """A provider observation to correlate, with identifiers kept exactly as received.

    Identifiers are compared exactly; nothing is trimmed, re-cased or otherwise
    corrected. Empty strings are treated as absent because the existing Daraja adapter
    emits ``""`` for a missing field. ``settlement_id`` is the ``MpesaReceiptNumber``.
    ``binding`` is set when the observation was already stored against an attempt.
    """

    source: ProviderObservationSource
    result_code: str | None = None
    checkout_request_id: str | None = None
    merchant_request_id: str | None = None
    settlement_id: str | None = None
    identity: ProviderIdentity = field(default_factory=ProviderIdentity)
    binding: AttemptBinding | None = None

    def __post_init__(self) -> None:
        if self.source not in SOURCE_CAPABILITIES:
            raise ValueError(f"Unsupported observation source: {self.source!r}")
        for name in ("result_code", "checkout_request_id", "merchant_request_id", "settlement_id"):
            if getattr(self, name) == "":
                object.__setattr__(self, name, None)

    @property
    def identity_key(self) -> tuple[object, ...]:
        """Fields that make two observations the same observation (for de-duplication).

        Includes the complete ``binding``: observations stored against different
        attempts are never merged, so every binding reaches correlation validation.
        """

        return (
            self.source,
            self.result_code,
            self.checkout_request_id,
            self.merchant_request_id,
            self.settlement_id,
            self.identity,
            self.binding,
        )


@dataclass(frozen=True)
class AttemptCorrelationContext:
    """An attempt and the initiation identifiers its owner observed for it.

    Identifiers are established only from owner-recorded initiation-response evidence
    (E2.1 requires the claim token for that source). A callback can never establish
    or change an attempt's identifiers.
    """

    binding: AttemptBinding
    identity: ProviderIdentity
    checkout_request_ids: frozenset[str]
    merchant_request_ids: frozenset[str]

    @property
    def identifiers_conflict(self) -> bool:
        return len(self.checkout_request_ids) > 1 or len(self.merchant_request_ids) > 1

    @property
    def checkout_request_id(self) -> str | None:
        if len(self.checkout_request_ids) != 1:
            return None
        return next(iter(self.checkout_request_ids))

    @classmethod
    def from_records(
        cls,
        attempt: ExecutionAttemptRecord,
        evidence: Iterable[ExecutionEvidenceRecord],
    ) -> AttemptCorrelationContext:
        """Build a context from E2.1 records; evidence of another attempt is refused."""

        binding = AttemptBinding(
            attempt_id=attempt.attempt_id,
            claim_id=attempt.claim_id,
            approval_id=attempt.approval_id,
            payload_hash=attempt.payload_hash,
        )
        checkout: set[str] = set()
        merchant: set[str] = set()
        for record in evidence:
            if _binding_of(record) != binding:
                raise ValueError("Evidence does not belong to this execution attempt.")
            if record.evidence_source != INITIATION_SOURCE:
                continue
            if record.checkout_request_id:
                checkout.add(record.checkout_request_id)
            if record.merchant_request_id:
                merchant.add(record.merchant_request_id)
        return cls(
            binding=binding,
            identity=ProviderIdentity(provider=attempt.provider, rail=attempt.rail),
            checkout_request_ids=frozenset(checkout),
            merchant_request_ids=frozenset(merchant),
        )


@dataclass(frozen=True)
class VerificationEligibility:
    """Whether an observation could be used as authoritative verification.

    Always ``False`` in E2.2a: no source in the current integration is authoritative.
    """

    eligible: Literal[False]
    reasons: tuple[IneligibilityReason, ...]


@dataclass(frozen=True)
class CorrelationResult:
    """Correlation of one observation. Never a payment outcome."""

    status: CorrelationStatus
    reasons: tuple[CorrelationReason, ...]
    attempt: AttemptBinding | None
    source_capability: SourceCapability
    eligibility: VerificationEligibility
    completion_established: Literal[False] = False


@dataclass(frozen=True)
class AttemptCorrelationAssessment:
    """Order-independent assessment of all observations for one attempt."""

    status: AttemptCorrelationStatus
    reasons: tuple[str, ...]
    distinct_observations: int
    duplicate_observations: int
    sources: frozenset[ProviderObservationSource]
    settlement_ids: frozenset[str]
    eligibility: VerificationEligibility
    completion_established: Literal[False] = False


def correlate_observation(
    observation: ProviderObservation,
    candidates: Sequence[AttemptCorrelationContext],
) -> CorrelationResult:
    """Correlate one observation to exactly one attempt, or say why it cannot be.

    Hierarchy: provider/rail must match; ``CheckoutRequestID`` is required and must
    equal exactly one candidate's established identifier; ``MerchantRequestID`` and an
    existing binding, when present, must agree; only a callback may carry a settlement
    identifier (initiation and status responses may repeat the ``CheckoutRequestID``).
    Missing data is ``uncorrelated``, several matches are ``ambiguous`` and disagreement
    is ``conflicting``; nothing is guessed or corrected.
    """

    capability = SOURCE_CAPABILITIES[observation.source]

    def result(
        status: CorrelationStatus,
        *reasons: CorrelationReason,
        attempt: AttemptBinding | None = None,
    ) -> CorrelationResult:
        ineligible: tuple[IneligibilityReason, ...] = (
            capability.ineligibility if status == "correlated" else ("not_correlated",)
        )
        return CorrelationResult(
            status=status,
            reasons=reasons,
            attempt=attempt,
            source_capability=capability,
            eligibility=VerificationEligibility(eligible=False, reasons=ineligible),
        )

    # Binding first: with a single known candidate, a stored observation bound to
    # another attempt conflicts even when its provider identifiers are missing.
    if (
        observation.binding is not None
        and len(candidates) == 1
        and observation.binding != candidates[0].binding
    ):
        return result("conflicting", "binding_mismatch", attempt=candidates[0].binding)
    # Identity next: with a single known candidate, a provider/rail mismatch is reported
    # even when the CheckoutRequestID is missing.
    if len(candidates) == 1 and observation.identity != candidates[0].identity:
        return result("uncorrelated", "provider_or_rail_mismatch")
    # Context next: a single known attempt whose own initiation identifiers conflict
    # cannot be correlated to, whatever identifiers the observation carries.
    if len(candidates) == 1 and candidates[0].identifiers_conflict:
        return result(
            "conflicting", "attempt_initiation_identifiers_conflict", attempt=candidates[0].binding
        )

    checkout = observation.checkout_request_id
    if checkout is None:
        return result("uncorrelated", "missing_checkout_request_id")

    same_provider = [c for c in candidates if c.identity == observation.identity]
    if candidates and not same_provider:
        return result("uncorrelated", "provider_or_rail_mismatch")
    matches = [c for c in same_provider if checkout in c.checkout_request_ids]
    if not matches:
        if same_provider and all(not c.checkout_request_ids for c in same_provider):
            return result("uncorrelated", "attempt_initiation_identifiers_unknown")
        return result("uncorrelated", "no_matching_attempt")
    if len(matches) > 1:
        return result("ambiguous", "multiple_matching_attempts")

    context = matches[0]
    conflicts: list[CorrelationReason] = []
    if context.identifiers_conflict:
        conflicts.append("attempt_initiation_identifiers_conflict")
    merchant = observation.merchant_request_id
    if (
        merchant is not None
        and context.merchant_request_ids
        and (merchant not in context.merchant_request_ids)
    ):
        conflicts.append("merchant_request_id_mismatch")
    if observation.binding is not None and observation.binding != context.binding:
        conflicts.append("binding_mismatch")
    if (
        observation.source != CALLBACK_SOURCE
        and observation.settlement_id is not None
        and observation.settlement_id != checkout
    ):
        conflicts.append("non_settlement_source_carries_settlement_identifier")
    if conflicts:
        return result("conflicting", *conflicts, attempt=context.binding)
    return result("correlated", "matched_checkout_request_id", attempt=context.binding)


def assess_attempt_correlation(
    context: AttemptCorrelationContext,
    observations: Iterable[ProviderObservation],
) -> AttemptCorrelationAssessment:
    """Assess every observation for one attempt, independent of arrival order.

    Identical observations collapse into one (duplicates are counted, not double
    counted). Different settlement identifiers, different callback result codes, a
    callback after a rejected initiation, or any observation that does not correlate
    to this attempt make the assessment ``conflicting``. Repeated observations from
    the same source are never treated as independent corroboration. Without any
    correlated observation the assessment is ``unresolved``.
    """

    unique: dict[tuple[object, ...], ProviderObservation] = {}
    total = 0
    for observation in observations:
        total += 1
        unique.setdefault(observation.identity_key, observation)
    distinct = sorted(unique.values(), key=lambda o: repr(o.identity_key))

    reasons: set[str] = set()
    # Context-level conflicts come first and never depend on observation correlation.
    if context.identifiers_conflict:
        reasons.add("attempt_initiation_identifiers_conflict")
    correlated: list[ProviderObservation] = []
    # Correctly bound observations that did not correlate by identifier cannot establish
    # correlation, but they take part in every consistency check so they cannot hide a
    # contradiction. A stored binding is not provider authentication.
    bound_uncorrelated: list[ProviderObservation] = []
    for observation in distinct:
        # Validate the stored binding before any identifier-based correlation; mis-bound
        # observations contribute nothing else to this attempt's assessment.
        if observation.binding is not None and observation.binding != context.binding:
            reasons.add("binding_mismatch")
            continue
        outcome = correlate_observation(observation, [context])
        if outcome.status == "correlated":
            correlated.append(observation)
            continue
        if outcome.status in ("conflicting", "ambiguous"):
            reasons.update(outcome.reasons)
        elif observation.checkout_request_id is not None and context.checkout_request_ids:
            # An observation for a different request cannot belong to this attempt.
            reasons.add("observation_for_different_request")
        else:
            reasons.update(outcome.reasons)
        if observation.binding == context.binding:
            bound_uncorrelated.append(observation)

    # Consistency is checked over correlated and correctly bound observations; only
    # identifier-correlated observations establish correlation or count as sources.
    consistency = [*correlated, *bound_uncorrelated]
    reasons.update(_consistency_reasons(context, consistency))
    settlement_ids = frozenset(
        o.settlement_id
        for o in consistency
        if o.source == CALLBACK_SOURCE
        and o.settlement_id is not None
        and o.identity == context.identity
    )

    status: AttemptCorrelationStatus
    if reasons & _CONFLICT_REASONS:
        status = "conflicting"
    elif correlated:
        status = "correlated"
    else:
        status = "unresolved"
        if not reasons:
            reasons.add("no_observations")

    sources = frozenset(o.source for o in correlated)
    eligibility_reasons: tuple[IneligibilityReason, ...] = (
        ("no_authoritative_verification_mechanism",)
        if status == "correlated"
        else ("not_correlated",)
    )
    return AttemptCorrelationAssessment(
        status=status,
        reasons=tuple(sorted(reasons)),
        distinct_observations=len(distinct),
        duplicate_observations=total - len(distinct),
        sources=sources,
        settlement_ids=settlement_ids,
        eligibility=VerificationEligibility(eligible=False, reasons=eligibility_reasons),
    )


def _consistency_reasons(
    context: AttemptCorrelationContext,
    observations: Sequence[ProviderObservation],
) -> set[str]:
    """Contradictions among observations that belong to ``context``'s attempt.

    Applied to identifier-correlated and correctly bound observations alike, so a
    missing or unestablished ``CheckoutRequestID`` can never suppress a conflict. Only
    identifiers that are present are compared; nothing absent becomes a mismatch.
    """

    reasons: set[str] = set()
    comparable: list[ProviderObservation] = []
    for observation in observations:
        if observation.identity != context.identity:
            # Another provider or rail: report it, and keep its codes and receipts out
            # of this attempt's comparisons.
            reasons.add("provider_or_rail_mismatch")
            continue
        comparable.append(observation)
        merchant = observation.merchant_request_id
        if (
            merchant is not None
            and context.merchant_request_ids
            and merchant not in context.merchant_request_ids
        ):
            reasons.add("merchant_request_id_mismatch")
        if observation.source != CALLBACK_SOURCE and observation.settlement_id is not None:
            # The existing adapters repeat the CheckoutRequestID here; any other value,
            # or one that cannot be tied to a known CheckoutRequestID, is improper.
            aliases = (
                {observation.checkout_request_id}
                if observation.checkout_request_id is not None
                else set(context.checkout_request_ids)
            )
            if observation.settlement_id not in aliases:
                reasons.add("non_settlement_source_carries_settlement_identifier")

    receipts = {
        o.settlement_id
        for o in comparable
        if o.source == CALLBACK_SOURCE and o.settlement_id is not None
    }
    if len(receipts) > 1:
        reasons.add("conflicting_settlement_identifiers")
    # Absent result codes (None; "" is normalized to None) are never compared and never
    # read as acceptance, failure or rejection.
    callback_codes = {
        o.result_code
        for o in comparable
        if o.source == CALLBACK_SOURCE and o.result_code is not None
    }
    if len(callback_codes) > 1:
        reasons.add("conflicting_callback_result_codes")
    initiation_codes = {
        o.result_code
        for o in comparable
        if o.source == INITIATION_SOURCE and o.result_code is not None
    }
    if len(initiation_codes) > 1:
        reasons.add("conflicting_initiation_result_codes")
    explicitly_rejected = any(code != INITIATION_ACCEPTED_CODE for code in initiation_codes)
    if explicitly_rejected and any(o.source == CALLBACK_SOURCE for o in comparable):
        reasons.add("callback_after_rejected_initiation")
    return reasons


_CONFLICT_REASONS = frozenset(
    {
        "attempt_initiation_identifiers_conflict",
        "merchant_request_id_mismatch",
        "binding_mismatch",
        "non_settlement_source_carries_settlement_identifier",
        "multiple_matching_attempts",
        "provider_or_rail_mismatch",
        "observation_for_different_request",
        "conflicting_settlement_identifiers",
        "conflicting_callback_result_codes",
        "conflicting_initiation_result_codes",
        "callback_after_rejected_initiation",
    }
)


def observation_from_evidence(
    record: ExecutionEvidenceRecord,
    *,
    identity: ProviderIdentity,
) -> ProviderObservation:
    """Map a stored E2.1 provider observation to a correlation input, unchanged.

    Only ``provider_response_observed`` rows are accepted. A callback's
    ``provider_transaction_id`` is its ``MpesaReceiptNumber``; on initiation and status
    responses the existing adapters store the ``CheckoutRequestID`` there, so it is
    passed through as-is and checked by ``correlate_observation``.
    """

    if record.evidence_type != "provider_response_observed":
        raise ValueError("Only provider observations can be correlated.")
    source = _provider_source(record.evidence_source)
    return ProviderObservation(
        source=source,
        result_code=record.result_code,
        checkout_request_id=record.checkout_request_id,
        merchant_request_id=record.merchant_request_id,
        settlement_id=record.provider_transaction_id,
        identity=identity,
        binding=_binding_of(record),
    )


def _provider_source(source: EvidenceSource) -> ProviderObservationSource:
    if source == INITIATION_SOURCE:
        return INITIATION_SOURCE
    if source == CALLBACK_SOURCE:
        return CALLBACK_SOURCE
    if source == STATUS_QUERY_SOURCE:
        return STATUS_QUERY_SOURCE
    raise ValueError(f"Not a provider observation source: {source!r}")


def _binding_of(record: ExecutionEvidenceRecord) -> AttemptBinding:
    return AttemptBinding(
        attempt_id=record.attempt_id,
        claim_id=record.claim_id,
        approval_id=record.approval_id,
        payload_hash=record.payload_hash,
    )
