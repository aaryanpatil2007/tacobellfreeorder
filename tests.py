"""
Comprehensive tests for mail_handler.py
Run: python3 -m pytest tests.py -v
"""

import pytest
from unittest.mock import patch, MagicMock, call

import mail_handler


# ─── _strip_html ─────────────────────────────────────────────────────────────

class TestStripHtml:
    def test_simple_paragraph(self):
        assert "hello world" in mail_handler._strip_html("<p>hello world</p>")

    def test_nested_tags(self):
        result = mail_handler._strip_html("<div><span>code: 123456</span></div>")
        assert "123456" in result

    def test_empty_string(self):
        assert mail_handler._strip_html("") == ""

    def test_plain_text_passthrough(self):
        result = mail_handler._strip_html("plain text no tags")
        assert "plain text no tags" in result

    def test_bold_with_code(self):
        html = "<p>Your sign-in code is <b>482910</b></p>"
        assert "482910" in mail_handler._strip_html(html)

    def test_anchor_tag(self):
        html = '<a href="https://example.com">Click here</a>'
        result = mail_handler._strip_html(html)
        assert "Click here" in result
        assert "href" not in result

    def test_complex_taco_bell_style_email(self):
        html = """
        <html><body>
          <table><tr><td>
            <h1>Sign in to Taco Bell</h1>
            <p>Your verification code is:</p>
            <div style="font-size:32px"><strong>391847</strong></div>
            <p>This code expires in 10 minutes.</p>
          </td></tr></table>
        </body></html>
        """
        result = mail_handler._strip_html(html)
        assert "391847" in result

    def test_none_input_via_empty_string(self):
        # Passing empty string (common defensive case)
        assert mail_handler._strip_html("") == ""

    def test_entities_preserved(self):
        # HTMLParser handles entities; text should be extractable
        result = mail_handler._strip_html("<p>code&nbsp;123456</p>")
        assert "123456" in result


# ─── _find_code ───────────────────────────────────────────────────────────────

class TestFindCode:
    def test_bare_6_digit(self):
        assert mail_handler._find_code("Your code: 123456") == "123456"

    def test_code_mid_sentence(self):
        assert mail_handler._find_code("Use code 482910 to log in.") == "482910"

    def test_no_code_returns_none(self):
        assert mail_handler._find_code("No numbers here at all") is None

    def test_five_digit_ignored(self):
        assert mail_handler._find_code("Call 12345 now") is None

    def test_seven_digit_not_matched_as_6(self):
        # Word boundary prevents 6-of-7 match
        assert mail_handler._find_code("Order #1234567 confirmed") is None

    def test_multiple_codes_returns_first(self):
        assert mail_handler._find_code("111111 or 222222") == "111111"

    def test_code_at_start_of_string(self):
        assert mail_handler._find_code("482910 is your verification code") == "482910"

    def test_code_at_end_of_string(self):
        assert mail_handler._find_code("Your verification code is 482910") == "482910"

    def test_code_extracted_from_stripped_html(self):
        html = "<p>Your code is <strong>739201</strong></p>"
        text = mail_handler._strip_html(html)
        assert mail_handler._find_code(text) == "739201"

    def test_code_only_digits(self):
        # Letters should not count as digits
        assert mail_handler._find_code("abcdef") is None

    def test_code_surrounded_by_punctuation(self):
        assert mail_handler._find_code("[482910]") == "482910"

    def test_code_in_multiline_text(self):
        text = "Hello,\n\nYour code is 938471.\n\nThanks."
        assert mail_handler._find_code(text) == "938471"


# ─── create_account – mail.tm ────────────────────────────────────────────────

