"""Tests for the FastAPI service's auth gate. CALLSCOUT_API_KEY is unset
during the rest of the suite (auth stays off for local/offline testing);
these tests set and restore it explicitly to check both states.
"""
import os

import pytest
from fastapi.testclient import TestClient

from callscout.api import app

client = TestClient(app)


def test_health_never_requires_auth():
    resp = client.get("/health")
    assert resp.status_code == 200


def test_extract_unauthenticated_when_no_key_configured(monkeypatch):
    monkeypatch.delenv("CALLSCOUT_API_KEY", raising=False)
    resp = client.post("/extract", json={"url": "http://127.0.0.1:1/doesnotmatter"})
    # No 401 -- auth is off. It'll fail to connect (this host/port isn't
    # real), which is a 502 from the extraction try/except, not an auth error.
    assert resp.status_code != 401


def test_extract_rejects_missing_key_when_configured(monkeypatch):
    monkeypatch.setenv("CALLSCOUT_API_KEY", "secret123")
    resp = client.post("/extract", json={"url": "http://127.0.0.1:1/doesnotmatter"})
    assert resp.status_code == 401


def test_extract_rejects_wrong_key_when_configured(monkeypatch):
    monkeypatch.setenv("CALLSCOUT_API_KEY", "secret123")
    resp = client.post(
        "/extract",
        json={"url": "http://127.0.0.1:1/doesnotmatter"},
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 401


def test_extract_accepts_correct_key_when_configured(monkeypatch):
    monkeypatch.setenv("CALLSCOUT_API_KEY", "secret123")
    resp = client.post(
        "/extract",
        json={"url": "http://127.0.0.1:1/doesnotmatter"},
        headers={"X-API-Key": "secret123"},
    )
    # Correct key clears auth; it still 502s because that host/port isn't
    # reachable, which proves auth passed and extraction was attempted.
    assert resp.status_code == 502


def test_discover_also_gated(monkeypatch):
    monkeypatch.setenv("CALLSCOUT_API_KEY", "secret123")
    resp = client.post("/discover", json={"seed_urls": ["http://127.0.0.1:1/doesnotmatter"]})
    assert resp.status_code == 401
