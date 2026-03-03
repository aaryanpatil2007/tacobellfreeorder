"""
Taco Bell Free Order Helper
────────────────────────────
1. Creates a disposable email via mail.tm or 1secmail (no browser needed)
2. Opens the Taco Bell sign-in page in your real browser (no bot detection)
3. Polls the disposable inbox via API every 4 seconds
4. Displays the 6-digit OTP the moment it arrives
"""

import platform
import subprocess
import sys
import time

import mail_handler

SIGNIN_URL = "https://www.tacobell.com/login"
TIMEOUT = 300   # 5 minutes
INTERVAL = 4    # seconds between inbox checks


# ─── Utilities ───────────────────────────────────────────────────────────────

def open_url(url: str) -> None:
    """Open a URL in the default system browser."""
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


# ─── Main flow ────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 54)
    print("   Taco Bell Free Order Helper")
    print("=" * 54)
    print()

    # ── Step 1: generate disposable email ────────────────────
    print("[ 1 / 3 ]  Generating disposable email...")
    try:
        email, token = mail_handler.create_account()
    except RuntimeError as e:
        print(f"\n   ERROR: Could not create temp email – {e}")
        sys.exit(1)

    provider = token.get("provider", "unknown")
    print()
    print("   YOUR EMAIL:")
    print(f"   >>> {email} <<<")
    print(f"   (via {provider})")
    print()

    # ── Step 2: open Taco Bell sign-in in real browser ────────
    print("[ 2 / 3 ]  Opening Taco Bell sign-in in your browser...")
    open_url(SIGNIN_URL)
    print()

    pad = max(0, 50 - len(email))
    print("  ┌──────────────────────────────────────────────────────┐")
    print("  │  In the browser window that just opened:             │")
    print("  │                                                       │")
    print("  │  1. Accept any cookies / prompts                     │")
    print("  │  2. Enter this email address:                        │")
    print(f"  │     {email}{' ' * pad}  │")
    print("  │  3. Click  Continue / Send Code                      │")
    print("  │                                                       │")
    print("  │  The inbox is checked automatically every 4 seconds! │")
    print("  └──────────────────────────────────────────────────────┘")
    print()

    # ── Step 3: poll inbox ────────────────────────────────────
    print(
        f"[ 3 / 3 ]  Monitoring inbox "
        f"(checking every {INTERVAL} s, up to 5 min)..."
    )
    print("  ", end="", flush=True)

    code = None
    try:
        for event, value in mail_handler.poll_for_code(
            token, timeout=TIMEOUT, interval=INTERVAL
        ):
            if event == "waiting":
                print(".", end="", flush=True)
            elif event == "code":
                code = value
                break
    except TimeoutError:
        print()
        print()
        print("   ERROR: No verification email arrived after 5 minutes.")
        print()
        print("   Tip: If Taco Bell showed a loading spinner when you")
        print("   entered the email, that domain is blocked. Run the")
        print("   script again to get a fresh address on a new domain.")
        sys.exit(1)

    # ── Display result ────────────────────────────────────────
    print()
    print()
    print("=" * 54)
    print("   VERIFICATION CODE:")
    print(f"   >>> {code} <<<")
    print("=" * 54)
    print()
    print("   Enter that code in the Taco Bell app or website to sign in.")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n   Cancelled.")
    except Exception as e:
        print(f"\n   ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
