"""HTTP proxy views for Birdfy HLS streams."""
from __future__ import annotations

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN


def register_views(hass: HomeAssistant) -> None:
    hass.http.register_view(BirdfyM3U8ProxyView(hass))
    hass.http.register_view(BirdfySegmentProxyView)


class BirdfyM3U8ProxyView(HomeAssistantView):
    """Proxies the M3U8 playlist and rewrites segment URLs to go through HA."""

    url = "/api/birdfy/m3u8/{alarm_id}"
    name = "api:birdfy:m3u8"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, alarm_id: str) -> web.Response:
        coordinator = None
        for c in self.hass.data.get(DOMAIN, {}).values():
            coordinator = c
            break
        if coordinator is None:
            return web.Response(status=503, text="Birdfy not ready")

        # Always fetch a fresh URL — cached ones expire after ~1h
        record_url = await coordinator.fetch_fresh_record_url(alarm_id)
        if not record_url:
            return web.Response(status=404, text="Event not found")

        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(record_url) as r:
                    if r.status != 200:
                        return web.Response(status=r.status, text="Upstream error")
                    content = await r.text()
        except Exception as e:
            return web.Response(status=502, text=str(e))

        # Fix #EXTINF durations: Netvue uses milliseconds, HLS spec requires seconds
        lines = []
        for line in content.splitlines():
            if line.startswith("#EXTINF:"):
                try:
                    dur_str = line.split(":")[1].rstrip(",")
                    dur_ms = float(dur_str)
                    if dur_ms > 1000:
                        line = f"#EXTINF:{dur_ms / 1000:.3f},"
                except Exception:
                    pass
            lines.append(line)

        return web.Response(
            text="\n".join(lines),
            content_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*"},
        )


class BirdfySegmentProxyView(HomeAssistantView):
    """Proxies a single .ts segment from S3."""

    url = "/api/birdfy/segment/{encoded_url}"
    name = "api:birdfy:segment"
    requires_auth = False

    async def get(self, request: web.Request, encoded_url: str) -> web.Response:
        import urllib.parse
        # aiohttp decodes path params once; decode again to handle double-encoding
        url = urllib.parse.unquote(urllib.parse.unquote(encoded_url))

        if not url.startswith("https://nvs-eu-central-1-videomotion.s3"):
            return web.Response(status=403, text="Forbidden")

        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url) as r:
                    data = await r.read()
                    return web.Response(
                        body=data,
                        content_type="video/mp2t",
                        headers={"Access-Control-Allow-Origin": "*"},
                    )
        except Exception as e:
            return web.Response(status=502, text=str(e))
