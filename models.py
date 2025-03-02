from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
from enum import Enum
import aiohttp


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
    episodes: List[SonarrWebhookEpisode]
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

    @property
    def is_sonarr(self) -> bool:
        return self.type.lower() == "sonarr"

    async def delete_series(self, tvdb_id: int) -> Dict[str, Any]:
        """Delete a series by TVDB ID"""
        # First get the series ID from TVDB ID
        series = await self.get_series_by_tvdb_id(tvdb_id)
        if not series:
            raise ValueError(f"Series with TVDB ID {tvdb_id} not found")
            
        url = f"{self.url}/api/v3/series/{series['id']}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete series: {await response.text()}")
                return await response.json()
    
    async def delete_episode(self, episode_id: int) -> Dict[str, Any]:
        """Delete an episode file"""
        url = f"{self.url}/api/v3/episodeFile/{episode_id}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete episode: {await response.text()}")
                return await response.json()

    async def refresh_series(self, series_id: int) -> Dict[str, Any]:
        """Refresh series metadata and scan files"""
        url = f"{self.url}/api/v3/command"
        data = {
            "name": "RefreshSeries",
            "seriesId": series_id
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as response:
                if response.status != 201:
                    raise Exception(f"Failed to refresh series: {await response.text()}")
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

    @property
    def is_radarr(self) -> bool:
        return self.type.lower() == "radarr"
        
    @property
    def headers(self) -> Dict[str, str]:
        """Return headers for API requests"""
        return {"X-Api-Key": self.api_key}
        
    async def get_movie_by_tmdb_id(self, tmdb_id: int) -> Dict[str, Any]:
        """Get a movie by TMDB ID"""
        url = f"{self.url}/api/v3/movie?tmdbId={tmdb_id}"
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
            
        url = f"{self.url}/api/v3/movie/{movie['id']}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete movie: {await response.text()}")
                return await response.json()
    
    async def delete_movie_file(self, movie_file_id: int) -> Dict[str, Any]:
        """Delete a movie file"""
        url = f"{self.url}/api/v3/movieFile/{movie_file_id}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers) as response:
                if response.status != 200:
                    raise Exception(f"Failed to delete movie file: {await response.text()}")
                return await response.json()

    async def refresh_movie(self, movie_id: int) -> Dict[str, Any]:
        """Refresh movie metadata and scan files"""
        url = f"{self.url}/api/v3/command"
        data = {
            "name": "RefreshMovie",
            "movieId": movie_id
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as response:
                if response.status != 201:
                    raise Exception(f"Failed to refresh movie: {await response.text()}")
                return await response.json()


class MediaServerBase(BaseModel):
    """Base model for media server configurations"""
    name: str
    type: str
    url: str
    enabled: bool = True

class PlexServer(MediaServerBase):
    token: str
    type: str = "plex"

class JellyfinServer(MediaServerBase):
    api_key: str
    type: str = "jellyfin"

class EmbyServer(MediaServerBase):
    api_key: str
    type: str = "emby"

MediaServer = Union[PlexServer, JellyfinServer, EmbyServer]
