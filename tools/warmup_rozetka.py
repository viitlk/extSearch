"""One-time Rozetka profile warm-up.

Rozetka sits behind Cloudflare: curl and headless browsers without cookies get
an empty placeholder. Solve the challenge once in a visible window — afterwards
the cookies live in the persistent profile and ``where="rozetka"`` works.

    python tools/warmup_rozetka.py

A Chromium window opens; wait until the ``cf_clearance`` cookie appears.
Needs a graphical session (run it from a desktop terminal, not over SSH).
The profile path is ``EXTSEARCH_PROFILE_DIR`` or ~/.local/share/extsearch.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extsearch.config import PROFILE_DIR  # noqa: E402

URL = "https://rozetka.com.ua/ua/"
TIMEOUT_SEC = 180


async def main() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright is not installed: pip install playwright "
              "&& python -m playwright install chromium")
        return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"profile: {PROFILE_DIR}")

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, locale="uk-UA",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:  # noqa: BLE001
            print(f"could not open the page: {exc}")

        print(f"waiting for the challenge (up to {TIMEOUT_SEC}s). "
              "If a captcha is shown, solve it in the window.")

        solved = False
        for _ in range(TIMEOUT_SEC):
            if "cf_clearance" in {c["name"] for c in await ctx.cookies()}:
                solved = True
                break
            await asyncio.sleep(1)

        cookies = {c["name"] for c in await ctx.cookies()}
        print("cf_clearance cookie:", "present" if solved else "missing")
        print(f"cookies total: {len(cookies)}")
        await ctx.close()

    if solved:
        print('\ndone: where="rozetka" should work now')
        return 0
    print("\nchallenge not solved; try again and make sure the window opened")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