class TestCreateAccountMailTm:
    def _domain_resp(self):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = {
            "hydra:member": [{"domain": "fake.mail", "isActive": True}]
        }
        return r

    def _acct_resp(self):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        return r

    def _token_resp(self, bearer="test-bearer"):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = {"token": bearer}
        return r

    @patch("mail_handler.requests.post")
    @patch("mail_handler.requests.get")
    def test_creates_email_with_correct_domain(self, mock_get, mock_post):
        mock_get.return_value = self._domain_resp()
        mock_post.side_effect = [self._acct_resp(), self._token_resp()]

        email, token = mail_handler.create_account(providers=["mailtm"])

        assert "@fake.mail" in email
        assert token["provider"] == "mailtm"
        assert token["bearer"] == "test-bearer"

    @patch("mail_handler.requests.post")
    @patch("mail_handler.requests.get")
    def test_email_has_random_local_part(self, mock_get, mock_post):
        mock_get.return_value = self._domain_resp()
        mock_post.side_effect = [self._acct_resp(), self._token_resp()]

        email, _ = mail_handler.create_account(providers=["mailtm"])
        local = email.split("@")[0]
        assert len(local) > 0

    @patch("mail_handler.requests.post")
    @patch("mail_handler.requests.get")
    def test_raises_when_no_active_domain(self, mock_get, mock_post):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = {
            "hydra:member": [{"domain": "fake.mail", "isActive": False}]
        }
        mock_get.return_value = r

        with pytest.raises(RuntimeError):
            mail_handler.create_account(providers=["mailtm"])

    @patch("mail_handler.requests.get")
    def test_falls_back_to_1secmail_on_mailtm_network_error(self, mock_get):
        failing = MagicMock()
        failing.raise_for_status.side_effect = Exception("network error")

        onesec = MagicMock()
        onesec.raise_for_status = MagicMock()
        onesec.json.return_value = ["1secmail.com", "1secmail.net"]

        mock_get.side_effect = [failing, onesec]

        email, token = mail_handler.create_account(providers=["mailtm", "1secmail"])

        assert token["provider"] == "1secmail"
        assert "@" in email

    @patch("mail_handler.requests.get")
    def test_raises_runtime_when_all_providers_fail(self, mock_get):
        r = MagicMock()
        r.raise_for_status.side_effect = Exception("down")
        mock_get.return_value = r

        with pytest.raises(RuntimeError, match="All providers failed"):
            mail_handler.create_account(providers=["mailtm", "1secmail"])


# ─── create_account – 1secmail ───────────────────────────────────────────────

class TestCreateAccount1SecMail:
    @patch("mail_handler.requests.get")
    def test_creates_account_with_valid_domain(self, mock_get):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = ["1secmail.com", "1secmail.net", "1secmail.org"]
        mock_get.return_value = r

        email, token = mail_handler.create_account(providers=["1secmail"])

        assert token["provider"] == "1secmail"
        assert "@" in email
        assert token["login"] in email
        assert token["domain"] in email
        assert token["domain"] in ["1secmail.com", "1secmail.net", "1secmail.org"]

    @patch("mail_handler.requests.get")
    def test_raises_when_no_domains(self, mock_get):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = []
        mock_get.return_value = r

        with pytest.raises(RuntimeError, match="All providers failed"):
            mail_handler.create_account(providers=["1secmail"])


# ─── poll_for_code – mail.tm ─────────────────────────────────────────────────

