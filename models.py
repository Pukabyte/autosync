from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from enum import Enum
import aiohttp
import xml.etree.ElementTree as ET
import logging

logger = logging.getLogger(__name__)


# Webhook Models (What we receive)
class Language(BaseModel):
    id: int
    name: str


class MediaImage(BaseModel):
    coverType: str
    url: str
    remoteUrl: str


# Base model for common webhook fields
class WebhookBase(BaseModel):
    eventType: str
    applicationUrl: Optional[str] = ""
    instanceName: str


class SonarrWebhookSeries(BaseModel):
    id: int
    title: str
    path: str
    tvdbId: int
    type: str
    year: int
    titleSlug: Optional[str] = None
    tvMazeId: Optional[int] = None
    tmdbId: Optional[int] = None
    imdbId: Optional[str] = None
    genres: Optional[List[str]] = []
    images: Optional[List[MediaImage]] = []
    tags: List[Any] = []
    originalLanguage: Optional[Language] = None


class SonarrWebhookEpisode(BaseModel):
    id: int
    episodeNumber: int
    seasonNumber: int
    title: str
    overview: Optional[str] = None
    airDate: Optional[str] = None
    airDateUtc: Optional[str] = None
    seriesId: int
    tvdbId: int


class SonarrCustomFormat(BaseModel):
    id: int
    name: str


class SonarrReleaseInfo(BaseModel):
    quality: str = None
    qualityVersion: int = None
    releaseTitle: str = None
    indexer: str = None
    size: int = None
    customFormatScore: int = None
    customFormats: List[str] = None
    languages: List[Language] = None


class SonarrCustomFormatInfo(BaseModel):
    customFormats: List[SonarrCustomFormat]
    customFormatScore: int


class WebhookPayload(BaseModel):
    series: SonarrWebhookSeries
    episodes: List[SonarrWebhookEpisode]
    eventType: str
    instanceName: str
    applicationUrl: str
    release: Optional[SonarrReleaseInfo] = None
    downloadClient: Optional[str] = None
    downloadClientType: Optional[str] = None
    customFormatInfo: Optional[SonarrCustomFormatInfo] = None


# Sonarr-specific webhook (reusing our existing WebhookPayload)
class SonarrWebhook(WebhookBase):
    series: SonarrWebhookSeries
    episodes: Optional[List[SonarrWebhookEpisode]] = None
    release: Optional[SonarrReleaseInfo] = None
    downloadClient: Optional[str] = None
    downloadClientType: Optional[str] = None
    customFormatInfo: Optional[SonarrCustomFormatInfo] = None


# Radarr-specific webhook (you'll need to define these models)
class RadarrWebhook(WebhookBase):
    movie: Dict[str, Any]  # Replace with proper movie model when needed


class MediaWebhook(BaseModel):
    webhook: Union[SonarrWebhook, RadarrWebhook]


# Sonarr API Models (What we send)
class SonarrMonitorTypes(str, Enum):
    unknown = "unknown"
    all = "all"
    future = "future"
    missing = "missing"
    existing = "existing"
    firstSeason = "firstSeason"
    lastSeason = "lastSeason"
    latestSeason = "latestSeason"
    pilot = "pilot"
    recent = "recent"
    monitorSpecials = "monitorSpecials"
    unmonitorSpecials = "unmonitorSpecials"
    none = "none"
    skip = "skip"


class SonarrAddSeriesOptions(BaseModel):
    ignoreEpisodesWithFiles: bool = False
    ignoreEpisodesWithoutFiles: bool = False
    monitor: SonarrMonitorTypes = SonarrMonitorTypes.all
    searchForMissingEpisodes: bool = False
    searchForCutoffUnmetEpisodes: bool = False


class Season(BaseModel):
    seasonNumber: int
    monitored: bool


class SonarrEpisode(BaseModel):
    """Model for Sonarr Episode Resource"""

    id: int
    seriesId: int
    tvdbId: Optional[int] = None
    episodeFileId: int
    seasonNumber: int
    episodeNumber: int
    title: Optional[str] = None
    airDate: Optional[str] = None
    airDateUtc: Optional[str] = None
    lastSearchTime: Optional[str] = None
    runtime: Optional[int] = None
    finaleType: Optional[str] = None
    overview: Optional[str] = None
    episodeFile: Optional[Dict[str, Any]] = None
    hasFile: bool
    monitored: bool
    absoluteEpisodeNumber: Optional[int] = None
    sceneAbsoluteEpisodeNumber: Optional[int] = None
    sceneEpisodeNumber: Optional[int] = None
    sceneSeasonNumber: Optional[int] = None
    unverifiedSceneNumbering: bool = None
    endTime: Optional[str] = None
    grabDate: Optional[str] = None
    series: Optional[Dict[str, Any]] = None
    images: List[Dict[str, Any]] = []

    class Config:
        extra = "ignore"  # Allow extra fields in the data


