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

        record_url = coordinator.record_url_cache.get(alarm_id)
        if not record_url and coordinator.data:
            for ev in coordinator.data.get("recent_events", []):
                if ev["alarm_id"] == alarm_id:
                    record_url = ev["record_url"]
                    break

        if not record_url:
            return web.Response(status=404, text="Event not found")

        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(record_url) as r:
                    if r.status != 200:
                        return web.Response(status=r.status, text="Upstream error")
                    content = await r.text()
                    import logging
                    logging.getLogger(__name__).error("M3U8 alarm=%s status=%s content=%s", alarm_id, r.status, content[:800])
        except Exception as e:
            return web.Response(status=502, text=str(e))

        # Fix durations: Netvue uses milliseconds, HLS spec requires seconds
        lines = []
        for line in content.splitlines():
            if line.startswith("#EXTINF:"):
                try:
                    dur_ms = float(line.split(":")[1].rstrip(","))
                    if dur_ms > 1000:
                        line = f"#EXTINF:{dur_ms / 1000:.3f},"
                except Exception:
                    pass
            elif line.startswith("#EXT-X-TARGETDURATION:"):
                try:
                    val = float(line.split(":")[1])
                    if val > 1000:
                        line = f"#EXT-X-TARGETDURATION:{int(val / 1000) + 1}"
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
