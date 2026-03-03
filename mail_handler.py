import requests
import random
import string
import re
import time

BASE = "https://api.mail.tm"


def _random_str(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def create_account():
    """Create a fresh mail.tm account. Returns (email, password, token)."""
    # Get an active domain
    r = requests.get(f"{BASE}/domains", timeout=10)
    r.raise_for_status()
    domains = r.json().get("hydra:member", [])
    domain = next(d["domain"] for d in domains if d.get("isActive"))

    email = f"{_random_str()}@{domain}"
    password = _random_str(16)

    # Create account
    r = requests.post(f"{BASE}/accounts", json={"address": email, "password": password}, timeout=10)
    r.raise_for_status()

    # Get token
    r = requests.post(f"{BASE}/token", json={"address": email, "password": password}, timeout=10)
    r.raise_for_status()
    token = r.json()["token"]

    return email, password, token


def poll_for_code(token, timeout=180, interval=4):
    """
    Poll the inbox every `interval` seconds for up to `timeout` seconds.
    Yields status strings as it goes, finally yields the code string.
    Raises TimeoutError if nothing arrives.
    """
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    seen = set()

    while time.time() < deadline:
        r = requests.get(f"{BASE}/messages", headers=headers, timeout=10)
        r.raise_for_status()
        messages = r.json().get("hydra:member", [])

        for msg in messages:
            mid = msg["id"]
            if mid in seen:
                continue
            seen.add(mid)

            # Fetch full message body
            r2 = requests.get(f"{BASE}/messages/{mid}", headers=headers, timeout=10)
            r2.raise_for_status()
            body = r2.json().get("text", "") + r2.json().get("html", "")

            # Look for a 6-digit code
            match = re.search(r"\b(\d{6})\b", body)
            if match:
                yield ("code", match.group(1))
                return

        yield ("waiting", None)
        time.sleep(interval)

    raise TimeoutError("No verification email received within timeout.")
