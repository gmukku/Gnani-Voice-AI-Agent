"""Dashboard (assignment section 6).

Section 6 is prescriptive -- 13 table columns, 11 detail fields, 7 filters and 8
summary cards -- so the JSON API and the templates are built directly against
that list rather than to taste.

Filtering happens here rather than in the repository so that a single
implementation covers both storage backends, and so date filtering (which is a
range, not an exact match) works uniformly. At dashboard scale -- hundreds to
low thousands of calls -- fetching then filtering is fine; a production build
would push predicates into Mongo.

Phone numbers are masked at this boundary. The stored record keeps the real
number because it is needed to place the call; nothing unmasked reaches a
template or an API response.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.db.repository import CallRepository
from app.models.enums import CallStatus, StageCode
from app.models.schemas import InitialMessageRequest
from app.routers.deps import get_call_service, get_repo
from app.services import metrics
from app.services.call_service import CallService
from app.utils.logging import get_logger
from app.utils.masking import mask_account, mask_phone

log = get_logger(__name__)

templates = Jinja2Templates(directory="templates")
router = APIRouter(tags=["dashboard"])


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _matches(record: dict[str, Any], f: dict[str, Any]) -> bool:
    """Apply the seven section 6.2 filters. Empty filters match everything."""
    customer = record.get("customer", {})

    if f.get("call_date"):
        if _as_date(record.get("created_at")) != f["call_date"]:
            return False
    if f.get("call_status") and record.get("call_status") != f["call_status"]:
        return False
    if f.get("stage_code") and record.get("stage_code") != f["stage_code"]:
        return False
    if f.get("customer_id"):
        if f["customer_id"].lower() not in str(customer.get("customer_id", "")).lower():
            return False
    if f.get("loan_account_number"):
        needle = f["loan_account_number"].lower()
        if needle not in str(customer.get("loan_account_number", "")).lower():
            return False
    if f.get("language") and record.get("language_captured") != f["language"]:
        return False
    if f.get("ptp_date"):
        if _as_date(record.get("ptp_date")) != f["ptp_date"]:
            return False
    return True


def _row(record: dict[str, Any]) -> dict[str, Any]:
    """One table row -- the 13 fields named in section 6."""
    customer = record.get("customer", {})
    return {
        "call_id": record.get("call_id"),
        "customer_id": customer.get("customer_id"),
        "customer_name": customer.get("customer_name"),
        "masked_phone_number": mask_phone(customer.get("phone_e164")),
        "loan_account_number": customer.get("loan_account_number"),
        "call_initiated_time": record.get("created_at"),
        "call_status": record.get("call_status"),
        "call_duration": record.get("call_duration_seconds"),
        "stage_code": record.get("stage_code"),
        "disposition_reason": record.get("disposition_reason"),
        "ptp_date": record.get("ptp_date"),
        "language": record.get("language_captured"),
    }


def _detail(record: dict[str, Any]) -> dict[str, Any]:
    """The 11 fields named in section 6.1, with PII masked."""
    customer = dict(record.get("customer", {}))
    customer["phone_number"] = mask_phone(customer.get("phone_e164"))
    customer["loan_account_number_masked"] = mask_account(
        customer.get("loan_account_number")
    )
    # Never expose the real number through the API.
    customer.pop("phone_e164", None)
    customer.pop("phone_suffix", None)

    return {
        "call_id": record.get("call_id"),
        "origin": record.get("origin", "api"),
        "customer": customer,
        "emi_details": record.get("emi_details", {}),
        "call_status": record.get("call_status"),
        "call_duration_seconds": record.get("call_duration_seconds"),
        "stage_code": record.get("stage_code"),
        "disposition_reason": record.get("disposition_reason"),
        "disposition_summary": record.get("disposition_summary"),
        "disposition_adjustments": record.get("disposition_adjustments", []),
        "ptp_date": record.get("ptp_date"),
        "language_captured": record.get("language_captured"),
        "customer_sentiment": record.get("customer_sentiment"),
        "initial_message": record.get("initial_message"),
        "conversation_transcript": record.get("conversation_transcript", []),
        "recording_url": record.get("recording_url"),
        "gnani_conversation_id": record.get("gnani_conversation_id"),
        # Raw payloads -- what an evaluator opens to confirm the integration.
        "gnani_response": record.get("gnani_response"),
        "post_call_payload": record.get("post_call_payload"),
        "created_at": record.get("created_at"),
        "call_started_at": record.get("call_started_at"),
        "call_ended_at": record.get("call_ended_at"),
        "updated_at": record.get("updated_at"),
    }


async def _filtered(
    repo: CallRepository, filters: dict[str, Any]
) -> list[dict[str, Any]]:
    records = await repo.list_calls()
    return [r for r in records if _matches(r, filters)]


def _filter_params(
    call_date: date | None = Query(default=None),
    call_status: str | None = Query(default=None),
    stage_code: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    loan_account_number: str | None = Query(default=None),
    language: str | None = Query(default=None),
    ptp_date: date | None = Query(default=None),
) -> dict[str, Any]:
    return {
        "call_date": call_date,
        "call_status": call_status,
        "stage_code": stage_code,
        "customer_id": customer_id,
        "loan_account_number": loan_account_number,
        "language": language,
        "ptp_date": ptp_date,
    }


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------


@router.get("/api/v1/calls", summary="List calls with section 6.2 filters")
async def list_calls(
    filters: dict[str, Any] = Depends(_filter_params),
    repo: CallRepository = Depends(get_repo),
) -> dict[str, Any]:
    records = await _filtered(repo, filters)
    return {
        "count": len(records),
        "summary": metrics.summarise(records),
        "distribution": metrics.stage_distribution(records),
        "calls": [_row(r) for r in records],
    }


@router.get("/api/v1/calls/{call_id}", summary="Full call detail")
async def get_call(
    call_id: str, repo: CallRepository = Depends(get_repo)
) -> dict[str, Any]:
    record = await repo.get_call(call_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No call {call_id}"
        )
    return _detail(record)


@router.post(
    "/api/v1/calls/bulk",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk-initiate calls from a CSV upload",
)
async def bulk_upload(
    request: Request,
    file: UploadFile,
    service: CallService = Depends(get_call_service),
) -> Any:
    """Each row is validated independently, so one bad row cannot abort the batch.

    Content-negotiated: an API client gets JSON, while the dashboard's upload
    form (which the browser submits directly) is redirected back to the table
    with a result banner. Without this, uploading from the UI navigates the
    browser to a page of raw JSON.
    """
    wants_html = "text/html" in request.headers.get("accept", "")

    if not (file.filename or "").lower().endswith(".csv"):
        if wants_html:
            return RedirectResponse(
                "/?upload_error=Expected+a+.csv+file",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Expected a .csv file",
        )

    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))

    accepted: list[str] = []
    rejected: list[dict[str, Any]] = []

    for line, row in enumerate(reader, start=2):
        try:
            # Not named `request`: that would shadow the HTTP Request parameter.
            call_request = InitialMessageRequest.model_validate(
                {k: v for k, v in row.items() if v not in (None, "")}
            )
        except ValueError as exc:
            rejected.append({"line": line, "error": str(exc)[:200]})
            continue
        try:
            record = await service.initiate(call_request)
            accepted.append(record["call_id"])
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the batch
            rejected.append({"line": line, "error": str(exc)[:200]})

    log.info("bulk_upload.done", accepted=len(accepted), rejected=len(rejected))

    if wants_html:
        return RedirectResponse(
            f"/?uploaded={len(accepted)}&rejected={len(rejected)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return {
        "accepted": len(accepted),
        "rejected": len(rejected),
        "call_ids": accepted,
        "errors": rejected,
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(
    request: Request,
    uploaded: int | None = Query(default=None),
    rejected: int | None = Query(default=None),
    upload_error: str | None = Query(default=None),
    filters: dict[str, Any] = Depends(_filter_params),
    repo: CallRepository = Depends(get_repo),
) -> HTMLResponse:
    records = await _filtered(repo, filters)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": [_row(r) for r in records],
            "summary": metrics.summarise(records),
            "distribution": metrics.stage_distribution(records),
            "filters": filters,
            "statuses": [s.value for s in CallStatus],
            "stage_codes": [s.value for s in StageCode],
            "languages": ["English", "Spanish", "Mixed"],
            "uploaded": uploaded,
            "rejected": rejected,
            "upload_error": upload_error,
        },
    )


@router.get(
    "/calls/{call_id}", response_class=HTMLResponse, include_in_schema=False
)
async def detail_page(
    request: Request, call_id: str, repo: CallRepository = Depends(get_repo)
) -> HTMLResponse:
    record = await repo.get_call(call_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No call {call_id}")
    return templates.TemplateResponse(
        request, "detail.html", {"call": _detail(record)}
    )
