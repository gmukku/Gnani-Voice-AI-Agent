"""Pydantic request/response models (assignment section 8).

Three inbound contracts:

* ``InitialMessageRequest``  -- operator -> us, starts a call (section 5.1)
* ``DynamicMessageRequest``  -- Gnani -> us at call start (section 5.2)
* ``PostCallWebhookPayload`` -- Gnani -> us after the call (section 5.3)

The Dynamic Messages request/response shape is dictated by the Gnani platform
(``additional_info.inya_data`` with mandatory ``text`` and ``user_context``);
the other two are ours to define.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.enums import CallStatus, Language, StageCode
from app.utils.masking import mask_account, mask_phone

_DIGITS_ONLY = re.compile(r"^\d{7,15}$")
_COUNTRY_CODE = re.compile(r"^\+\d{1,4}$")


# --------------------------------------------------------------------------
# 5.1 Initial Message -- operator initiates a call
# --------------------------------------------------------------------------


class InitialMessageRequest(BaseModel):
    """Customer information used to initiate an outbound call."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "customer_id": "CUST001",
                "customer_name": "Rahul Sharma",
                "phone_number": "5551234567",
                "country_code": "+1",
                "loan_account_number": "LAN123456",
                "emi_amount": 1200,
                "emi_due_date": "2026-07-25",
                "preferred_language": "en-US",
            }
        },
    )

    customer_id: Annotated[str, Field(min_length=1, max_length=64)]
    customer_name: Annotated[str, Field(min_length=1, max_length=128)]
    phone_number: Annotated[str, Field(min_length=7, max_length=15)]
    country_code: Annotated[str, Field(default="+1", max_length=5)]
    loan_account_number: Annotated[str, Field(min_length=1, max_length=64)]
    emi_amount: Annotated[float, Field(gt=0, le=10_000_000)]
    emi_due_date: date
    preferred_language: Language = Language.EN_US
    currency: Annotated[str, Field(default="USD", min_length=3, max_length=3)]
    #: Optional override. When absent the greeting is generated from the
    #: customer data by ``app/services/greeting.py``.
    initial_message: str | None = None

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: str) -> str:
        digits = re.sub(r"[\s\-()]", "", v).lstrip("+")
        if not _DIGITS_ONLY.match(digits):
            raise ValueError(
                "phone_number must be 7-15 digits, without country code"
            )
        return digits

    @field_validator("country_code")
    @classmethod
    def _validate_country_code(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("+"):
            v = f"+{v}"
        if not _COUNTRY_CODE.match(v):
            raise ValueError("country_code must look like '+1' or '+91'")
        return v

    @property
    def e164(self) -> str:
        return f"{self.country_code}{self.phone_number}"


class InitialMessageResponse(BaseModel):
    call_id: str
    status: CallStatus
    message: str
    e164_phone_masked: str
    gnani_response: dict[str, Any] | None = None


# --------------------------------------------------------------------------
# 5.2 Dynamic Messages -- Gnani calls us at call start
# --------------------------------------------------------------------------


class DynamicMessageRequest(BaseModel):
    """Inbound from Gnani. Shape is fixed by the platform."""

    model_config = ConfigDict(extra="allow")

    conversation_id: str
    mobile: str


class InyaData(BaseModel):
    """Both fields are mandatory per the Gnani docs; omitting either fails."""

    text: str
    user_context: dict[str, Any]


class AdditionalInfo(BaseModel):
    inya_data: InyaData


class DynamicMessageResponse(BaseModel):
    additional_info: AdditionalInfo


# --------------------------------------------------------------------------
# 5.3 Post-call webhook -- Gnani calls us after the call
# --------------------------------------------------------------------------


class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="allow")

    speaker: str
    text: str
    timestamp: datetime | None = None
    language: str | None = None


class PostCallWebhookPayload(BaseModel):
    """Post-call disposition delivered by a Gnani Post-Call Action.

    The variable mapping is ours to configure in the console, so this is the
    contract we chose. Everything except ``conversation_id`` is optional: a
    dropped or partial call must still be recordable, and the guardrail in
    ``app/services/disposition.py`` decides what an absent stage code means.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    conversation_id: str
    #: Idempotency key. When Gnani does not supply one, the webhook router
    #: derives a deterministic hash of the payload instead.
    event_id: str | None = None
    call_id: str | None = None

    #: The Agents Console reserves the field name ``DISPOSITION`` for the
    #: disposition enum -- configuring it as ``stage_code`` makes the bot
    #: config fail validation with "A DISPOSITION field is required with enum
    #: type and with valid options". The docs' own example uses
    #: ``STAGE_CODE``, so all three spellings are accepted here rather than
    #: betting on one.
    stage_code: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "DISPOSITION", "disposition", "stage_code", "STAGE_CODE"
        ),
    )
    disposition_reason: str | None = None
    disposition_summary: str | None = None
    #: Kept as a string, not a date: the extraction field may return a raw
    #: spoken phrase ("the thirtieth"), which resolve_ptp_date() interprets
    #: against the real call date rather than trusting the model to do it.
    ptp_date: str | None = None
    partial_amount: str | None = None
    language_captured: str | None = None
    customer_sentiment: str | None = None

    call_status: str | None = None
    call_duration_seconds: int | None = Field(default=None, ge=0)
    call_started_at: datetime | None = None
    call_ended_at: datetime | None = None
    recording_url: str | None = None

    transcript: list[TranscriptTurn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_some_identifier(self) -> PostCallWebhookPayload:
        if not self.conversation_id and not self.call_id:
            raise ValueError("one of conversation_id or call_id is required")
        return self


class WebhookAck(BaseModel):
    status: str
    call_id: str | None = None
    detail: str | None = None


# --------------------------------------------------------------------------
# Dashboard views -- always masked
# --------------------------------------------------------------------------


class CallSummaryView(BaseModel):
    """One row of the dashboard call-summary table (section 6, 13 fields)."""

    call_id: str
    customer_id: str
    customer_name: str
    masked_phone_number: str
    loan_account_number: str
    call_initiated_time: datetime | None
    call_status: CallStatus
    call_duration: int | None
    stage_code: StageCode | None
    disposition_reason: str | None
    ptp_date: date | None
    language: str | None

    @classmethod
    def from_record(cls, doc: dict[str, Any]) -> CallSummaryView:
        customer = doc.get("customer", {})
        return cls(
            call_id=doc["call_id"],
            customer_id=customer.get("customer_id", ""),
            customer_name=customer.get("customer_name", ""),
            masked_phone_number=mask_phone(customer.get("phone_number")),
            loan_account_number=mask_account(customer.get("loan_account_number")),
            call_initiated_time=doc.get("created_at"),
            call_status=doc.get("call_status", CallStatus.INITIATED),
            call_duration=doc.get("call_duration_seconds"),
            stage_code=doc.get("stage_code"),
            disposition_reason=doc.get("disposition_reason"),
            ptp_date=doc.get("ptp_date"),
            language=doc.get("language_captured"),
        )