class TestPollForCodeMailTm:
    def _token(self):
        return {"provider": "mailtm", "bearer": "fake-bearer"}

    def _empty_inbox(self):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = {"hydra:member": []}
        return r

    def _inbox_with(self, msg_id="msg-1"):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = {"hydra:member": [{"id": msg_id}]}
        return r

    def _message(self, text="", html=""):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = {"text": text, "html": html}
        return r

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_finds_code_in_text_body(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._inbox_with(),
            self._message(text="Your code is 482910"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "482910") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_finds_code_in_html_body(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._inbox_with(),
            self._message(html="<p>Sign in code: <b>739201</b></p>"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "739201") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_yields_waiting_on_empty_inbox(self, mock_get, _sleep):
        # First poll empty, second has code
        mock_get.side_effect = [
            self._empty_inbox(),
            self._inbox_with(),
            self._message(text="Code: 391847"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("waiting", None) in events
        assert ("code", "391847") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_waiting_appears_before_code(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._empty_inbox(),
            self._inbox_with(),
            self._message(text="Code: 291847"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        waiting_idx = events.index(("waiting", None))
        code_idx = next(i for i, e in enumerate(events) if e[0] == "code")
        assert waiting_idx < code_idx

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_skips_already_seen_message_ids(self, mock_get, _sleep):
        # Same message returned twice; should only fetch body once
        mock_get.side_effect = [
            self._inbox_with("msg-abc"),
            self._message(text="Code: 123456"),
            self._inbox_with("msg-abc"),   # second poll — same msg already seen
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        # Body fetched only once
        assert mock_get.call_count == 2
        assert ("code", "123456") in events

    def test_raises_timeout_immediately_when_deadline_past(self):
        with pytest.raises(TimeoutError):
            list(mail_handler.poll_for_code(self._token(), timeout=-1, interval=0))

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_survives_network_hiccup(self, mock_get, _sleep):
        # First GET raises; second poll succeeds with code
        mock_get.side_effect = [
            Exception("connection reset"),
            self._inbox_with(),
            self._message(text="Code: 847291"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "847291") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_code_from_html_only_email(self, mock_get, _sleep):
        """Taco Bell emails are often HTML-only."""
        html = """
        <html><body>
          <p>Welcome!</p>
          <p>Your one-time sign-in code is:</p>
          <h2>938271</h2>
          <p>This code expires in 15 minutes.</p>
        </body></html>
        """
        mock_get.side_effect = [
            self._inbox_with(),
            self._message(text="", html=html),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "938271") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_new_message_after_seen_one_without_code(self, mock_get, _sleep):
        """First email has no code; second does."""
        mock_get.side_effect = [
            self._inbox_with("msg-1"),
            self._message(text="Welcome to Taco Bell!"),   # no code
            self._inbox_with("msg-2"),
            self._message(text="Your sign-in code: 482910"),
        ]
        # Two polls, so two "inbox" GETs: first returns msg-1, second returns msg-2
        mock_get.side_effect = [
            MagicMock(**{
                "raise_for_status": MagicMock(),
                "json.return_value": {"hydra:member": [{"id": "msg-1"}]},
            }),
            self._message(text="Welcome, no code here"),
            MagicMock(**{
                "raise_for_status": MagicMock(),
                "json.return_value": {"hydra:member": [{"id": "msg-1"}, {"id": "msg-2"}]},
            }),
            self._message(text="Your sign-in code: 482910"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "482910") in events


# ─── poll_for_code – 1secmail ────────────────────────────────────────────────

class TestPollForCode1SecMail:
    def _token(self):
        return {"provider": "1secmail", "login": "testuser", "domain": "1secmail.com"}

    def _empty_inbox(self):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = []
        return r

    def _inbox_with(self, msg_id=42):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = [{"id": msg_id}]
        return r

    def _message(self, text="", html=""):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json.return_value = {"textBody": text, "htmlBody": html}
        return r

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_finds_code_in_text_body(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._inbox_with(),
            self._message(text="Your code is 123456"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "123456") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_finds_code_in_html_body(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._inbox_with(),
            self._message(html="<div>Code: <b>654321</b></div>"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "654321") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_yields_waiting_on_empty_inbox(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._empty_inbox(),
            self._inbox_with(),
            self._message(text="Code: 999888"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("waiting", None) in events
        assert ("code", "999888") in events

    def test_raises_timeout_immediately_when_deadline_past(self):
        with pytest.raises(TimeoutError):
            list(mail_handler.poll_for_code(self._token(), timeout=-1, interval=0))

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_survives_network_hiccup(self, mock_get, _sleep):
        mock_get.side_effect = [
            Exception("timeout"),
            self._inbox_with(),
            self._message(text="Code: 847291"),
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert ("code", "847291") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_skips_seen_messages(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._inbox_with(10),
            self._message(text="Code: 112233"),
            self._inbox_with(10),   # same msg returned again
        ]
        events = list(mail_handler.poll_for_code(self._token(), timeout=30, interval=0))
        assert mock_get.call_count == 2
        assert ("code", "112233") in events


# ─── poll_for_code – unknown provider ────────────────────────────────────────

class TestPollForCodeUnknownProvider:
    def test_raises_value_error(self):
        token = {"provider": "notreal"}
        with pytest.raises(ValueError, match="Unknown provider"):
            list(mail_handler.poll_for_code(token, timeout=1, interval=0))


# ─── Full integration flows (all mocked) ─────────────────────────────────────

class TestIntegrationFlows:
    """End-to-end flow: create_account → poll_for_code, fully mocked."""

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.post")
    @patch("mail_handler.requests.get")
    def test_full_mailtm_flow_with_waiting_then_code(self, mock_get, mock_post, _sleep):
        # create_account: GET /domains
        domains_resp = MagicMock()
        domains_resp.raise_for_status = MagicMock()
        domains_resp.json.return_value = {
            "hydra:member": [{"domain": "mail.fake", "isActive": True}]
        }
        # poll: first empty, then code
        empty = MagicMock()
        empty.raise_for_status = MagicMock()
        empty.json.return_value = {"hydra:member": []}

        inbox = MagicMock()
        inbox.raise_for_status = MagicMock()
        inbox.json.return_value = {"hydra:member": [{"id": "m1"}]}

        msg = MagicMock()
        msg.raise_for_status = MagicMock()
        msg.json.return_value = {"text": "Taco Bell sign-in code: 847291", "html": ""}

        mock_get.side_effect = [domains_resp, empty, inbox, msg]

        acct = MagicMock()
        acct.raise_for_status = MagicMock()
        tok = MagicMock()
        tok.raise_for_status = MagicMock()
        tok.json.return_value = {"token": "bearer-abc"}
        mock_post.side_effect = [acct, tok]

        email, token = mail_handler.create_account(providers=["mailtm"])
        assert "@mail.fake" in email

        events = list(mail_handler.poll_for_code(token, timeout=60, interval=0))
        assert ("waiting", None) in events
        assert ("code", "847291") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.get")
    def test_full_1secmail_flow(self, mock_get, _sleep):
        domains_resp = MagicMock()
        domains_resp.raise_for_status = MagicMock()
        domains_resp.json.return_value = ["1secmail.com"]

        msgs_resp = MagicMock()
        msgs_resp.raise_for_status = MagicMock()
        msgs_resp.json.return_value = [{"id": 99}]

        msg_detail = MagicMock()
        msg_detail.raise_for_status = MagicMock()
        msg_detail.json.return_value = {
            "textBody": "Sign in with code 291847",
            "htmlBody": "",
        }

        mock_get.side_effect = [domains_resp, msgs_resp, msg_detail]

        email, token = mail_handler.create_account(providers=["1secmail"])
        assert "@1secmail.com" in email

        events = list(mail_handler.poll_for_code(token, timeout=60, interval=0))
        assert ("code", "291847") in events

    @patch("mail_handler.time.sleep", return_value=None)
    @patch("mail_handler.requests.post")
    @patch("mail_handler.requests.get")
    def test_fallback_from_mailtm_to_1secmail_full_flow(
        self, mock_get, mock_post, _sleep
    ):
        # mailtm fails
        mailtm_fail = MagicMock()
        mailtm_fail.raise_for_status.side_effect = Exception("503 Service Unavailable")

        # 1secmail create
        onesec_domains = MagicMock()
        onesec_domains.raise_for_status = MagicMock()
        onesec_domains.json.return_value = ["1secmail.com"]

        # 1secmail poll
        msgs = MagicMock()
        msgs.raise_for_status = MagicMock()
        msgs.json.return_value = [{"id": 7}]

        msg = MagicMock()
        msg.raise_for_status = MagicMock()
        msg.json.return_value = {
            "textBody": "Your verification code: 112233",
            "htmlBody": "",
        }

        mock_get.side_effect = [mailtm_fail, onesec_domains, msgs, msg]

        email, token = mail_handler.create_account(providers=["mailtm", "1secmail"])
        assert token["provider"] == "1secmail"

        events = list(mail_handler.poll_for_code(token, timeout=60, interval=0))
        assert ("code", "112233") in events
