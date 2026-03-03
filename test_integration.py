"""
Integration tests for mail_handler.py using a real local HTTP server.

Unlike tests.py (which patches requests at the Python level), these tests
make actual TCP connections to a local werkzeug server that mimics the
mail.tm and 1secmail APIs. This validates that:
  - Correct URLs and HTTP methods are used
  - Authorization headers are sent
  - Request bodies / query strings are well-formed
  - Response parsing handles real JSON over the wire

Run: python3 -m pytest test_integration.py -v
"""

import json
import time
from unittest.mock import patch

import pytest
import requests
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

import mail_handler


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _json_resp(data, status=200):
    return Response(json.dumps(data), status=status, content_type="application/json")


def _patch_bases(monkeypatch, mailtm_url=None, onesec_url=None):
    if mailtm_url is not None:
        monkeypatch.setattr(mail_handler, "MAILTM_BASE", mailtm_url.rstrip("/"))
    if onesec_url is not None:
        monkeypatch.setattr(mail_handler, "ONESEC_BASE", onesec_url.rstrip("/") + "/")


# ─── mail.tm – account creation ──────────────────────────────────────────────

class TestMailTmCreateAccount:
    def test_correct_endpoint_sequence(self, httpserver, monkeypatch):
        """create_account() must call GET /domains, POST /accounts, POST /token."""
        calls = []

        def domains(req):
            calls.append("GET /domains")
            return _json_resp({"hydra:member": [{"domain": "srv.test", "isActive": True}]})

        def accounts(req):
            calls.append("POST /accounts")
            return _json_resp({"id": "acc1"}, status=201)

        def token(req):
            calls.append("POST /token")
            return _json_resp({"token": "bearer-xyz"})

        httpserver.expect_request("/domains", method="GET").respond_with_handler(domains)
        httpserver.expect_request("/accounts", method="POST").respond_with_handler(accounts)
        httpserver.expect_request("/token", method="POST").respond_with_handler(token)

        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        email, tok = mail_handler.create_account(providers=["mailtm"])

        assert calls == ["GET /domains", "POST /accounts", "POST /token"]
        assert "@srv.test" in email
        assert tok["provider"] == "mailtm"
        assert tok["bearer"] == "bearer-xyz"

    def test_account_and_token_use_same_credentials(self, httpserver, monkeypatch):
        """Password sent to /accounts must match password sent to /token."""
        creds = {}

        def domains(req):
            return _json_resp({"hydra:member": [{"domain": "x.test", "isActive": True}]})

        def accounts(req):
            creds["acct"] = req.get_json()
            return _json_resp({}, status=201)

        def token(req):
            creds["token"] = req.get_json()
            return _json_resp({"token": "t"})

        httpserver.expect_request("/domains").respond_with_handler(domains)
        httpserver.expect_request("/accounts", method="POST").respond_with_handler(accounts)
        httpserver.expect_request("/token", method="POST").respond_with_handler(token)

        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        email, _ = mail_handler.create_account(providers=["mailtm"])

        assert creds["acct"]["address"] == creds["token"]["address"] == email
        assert creds["acct"]["password"] == creds["token"]["password"]
        assert len(creds["acct"]["password"]) >= 12

    def test_raises_when_no_active_domain(self, httpserver, monkeypatch):
        httpserver.expect_request("/domains").respond_with_handler(
            lambda r: _json_resp(
                {"hydra:member": [{"domain": "x.test", "isActive": False}]}
            )
        )
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        with pytest.raises(RuntimeError):
            mail_handler.create_account(providers=["mailtm"])

    def test_raises_when_server_returns_500(self, httpserver, monkeypatch):
        httpserver.expect_request("/domains").respond_with_data("error", status=500)
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        with pytest.raises(RuntimeError):
            mail_handler.create_account(providers=["mailtm"])

    def test_each_call_produces_unique_email(self, httpserver, monkeypatch):
        def domains(req):
            return _json_resp({"hydra:member": [{"domain": "u.test", "isActive": True}]})

        def accounts(req):
            return _json_resp({}, status=201)

        def token_fn(req):
            return _json_resp({"token": "t"})

        httpserver.expect_request("/domains").respond_with_handler(domains)
        httpserver.expect_request("/accounts", method="POST").respond_with_handler(accounts)
        httpserver.expect_request("/token", method="POST").respond_with_handler(token_fn)

        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        emails = {mail_handler.create_account(providers=["mailtm"])[0] for _ in range(5)}
        assert len(emails) == 5, "Expected 5 unique email addresses"


# ─── mail.tm – inbox polling ─────────────────────────────────────────────────

class TestMailTmPollForCode:
    @patch("mail_handler.time.sleep", return_value=None)
    def test_sends_bearer_in_auth_header(self, _sleep, httpserver, monkeypatch):
        """Authorization: Bearer <token> must be present on every poll request."""
        seen_auth = []

        def messages(req):
            seen_auth.append(req.headers.get("Authorization", ""))
            return _json_resp({"hydra:member": [{"id": "m1"}]})

        def message(req):
            seen_auth.append(req.headers.get("Authorization", ""))
            return _json_resp({"text": "Code: 482910", "html": ""})

        httpserver.expect_request("/messages").respond_with_handler(messages)
        httpserver.expect_request("/messages/m1").respond_with_handler(message)

        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "SECRET-TOKEN"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))

        assert ("code", "482910") in events
        assert all(a == "Bearer SECRET-TOKEN" for a in seen_auth)

    @patch("mail_handler.time.sleep", return_value=None)
    def test_code_from_plain_text_body(self, _sleep, httpserver, monkeypatch):
        httpserver.expect_request("/messages").respond_with_handler(
            lambda r: _json_resp({"hydra:member": [{"id": "m1"}]})
        )
        httpserver.expect_request("/messages/m1").respond_with_handler(
            lambda r: _json_resp({"text": "Your Taco Bell sign-in code is 391847", "html": ""})
        )
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert ("code", "391847") in events

    @patch("mail_handler.time.sleep", return_value=None)
    def test_code_from_html_only_body(self, _sleep, httpserver, monkeypatch):
        """Taco Bell typically sends HTML-only emails."""
        html = """
        <html><body>
          <table><tr><td style="font-size:32px;font-weight:bold">847291</td></tr></table>
          <p>Enter this code to sign in to your Taco Bell account.</p>
        </body></html>
        """
        httpserver.expect_request("/messages").respond_with_handler(
            lambda r: _json_resp({"hydra:member": [{"id": "m1"}]})
        )
        httpserver.expect_request("/messages/m1").respond_with_handler(
            lambda r: _json_resp({"text": "", "html": html})
        )
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert ("code", "847291") in events

    @patch("mail_handler.time.sleep", return_value=None)
    def test_empty_inbox_then_code(self, _sleep, httpserver, monkeypatch):
        """First poll: empty. Second poll: message with code."""
        call_n = [0]

        def messages(req):
            call_n[0] += 1
            if call_n[0] == 1:
                return _json_resp({"hydra:member": []})
            return _json_resp({"hydra:member": [{"id": "m1"}]})

        httpserver.expect_request("/messages").respond_with_handler(messages)
        httpserver.expect_request("/messages/m1").respond_with_handler(
            lambda r: _json_resp({"text": "Code: 291847", "html": ""})
        )
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))

        assert ("waiting", None) in events
        assert ("code", "291847") in events
        wi = events.index(("waiting", None))
        ci = next(i for i, e in enumerate(events) if e[0] == "code")
        assert wi < ci

    @patch("mail_handler.time.sleep", return_value=None)
    def test_message_without_code_does_not_stop_polling(self, _sleep, httpserver, monkeypatch):
        """A marketing email (no 6-digit code) should not stop the poll."""
        call_n = [0]

        def messages(req):
            call_n[0] += 1
            if call_n[0] == 1:
                return _json_resp({"hydra:member": [{"id": "marketing"}]})
            return _json_resp({"hydra:member": [{"id": "marketing"}, {"id": "otp"}]})

        def message(req):
            mid = req.path.split("/")[-1]
            if mid == "marketing":
                return _json_resp({"text": "Welcome to Taco Bell loyalty rewards!", "html": ""})
            return _json_resp({"text": "Your sign-in code is 938271", "html": ""})

        httpserver.expect_request("/messages").respond_with_handler(messages)
        httpserver.expect_request("/messages/marketing").respond_with_handler(message)
        httpserver.expect_request("/messages/otp").respond_with_handler(message)

        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert ("code", "938271") in events

    @patch("mail_handler.time.sleep", return_value=None)
    def test_seen_message_fetched_only_once(self, _sleep, httpserver, monkeypatch):
        """A message ID that was already processed should not trigger a second fetch."""
        fetch_count = [0]

        def messages(req):
            return _json_resp({"hydra:member": [{"id": "m1"}]})

        def message(req):
            fetch_count[0] += 1
            return _json_resp({"text": "Code: 112233", "html": ""})

        httpserver.expect_request("/messages").respond_with_handler(messages)
        httpserver.expect_request("/messages/m1").respond_with_handler(message)

        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert fetch_count[0] == 1

    @patch("mail_handler.time.sleep", return_value=None)
    def test_recovers_after_5xx_response(self, _sleep, httpserver, monkeypatch):
        """A transient 500 on /messages should be ignored; next poll should succeed."""
        call_n = [0]

        def messages(req):
            call_n[0] += 1
            if call_n[0] == 1:
                return Response("Gateway Timeout", status=504)
            return _json_resp({"hydra:member": [{"id": "m1"}]})

        httpserver.expect_request("/messages").respond_with_handler(messages)
        httpserver.expect_request("/messages/m1").respond_with_handler(
            lambda r: _json_resp({"text": "Code: 773819", "html": ""})
        )
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert ("code", "773819") in events

    @patch("mail_handler.time.sleep", return_value=None)
    def test_sleep_called_with_interval(self, mock_sleep, httpserver, monkeypatch):
        """time.sleep() must be called with the configured interval each cycle."""
        call_n = [0]

        def messages(req):
            call_n[0] += 1
            if call_n[0] < 3:
                return _json_resp({"hydra:member": []})
            return _json_resp({"hydra:member": [{"id": "m1"}]})

        httpserver.expect_request("/messages").respond_with_handler(messages)
        httpserver.expect_request("/messages/m1").respond_with_handler(
            lambda r: _json_resp({"text": "Code: 483920", "html": ""})
        )
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        list(mail_handler.poll_for_code(tok, timeout=30, interval=7))

        # sleep(7) called for each "waiting" cycle (call_n 1 and 2 had no code)
        assert mock_sleep.call_count == 2
        for c in mock_sleep.call_args_list:
            assert c.args[0] == 7

    def test_times_out_when_deadline_already_past(self, httpserver, monkeypatch):
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))
        tok = {"provider": "mailtm", "bearer": "t"}
        with pytest.raises(TimeoutError):
            list(mail_handler.poll_for_code(tok, timeout=-1, interval=0))

    @patch("mail_handler.time.sleep", return_value=None)
    def test_combined_text_and_html_searched(self, _sleep, httpserver, monkeypatch):
        """Code could be in text OR html field; both must be searched."""
        httpserver.expect_request("/messages").respond_with_handler(
            lambda r: _json_resp({"hydra:member": [{"id": "m1"}]})
        )
        # Code only in html, text is empty
        httpserver.expect_request("/messages/m1").respond_with_handler(
            lambda r: _json_resp(
                {"text": "Welcome", "html": "<strong>Your code: 554433</strong>"}
            )
        )
        _patch_bases(monkeypatch, mailtm_url=httpserver.url_for(""))

        tok = {"provider": "mailtm", "bearer": "t"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert ("code", "554433") in events


# ─── 1secmail – account creation ─────────────────────────────────────────────

class TestOneSecMailCreateAccount:
    def test_uses_domain_from_list(self, httpserver, monkeypatch):
        httpserver.expect_request("/").respond_with_handler(
            lambda r: _json_resp(["alpha.test", "beta.test"])
            if r.args.get("action") == "getDomainList"
            else Response("not found", status=404)
        )
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))

        email, tok = mail_handler.create_account(providers=["1secmail"])

        assert tok["provider"] == "1secmail"
        assert tok["domain"] in ["alpha.test", "beta.test"]
        assert email == f"{tok['login']}@{tok['domain']}"

    def test_raises_on_empty_domain_list(self, httpserver, monkeypatch):
        httpserver.expect_request("/").respond_with_handler(
            lambda r: _json_resp([])
        )
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))

        with pytest.raises(RuntimeError):
            mail_handler.create_account(providers=["1secmail"])

    def test_raises_on_server_error(self, httpserver, monkeypatch):
        httpserver.expect_request("/").respond_with_data("error", status=503)
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))

        with pytest.raises(RuntimeError):
            mail_handler.create_account(providers=["1secmail"])


