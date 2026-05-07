"""Birdfy Media Source — browse and play recorded events by day."""
from __future__ import annotations

import datetime
import time

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BirdfyCoordinator

DAYS_BACK = 30


async def async_get_media_source(hass: HomeAssistant) -> BirdfyMediaSource:
    return BirdfyMediaSource(hass)


class BirdfyMediaSource(MediaSource):
    """Birdfy recorded events as a browsable media source."""

    name = "Birdfy"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    def _get_coordinator(self) -> BirdfyCoordinator | None:
        for coordinator in self.hass.data.get(DOMAIN, {}).values():
            return coordinator
        return None

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Return the proxied HLS URL for a given alarm_id."""
        coordinator = self._get_coordinator()
        if coordinator is None:
            raise Unresolvable("Birdfy coordinator not found")

        alarm_id = item.identifier
        # Search in recent_events first
        events = coordinator.data.get("recent_events", []) if coordinator.data else []
        for ev in events:
            if ev["alarm_id"] == alarm_id:
                return PlayMedia(
                    url=f"/api/birdfy/m3u8/{alarm_id}",
                    mime_type="video/mp4",
                )

        # Not in cache — still serve the proxy URL (http.py will fetch it)
        if alarm_id:
            return PlayMedia(
                url=f"/api/birdfy/m3u8/{alarm_id}",
                mime_type="video/mp4",
            )
        raise Unresolvable(f"Event {alarm_id} not found")

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        coordinator = self._get_coordinator()

        identifier = item.identifier or ""

        # Root level — show last 30 days as folders
        if not identifier:
            return self._browse_root(coordinator)

        # Day level — show clips for that day
        if identifier.startswith("day:"):
            date_str = identifier[4:]
            return await self._browse_day(coordinator, date_str)

        # Should not happen
        raise Unresolvable(f"Unknown identifier: {identifier}")

    def _browse_root(self, coordinator: BirdfyCoordinator | None) -> BrowseMediaSource:
        today = datetime.date.today()
        children = []
        for i in range(DAYS_BACK):
            d = today - datetime.timedelta(days=i)
            date_str = d.strftime("%Y-%m-%d")
            label = d.strftime("%d %b %Y")
            if i == 0:
                label = f"Today — {label}"
            elif i == 1:
                label = f"Yesterday — {label}"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"day:{date_str}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type="video/mp4",
                    title=label,
                    can_play=False,
                    can_expand=True,
                )
            )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="",
            media_class=MediaClass.DIRECTORY,
            media_content_type="video/mp4",
            title="Birdfy",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_day(self, coordinator: BirdfyCoordinator | None, date_str: str) -> BrowseMediaSource:
        if coordinator is None:
            events = []
        else:
            events = await coordinator.fetch_events_for_day(date_str)

        children = []
        for ev in events:
            if not ev.get("record_url") and not coordinator.segments_cache.get(ev.get("alarm_id")):
                continue
            alarm_id = ev["alarm_id"]
            alert_time = ev.get("alert_time", 0)
            ts = time.strftime("%H:%M:%S", time.localtime(alert_time / 1000)) if alert_time else "?"
            label = ev.get("label", "unknown")
            title = f"{ts} — {label}"
            thumbnail = coordinator.thumbnail_cache.get(alarm_id) if coordinator else None
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=alarm_id,
                    media_class=MediaClass.VIDEO,
                    media_content_type="video/mp4",
                    title=title,
                    can_play=True,
                    can_expand=False,
                    thumbnail=thumbnail,
                )
            )

        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        title = d.strftime("%d %B %Y") + f" ({len(children)} clips)"

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"day:{date_str}",
            media_class=MediaClass.DIRECTORY,
            media_content_type="video/mp4",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )
