"""
YouTube video upload via the Data API v3.

Handles resumable uploads with retry logic, metadata setting,
and custom thumbnail upload.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from peanut_reacts.core.retry import retry

logger = logging.getLogger(__name__)


@dataclass
class UploadMetadata:
    """Metadata for a YouTube video upload."""
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = "24"             # Entertainment
    privacy_status: str = "private"     # private | unlisted | public
    thumbnail_path: Optional[Path] = None


@dataclass(frozen=True)
class UploadResult:
    """Result of a successful upload."""
    video_id: str
    url: str


class YouTubeUploader:
    """Upload videos to YouTube via the Data API v3."""

    def __init__(self, service) -> None:
        self._yt = service

    @retry(max_attempts=3, delay=5.0, backoff=2.0, exceptions=(HttpError, ConnectionError, TimeoutError))
    def upload_video(self, video_path: Path, metadata: UploadMetadata) -> UploadResult:
        """Upload a video file to YouTube with metadata.

        Uses resumable upload (10MB chunks) for reliability on large files.
        Returns UploadResult with video_id and URL.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        body = {
            "snippet": {
                "title": metadata.title[:100],
                "description": metadata.description[:5000],
                "tags": metadata.tags[:500],
                "categoryId": metadata.category_id,
            },
            "status": {
                "privacyStatus": metadata.privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10MB chunks
        )

        logger.info("Uploading %s (%s) ...", video_path.name, metadata.privacy_status)

        request = self._yt.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # Resumable upload loop
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                logger.info("  Upload progress: %d%%", pct)

        video_id = response["id"]
        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info("Upload complete! %s", url)

        # Upload thumbnail if provided
        if metadata.thumbnail_path and metadata.thumbnail_path.exists():
            self.set_thumbnail(video_id, metadata.thumbnail_path)

        return UploadResult(video_id=video_id, url=url)

    @retry(max_attempts=2, delay=2.0, exceptions=(HttpError,))
    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        """Upload a custom thumbnail for an already-uploaded video."""
        logger.info("Setting thumbnail from %s ...", thumbnail_path.name)
        media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg")
        self._yt.thumbnails().set(
            videoId=video_id,
            media_body=media,
        ).execute()
        logger.info("Thumbnail set for %s", video_id)