class SonarrSeries(BaseModel):
    """Model for series creation/updates in Sonarr"""

    tvdbId: int
    title: str
    qualityProfileId: int
    seasonFolder: bool
    rootFolderPath: str
    monitored: bool = True
    seasons: List[Season]
    addOptions: Optional[SonarrAddSeriesOptions] = None
    seriesType: str = "standard"


class PathRewrite(BaseModel):
    """Model for path rewriting configuration"""
    from_path: str
    to_path: str

    class Config:
        json_schema_extra = {
            "example": {
                "from_path": "/mnt/plex",
                "to_path": "/mnt/remote/plex"
            }
        }

class MediaServerBase(BaseModel):
    """Base model for media server configurations"""
    name: str
    type: str
    url: str
    enabled: bool = True
    rewrite: Optional[List[PathRewrite]] = []

    @property
    def base_url(self) -> str:
        """Return the base URL with protocol"""
        if not self.url.startswith(('http://', 'https://')):
            url = f"http://{self.url}"
            logger.debug(f"Added http:// protocol to URL: {url}")
            return url
        return self.url

    async def _make_request(self, method: str, endpoint: str, **kwargs) -> Any:
        """Make an HTTP request with proper URL handling"""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, headers=self.headers, **kwargs) as response:
                if response.status not in [200, 201, 204]:
                    error_text = await response.text()
                    raise Exception(f"Request failed with status {response.status}: {error_text}")
                if response.status == 204:
                    return {"status": "success"}
                try:
                    return await response.json()
                except:
                    return await response.text()

class PlexServer(MediaServerBase):
    token: str
    type: str = "plex"

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the server configuration"""
        return getattr(self, key, default)

    @property
    def headers(self) -> Dict[str, str]:
        """Return headers for API requests"""
        return {
            "X-Plex-Token": self.token,
            "Accept": "application/xml"  # Force XML response
        }

    async def scan_path(self, path: str) -> Dict[str, Any]:
        """Scan a specific path in Plex"""
        # First get library sections
        sections_text = await self._make_request("GET", "library/sections")
        root = ET.fromstring(sections_text)
                
        # Find matching section for the path
        section_id = None
        for directory in root.findall(".//Directory"):
            for location in directory.findall(".//Location"):
                if path.startswith(location.get("path", "")):
                    section_id = directory.get("key")
                    break
            if section_id:
                break

        if not section_id:
            raise ValueError(f"No matching library section found for path: {path}")

        # Trigger scan for the section
        await self._make_request("POST", f"library/sections/{section_id}/refresh")
        return {"status": "success", "message": "Scan initiated"}

class JellyfinServer(MediaServerBase):
    api_key: str
    type: str = "jellyfin"

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the server configuration"""
        return getattr(self, key, default)

    @property
    def headers(self) -> Dict[str, str]:
        """Return headers for API requests"""
        return {"X-MediaBrowser-Token": self.api_key}

    async def scan_path(self, path: str) -> Dict[str, Any]:
        """Scan a specific path in Jellyfin"""
        await self._make_request("POST", "Library/Refresh")
        return {"status": "success", "message": "Scan initiated"}

