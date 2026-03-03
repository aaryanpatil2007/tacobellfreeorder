"""
Taco Bell Free Order Helper
────────────────────────────
1. Opens temp-mail.org in real Chrome via Playwright to grab a fresh email
2. Opens Taco Bell sign-up in a separate real browser window (no bot detection)
3. Monitors the temp-mail.org inbox automatically
4. Displays the verification code the moment it arrives
"""

import asyncio
import re
import subprocess
import platform
import sys
import time

from playwright.async_api import async_playwright, Page

REGISTER_URL  = "https://www.tacobell.com/register/yum"
TEMPMAIL_URL  = "https://temp-mail.org/en/"
POLL_INTERVAL = 5    # seconds between inbox checks
TIMEOUT       = 300  # 5 minutes


# ─── browser opener (for Taco Bell — user's default browser) ──────────────────

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


# ─── temp-mail.org helpers ────────────────────────────────────────────────────

async def dismiss_overlays(page: Page) -> None:
    """Click away cookie banners and popups if present."""
    for selector in [
        "button[id*='accept']",
        "button[class*='accept']",
        "button[class*='consent']",
        "button[class*='agree']",
        "[aria-label*='Accept']",
        "[aria-label*='Close']",
        ".fc-button-label",          # Funding Choices (Google CMP)
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await asyncio.sleep(0.5)
        except Exception:
            pass


async def get_temp_email(page: Page) -> str:
    """Navigate to temp-mail.org and return the auto-generated email."""
    await page.goto(TEMPMAIL_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)
    await dismiss_overlays(page)

    email_loc = page.locator("#mail")
    await email_loc.wait_for(state="visible", timeout=20000)

    for _ in range(40):
        val = await email_loc.input_value()
        if val and "@" in val:
            return val
        await asyncio.sleep(1)

    raise RuntimeError("temp-mail.org did not generate an email in time")


async def poll_tempmail(page: Page, email: str, timeout: int = TIMEOUT) -> tuple:
    """
    Reload temp-mail.org and scan inbox for the verification email.
    Returns ("code", "123456") or ("link", "https://…").
    """
    seen: set = set()
    deadline = time.time() + timeout

    print("  ", end="", flush=True)

    while time.time() < deadline:
        try:
            await page.reload(wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)
            await dismiss_overlays(page)

            # Use JS to find inbox items with real email content.
            # We filter out empty placeholders and column headers (short/single words).
            inbox_items = await page.evaluate("""
                () => {
                    const HEADERS = new Set(['sender','subject','view','date','from','received','inbox']);
                    const selectors = [
                        '.inbox-dataList li',
                        '.inbox-dataList .message',
                        '[class*="message-item"]',
                    ];
                    for (const sel of selectors) {
                        const items = [...document.querySelectorAll(sel)];
                        const found = items
                            .map((el, i) => ({
                                index: i,
                                sel: sel,
                                text: (el.innerText || '').trim().slice(0, 120)
                            }))
                            .filter(x => {
                                const t = x.text;
                                if (t.length < 15) return false;  // too short = placeholder
                                const lower = t.toLowerCase();
                                // single-word column headers
                                if (HEADERS.has(lower)) return false;
                                return true;
                            });
                        if (found.length) return found;
                    }
                    return [];
                }
            """)

            for item in inbox_items:
                key = item["text"][:60]
                if key in seen:
                    continue
                seen.add(key)

                print(f"\n  [email found: {key[:50]}]", end="", flush=True)

                try:
                    await page.locator(item["sel"]).nth(item["index"]).click()
                    # Wait for email content to fully render
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    await asyncio.sleep(3)

                    # Collect text from main page + all iframes
                    combined = await page.inner_text("body")
                    combined += "\n" + await page.content()  # raw HTML too
                    for frame in page.frames[1:]:
                        try:
                            combined += "\n" + await frame.inner_text("body")
                            combined += "\n" + await frame.content()
                        except Exception:
                            pass

                    # 6-digit code
                    m = re.search(r"\b(\d{6})\b", combined)
                    if m:
                        print()
                        return "code", m.group(1)

                    # Verification / activation link (broad match)
                    m2 = re.search(
                        r'https?://[^\s"\'<>]+'
                        r'(?:verif|confirm|activ|account|click|token|magic)[^\s"\'<>]*',
                        combined, re.IGNORECASE,
                    )
                    if m2:
                        print()
                        return "link", m2.group(0)

                    # Any tacobell / yum link as last resort
                    m3 = re.search(
                        r'https?://[^\s"\'<>]*(?:tacobell|yum\.com)[^\s"\'<>]*',
                        combined, re.IGNORECASE,
                    )
                    if m3:
                        print()
                        return "link", m3.group(0)

                    # Didn't find a code — navigate back
                    try:
                        await page.go_back()
                    except Exception:
                        await page.goto(TEMPMAIL_URL, wait_until="domcontentloaded")
                    await asyncio.sleep(2)

                except Exception as e:
                    print(f"\n  [error opening email: {e}]", end="", flush=True)
                    try:
                        await page.goto(TEMPMAIL_URL, wait_until="domcontentloaded")
                    except Exception:
                        pass

        except Exception:
            pass

        print(".", end="", flush=True)
        await asyncio.sleep(POLL_INTERVAL)

    raise TimeoutError


# ─── main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    print()
    print("=" * 54)
    print("   Taco Bell Free Order Helper")
    print("=" * 54)
    print()

    async with async_playwright() as p:
        # Try real installed Chrome first — passes Cloudflare on temp-mail.org.
        # Falls back to bundled Chromium if Chrome isn't installed.
        try:
            browser = await p.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox"],
            )

        context = await browser.new_context(
            viewport={"width": 1100, "height": 800},
        )
        page = await context.new_page()

        # ── Step 1: get temp email ─────────────────────────────
        print("[ 1 / 3 ]  Opening temp-mail.org to get email...")
        email = await get_temp_email(page)
        print()
        print("   YOUR EMAIL:")
        print(f"   >>> {email} <<<")
        print()

        # ── Step 2: open Taco Bell in user's default browser ───
        print("[ 2 / 3 ]  Opening Taco Bell sign-up in your browser...")
        open_url(REGISTER_URL)
        print()
        pad = max(0, 48 - len(email))
        print("  ┌──────────────────────────────────────────────────────┐")
        print("  │  In the browser window that just opened:             │")
        print("  │                                                       │")
        print("  │  1. Accept any cookies / prompts                     │")
        print("  │  2. Enter this email address:                        │")
        print(f"  │     {email}{' ' * pad}  │")
        print("  │  3. Click CONFIRM, fill name + password, submit      │")
        print("  │                                                       │")
        print("  │  The inbox is checked automatically every 5 seconds! │")
        print("  └──────────────────────────────────────────────────────┘")
        print()

        # ── Step 3: monitor temp-mail.org inbox ────────────────
        print("[ 3 / 3 ]  Monitoring inbox (checking every 5 s, up to 5 min)...")
        print()

        result_type, result_value = await poll_tempmail(page, email, timeout=TIMEOUT)
        await browser.close()

    # ── Display result ─────────────────────────────────────────
    print()
    print("=" * 54)
    if result_type == "code":
        print("   VERIFICATION CODE:")
        print(f"   >>> {result_value} <<<")
        print("=" * 54)
        print()
        print("   Enter that code on the Taco Bell site.")
    else:
        print("   VERIFICATION LINK — opening in browser...")
        print("=" * 54)
        print()
        print(f"   {result_value}")
        open_url(result_value)
        print()
        print("   Your account is now being verified.")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except TimeoutError:
        print("\n   ERROR: No verification email after 5 minutes. Try again.")
    except KeyboardInterrupt:
        print("\n   Cancelled.")
    except Exception as e:
        print(f"\n   ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
