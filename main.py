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
POLL_INTERVAL = 3    # seconds between inbox checks
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


# ─── extraction helpers ───────────────────────────────────────────────────────

def _extract(text: str):
    """
    Return ("code", val) or ("link", val) if found, else None.
    Strips spaces between digits to catch "1 2 3 4 5 6" style codes.
    """
    # Collapse spaces between single digits (e.g. "1 2 3 4 5 6" → "123456")
    compact = re.sub(r'(?<=\d)\s(?=\d)', '', text)
    m = re.search(r'\b(\d{6})\b', compact)
    if m:
        return "code", m.group(1)

    for pattern in [
        r'https?://[^\s"\'<>]+(?:verif|confirm|activ|account|token|magic|click)[^\s"\'<>]*',
        r'https?://[^\s"\'<>]*(?:tacobell|yum\.com)[^\s"\'<>]+',
    ]:
        m2 = re.search(pattern, text, re.IGNORECASE)
        if m2:
            return "link", m2.group(0)

    return None


async def _read_all(page: Page) -> str:
    """Read text + HTML from page body and every accessible same-origin iframe."""
    return await page.evaluate("""
        () => {
            let out = (document.body.innerText || '') + '\\n'
                    + (document.body.innerHTML || '');
            for (const f of document.querySelectorAll('iframe')) {
                try {
                    out += '\\n' + f.contentDocument.body.innerText;
                    out += '\\n' + f.contentDocument.body.innerHTML;
                } catch(e) {}
            }
            for (const inp of document.querySelectorAll('input')) {
                if (inp.value) out += '\\n' + inp.value;
            }
            return out;
        }
    """)


async def poll_tempmail(page: Page, email: str, timeout: int = TIMEOUT) -> tuple:
    """
    Reload temp-mail.org and scan inbox for the verification email.
    Returns ("code", "123456") or ("link", "https://…").
    """
    seen: set = set()
    deadline = time.time() + timeout
    captured: list = []

    async def on_response(resp):
        if "temp-mail.org" not in resp.url:
            return
        ct = resp.headers.get("content-type", "")
        if not ("json" in ct or "html" in ct or "text" in ct):
            return
        try:
            captured.append(await resp.text())
        except Exception:
            pass

    page.on("response", on_response)
    print("  ", end="", flush=True)

    while time.time() < deadline:
        captured.clear()
        inbox_url = page.url

        try:
            await page.reload(wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(2)
            await dismiss_overlays(page)

            # Check intercepted responses first (fastest path)
            for blob in list(captured):
                result = _extract(blob)
                if result:
                    print()
                    return result

            # Scan inbox DOM for clickable email items
            inbox_items = await page.evaluate("""
                () => {
                    const SKIP = new Set(['sender','subject','view','date','from',
                                         'received','inbox','loading','empty','no messages']);
                    for (const sel of ['.inbox-dataList li',
                                       '.inbox-dataList .message',
                                       '[class*="message-item"]']) {
                        const found = [...document.querySelectorAll(sel)]
                            .map((el, i) => ({i, sel, text: (el.innerText||'').trim().slice(0,120)}))
                            .filter(x => x.text.length >= 15 && !SKIP.has(x.text.toLowerCase()));
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
                print(f"\n  [inbox: {key[:55]}]", end="", flush=True)

                captured.clear()
                try:
                    await page.locator(item["sel"]).nth(item["i"]).click()
                    try:
                        await page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    await asyncio.sleep(2)

                    # Check captured network responses after clicking
                    for blob in list(captured):
                        result = _extract(blob)
                        if result:
                            print()
                            return result

                    # Full DOM read (page + all iframes)
                    result = _extract(await _read_all(page))
                    if result:
                        print()
                        return result

                except Exception as e:
                    print(f"\n  [click error: {e.__class__.__name__}]", end="", flush=True)

                # Navigate back to inbox
                try:
                    if page.url != inbox_url:
                        await page.go_back()
                    else:
                        await page.goto(TEMPMAIL_URL, wait_until="domcontentloaded")
                except Exception:
                    try:
                        await page.goto(TEMPMAIL_URL, wait_until="domcontentloaded")
                    except Exception:
                        pass
                await asyncio.sleep(1)

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
        print("  │  The inbox is checked automatically every 3 seconds! │")
        print("  └──────────────────────────────────────────────────────┘")
        print()

        # ── Step 3: monitor temp-mail.org inbox ────────────────
        print("[ 3 / 3 ]  Monitoring inbox (checking every 3 s, up to 5 min)...")
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
