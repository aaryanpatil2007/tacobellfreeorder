"""
Disposable email helper for Taco Bell OTP sign-in.

Providers (tried in order):
  1. mail.tm   – REST API, no browser, good deliverability
  2. 1secmail  – Ultra-simple API, large domain pool

Public API
----------
  email, token = create_account()
  for event, value in poll_for_code(token, timeout=180, interval=4):
      # event == "waiting"  → still polling
      # event == "code"     → value is the 6-digit string
  # raises TimeoutError if nothing arrives in time
"""

import random
import re
import string
import time
from html.parser import HTMLParser

import requests

# ─── Constants ───────────────────────────────────────────────────────────────

MAILTM_BASE = "https://api.mail.tm"
ONESEC_BASE = "https://www.1secmail.com/api/v1/"

DEFAULT_PROVIDERS = ["mailtm", "1secmail"]


# ─── HTML stripping ───────────────────────────────────────────────────────────

class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return " ".join(self.fed)


def _strip_html(html_text: str) -> str:
    """Return plain text from an HTML string."""
    if not html_text:
        return ""
    s = _Stripper()
    try:
        s.feed(html_text)
        return s.get_data()
    except Exception:
        # Fallback: crude tag removal
        return re.sub(r"<[^>]+>", " ", html_text)


# ─── Code extraction ──────────────────────────────────────────────────────────

def _find_code(text: str):
    """Return first isolated 6-digit sequence from text, or None."""
    m = re.search(r"\b([0-9]{6})\b", text)
    return m.group(1) if m else None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _random_str(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ─── mail.tm provider ────────────────────────────────────────────────────────

def _mailtm_create():
    """Create a mail.tm inbox. Returns (email, token_dict)."""
    r = requests.get(f"{MAILTM_BASE}/domains", timeout=10)
    r.raise_for_status()
    domains = r.json().get("hydra:member", [])
    domain = next((d["domain"] for d in domains if d.get("isActive")), None)
    if not domain:
        raise RuntimeError("mail.tm: no active domain available")

    email = f"{_random_str()}@{domain}"
    password = _random_str(16)

    r = requests.post(
        f"{MAILTM_BASE}/accounts",
        json={"address": email, "password": password},
        timeout=10,
    )
    r.raise_for_status()

    r = requests.post(
        f"{MAILTM_BASE}/token",
        json={"address": email, "password": password},
        timeout=10,
    )
    r.raise_for_status()
    bearer = r.json()["token"]

    return email, {"provider": "mailtm", "bearer": bearer}


def _mailtm_poll(tok, timeout, interval):
    headers = {"Authorization": f"Bearer {tok['bearer']}"}
    deadline = time.time() + timeout
    seen = set()

    while time.time() < deadline:
        try:
            r = requests.get(f"{MAILTM_BASE}/messages", headers=headers, timeout=10)
            r.raise_for_status()
            for msg in r.json().get("hydra:member", []):
                mid = msg["id"]
                if mid in seen:
                    continue
                seen.add(mid)

                r2 = requests.get(
                    f"{MAILTM_BASE}/messages/{mid}", headers=headers, timeout=10
                )
                r2.raise_for_status()
                data = r2.json()
                combined = data.get("text", "") + " " + _strip_html(data.get("html", ""))
                code = _find_code(combined)
                if code:
                    yield ("code", code)
                    return
        except Exception:
            pass  # network hiccup — keep going

        yield ("waiting", None)
        time.sleep(interval)

    raise TimeoutError("No verification email received within timeout.")


# ─── 1secmail provider ────────────────────────────────────────────────────────

def _onesec_create():
    """Create a 1secmail inbox. Returns (email, token_dict)."""
    r = requests.get(f"{ONESEC_BASE}?action=getDomainList", timeout=10)
    r.raise_for_status()
    domains = r.json()
    if not domains:
        raise RuntimeError("1secmail: no domains available")
    domain = random.choice(domains)
    login = _random_str(10)
    email = f"{login}@{domain}"
    return email, {"provider": "1secmail", "login": login, "domain": domain}


def _onesec_poll(tok, timeout, interval):
    login, domain = tok["login"], tok["domain"]
    deadline = time.time() + timeout
    seen = set()

    while time.time() < deadline:
        try:
            r = requests.get(
                ONESEC_BASE,
                params={"action": "getMessages", "login": login, "domain": domain},
                timeout=10,
            )
            r.raise_for_status()
            for msg in r.json():
                mid = msg["id"]
                if mid in seen:
                    continue
                seen.add(mid)

                r2 = requests.get(
                    ONESEC_BASE,
                    params={
                        "action": "readMessage",
                        "login": login,
                        "domain": domain,
                        "id": mid,
                    },
                    timeout=10,
                )
                r2.raise_for_status()
                data = r2.json()
                combined = data.get("textBody", "") + " " + _strip_html(
                    data.get("htmlBody", "")
                )
                code = _find_code(combined)
                if code:
                    yield ("code", code)
                    return
        except Exception:
            pass  # network hiccup — keep going

        yield ("waiting", None)
        time.sleep(interval)

    raise TimeoutError("No verification email received within timeout.")


# ─── Public API ───────────────────────────────────────────────────────────────

def create_account(providers=None):
    """
    Create a disposable inbox, trying providers in order.

    Parameters
    ----------
    providers : list[str] | None
        Provider names to try. Defaults to ["mailtm", "1secmail"].

    Returns
    -------
    (email: str, token: dict)
        token is an opaque dict passed back to poll_for_code().
    """
    if providers is None:
        providers = DEFAULT_PROVIDERS

    last_err = None
    for name in providers:
        try:
            if name == "mailtm":
                return _mailtm_create()
            elif name == "1secmail":
                return _onesec_create()
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"All providers failed. Last error: {last_err}")


def poll_for_code(token, timeout=180, interval=4):
    """
    Generator: yields ("waiting", None) each polling cycle,
    then ("code", "123456") when found.
    Raises TimeoutError if no code arrives within `timeout` seconds.
    """
    provider = token.get("provider")
    if provider == "mailtm":
        yield from _mailtm_poll(token, timeout, interval)
    elif provider == "1secmail":
        yield from _onesec_poll(token, timeout, interval)
    else:
        raise ValueError(f"Unknown provider: {provider!r}")