class EmbyServer(MediaServerBase):
    api_key: str
    type: str = "emby"

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the server configuration"""
        return getattr(self, key, default)

    @property
    def headers(self) -> Dict[str, str]:
        """Return headers for API requests"""
        return {"X-Emby-Token": self.api_key}

    async def scan_path(self, path: str) -> Dict[str, Any]:
        """Scan a specific path in Emby"""
        await self._make_request("POST", "Library/Refresh")
        return {"status": "success", "message": "Scan initiated"}

MediaServer = Union[PlexServer, JellyfinServer, EmbyServer]

class SonarrInstance(BaseModel):
    """Configuration model for Sonarr instances"""

    name: str
    type: str
    url: str
    api_key: str
    root_folder_path: str
    season_folder: bool = True
    quality_profile_id: int = 1
    language_profile_id: int = 1
    search_on_sync: bool = False
    enabled_events: List[str] = []
    rewrite: Optional[List[PathRewrite]] = []

    @property
    def is_sonarr(self) -> bool:
        return self.type.lower() == "sonarr"

    @property
    def base_url(self) -> str:
        """Return the base URL with protocol"""
        if not self.url.startswith(('http://', 'https://')):
            return f"http://{self.url}"
        return self.url

    @property
    def headers(self) -> Dict[str, str]:
        """Return headers for API requests"""
        return {"X-Api-Key": self.api_key}

    async def get_series_by_tvdb_id(self, tvdb_id: int) -> Optional[Dict[str, Any]]:
        """Get a series by TVDB ID"""
        url = f"{self.base_url}/api/v3/series?tvdbId={tvdb_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to get series: {await response.text()}")
                series = await response.json()
                return series[0] if series else None

    async def delete_series(self, tvdb_id: int) -> Dict[str, Any]:
        """Delete a series by TVDB ID"""
        # First get the series ID from TVDB ID
        series = await self.get_series_by_tvdb_id(tvdb_id)
        if not series:
            raise ValueError(f"Series with TVDB ID {tvdb_id} not found")
            
        url = f"{self.base_url}/api/v3/series/{series['id']}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete series: {await response.text()}")
                return await response.json()
    
    async def delete_episode(self, episode_id: int) -> Dict[str, Any]:
        """Delete an episode file"""
        url = f"{self.base_url}/api/v3/episodeFile/{episode_id}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete episode: {await response.text()}")
                return await response.json()

    async def refresh_series(self, series_id: int) -> Dict[str, Any]:
        """Refresh series metadata and scan files"""
        url = f"{self.base_url}/api/v3/command"
        data = {
            "name": "RefreshSeries",
            "seriesId": series_id
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as response:
                if response.status != 201:
                    raise Exception(f"Failed to refresh series: {await response.text()}")
                return await response.json()

    async def import_series(self, tvdb_id: int, path: str) -> Dict[str, Any]:
        """Import a series by refreshing and rescanning"""
        # First get the series ID from TVDB ID
        series = await self.get_series_by_tvdb_id(tvdb_id)
        if not series:
            raise ValueError(f"Series with TVDB ID {tvdb_id} not found")
            
        series_id = series["id"]
        
        # First refresh the series
        await self.refresh_series(series_id)
        
        # Then trigger a rescan
        url = f"{self.base_url}/api/v3/command"
        data = {
            "name": "RescanSeries",
            "seriesId": series_id
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as response:
                if response.status != 201:
                    raise Exception(f"Failed to rescan series: {await response.text()}")
                return await response.json()


class RadarrInstance(BaseModel):
    """Configuration model for Radarr instances"""

    name: str
    type: str
    url: str
    api_key: str
    root_folder_path: str
    quality_profile_id: int = 1
    search_on_sync: bool = False
    enabled_events: List[str] = []
    rewrite: Optional[List[PathRewrite]] = []

    @property
    def is_radarr(self) -> bool:
        return self.type.lower() == "radarr"
        
    @property
    def base_url(self) -> str:
        """Return the base URL with protocol"""
        if not self.url.startswith(('http://', 'https://')):
            return f"http://{self.url}"
        return self.url
        
    @property
    def headers(self) -> Dict[str, str]:
        """Return headers for API requests"""
        return {"X-Api-Key": self.api_key}
        
    async def get_movie_by_tmdb_id(self, tmdb_id: int) -> Dict[str, Any]:
        """Get a movie by TMDB ID"""
        url = f"{self.base_url}/api/v3/movie?tmdbId={tmdb_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to get movie: {await response.text()}")
                movies = await response.json()
                return movies[0] if movies else None

    async def delete_movie(self, tmdb_id: int) -> Dict[str, Any]:
        """Delete a movie by TMDB ID"""
        # First get the movie ID from TMDB ID
        movie = await self.get_movie_by_tmdb_id(tmdb_id)
        if not movie:
            raise ValueError(f"Movie with TMDB ID {tmdb_id} not found")
            
        url = f"{self.base_url}/api/v3/movie/{movie['id']}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete movie: {await response.text()}")
                return await response.json()
    
    async def delete_movie_file(self, movie_file_id: int) -> Dict[str, Any]:
        """Delete a movie file"""
        url = f"{self.base_url}/api/v3/movieFile/{movie_file_id}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete movie file: {await response.text()}")
                return await response.json()

    async def refresh_movie(self, movie_id: int) -> Dict[str, Any]:
        """Refresh movie metadata and scan files"""
        url = f"{self.base_url}/api/v3/command"
        data = {
            "name": "RefreshMovie",
            "movieId": movie_id
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as response:
                if response.status != 201:
                    raise Exception(f"Failed to refresh movie: {await response.text()}")
                return await response.json()
