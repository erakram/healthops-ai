"""Bank reconciliation: deposits matched to 835s by trace number, and the exceptions."""

import pytest

from app.reconciliation import parse_statement
from tests.conftest import login


def test_csv_reading_and_trace_numbers():
    rows = parse_statement("posted_on,amount,description,bank_reference\n"
                           "2026-11-01,95.70,Payer HCCLAIMPMT CCD+ TRN*1*NWH0000012*1512345678\\,BNK1\n")
    assert rows[0]["trace_number"] == "NWH0000012" and rows[0]["amount"] == 95.7
    with pytest.raises(ValueError):
        parse_statement("date,amt\n2026-11-01,5\n")


def test_demo_statement_reconciles_with_three_kinds_of_exception(client):
    login(client, "biller")
    first = client.post("/api/reconciliation/demo-statement").json()
    assert first["added"] > 0 and first["matched"] > 0
    assert first["amount_differs"] == 1                  # a recoupment taken out of one EFT
    assert first["remittances_without_deposit"] >= 1     # an EFT that never arrived
    assert first["unmatched"] >= 1                       # money from a payer that sent no 835
    again = client.post("/api/reconciliation/demo-statement").json()
    assert again["added"] == 0 and again["already_imported"] == first["added"]  # importing twice changes nothing
    overview = client.get("/api/reconciliation").json()
    short = next(d for d in overview["deposits"] if d["status"] == "amount_differs")
    assert "difference $-12.50" in short["note"] and short["remittances"]
    unmatched = next(d for d in overview["deposits"] if d["status"] == "unmatched")
    r = client.post(f"/api/reconciliation/deposits/{unmatched['id']}/resolve", json={"note": "835 requested from payer"})
    assert r.status_code == 200
    assert client.post(f"/api/reconciliation/deposits/{short['id']}/resolve",
                       json={"note": "Recoupment for claim CLM-1 confirmed"}).status_code == 200
