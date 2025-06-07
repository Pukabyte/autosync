import logging
import requests
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from models import PlexServer as PlexServerModel, JellyfinServer as JellyfinServerModel, EmbyServer as EmbyServerModel
from urllib.parse import urljoin
from pathlib import Path
from utils import rewrite_path
import xml.etree.ElementTree as ET
from urllib.parse import quote

# Create module logger
logger = logging.getLogger(__name__)

class MediaServerBase:
    """Base class for media server implementations"""
    def __init__(self, **kwargs):
        self.name = kwargs.get('name', 'Unknown')
        self.url = kwargs.get('url', '')
        self.type = kwargs.get('type', 'unknown')
        self.rewrite = kwargs.get('rewrite', [])
        
    async def _make_request(self, method: str, endpoint: str, **kwargs) -> str:
        """Make a request to the media server API"""
        url = urljoin(self.url, endpoint)
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                return await response.text()
                
    async def scan_path(self, path: str, **kwargs) -> Dict[str, Any]:
        """Scan a path on the media server"""
        raise NotImplementedError("Subclasses must implement scan_path")

class PlexServer(MediaServerBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.token = kwargs.get('token', '')
        self.type = "plex"

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

    async def scan_path(self, path: str, plex_library_id: Optional[int] = None) -> Dict[str, Any]:
        """Scan a specific path in Plex"""
        # First get library sections
        sections_text = await self._make_request("GET", "library/sections", headers=self.headers)
        root = ET.fromstring(sections_text)
                
        # Find matching section for the path
        section_id = None
        
        # If plex_library_id is provided, use it directly
        if plex_library_id is not None:
            for directory in root.findall(".//Directory"):
                if int(directory.get("key", "0")) == plex_library_id:
                    section_id = str(plex_library_id)
                    logger.debug(f"Using specified Plex library ID: {section_id}")
                    break
            if not section_id:
                raise ValueError(f"Specified Plex library ID {plex_library_id} not found")
        else:
            # Fall back to path-based matching
            # First, apply any path rewrites
            rewritten_path = rewrite_path(path, self.rewrite)
            logger.debug(f"Original path: {path}")
            logger.debug(f"Rewritten path: {rewritten_path}")
            
            # Find the exact matching section
            for directory in root.findall(".//Directory"):
                for location in directory.findall(".//Location"):
                    location_path = location.get("path", "")
                    # Normalize paths for comparison
                    normalized_scan_path = Path(rewritten_path).resolve()
                    normalized_location = Path(location_path).resolve()
                    
                    # Check if the scan path is within this location
                    try:
                        if normalized_scan_path.is_relative_to(normalized_location):
                            section_id = directory.get("key")
                            logger.debug(f"Found exact matching Plex library by path: {section_id}")
                            logger.debug(f"  ├─ Scan path: {normalized_scan_path}")
                            logger.debug(f"  └─ Library path: {normalized_location}")
                            break
                    except ValueError:
                        # Path is not relative to location, continue checking
                        continue
                if section_id:
                    break

        if not section_id:
            raise ValueError(f"No matching library section found for path: {path}")

        # URL encode the path
        encoded_path = quote(path)

        # Trigger scan for the section with the specific path
        await self._make_request("POST", f"library/sections/{section_id}/refresh?path={encoded_path}", headers=self.headers)
        return {"status": "success", "message": "Scan initiated"}

class JellyfinServer(MediaServerBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api_key = kwargs.get('api_key', '')
        self.type = "jellyfin"

    async def scan_path(self, path: str) -> Dict[str, Any]:
        """Scan a path in Jellyfin"""
        headers = {
            "X-MediaBrowser-Token": self.api_key
        }
        
        # Trigger library scan
        scan_url = urljoin(self.url, "/Library/Refresh")
        async with aiohttp.ClientSession() as session:
            async with session.post(scan_url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                return {"message": "Scan initiated"}

class EmbyServer(MediaServerBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api_key = kwargs.get('api_key', '')
        self.type = "emby"

    async def scan_path(self, path: str) -> Dict[str, Any]:
        """Scan a path in Emby"""
        headers = {
            "X-Emby-Token": self.api_key
        }
        
        # Trigger library scan
        scan_url = urljoin(self.url, "/Library/Refresh")
        async with aiohttp.ClientSession() as session:
            async with session.post(scan_url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                return {"message": "Scan initiated"}

class MediaServerScanner:
    def __init__(self, media_servers: List[Dict[str, Any]]):
        self.media_servers = []
        logger.info(f"Initializing MediaServerScanner with \033[1m{len(media_servers)}\033[0m server(s)")
        
        if not media_servers:
            logger.warning("No media servers provided for scanning")
            return
            
        # First log all server details
        logger.info("Media server details:")
        for idx, server_data in enumerate(media_servers):
            server_name = server_data.get('name', 'Unknown')
            server_type = server_data.get('type', 'Unknown')
            server_enabled = server_data.get('enabled', True)
            
            status_color = "\033[32m" if server_enabled else "\033[31m"  # Green for enabled, red for disabled
            status_text = f"{status_color}{'enabled' if server_enabled else 'disabled'}\033[0m"
            
            prefix = "  └─ " if idx == len(media_servers) - 1 else "  ├─ "
            logger.info(f"{prefix}\033[1m{server_name}\033[0m ({server_type}): {status_text}")
        
        # Then initialize server objects
        for server_data in media_servers:
            if server_data.get('enabled', True):  # Only add enabled servers
                server_type = server_data.get('type', '').lower()
                if server_type == "plex":
                    self.media_servers.append(PlexServer(**server_data))
                elif server_type == "jellyfin":
                    self.media_servers.append(JellyfinServer(**server_data))
                elif server_type == "emby":
                    self.media_servers.append(EmbyServer(**server_data))

    async def scan_path(self, path: str, is_series: bool = False, plex_library_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Scan a path on all configured media servers."""
        results = []
        
        # Use the exact path passed in without modification
        logger.debug(f"Using path for scanning: {path}")
        
        for server in self.media_servers:
            try:
                # Apply path rewriting if configured
                rewritten_path = rewrite_path(path, server.rewrite)
                
                try:
                    # Pass plex_library_id to Plex servers
                    if isinstance(server, PlexServer):
                        result = await server.scan_path(rewritten_path, plex_library_id=plex_library_id)
                    else:
                        result = await server.scan_path(rewritten_path)
                        
                    results.append({
                        "server": server.name,
                        "type": server.type,
                        "status": "success",
                        "message": result.get("message", "Scan initiated")
                    })
                except Exception as e:
                    logger.error(f"Failed to scan {server.name}: {str(e)}")
                    results.append({
                        "server": server.name,
                        "type": server.type,
                        "status": "error",
                        "message": str(e)
                    })
                
            except Exception as e:
                logger.error(f"Failed to process server {server.name}: {str(e)}")
                results.append({
                    "server": server.name,
                    "type": server.type,
                    "status": "error",
                    "message": str(e)
                })
        
        return results

    async def _scan_plex(self, server: PlexServer, path: str, library_type: str) -> Dict[str, Any]:
        headers = {
            "X-Plex-Token": server.token,
            "Accept": "application/json"
        }
        
        # Get library sections
        sections_url = urljoin(server.url, "library/sections")
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(sections_url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to get Plex sections. Status: {response.status}, Response: {error_text}")
                        raise ValueError(f"Failed to get Plex sections: {response.status} - {error_text}")
                    
                    sections = await response.json()
                    section_count = len(sections.get('MediaContainer', {}).get('Directory', []))
                    logger.debug(f"Found \033[1m{section_count}\033[0m library sections")

                # Find matching sections
                matching_sections = []
                for section in sections["MediaContainer"]["Directory"]:
                    if ((library_type == "Movies" and section["type"] == "movie") or
                        (library_type == "Series" and section["type"] == "show")):
                        for location in section["Location"]:
                            matching_sections.append((section, location["path"]))

                if not matching_sections:
                    error_msg = f"No \033[1m{library_type}\033[0m libraries found"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                # Find best matching section
                section_id = None
                for section, location_path in matching_sections:
                    normalized_scan_path = Path(path).as_posix()
                    normalized_location = Path(location_path).as_posix()
                    
                    if normalized_scan_path.startswith(normalized_location):
                        section_id = section["key"]
                        logger.debug(f"Found exact match in section: \033[1m{section['title']}\033[0m (id: {section_id})")
                        break
                    elif normalized_location in normalized_scan_path:
                        section_id = section["key"]
                        logger.debug(f"Found partial match in section: \033[1m{section['title']}\033[0m (id: {section_id})")
                        break

                if not section_id:
                    error_msg = f"No matching library section found for path: \033[1m{path}\033[0m"
                    logger.error(f"{error_msg}")
                    raise ValueError(error_msg)

                # Construct scan URL
                encoded_path = quote(path)
                scan_url = f"{server.url}/library/sections/{section_id}/refresh?path={encoded_path}"
                
                async with session.get(scan_url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Plex scan failed. Status: {response.status}, Response: {error_text}")
                        raise ValueError(f"Plex scan failed: {response.status} - {error_text}")
                    
                    logger.debug(f"Scan initiated for section \033[1m{section_id}\033[0m")
                    
                    return {
                        "message": "Scan initiated",
                        "section": section_id,
                        "path": path,
                        "scan_url": scan_url
                    }
                
            except aiohttp.ClientError as e:
                logger.error(f"Plex API error: {str(e)}")
                raise
            except Exception as e:
                logger.error(f"Error scanning Plex: {str(e)}")
                raise

    async def _scan_jellyfin(self, server: JellyfinServer, path: str) -> Dict[str, Any]:
        headers = {
            "X-MediaBrowser-Token": server.api_key
        }
        
        # Trigger library scan
        scan_url = urljoin(server.url, "/Library/Refresh")
        async with aiohttp.ClientSession() as session:
            async with session.post(scan_url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                return {"message": "Scan initiated"}

    async def _scan_emby(self, server: EmbyServer, path: str) -> Dict[str, Any]:
        headers = {
            "X-Emby-Token": server.api_key
        }
        
        # Trigger library scan
        scan_url = urljoin(server.url, "/Library/Refresh")
        async with aiohttp.ClientSession() as session:
            async with session.post(scan_url, headers=headers, timeout=30) as response:
                response.raise_for_status()
                return {"message": "Scan initiated"} 