# ─── 1secmail – inbox polling ────────────────────────────────────────────────

class TestOneSecMailPollForCode:
    @patch("mail_handler.time.sleep", return_value=None)
    def test_polls_correct_login_and_domain(self, _sleep, httpserver, monkeypatch):
        """login and domain from token must appear in every GET request."""
        seen_args = []

        def handler(req):
            seen_args.append(dict(req.args))
            action = req.args.get("action")
            if action == "getMessages":
                return _json_resp([{"id": 1}])
            if action == "readMessage":
                return _json_resp({"textBody": "Code: 829103", "htmlBody": ""})
            return Response("", status=404)

        httpserver.expect_request("/").respond_with_handler(handler)
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))

        tok = {"provider": "1secmail", "login": "mylogin99", "domain": "1secmail.com"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))

        assert ("code", "829103") in events
        for args in seen_args:
            assert args.get("login") == "mylogin99"
            assert args.get("domain") == "1secmail.com"

    @patch("mail_handler.time.sleep", return_value=None)
    def test_code_from_html_body(self, _sleep, httpserver, monkeypatch):
        def handler(req):
            action = req.args.get("action")
            if action == "getMessages":
                return _json_resp([{"id": 5}])
            if action == "readMessage":
                return _json_resp({
                    "textBody": "",
                    "htmlBody": "<div><p>Sign-in code:</p><h1>654321</h1></div>",
                })
            return Response("", status=404)

        httpserver.expect_request("/").respond_with_handler(handler)
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))

        tok = {"provider": "1secmail", "login": "u", "domain": "1secmail.com"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert ("code", "654321") in events

    @patch("mail_handler.time.sleep", return_value=None)
    def test_empty_inbox_then_code(self, _sleep, httpserver, monkeypatch):
        call_n = [0]

        def handler(req):
            action = req.args.get("action")
            if action == "getMessages":
                call_n[0] += 1
                if call_n[0] == 1:
                    return _json_resp([])
                return _json_resp([{"id": 7}])
            if action == "readMessage":
                return _json_resp({"textBody": "Code: 112358", "htmlBody": ""})
            return Response("", status=404)

        httpserver.expect_request("/").respond_with_handler(handler)
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))

        tok = {"provider": "1secmail", "login": "u", "domain": "1secmail.com"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))

        assert ("waiting", None) in events
        assert ("code", "112358") in events

    @patch("mail_handler.time.sleep", return_value=None)
    def test_recovers_after_network_error(self, _sleep, httpserver, monkeypatch):
        call_n = [0]

        def handler(req):
            action = req.args.get("action")
            call_n[0] += 1
            if action == "getMessages":
                if call_n[0] == 1:
                    # Simulate connection drop by returning 500
                    return Response("error", status=500)
                return _json_resp([{"id": 9}])
            if action == "readMessage":
                return _json_resp({"textBody": "Code: 987654", "htmlBody": ""})
            return Response("", status=404)

        httpserver.expect_request("/").respond_with_handler(handler)
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))

        tok = {"provider": "1secmail", "login": "u", "domain": "1secmail.com"}
        events = list(mail_handler.poll_for_code(tok, timeout=10, interval=0))
        assert ("code", "987654") in events

    def test_times_out_immediately(self, httpserver, monkeypatch):
        _patch_bases(monkeypatch, onesec_url=httpserver.url_for(""))
        tok = {"provider": "1secmail", "login": "u", "domain": "1secmail.com"}
        with pytest.raises(TimeoutError):
            list(mail_handler.poll_for_code(tok, timeout=-1, interval=0))


