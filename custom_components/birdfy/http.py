"""HTTP proxy views for Birdfy HLS streams."""
from __future__ import annotations

import logging

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Approximate segment duration in seconds (Netvue segments are ~2s each)
_SEGMENT_DURATION = 2.0


def register_views(hass: HomeAssistant) -> None:
    hass.http.register_view(BirdfyM3U8ProxyView(hass))
    hass.http.register_view(BirdfySegmentProxyView)


def _build_m3u8_from_segments(segments: list[str]) -> str:
    """Build a valid HLS playlist from a list of .ts URLs with JWT tokens."""
    lines = [
        "#EXTM3U",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        f"#EXT-X-TARGETDURATION:{int(_SEGMENT_DURATION) + 1}",
        "#EXT-X-VERSION:3",
    ]
    for url in segments:
        lines.append(f"#EXTINF:{_SEGMENT_DURATION:.3f},")
        lines.append(url)
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines)


def _fix_proxied_m3u8(content: str) -> str:
    """Normalize a Netvue M3U8 fetched from the signed recordUrl."""
    content = content.replace(" #", "\n#")
    lines = []
    has_endlist = False
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
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
        elif line == "#EXT-X-ENDLIST":
            has_endlist = True
        lines.append(line)
    if not has_endlist:
        lines.append("#EXT-X-ENDLIST")
    if lines and lines[0] == "#EXTM3U" and "#EXT-X-INDEPENDENT-SEGMENTS" not in lines:
        lines.insert(1, "#EXT-X-INDEPENDENT-SEGMENTS")
    return "\n".join(lines)


class BirdfyM3U8ProxyView(HomeAssistantView):
    """Serves an HLS playlist for a given alarm_id."""

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

        # Prefer pre-fetched JWT segments (3-day TTL, better compatibility)
        segments = coordinator.segments_cache.get(alarm_id)
        if segments:
            result = _build_m3u8_from_segments(segments)
            return web.Response(
                text=result,
                content_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        # Fallback: fetch the signed M3U8 URL from cache
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
        except Exception as e:
            return web.Response(status=502, text=str(e))

        result = _fix_proxied_m3u8(content)
        return web.Response(
            text=result,
            content_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*"},
        )


class BirdfySegmentProxyView(HomeAssistantView):
    """Proxies a single .ts segment from S3 (fallback for old-style URLs)."""

    url = "/api/birdfy/segment/{encoded_url}"
    name = "api:birdfy:segment"
    requires_auth = False

    async def get(self, request: web.Request, encoded_url: str) -> web.Response:
        import urllib.parse
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
