"""
Live end-to-end tests against real provider APIs.

These tests require internet access.  They are intentionally kept out of the
main test suite (tests.py / test_integration.py) so CI doesn't need network.

Run manually on a machine with internet access:
    python3 test_live.py

What it checks
--------------
  1. mail.tm API reachable, returns active domains
  2. mail.tm account creation works (email + bearer token returned)
  3. mail.tm inbox polling: empty inbox returns [] without error
  4. 1secmail API reachable, returns domain list
  5. 1secmail account creation works
  6. 1secmail inbox polling: empty inbox returns [] without error
  7. create_account(providers=None) auto-picks a working provider
  8. Two sequential create_account() calls produce different emails
  9. Verify the email address format (local@domain.tld)
 10. Verify token structure matches expected provider schema
"""

import sys
import time
import re
import mail_handler

PASS = "  [PASS]"
FAIL = "  [FAIL]"
SKIP = "  [SKIP]"

results = []


def check(name, fn):
    try:
        fn()
        print(f"{PASS}  {name}")
        results.append(("PASS", name))
    except AssertionError as e:
        msg = str(e) or "assertion failed"
        print(f"{FAIL}  {name}")
        print(f"          {msg}")
        results.append(("FAIL", name))
    except Exception as e:
        print(f"{FAIL}  {name}")
        print(f"          {type(e).__name__}: {e}")
        results.append(("FAIL", name))


# ─── mail.tm ─────────────────────────────────────────────────────────────────

def test_mailtm_domains_reachable():
    import requests
    r = requests.get(f"{mail_handler.MAILTM_BASE}/domains", timeout=10)
    r.raise_for_status()
    domains = r.json().get("hydra:member", [])
    assert len(domains) > 0, "Expected at least one domain from mail.tm"
    active = [d for d in domains if d.get("isActive")]
    assert len(active) > 0, "Expected at least one active domain from mail.tm"


def test_mailtm_create_account():
    global _mailtm_email, _mailtm_token
    _mailtm_email, _mailtm_token = mail_handler.create_account(providers=["mailtm"])
    assert "@" in _mailtm_email, f"Not a valid email: {_mailtm_email!r}"
    assert _mailtm_token.get("provider") == "mailtm"
    assert _mailtm_token.get("bearer"), "Bearer token must be non-empty"
    assert len(_mailtm_token["bearer"]) > 20, "Bearer token suspiciously short"


def test_mailtm_poll_empty_inbox():
    """Poll should run one cycle and yield ("waiting", None) without error."""
    events = []
    # One cycle then timeout
    for event, val in mail_handler.poll_for_code(
        _mailtm_token, timeout=5, interval=10
    ):
        events.append((event, val))
        break  # only need first event

    # On an empty inbox we always get at least one "waiting"
    assert ("waiting", None) in events or any(e[0] == "code" for e in events), \
        "poll_for_code must yield at least one event"


def test_mailtm_email_format():
    assert re.match(r"^[a-z0-9]+@[a-z0-9]+\.[a-z]{2,}$", _mailtm_email), \
        f"Unexpected email format: {_mailtm_email!r}"


# ─── 1secmail ────────────────────────────────────────────────────────────────

def test_1secmail_domains_reachable():
    import requests
    r = requests.get(
        f"{mail_handler.ONESEC_BASE}?action=getDomainList", timeout=10
    )
    r.raise_for_status()
    domains = r.json()
    assert isinstance(domains, list) and len(domains) > 0, \
        "Expected non-empty domain list from 1secmail"


def test_1secmail_create_account():
    global _onesec_email, _onesec_token
    _onesec_email, _onesec_token = mail_handler.create_account(providers=["1secmail"])
    assert "@" in _onesec_email
    assert _onesec_token.get("provider") == "1secmail"
    assert _onesec_token.get("login")
    assert _onesec_token.get("domain")
    assert _onesec_email == f"{_onesec_token['login']}@{_onesec_token['domain']}"


def test_1secmail_poll_empty_inbox():
    import requests
    login = _onesec_token["login"]
    domain = _onesec_token["domain"]
    r = requests.get(
        mail_handler.ONESEC_BASE,
        params={"action": "getMessages", "login": login, "domain": domain},
        timeout=10,
    )
    r.raise_for_status()
    msgs = r.json()
    # Brand new inbox should be empty
    assert isinstance(msgs, list), f"Expected list, got {type(msgs)}"
    # May or may not have messages; just verify it doesn't error


def test_1secmail_email_format():
    assert re.match(r"^[a-z0-9]+@[a-z0-9.]+$", _onesec_email), \
        f"Unexpected email format: {_onesec_email!r}"


# ─── Auto-provider ───────────────────────────────────────────────────────────

def test_auto_provider_returns_email():
    email, token = mail_handler.create_account(providers=None)
    assert "@" in email
    assert token.get("provider") in ("mailtm", "1secmail")


def test_two_accounts_have_different_emails():
    e1, _ = mail_handler.create_account()
    e2, _ = mail_handler.create_account()
    assert e1 != e2, f"Got same email twice: {e1!r}"


# ─── Rate-limit safety ───────────────────────────────────────────────────────

def test_rapid_successive_polls_dont_crash():
    """
    Verify that polling multiple times in quick succession doesn't hit
    rate limits or raise exceptions.
    """
    _, token = mail_handler.create_account()
    errors = []
    for i in range(3):
        try:
            event = next(iter(
                mail_handler.poll_for_code(token, timeout=5, interval=0)
            ))
            assert event[0] in ("waiting", "code")
        except (TimeoutError, StopIteration):
            pass  # acceptable — inbox still empty
        except Exception as e:
            errors.append(str(e))
        time.sleep(1)
    assert not errors, f"Errors during rapid polls: {errors}"


# ─── Run ─────────────────────────────────────────────────────────────────────

# Globals shared between tests (set by earlier test functions)
_mailtm_email = None
_mailtm_token = None
_onesec_email = None
_onesec_token = None


if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  Live API Tests – mail_handler.py")
    print("=" * 60)
    print()

    print("── mail.tm ──────────────────────────────────────────────")
    check("Domains endpoint reachable", test_mailtm_domains_reachable)
    check("Create account", test_mailtm_create_account)
    check("Email format valid", test_mailtm_email_format)
    check("Poll empty inbox (no crash)", test_mailtm_poll_empty_inbox)
    print()

    print("── 1secmail ─────────────────────────────────────────────")
    check("Domains endpoint reachable", test_1secmail_domains_reachable)
    check("Create account", test_1secmail_create_account)
    check("Email format valid", test_1secmail_email_format)
    check("Poll empty inbox (no crash)", test_1secmail_poll_empty_inbox)
    print()

    print("── Provider selection ───────────────────────────────────")
    check("Auto-provider returns an email", test_auto_provider_returns_email)
    check("Two calls give different emails", test_two_accounts_have_different_emails)
    check("Rapid polls don't crash", test_rapid_successive_polls_dont_crash)
    print()

    passed = sum(1 for s, _ in results if s == "PASS")
    failed = sum(1 for s, _ in results if s == "FAIL")
    total = len(results)

    print("=" * 60)
    print(f"  Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  ({failed} FAILED)")
        print()
        print("  Failed tests:")
        for status, name in results:
            if status == "FAIL":
                print(f"    - {name}")
    else:
        print("  – all passed!")
    print("=" * 60)
    print()

    if failed:
        sys.exit(1)
