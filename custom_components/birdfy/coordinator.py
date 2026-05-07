"""Birdfy DataUpdateCoordinator — polls Netvue Android API."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=5)

API_BASE  = "https://eu-central-1-api2.nvts.co"
LOGIN_URL = "https://localweb.nvts.co/v1/users/login/v2"


def _hmac_sha256(key: bytes, msg: str) -> str:
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def _make_signature(token: str, userid: str, ts: str, ucid: str, udid: str) -> str:
    k = "nvs1" + token
    for msg in [ucid, udid, userid, ts]:
        k = _hmac_sha256(k.encode(), msg)
    return _hmac_sha256(k.encode(), "nvs1_request")


def _auth_headers(token: str, userid: str, ucid: str, udid: str) -> dict:
    ts = str(int(time.time() * 1000))
    sig = _make_signature(token, userid, ts, ucid, udid)
    return {
        "Accept": "application/json",
        "Accept-Charset": "UTF-8",
        "Accept-Encoding": "gzip",
        "User-Agent": "Birdfy/1.19.2 (build 123960) NetvueSDK/1.6.1 Android/12",
        "x-nvs-signature": sig,
        "x-nvs-time": ts,
        "x-nvs-ucid": ucid,
        "x-nvs-udid": udid,
        "x-nvs-userid": userid,
        "x-nvs-version": '{"signature":2}',
    }


async def _android_login(session: aiohttp.ClientSession, email: str, password: str,
                          ucid: str, udid: str) -> dict:
    pwd_md5 = hashlib.md5(password.encode()).hexdigest()
    payload = {"username": email, "password": pwd_md5, "locale": "en-US"}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-nvs-ucid": ucid,
        "x-nvs-udid": udid,
        "User-Agent": "Birdfy/1.19.2 (build 123960) NetvueSDK/1.6.1 Android/12",
    }
    async with session.post(LOGIN_URL, json=payload, headers=headers) as r:
        data = await r.json(content_type=None)
        if data.get("ret", 0) != 0 or not data.get("token"):
            raise UpdateFailed(f"Login failed: {data.get('msg', data)}")
        return data


def _parse_event(ev: dict) -> dict:
    record_url = ""
    record_dir = ""
    pic = ev.get("pic", "")
    try:
        desc = json.loads(ev.get("description", "{}"))
        record_url = desc.get("recordUrl", "")
        record_dir = desc.get("recordDir", "")
    except Exception:
        pass
    return {
        "alarm_id":   ev.get("alarmId", ""),
        "alert_time": ev.get("alertTime", 0),
        "label":      ev.get("label", "unknown"),
        "record_url": record_url,
        "record_dir": record_dir,
        "pic":        pic,
    }


class BirdfyCoordinator(DataUpdateCoordinator):
    """Polls Netvue API and stores latest events."""

    def __init__(self, hass: HomeAssistant, email: str, password: str,
                 ucid: str = "", udid: str = "") -> None:
        super().__init__(hass, _LOGGER, name="birdfy", update_interval=SCAN_INTERVAL)
        self._email    = email
        self._password = password
        self._ucid     = ucid
        self._udid     = udid
        self._token    = ""
        self._userid   = ""
        self._device_id = ""
        self.image_url  = ""
        self.highlights_url = ""
        self.record_url_cache: dict[str, str] = {}
        self.segments_cache: dict[str, list[str]] = {}
        self.thumbnail_cache: dict[str, str] = {}

    async def _ensure_login(self, session: aiohttp.ClientSession) -> None:
        if self._token:
            return
        data = await _android_login(session, self._email, self._password, self._ucid, self._udid)
        self._token  = data["token"]
        self._userid = str(data["userID"])

    async def _ensure_device(self, session: aiohttp.ClientSession) -> None:
        if self._device_id:
            return
        url = "https://localweb.nvts.co/v1/devices/v3"
        async with session.get(url, headers=_auth_headers(self._token, self._userid, self._ucid, self._udid)) as r:
            data = await r.json(content_type=None)
        devices = data.get("devices", data.get("deviceList", []))
        if not devices:
            raise UpdateFailed("No devices found in Birdfy account")
        dev = devices[0]
        self._device_id = (
            dev.get("serialNumber")
            or dev.get("deviceSn")
            or dev.get("sn")
            or dev.get("addxSn")
        )

    async def _fetch_image_url(self, session: aiohttp.ClientSession, alarm_id: str) -> str:
        url = f"{API_BASE}/devices/{self._device_id}/events/{alarm_id}/pic"
        async with session.get(url, headers=_auth_headers(self._token, self._userid, self._ucid, self._udid)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return data.get("url", "")
        return ""

    async def fetch_download_links(self, session: aiohttp.ClientSession, events: list) -> None:
        """Call downloadLink API and populate segments_cache and thumbnail_cache."""
        if not events:
            return
        event_list = []
        for ev in events:
            if not ev.get("alarm_id"):
                continue
            entry: dict = {
                "serialnumber": self._device_id,
                "alarmId": ev["alarm_id"],
                "dataGroup": ["video", "pic"],
                "type": 56,
            }
            if ev.get("record_dir"):
                entry["recordDir"] = ev["record_dir"]
            if ev.get("pic"):
                entry["pic"] = ev["pic"]
            event_list.append(entry)

        if not event_list:
            return

        try:
            async with session.post(
                "https://localapi2.nvts.co/event/downloadLink",
                json={"eventList": event_list},
                headers=_auth_headers(self._token, self._userid, self._ucid, self._udid),
            ) as r:
                if r.status != 200:
                    return
                data = await r.json(content_type=None)
        except Exception:
            return

        for item in data.get("eventList", []):
            alarm_id = item.get("alarmId", "")
            if not alarm_id:
                continue
            segments = item.get("videoFileList", [])
            if segments:
                self.segments_cache[alarm_id] = segments
            pics = item.get("picList", [])
            if pics:
                self.thumbnail_cache[alarm_id] = pics[0]

    async def fetch_fresh_record_url(self, alarm_id: str) -> str:
        """Fetch a fresh (non-expired) record URL for a given alarm_id."""
        url = f"{API_BASE}/devices/{self._device_id}/events/{alarm_id}"
        async with aiohttp.ClientSession() as session:
            await self._ensure_login(session)
            await self._ensure_device(session)
            async with session.get(url, headers=_auth_headers(self._token, self._userid, self._ucid, self._udid)) as r:
                if r.status != 200:
                    cached = self.record_url_cache.get(alarm_id, "")
                    _LOGGER.warning("fetch_fresh_record_url status=%s, falling back to cache", r.status)
                    return cached
                data = await r.json(content_type=None)
        ev = data.get("event", data)
        try:
            desc = json.loads(ev.get("description", "{}"))
            record_url = desc.get("recordUrl", "")
        except Exception:
            record_url = ""
        if record_url:
            self.record_url_cache[alarm_id] = record_url
        return record_url

    async def fetch_events_for_day(self, date_str: str) -> list:
        """Fetch up to 100 events for a given day (YYYY-MM-DD, local time)."""
        import datetime
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        start_ts = int(datetime.datetime(d.year, d.month, d.day, 0, 0, 0).timestamp() * 1000)
        end_ts   = int(datetime.datetime(d.year, d.month, d.day, 23, 59, 59).timestamp() * 1000)
        url = f"{API_BASE}/devices/{self._device_id}/events"
        params = {
            "limit": 100,
            "ignoreAiLabels": "false",
            "reverse": 1,
            "startTime": start_ts,
            "endTime": end_ts,
        }
        async with aiohttp.ClientSession() as session:
            await self._ensure_login(session)
            await self._ensure_device(session)
            async with session.get(url, headers=_auth_headers(self._token, self._userid, self._ucid, self._udid), params=params) as r:
                if r.status == 401:
                    self._token = ""
                    return []
                raw = await r.json(content_type=None)
            events = [_parse_event(e) for e in raw.get("events", [])]
            for ev in events:
                if ev.get("alarm_id") and ev.get("record_url"):
                    self.record_url_cache[ev["alarm_id"]] = ev["record_url"]
            await self.fetch_download_links(session, events)
        return events

    async def _async_update_data(self) -> dict:
        async with aiohttp.ClientSession() as session:
            try:
                await self._ensure_login(session)
            except UpdateFailed:
                raise
            except Exception as e:
                raise UpdateFailed(f"Login error: {e}") from e

            try:
                await self._ensure_device(session)
            except UpdateFailed:
                raise
            except Exception as e:
                raise UpdateFailed(f"Device fetch error: {e}") from e

            url = f"{API_BASE}/devices/{self._device_id}/events"
            params = {"limit": 10, "ignoreAiLabels": "false", "reverse": 1}
            try:
                async with session.get(
                    url,
                    headers=_auth_headers(self._token, self._userid, self._ucid, self._udid),
                    params=params,
                ) as r:
                    if r.status == 401:
                        # Token expired — force re-login next cycle
                        self._token = ""
                        raise UpdateFailed("Token expired, will re-login next cycle")
                    raw = await r.json(content_type=None)
            except UpdateFailed:
                raise
            except Exception as e:
                raise UpdateFailed(f"Events fetch error: {e}") from e

            events = [_parse_event(e) for e in raw.get("events", [])]
            for ev in events:
                if ev.get("alarm_id") and ev.get("record_url"):
                    self.record_url_cache[ev["alarm_id"]] = ev["record_url"]
            last = events[0] if events else {}

            if last.get("alarm_id"):
                try:
                    self.image_url = await self._fetch_image_url(session, last["alarm_id"])
                except Exception:
                    pass

            try:
                async with session.get(
                    "https://api2.nvts.co/users/dynamicSetting",
                    headers=_auth_headers(self._token, self._userid, self._ucid, self._udid),
                ) as r:
                    if r.status == 200:
                        ds = await r.json(content_type=None)
                        self.highlights_url = ds.get("highlightsUrl", "")
            except Exception:
                pass

            return {
                "last_event":    last,
                "recent_events": events,
                "device_id":     self._device_id,
            }