# ─── Fallback flow (two servers) ─────────────────────────────────────────────

class TestFallbackBetweenProviders:
    def test_falls_back_to_1secmail_when_mailtm_500(self, monkeypatch):
        """When mail.tm returns 500, create_account must use 1secmail."""
        with HTTPServer() as mailtm_srv, HTTPServer() as onesec_srv:
            mailtm_srv.expect_request("/domains").respond_with_data(
                "Service Unavailable", status=503
            )
            onesec_srv.expect_request("/").respond_with_handler(
                lambda r: _json_resp(["fallback.test"])
                if r.args.get("action") == "getDomainList"
                else Response("", status=404)
            )

            monkeypatch.setattr(
                mail_handler, "MAILTM_BASE", mailtm_srv.url_for("").rstrip("/")
            )
            monkeypatch.setattr(
                mail_handler, "ONESEC_BASE", onesec_srv.url_for("/")
            )

            email, tok = mail_handler.create_account(providers=["mailtm", "1secmail"])

        assert tok["provider"] == "1secmail"
        assert "@fallback.test" in email

    def test_both_providers_fail_raises_runtime_error(self, monkeypatch):
        with HTTPServer() as mailtm_srv, HTTPServer() as onesec_srv:
            mailtm_srv.expect_request("/domains").respond_with_data("", status=500)
            onesec_srv.expect_request("/").respond_with_data("", status=500)

            monkeypatch.setattr(
                mail_handler, "MAILTM_BASE", mailtm_srv.url_for("").rstrip("/")
            )
            monkeypatch.setattr(
                mail_handler, "ONESEC_BASE", onesec_srv.url_for("/")
            )

            with pytest.raises(RuntimeError, match="All providers failed"):
                mail_handler.create_account(providers=["mailtm", "1secmail"])

    @patch("mail_handler.time.sleep", return_value=None)
    def test_full_flow_mailtm_create_then_poll(self, _sleep, monkeypatch):
        """End-to-end: create via mail.tm, poll for code, find it."""
        with HTTPServer() as srv:
            call_n = [0]

            def domains(req):
                return _json_resp({"hydra:member": [{"domain": "e2e.test", "isActive": True}]})

            def accounts(req):
                return _json_resp({}, status=201)

            def token_fn(req):
                return _json_resp({"token": "e2e-bearer"})

            def messages(req):
                call_n[0] += 1
                if call_n[0] == 1:
                    return _json_resp({"hydra:member": []})
                return _json_resp({"hydra:member": [{"id": "e2e-msg"}]})

            def message(req):
                return _json_resp({"text": "Your Taco Bell code: 204816", "html": ""})

            srv.expect_request("/domains").respond_with_handler(domains)
            srv.expect_request("/accounts", method="POST").respond_with_handler(accounts)
            srv.expect_request("/token", method="POST").respond_with_handler(token_fn)
            srv.expect_request("/messages").respond_with_handler(messages)
            srv.expect_request("/messages/e2e-msg").respond_with_handler(message)

            monkeypatch.setattr(mail_handler, "MAILTM_BASE", srv.url_for("").rstrip("/"))

            email, tok = mail_handler.create_account(providers=["mailtm"])
            assert "@e2e.test" in email

            events = list(mail_handler.poll_for_code(tok, timeout=30, interval=0))

        assert ("waiting", None) in events
        assert ("code", "204816") in events

    @patch("mail_handler.time.sleep", return_value=None)
    def test_full_flow_1secmail_create_then_poll(self, _sleep, monkeypatch):
        """End-to-end: create via 1secmail, poll for code, find it."""
        with HTTPServer() as srv:
            def handler(req):
                action = req.args.get("action")
                if action == "getDomainList":
                    return _json_resp(["e2e.test"])
                if action == "getMessages":
                    return _json_resp([{"id": 99}])
                if action == "readMessage":
                    return _json_resp({
                        "textBody": "Sign in: 736291",
                        "htmlBody": "",
                    })
                return Response("", status=404)

            srv.expect_request("/").respond_with_handler(handler)
            monkeypatch.setattr(mail_handler, "ONESEC_BASE", srv.url_for("/"))

            email, tok = mail_handler.create_account(providers=["1secmail"])
            assert "@e2e.test" in email

            events = list(mail_handler.poll_for_code(tok, timeout=30, interval=0))

        assert ("code", "736291") in events
