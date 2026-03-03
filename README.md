get free taco bell from your terminal.

1. Generates a disposable email address instantly
2. Opens the Taco Bell sign-up page in your real browser
3. Monitors the inbox automatically every 5 seconds
4. Prints the verification code the moment it arrives

## Requirements

- Python 3.9+
- `requests` library

## Setup

```bash
pip3 install requests
```

## Run

```bash
python3 main.py
```

## What happens

```
==================================================
   Taco Bell Free Order Helper
==================================================

[ 1 / 3 ]  Generating disposable email...

   YOUR EMAIL:
   >>> abc123@guerrillamailblock.com <<<

[ 2 / 3 ]  Opening Taco Bell sign-up in your browser...

  ┌──────────────────────────────────────────────────┐
  │  In the browser window that just opened:         │
  │                                                  │
  │  1. Accept cookies if prompted                   │
  │  2. Enter this email                             │
  │  3. Click CONFIRM, fill name + password, submit  │
  │                                                  │
  │  Inbox is checked automatically every 5 seconds! │
  └──────────────────────────────────────────────────┘

[ 3 / 3 ]  Monitoring inbox (checking every 5 s, up to 5 min)...

  ..................

==================================================
   VERIFICATION CODE:
   >>> 482910 <<<
==================================================

   Enter that code on the Taco Bell site.
```

## Run with Docker

```bash
docker compose up --build
```

## Notes

- If Taco Bell shows a loading spinner when you enter the email, the domain may be blocked. Run the script again to get a new address and try a different one.
- The script waits up to 5 minutes for the verification email before timing out.
