"""Shared fixtures.

Tests never touch the real Gnani API or a real database: the repository fixture
uses the JSON backend against a tmp_path, and the Gnani client is driven by an
``httpx.MockTransport``.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.config import Settings
from app.db.repository import JsonCallRepository
from app.models.schemas import InitialMessageRequest

#: Fixed reference date used across the suite so relative-date assertions are
#: deterministic. 2026-07-29 is a Wednesday.
REFERENCE_DATE = date(2026, 7, 29)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        environment="test",
        timezone="America/New_York",
        storage_backend="json",
        json_store_path=str(tmp_path / "calls.json"),
        gnani_base_url="http://gnani.test",
        gnani_agent_id="test-bot-id",
        gnani_max_retries=2,
        gnani_timeout_seconds=1.0,
        webhook_api_key="test-webhook-key",
        currency="INR",
        lender_name="ICICI Bank",
    )


@pytest.fixture
async def repo(settings: Settings) -> JsonCallRepository:
    repository = JsonCallRepository(settings)
    await repository.init()
    return repository


@pytest.fixture
def initial_request() -> InitialMessageRequest:
    return InitialMessageRequest(
        customer_id="CUST001",
        customer_name="Rahul Sharma",
        phone_number="9123456789",
        country_code="+91",
        loan_account_number="LAN123456",
        emi_amount=12500,
        emi_due_date=date(2026, 7, 30),
        preferred_language="en-US",
        currency="INR",
    )
