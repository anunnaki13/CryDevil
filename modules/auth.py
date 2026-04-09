"""Authentication manager for partai34848.com with Cloudflare bypass."""

import logging
import asyncio
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from config import (
    BASE_URL, USERNAME, PASSWORD,
    HEADERS, AJAX_HEADERS, SESSION_VALIDATION_INTERVAL,
)

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages login session with httpx; falls back to Playwright on Cloudflare block."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._last_validated: float = 0.0
        self._playwright_cookies: dict = {}

    # ─── Client factory ──────────────────────────────────────────────────────

    async def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=30.0,
            http2=True,
        )

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = await self._make_client()
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─── CSRF ────────────────────────────────────────────────────────────────

    async def _fetch_csrf_token(self) -> Optional[str]:
        client = await self.get_client()
        try:
            resp = await client.get(BASE_URL + "/")
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            token_tag = soup.find("input", {"name": "_token"})
            if token_tag:
                return token_tag.get("value")
            # Also try meta tag
            meta = soup.find("meta", {"name": "csrf-token"})
            if meta:
                return meta.get("content")
        except Exception as e:
            logger.error("CSRF fetch failed: %s", e)
        return None

    # ─── Login ───────────────────────────────────────────────────────────────

    async def login(self) -> bool:
        """Login to partai34848.com. Returns True on success."""
        token = await self._fetch_csrf_token()
        if not token:
            logger.warning("No CSRF token — attempting Playwright fallback")
            return await self._playwright_login()

        client = await self.get_client()
        payload = {
            "_token": token,
            "entered_login": USERNAME,
            "entered_password": PASSWORD,
        }
        try:
            resp = await client.post(
                BASE_URL + "/json/post/ceklogin-ts",
                data=payload,
                headers={**AJAX_HEADERS, "Referer": BASE_URL + "/"},
            )
            data = resp.json()
            # Site returns {"message":"redirect_index","status_code":"0"} on success
            msg = data.get("message", "")
            sc = str(data.get("status_code", data.get("status", "")))
            is_ok = (
                msg in ("redirect_index", "ok", "success")
                or sc in ("1", "true", "ok", "success")
                or data.get("status") in (1, "1", True, "true", "ok", "success")
            )
            if is_ok:
                logger.info("Login successful (msg=%s, status_code=%s)", msg, sc)
                self._last_validated = time.monotonic()
                return True
            logger.error("Login rejected: %s", data)
        except Exception as e:
            logger.error("Login request failed: %s", e)
            # Cloudflare likely challenged — fall back
            return await self._playwright_login()
        return False

    # ─── Session validation ───────────────────────────────────────────────────

    async def is_logged_in(self) -> bool:
        now = time.monotonic()
        if now - self._last_validated < SESSION_VALIDATION_INTERVAL:
            return True  # assume still valid within interval
        return await self._validate_session()

    async def _validate_session(self) -> bool:
        client = await self.get_client()
        try:
            resp = await client.get(
                BASE_URL + "/json/post/validate-login",
                headers=AJAX_HEADERS,
            )
            data = resp.json()
            msg = data.get("message", "")
            sc = str(data.get("status_code", data.get("status", "")))
            valid = (
                msg in ("redirect_index", "ok", "success", "logged_in")
                or sc in ("1", "true", "ok", "success")
                or data.get("status") in (1, "1", True, "true", "ok", "success")
            )
            if valid:
                self._last_validated = time.monotonic()
            return valid
        except Exception as e:
            logger.error("Session validation failed: %s", e)
            return False

    async def ensure_logged_in(self) -> bool:
        """Re-login if session has expired. Returns True when authenticated."""
        if await self.is_logged_in():
            return True
        logger.info("Session expired — re-logging in")
        # Reset client to clear stale Cloudflare cookies before re-login
        await self.close()
        for attempt in range(1, 4):
            result = await self.login()
            if result:
                return True
            if attempt < 3:
                logger.warning("Re-login attempt %d/3 failed — retrying in 5s", attempt)
                await asyncio.sleep(5)
        logger.error("Re-login failed after 3 attempts")
        return False

    # ─── Balance ─────────────────────────────────────────────────────────────

    async def get_balance(self) -> Optional[int]:
        """Return current balance in IDR or None on failure."""
        client = await self.get_client()
        try:
            resp = await client.post(
                BASE_URL + "/request-balance",
                headers=AJAX_HEADERS,
            )
            data = resp.json()
            raw = data.get("balance") or data.get("saldo") or data.get("data", {}).get("balance")
            if raw is not None:
                # Strip non-numeric chars and convert
                clean = str(raw).replace(".", "").replace(",", "").replace("Rp", "").strip()
                return int(float(clean))
        except Exception as e:
            logger.error("Balance fetch failed: %s", e)
        return None

    # ─── Playwright fallback ──────────────────────────────────────────────────

    async def _playwright_login(self) -> bool:
        """Use headless Chromium to solve Cloudflare challenge, extract cookies."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright not installed — cannot bypass Cloudflare")
            return False

        logger.info("Starting Playwright headless login")
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="id-ID",
                )
                page = await context.new_page()

                # Navigate to homepage (solves CF challenge)
                await page.goto(BASE_URL + "/", wait_until="networkidle", timeout=60_000)

                # Fill login form if present
                try:
                    await page.fill('input[name="entered_login"]', USERNAME or "")
                    await page.fill('input[name="entered_password"]', PASSWORD or "")
                    await page.click('button[type="submit"], input[type="submit"]')
                    await page.wait_for_load_state("networkidle", timeout=30_000)
                except Exception:
                    pass

                # Extract cookies and inject into httpx client
                cookies = await context.cookies()
                await browser.close()

                # Rebuild httpx client with new cookies
                await self.close()
                self._client = await self._make_client()
                for c in cookies:
                    self._client.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

                self._last_validated = time.monotonic()
                logger.info("Playwright login completed, cookies extracted")
                return True

        except Exception as e:
            logger.error("Playwright login failed: %s", e)
            return False
