"""End-to-end smoke journey against a running server.

Not part of the test suite. This is the "demonstrate a vertical business
journey" check from SVX-PRD-001 section 9.4, run against a real server, a real
database and real session cookies — the things the pytest suite deliberately
stubs or bypasses.

    python manage.py runserver 127.0.0.1:8000 --noreload
    SMOKE_PASSWORD=... uv run python smoke_journey.py

It logs in as the bootstrapped owner, walks lead -> deal -> conversion ->
delivery -> ticket, and prints what each step produced.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import httpx

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8000")
EMAIL = os.environ.get("SMOKE_EMAIL", "ceo@scalevexo.test")
# Never hardcoded: this runs against a real server, and a credential in source
# is a credential in the repository.
PASSWORD = os.environ.get("SMOKE_PASSWORD", "")

# This writes to a real, persistent database, so every run needs its own
# contact. Reusing one would collide with the workspace-unique email index --
# correctly, but it would be testing the wrong thing.
RUN_ID = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

ok_count = 0
fail_count = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if condition:
        ok_count += 1
        print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))
    else:
        fail_count += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    if not PASSWORD:
        print("Set SMOKE_PASSWORD to the bootstrapped owner password.")
        return 2

    with httpx.Client(base_url=BASE, timeout=30.0, follow_redirects=True) as client:
        print("\n1. Unauthenticated access")
        r = client.get("/api/v1/today/")
        check("anonymous /today is 401", r.status_code == 401, str(r.status_code))

        print("\n2. Login")
        page = client.get("/accounts/login/")
        csrf = client.cookies.get("csrftoken")
        if csrf is None:
            print("  no csrftoken cookie on the login page")
            return 1
        r = client.post(
            "/accounts/login/",
            data={"login": EMAIL, "password": PASSWORD, "csrfmiddlewaretoken": csrf},
            headers={"Referer": f"{BASE}/accounts/login/"},
        )
        logged_in = "sessionid" in client.cookies
        check("session established", logged_in, f"status {r.status_code}")
        if not logged_in:
            print("  login response snippet:", page.status_code, r.url)
            return 1

        csrf = client.cookies.get("csrftoken")
        hdr = {"X-CSRFToken": csrf, "Referer": BASE, "Content-Type": "application/json"}

        print("\n3. Session context")
        me = client.get("/api/v1/me/").json()
        check("role is owner", me.get("role") == "owner", str(me.get("role")))
        check(
            "export withheld without MFA",
            "workspace.export" not in me.get("permissions", []),
            "MFA not enrolled",
        )

        print("\n4. Contact and lead")
        contact = client.post(
            "/api/v1/contacts/",
            json={
                "display_name": f"Northwind Ltd {RUN_ID}",
                "email": f"ops+{RUN_ID}@northwind.test",
            },
            headers=hdr,
        )
        check("contact created", contact.status_code == 201, str(contact.status_code))
        contact_id = contact.json()["id"]

        lead = client.post(
            "/api/v1/leads/",
            json={"contact": contact_id, "source": "referral"},
            headers=hdr,
        )
        check("lead created", lead.status_code == 201, str(lead.status_code))

        print("\n5. Opportunity through the stages")
        deal = client.post(
            "/api/v1/opportunities/",
            json={
                "contact": contact_id,
                "service": "Website rebuild",
                "amount": "14000.00",
                "currency": "USD",
            },
            headers=hdr,
        ).json()
        check("deal starts in discovery", deal["stage"] == "discovery", deal["stage"])

        version = deal["version"]
        for target, extra in (
            ("qualified", {}),
            ("proposal", {"scope_reference": "SOW-2026-101"}),
            ("negotiation", {}),
        ):
            resp = client.post(
                f"/api/v1/opportunities/{deal['id']}/transition/",
                json={"target_stage": target, "expected_version": version, **extra},
                headers=hdr,
            )
            check(f"moved to {target}", resp.status_code == 200, str(resp.status_code))
            version = resp.json()["version"]

        print("\n6. Guardrails")
        stale = client.post(
            f"/api/v1/opportunities/{deal['id']}/transition/",
            json={"target_stage": "qualified", "expected_version": 1},
            headers=hdr,
        )
        check(
            "stale version rejected with 409",
            stale.status_code == 409 and stale.json()["code"] == "version_conflict",
            stale.json().get("code", ""),
        )

        won_direct = client.post(
            f"/api/v1/opportunities/{deal['id']}/transition/",
            json={"target_stage": "won", "expected_version": version},
            headers=hdr,
        )
        check(
            "won refused outside convert",
            won_direct.json().get("code") == "use_convert_endpoint",
            won_direct.json().get("code", ""),
        )

        print("\n7. Conversion (CRM07)")
        convert = client.post(
            f"/api/v1/opportunities/{deal['id']}/convert/",
            json={
                "expected_version": version,
                "accepted_scope_evidence": "Signed SOW-2026-101.",
                "commercial_reference": "INV-2026-0099",
                "delivery_owner_id": me["user_id"],
            },
            headers={**hdr, "Idempotency-Key": f"smoke-convert-{RUN_ID}"},
        )
        check("conversion created", convert.status_code == 201, str(convert.status_code))
        body = convert.json()
        client_id = body["client"]["id"]
        project = body["project"]
        check(
            "handover awaits acceptance",
            body["client"]["status"] == "pending_handover",
            body["client"]["status"],
        )
        check(
            "onboarding milestones created",
            len(project["milestones"]) == 4,
            f"{len(project['milestones'])} milestones",
        )

        replay = client.post(
            f"/api/v1/opportunities/{deal['id']}/convert/",
            json={
                "expected_version": version,
                "accepted_scope_evidence": "Signed SOW-2026-101.",
                "commercial_reference": "INV-2026-0099",
                "delivery_owner_id": me["user_id"],
            },
            headers={**hdr, "Idempotency-Key": f"smoke-convert-{RUN_ID}"},
        )
        check(
            "replayed conversion makes no duplicate",
            replay.json()["client"]["id"] == client_id,
            "same client returned",
        )

        print("\n8. Delivery acceptance (CRM08)")
        accept = client.post(
            f"/api/v1/clients/{client_id}/accept-handover/",
            json={"expected_version": body["client"]["version"], "note": "Understood."},
            headers=hdr,
        )
        check("handover accepted", accept.status_code == 200, str(accept.status_code))

        milestone = project["milestones"][0]
        started = client.post(
            f"/api/v1/milestones/{milestone['id']}/start/",
            json={"expected_version": milestone["version"]},
            headers=hdr,
        )
        check("milestone started", started.status_code == 200, str(started.status_code))

        submitted = client.post(
            f"/api/v1/milestones/{milestone['id']}/submit/",
            json={
                "evidence": "Kick-off booked for Tuesday.",
                "expected_version": started.json()["version"],
            },
            headers=hdr,
        )
        check("milestone submitted", submitted.status_code == 200, str(submitted.status_code))
        check(
            "submission is not acceptance",
            submitted.json()["accepted_at"] is None,
            "accepted_at is null",
        )

        accepted = client.post(
            f"/api/v1/milestones/{milestone['id']}/accept/",
            json={"expected_version": submitted.json()["version"], "note": "Confirmed."},
            headers=hdr,
        )
        check(
            "milestone accepted", accepted.json()["status"] == "accepted", accepted.json()["status"]
        )

        print("\n9. Ticket (CRM09)")
        ticket = client.post(
            "/api/v1/tickets/",
            json={
                "title": "Contact form failing",
                "client": client_id,
                "priority": "high",
            },
            headers=hdr,
        ).json()
        tv = ticket["version"]
        for target, extra in (
            ("triaged", {}),
            ("in_progress", {}),
        ):
            resp = client.post(
                f"/api/v1/tickets/{ticket['id']}/transition/",
                json={"target_state": target, "expected_version": tv, **extra},
                headers=hdr,
            )
            tv = resp.json()["version"]

        no_evidence = client.post(
            f"/api/v1/tickets/{ticket['id']}/transition/",
            json={
                "target_state": "resolved",
                "expected_version": tv,
                "resolution_note": "Fixed.",
            },
            headers=hdr,
        )
        check(
            "resolution needs a closure test",
            no_evidence.status_code == 400,
            no_evidence.json().get("code", ""),
        )

        comment = client.post(
            f"/api/v1/tickets/{ticket['id']}/comments/",
            json={"body": "Internal: their DNS is misconfigured."},
            headers=hdr,
        ).json()
        check(
            "comment defaults to internal",
            comment["visibility"] == "internal",
            comment["visibility"],
        )

        print("\n10. Reporting (CRM11)")
        overview = client.get("/api/v1/reports/overview/").json()
        # The database persists between runs, so this checks that *this* run's
        # deal is included rather than asserting an exact total.
        won_usd = float(overview["won_contract_value"]["amounts"].get("USD", "0"))
        check(
            "this run's won value is included",
            won_usd >= 14000.0,
            f"USD {won_usd:,.2f} won this period",
        )
        check(
            "no cash recorded from winning",
            overview["cash_received"]["amounts"] == {},
            "cash_received empty",
        )
        check(
            "currencies never combined",
            overview["basis"]["currencies_combined"] is False,
            "",
        )

    print(f"\n{'=' * 52}\n  {ok_count} passed, {fail_count} failed\n{'=' * 52}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
