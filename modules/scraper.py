"""Data scraper — timer, current period, draw history, bet history."""

import logging
import asyncio
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from config import (
    BASE_URL, POOL_ID, GAME_TYPE, TIMER_API_URL,
    AJAX_HEADERS, HEADERS,
)
from modules.auth import AuthManager

logger = logging.getLogger(__name__)


class Scraper:
    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    async def _client(self) -> httpx.AsyncClient:
        return await self._auth.get_client()

    # ─── Timer ───────────────────────────────────────────────────────────────

    async def get_seconds_until_close(self) -> Optional[int]:
        """Fetch seconds remaining until draw close from external timer API."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(TIMER_API_URL)
                data = resp.json()
                # API returns list of pasaran objects; find OKAQ / hokidraw
                if isinstance(data, list):
                    for item in data:
                        name = str(item.get("name", "") + item.get("code", "")).lower()
                        if "hoki" in name or "okaq" in name or "p76368" in name:
                            return int(item.get("seconds", item.get("sisa", 0)))
                    # fallback: first item
                    if data:
                        return int(data[0].get("seconds", data[0].get("sisa", 0)))
                elif isinstance(data, dict):
                    return int(data.get("seconds", data.get("sisa", 0)))
        except Exception as e:
            logger.error("Timer API failed: %s", e)

        # Fallback: parse game page
        return await self._get_timer_from_game_page()

    async def _get_timer_from_game_page(self) -> Optional[int]:
        client = await self._client()
        try:
            resp = await client.get(
                f"{BASE_URL}/games/4d/{POOL_ID}",
                headers=HEADERS,
            )
            soup = BeautifulSoup(resp.text, "lxml")
            timer_tag = soup.find(attrs={"name": "timerpools"}) or soup.find(id="timerpools")
            if timer_tag:
                return int(timer_tag.get("value", 0))
        except Exception as e:
            logger.error("Game page timer parse failed: %s", e)
        return None

    # ─── Current period ───────────────────────────────────────────────────────

    async def get_current_periode(self) -> Optional[str]:
        """Parse current betting periode from game page or derive from history."""
        client = await self._client()
        try:
            resp = await client.get(
                f"{BASE_URL}/games/4d/{POOL_ID}",
                headers=HEADERS,
            )
            soup = BeautifulSoup(resp.text, "lxml")
            # Hidden field named 'periode'
            tag = soup.find("input", {"name": "periode"}) or soup.find(attrs={"name": "periode"})
            if tag and tag.get("value", "").strip():
                return tag.get("value", "").strip()
            # Fallback: find in text
            match = re.search(r"periode[\"'\s:]+([A-Z0-9\-]+)", resp.text)
            if match:
                return match.group(1)
        except Exception as e:
            logger.error("Period fetch from game page failed: %s", e)

        # Fallback: derive next periode from latest history entry
        try:
            history = await self.get_draw_history(limit=1)
            if history:
                last_period = history[0]["periode"]
                # Periode is numeric and increments by 1
                next_period = str(int(last_period) + 1)
                logger.info("Derived current periode from history: %s (last=%s)", next_period, last_period)
                return next_period
        except Exception as e:
            logger.error("Period derivation from history failed: %s", e)
        return None

    # ─── Draw history ─────────────────────────────────────────────────────────

    async def get_draw_history(self, limit: int = 200) -> list[dict]:
        """Fetch draw history (JSON endpoint with HTML table fallback)."""
        results = await self._fetch_history_json(limit=limit)
        if not results:
            results = await self._fetch_history_html()
        return results

    async def _fetch_history_json(self, limit: int = 200) -> list[dict]:
        client = await self._client()
        parsed = []
        per_page = 10
        pages_needed = min((limit + per_page - 1) // per_page, 30)  # cap at 30 pages

        for page in range(1, pages_needed + 1):
            try:
                resp = await client.get(
                    f"{BASE_URL}/history/detail/data/{POOL_ID}-{page}",
                    headers=AJAX_HEADERS,
                )
                data = resp.json()

                # API returns {angka_keluar: {data: [...]}} or flat list/dict
                if isinstance(data, dict) and "angka_keluar" in data:
                    rows = data["angka_keluar"].get("data", [])
                elif isinstance(data, list):
                    rows = data
                else:
                    rows = data.get("data", data.get("results", data.get("history", [])))

                if not rows:
                    break

                for row in rows:
                    periode = str(row.get("periode", row.get("period", row.get("id", ""))))
                    # API uses "angka" for the 4D result
                    result = str(
                        row.get("angka", row.get("result", row.get("keluaran", row.get("number", ""))))
                    )
                    draw_time = str(
                        row.get("jam", row.get("draw_time", row.get("time", row.get("tanggal", ""))))
                    )
                    if periode and result:
                        parsed.append({"periode": periode, "result": result, "draw_time": draw_time})

                if len(parsed) >= limit:
                    break

            except Exception as e:
                logger.debug("JSON history page %d failed: %s", page, e)
                break

        return parsed[:limit]

    async def _fetch_history_html(self) -> list[dict]:
        client = await self._client()
        try:
            resp = await client.get(
                f"{BASE_URL}/games/4d/history/{GAME_TYPE}/{POOL_ID}",
                headers=HEADERS,
            )
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tr")
            parsed = []
            for tr in rows[1:]:  # skip header
                cols = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cols) >= 2:
                    parsed.append({
                        "periode": cols[0],
                        "result": cols[1] if len(cols) > 1 else "",
                        "draw_time": cols[2] if len(cols) > 2 else "",
                    })
            return parsed
        except Exception as e:
            logger.debug("HTML history fetch failed: %s", e)
        return []

    # ─── Latest result ────────────────────────────────────────────────────────

    async def get_latest_result(self) -> Optional[dict]:
        """Return the most recent draw result dict {periode, result, draw_time}."""
        history = await self.get_draw_history(limit=1)
        if not history:
            return None
        item = history[0]
        # Ensure both 'period' and 'periode' keys exist for compatibility
        item.setdefault("period", item.get("periode", ""))
        item.setdefault("periode", item.get("period", ""))
        return item

    # ─── Bet history ──────────────────────────────────────────────────────────

    async def get_bet_history(self) -> list[dict]:
        """Parse bet history HTML table."""
        client = await self._client()
        try:
            resp = await client.get(
                f"{BASE_URL}/games/4d/history/{GAME_TYPE}/{POOL_ID}",
                headers=HEADERS,
            )
            soup = BeautifulSoup(resp.text, "lxml")
            tables = soup.find_all("table")
            bets = []
            for table in tables:
                for tr in table.find_all("tr")[1:]:
                    cols = [td.get_text(strip=True) for td in tr.find_all("td")]
                    if cols:
                        bets.append({"raw": cols})
            return bets
        except Exception as e:
            logger.error("Bet history fetch failed: %s", e)
        return []
