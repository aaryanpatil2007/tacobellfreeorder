"""
Taco Bell Free Order Helper
────────────────────────────
1. Creates a disposable email via Guerrilla Mail API (no browser needed)
2. Opens Taco Bell sign-up in your REAL browser (no bot detection)
3. Polls Guerrilla Mail inbox via API every 5 seconds
4. Displays the verification code the moment it arrives
"""

import re
import subprocess
import platform
import sys
import time
import requests

GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"
REGISTER_URL  = "https://www.tacobell.com/register/yum"
POLL_INTERVAL = 5    # seconds between inbox checks
TIMEOUT       = 300  # 5 minutes


# ─── email helpers ──────────────────────────────────────────────────────────────

def gen_email() -> tuple:
    """Get a fresh Guerrilla Mail address and session token."""
    r = requests.get(GUERRILLA_API, params={"f": "get_email_address"}, timeout=10)
    r.raise_for_status()
    data = r.json()
    email = data["email_addr"]
    token = data["sid_token"]
    return email, token


def check_inbox(token: str, seq: int = 0) -> list:
    r = requests.get(
        GUERRILLA_API,
        params={"f": "check_email", "sid_token": token, "seq": seq},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("list", [])


def fetch_email(token: str, email_id: str) -> str:
    r = requests.get(
        GUERRILLA_API,
        params={"f": "fetch_email", "sid_token": token, "email_id": email_id},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("mail_body", "") + " " + data.get("mail_excerpt", "")


# ─── browser opener ─────────────────────────────────────────────────────────────

def open_url(url: str) -> None:
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", url])
        elif system == "Linux":
            subprocess.Popen(["xdg-open", url])
        elif system == "Windows":
            subprocess.Popen(["start", url], shell=True)
    except Exception:
        pass


# ─── inbox poller ───────────────────────────────────────────────────────────────

def poll_for_code(token: str, timeout: int = TIMEOUT) -> tuple:
    """
    Poll Guerrilla Mail until a 6-digit code or verification link appears.
    Returns ("code", "123456") or ("link", "https://…").
    """
    seen: set = set()
    deadline = time.time() + timeout

    print("  ", end="", flush=True)

    while time.time() < deadline:
        try:
            msgs = check_inbox(token)
            for msg in msgs:
                mid = str(msg.get("mail_id", ""))
                if not mid or mid in seen:
                    continue
                seen.add(mid)

                body = fetch_email(token, mid)

                # 6-digit verification code?
                m = re.search(r"\b(\d{6})\b", body)
                if m:
                    print()
                    return "code", m.group(1)

                # Verification/activation link?
                m2 = re.search(
                    r'https?://[^\s"\'<>]*(verif|confirm|activ|account)[^\s"\'<>]*',
                    body,
                    re.IGNORECASE,
                )
                if m2:
                    print()
                    return "link", m2.group(0)

        except Exception:
            pass  # network hiccup — keep going

        print(".", end="", flush=True)
        time.sleep(POLL_INTERVAL)

    raise TimeoutError


# ─── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 50)
    print("   Taco Bell Free Order Helper")
    print("=" * 50)
    print()

    # ── Step 1: generate temp email ────────────────────
    print("[ 1 / 3 ]  Generating disposable email...")
    email, token = gen_email()
    print()
    print("   YOUR EMAIL:")
    print(f"   >>> {email} <<<")
    print()

    # ── Step 2: open Taco Bell in real browser ─────────
    print("[ 2 / 3 ]  Opening Taco Bell sign-up in your browser...")
    open_url(REGISTER_URL)
    print()
    print("  ┌──────────────────────────────────────────────────┐")
    print("  │  In the browser window that just opened:         │")
    print("  │                                                  │")
    print("  │  1. Accept cookies if prompted                   │")
    print("  │  2. Enter this email:                            │")
    print(f"  │     {email:<46}  │")
    print("  │  3. Click CONFIRM, fill name + password, submit  │")
    print("  │                                                  │")
    print("  │  Inbox is checked automatically every 5 seconds! │")
    print("  └──────────────────────────────────────────────────┘")
    print()

    # ── Step 3: poll inbox ─────────────────────────────
    print("[ 3 / 3 ]  Monitoring inbox (checking every 5 s, up to 5 min)...")
    print()

    result_type, result_value = poll_for_code(token)

    # ── Display result ─────────────────────────────────
    print()
    print("=" * 50)
    if result_type == "code":
        print("   VERIFICATION CODE:")
        print(f"   >>> {result_value} <<<")
        print("=" * 50)
        print()
        print("   Enter that code on the Taco Bell site.")
    else:
        print("   VERIFICATION LINK — opening in browser...")
        print("=" * 50)
        print()
        print(f"   {result_value}")
        open_url(result_value)
        print()
        print("   Your account is now being verified.")
    print()


if __name__ == "__main__":
    try:
        main()
    except TimeoutError:
        print("\n   ERROR: No verification email after 5 minutes. Try running again.")
    except KeyboardInterrupt:
        print("\n   Cancelled.")
    except Exception as e:
        print(f"\n   ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
