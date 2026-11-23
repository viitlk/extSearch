"""One-time Rozetka profile warm-up.

Rozetka sits behind a Cloudflare challenge: a fresh browser gets a page without
products. This opens a visible Chromium, waits for the `cf_clearance` cookie,
then actually loads a search page and waits for product cards — the challenge
often needs a real page view before the clearance sticks.

    python tools/warmup_rozetka.py
    python tools/warmup_rozetka.py --query "навушники" --pause 10

Needs a graphical session (a desktop terminal, not SSH). The profile path comes
from EXTSEARCH_PROFILE_DIR or ~/.local/share/extsearch/browser-profile.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extsearch.config import PROFILE_DIR  # noqa: E402

HOME = "https://rozetka.com.ua/ua/"
SEARCH = "https://rozetka.com.ua/ua/search/?text={q}"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="навушники", help="search to load after the challenge")
    parser.add_argument("--pause", type=int, default=5, help="seconds to keep the window open")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright is not installed: pip install playwright "
              "&& python -m playwright install chromium")
        return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"profile: {PROFILE_DIR}")
    search_url = SEARCH.format(q=urllib.parse.quote(args.query))

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, locale="uk-UA",
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        try:
            await page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:  # noqa: BLE001
            print(f"home page: {exc}")

        print(f"waiting for the challenge (up to {args.timeout}s)...")
        cleared = False
        for _ in range(args.timeout):
            if "cf_clearance" in {c["name"] for c in await ctx.cookies()}:
                cleared = True
                break
            await asyncio.sleep(1)
        print("cf_clearance cookie:", "present" if cleared else "missing")

        # The challenge is not the goal: a product page that renders is.
        print(f"opening a real search: {search_url}")
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector('a[href*="/p"]', timeout=45000)
        except Exception as exc:  # noqa: BLE001
            print(f"search page: {exc}")

        await page.mouse.wheel(0, 1200)
        await asyncio.sleep(2)
        cards = await page.eval_on_selector_all('a[href*="/p"]', "els => els.length")
        print(f"product links on the page: {cards}")

        if args.pause:
            print(f"keeping the window for {args.pause}s")
            await asyncio.sleep(args.pause)
        cookies = {c["name"] for c in await ctx.cookies()}
        print(f"cookies total: {len(cookies)}")
        await ctx.close()

    if cards:
        print('\ndone: where="rozetka" should return products now')
        return 0
    print("\nno products on the page: pass the challenge in the window, then rerun")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
