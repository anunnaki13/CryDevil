"""Authentication manager for partai34848.com."""

import logging
import asyncio
import time
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from config import (
    BASE_URL, USERNAME, PASSWORD, POOL_ID,
    HEADERS, AJAX_HEADERS, SESSION_VALIDATION_INTERVAL,
)
from modules import database as db

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages login session and cookie-backed authenticated requests."""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._last_validated: float = 0.0
        self._playwright_cookies: dict = {}

    async def _set_site_status(self, status: str, detail: str = "") -> None:
        await db.set_state("site_status", status)
        await db.set_state("site_status_detail", detail[:500])

    async def _mark_normal(self) -> None:
        await self._set_site_status("normal", "")

    @staticmethod
    def _classify_response_status(url: str, text: str) -> str:
        lower_url = (url or "").lower()
        lower_text = (text or "").lower()
        if "/maintenance" in lower_url or "maintenance" in lower_text:
            return "maintenance"
        if "<html" in lower_text or "<!doctype" in lower_text:
            return "session_invalid"
        return "unknown"

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

    # ─── Login ───────────────────────────────────────────────────────────────

    async def login(self) -> bool:
        """Login to partai34848.com. Returns True on success."""
        logger.info("Starting Playwright-backed login")
        return await self._playwright_login()

    # ─── Session validation ───────────────────────────────────────────────────

    async def is_logged_in(self) -> bool:
        now = time.monotonic()
        if now - self._last_validated < SESSION_VALIDATION_INTERVAL:
            return True  # assume still valid within interval
        return await self._validate_session()

    async def _validate_session(self) -> bool:
        client = await self.get_client()
        try:
            resp = await client.post(
                BASE_URL + "/request-balance",
                headers=AJAX_HEADERS,
            )
            text = resp.text.strip()
            valid = False

            # When logged in this endpoint returns a plain numeric balance like "50018.00".
            if text:
                try:
                    float(text.replace(",", "").replace("Rp", "").strip())
                    valid = True
                except ValueError:
                    valid = False

            if valid:
                self._last_validated = time.monotonic()
                await self._mark_normal()
            else:
                status = self._classify_response_status(str(resp.url), text)
                if status == "maintenance":
                    await self._set_site_status(
                        "maintenance",
                        f"Session validation diarahkan ke halaman maintenance ({resp.url})",
                    )
                elif status == "session_invalid":
                    await self._set_site_status(
                        "session_invalid",
                        f"Session validation menerima HTML/redirect, bukan balance numerik ({resp.url})",
                    )
            return valid
        except Exception as e:
            logger.error("Session validation failed: %s", e)
            await self._set_site_status("degraded", f"Session validation error: {e}")
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
        for attempt in range(2):
            try:
                resp = await client.post(
                    BASE_URL + "/request-balance",
                    headers=AJAX_HEADERS,
                )
                text = resp.text.strip()
                status = self._classify_response_status(str(resp.url), text)
                if status in {"maintenance", "session_invalid"}:
                    if status == "maintenance":
                        await self._set_site_status("maintenance", "Balance fetch diarahkan ke halaman maintenance")
                        return None
                    if attempt == 0:
                        logger.info("Balance fetch menerima redirect/login page — mencoba re-login sekali")
                        if await self.ensure_logged_in():
                            client = await self.get_client()
                            continue
                    await self._set_site_status("session_invalid", "Balance fetch menerima redirect/login page")
                    return None

                if text:
                    try:
                        await self._mark_normal()
                        return int(float(text.replace(",", "").replace("Rp", "").strip()))
                    except ValueError:
                        pass

                data = resp.json()
                # API may return a bare number (float/int) instead of a dict
                if isinstance(data, (int, float)):
                    await self._mark_normal()
                    return int(data)
                if isinstance(data, dict):
                    raw = data.get("balance") or data.get("saldo") or data.get("data", {}).get("balance")
                else:
                    raw = data
                if raw is not None:
                    # Strip non-numeric chars and convert
                    clean = str(raw).replace(".", "").replace(",", "").replace("Rp", "").strip()
                    await self._mark_normal()
                    return int(float(clean))
            except Exception as e:
                logger.error("Balance fetch failed: %s", e)
                if "resp" in locals():
                    status = self._classify_response_status(str(resp.url), resp.text)
                    if status == "maintenance":
                        await self._set_site_status("maintenance", "Balance fetch diarahkan ke halaman maintenance")
                        return None
                    if status == "session_invalid" and attempt == 0:
                        logger.info("Balance fetch error saat sesi invalid — mencoba re-login sekali")
                        if await self.ensure_logged_in():
                            client = await self.get_client()
                            continue
                    if status == "session_invalid":
                        await self._set_site_status("session_invalid", "Balance fetch menerima redirect/login page")
                    else:
                        await self._set_site_status("degraded", f"Balance fetch error: {e}")
                else:
                    await self._set_site_status("degraded", f"Balance fetch error: {e}")
                break

        # Fallback dari hidden field panel game.
        try:
            resp = await client.get(
                f"{BASE_URL}/games/4d/load/4d/{POOL_ID}",
                headers=HEADERS,
            )
            status = self._classify_response_status(str(resp.url), resp.text)
            if status == "maintenance":
                await self._set_site_status("maintenance", "Balance fallback diarahkan ke halaman maintenance")
                return None
            if status == "session_invalid":
                await self._set_site_status("session_invalid", "Balance fallback menerima redirect/login page")
                return None
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            tag = soup.find("input", {"id": "duitku"})
            if tag and tag.get("value", "").strip():
                await self._mark_normal()
                return int(float(tag.get("value", "0").strip()))
        except Exception as e:
            logger.error("Balance fallback from game panel failed: %s", e)
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
                await page.wait_for_timeout(2_000)
                if "/maintenance" in page.url.lower():
                    logger.warning("Website sedang maintenance saat login Playwright: %s", page.url)
                    await self._set_site_status("maintenance", f"Homepage login mengarah ke {page.url}")
                    await browser.close()
                    return False
                try:
                    # This site uses a JS login handler on the navbar form.
                    user_field = page.locator(
                        '#navbar_username, input[name="entered_login"]'
                    ).first
                    pass_field = page.locator(
                        '#navbar_password, input[name="entered_password"]'
                    ).first

                    if not await user_field.count() or not await pass_field.count():
                        logger.error("Playwright could not find navbar login inputs on page %s", page.url)
                        page_content = await page.content()
                        if "/maintenance" in page.url.lower() or "maintenance" in page_content.lower():
                            await self._set_site_status("maintenance", f"Form login tidak tersedia karena maintenance ({page.url})")
                        else:
                            await self._set_site_status("session_invalid", f"Form login tidak ditemukan di {page.url}")
                        return False

                    await user_field.fill(USERNAME or "")
                    await pass_field.fill(PASSWORD or "")

                    login_response = None
                    try:
                        async with page.expect_response(
                            lambda resp: "/auth/signin" in resp.url,
                            timeout=30_000,
                        ) as response_info:
                            await page.evaluate(
                                """
                                () => {
                                    const btn = document.getElementById("submitlogin")
                                        || document.getElementById("loginBtnHeader");
                                    if (btn) {
                                        btn.click();
                                        return;
                                    }
                                    const form = document.querySelector("form");
                                    if (form) {
                                        form.requestSubmit();
                                    }
                                }
                                """
                            )
                        login_response = await response_info.value
                    except Exception as e:
                        logger.warning("Playwright did not capture /auth/signin response: %s", e)
                        await pass_field.press("Enter")

                    if login_response is not None:
                        try:
                            payload = await login_response.json()
                            logger.info(
                                "Playwright auth response: status=%s message=%s url=%s",
                                payload.get("status_code", payload.get("status")),
                                payload.get("message"),
                                login_response.url,
                            )
                        except Exception:
                            logger.info("Playwright auth response received from %s", login_response.url)

                    try:
                        await page.wait_for_url("**/rules", timeout=30_000)
                    except Exception:
                        try:
                            await page.wait_for_load_state("networkidle", timeout=30_000)
                        except Exception:
                            logger.info("Playwright login submit did not reach /rules or networkidle; continuing")

                    await page.wait_for_timeout(3_000)
                    logger.info("Playwright submit attempted; current URL: %s", page.url)
                except Exception as e:
                    logger.error("Playwright form interaction failed: %s", e)
                    return False

                # Extract cookies and inject into httpx client
                cookies = await context.cookies()
                await browser.close()

                # Rebuild httpx client with new cookies
                await self.close()
                self._client = await self._make_client()
                for c in cookies:
                    self._client.cookies.set(
                        c["name"],
                        c["value"],
                        domain=c.get("domain", ""),
                        path=c.get("path", "/"),
                    )

                if await self._validate_session():
                    logger.info("Playwright login completed and session validated")
                    await self._mark_normal()
                    return True

                logger.error("Playwright flow completed but session is still not authenticated")
                await self._set_site_status("session_invalid", "Playwright login selesai tetapi sesi belum tervalidasi")
                return False

        except Exception as e:
            logger.error("Playwright login failed: %s", e)
            await self._set_site_status("degraded", f"Playwright login failed: {e}")
            return False
