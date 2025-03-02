import logging
import requests
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from models import PlexServer, JellyfinServer, EmbyServer
from urllib.parse import urljoin
from pathlib import Path

# Create module logger
logger = logging.getLogger(__name__)

class MediaServerScanner:
    def __init__(self, servers: List[Dict[str, Any]]):
        self.servers = []
        logger.info(f"Initializing MediaServerScanner with {len(servers)} server(s)")
        for server in servers:
            logger.info(f"Processing server config: name={server.get('name')}, type={server.get('type')}, enabled={server.get('enabled')}")
            if server["type"] == "plex":
                self.servers.append(PlexServer(**server))
            elif server["type"] == "jellyfin":
                self.servers.append(JellyfinServer(**server))
            elif server["type"] == "emby":
                self.servers.append(EmbyServer(**server))

    async def scan_path(self, path: str, is_series: bool = False) -> List[Dict[str, Any]]:
        try:
            # Get parent folder for both movies and series
            parent_path = str(Path(path).parent)
            logger.info(f"Scanning path: {path} (parent: {parent_path}, is_series: {is_series})")
            
            if is_series:
                scan_path = path
                library_type = "Series"
            else:
                scan_path = path  # Use exact path for movies
                library_type = "Movies"
                await asyncio.sleep(2)

            logger.info(f"Using scan path: {scan_path} for library type: {library_type}")

            if not self.servers:
                logger.error("No media servers configured or all servers are disabled")
                return [{"status": "error", "error": "No media servers configured"}]

            results = []
            active_servers = [s for s in self.servers if s.enabled]
            logger.info(f"Found {len(active_servers)} enabled media servers out of {len(self.servers)} total")
            
            if not active_servers:
                logger.error("All media servers are disabled")
                return [{"status": "error", "error": "All media servers are disabled"}]

            for server in self.servers:
                if not server.enabled:
                    logger.info(f"Skipping disabled server: {server.name}")
                    continue
                    
                try:
                    logger.info(f"Processing server: name={server.name}, type={server.type}, url={server.url}")
                    if server.type == "plex":
                        result = await self._scan_plex(server, scan_path, library_type)
                        logger.info(f"Scan moved to target library={library_type} path=\"{scan_path}\" target=plex url={server.url}")
                        results.append({
                            "server": server.name,
                            "type": server.type,
                            "status": "success",
                            "result": result
                        })
                    elif server.type == "jellyfin":
                        result = await self._scan_jellyfin(server, scan_path)
                        results.append({
                            "server": server.name,
                            "type": server.type,
                            "status": "success",
                            "result": result
                        })
                    elif server.type == "emby":
                        result = await self._scan_emby(server, scan_path)
                        results.append({
                            "server": server.name,
                            "type": server.type,
                            "status": "success",
                            "result": result
                        })
                    else:
                        logger.warning(f"Unknown server type: {server.type}")
                        continue
                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"Failed to scan {server.type} target={server.name} error=\"{error_msg}\"", exc_info=True)
                    results.append({
                        "server": server.name,
                        "type": server.type,
                        "status": "error",
                        "error": error_msg
                    })

            if not results:
                logger.error("No scan results were generated. Check server configurations.")
                return [{"status": "error", "error": "No scan results were generated"}]
                
            return results
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error in scan_path: {error_msg}", exc_info=True)
            return [{"status": "error", "error": f"Scan path failed: {error_msg}"}]

    async def _scan_plex(self, server: PlexServer, path: str, library_type: str) -> Dict[str, Any]:
        headers = {
            "X-Plex-Token": server.token,
            "Accept": "application/json"
        }
        
        logger.info(f"Starting Plex scan for server {server.name} at path: {path}")
        logger.info(f"Server URL: {server.url}, Library Type: {library_type}")
        
        async with aiohttp.ClientSession() as session:
            try:
                # First get all library sections
                sections_url = urljoin(server.url, "library/sections")
                logger.info(f"Fetching Plex library sections from: {sections_url}")
                async with session.get(sections_url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to get Plex sections. Status: {response.status}, Response: {error_text}")
                        raise ValueError(f"Failed to get Plex sections: {response.status} - {error_text}")
                    
                    sections = await response.json()
                    logger.info(f"Retrieved {len(sections.get('MediaContainer', {}).get('Directory', []))} sections from Plex")

                # Find the section containing our path
                section_id = None
                matching_sections = []

                # First pass: collect all sections of the correct type
                for section in sections["MediaContainer"]["Directory"]:
                    section_type = section["type"]
                    section_title = section["title"]
                    logger.info(f"Checking section: {section_title} (type: {section_type})")
                    
                    if (library_type == "Movies" and section_type == "movie") or \
                       (library_type == "Series" and section_type == "show"):
                        for location in section["Location"]:
                            location_path = location["path"]
                            logger.info(f"Found matching library type: {section_title} with path: {location_path}")
                            matching_sections.append((section, location_path))

                if not matching_sections:
                    error_msg = f"No {library_type} libraries found in Plex"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                logger.info(f"Found {len(matching_sections)} potential matching sections")

                # Second pass: find best matching section
                for section, location_path in matching_sections:
                    logger.info(f"Checking if path {path} matches library path {location_path}")
                    
                    # Normalize both paths for comparison
                    normalized_scan_path = Path(path).as_posix()
                    normalized_location = Path(location_path).as_posix()
                    
                    # Try different path matching strategies
                    if normalized_scan_path.startswith(normalized_location):
                        section_id = section["key"]
                        logger.info(f"Found exact path match in section: {section['title']} (id: {section_id})")
                        break
                    elif normalized_location in normalized_scan_path:
                        section_id = section["key"]
                        logger.info(f"Found partial path match in section: {section['title']} (id: {section_id})")
                        break

                if not section_id:
                    error_msg = f"No matching library section found for path: {path}"
                    logger.error(f"{error_msg}. Available {library_type} libraries: {[s[0]['title'] for s in matching_sections]}")
                    raise ValueError(error_msg)

                # Use the exact movie folder path for scanning
                scan_path = path  # This is the exact movie folder path we want to scan
                logger.info(f"Using exact folder path for scan: {scan_path}")

                # Properly encode the path parameter
                from urllib.parse import quote
                encoded_path = quote(scan_path)
                
                # Construct the scan URL with the correct format
                scan_url = f"{server.url}/library/sections/{section_id}/refresh?path={encoded_path}"
                logger.info(f"Initiating Plex scan with URL: {scan_url}")
                
                async with session.get(scan_url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Plex scan failed. Status: {response.status}, Response: {error_text}")
                        raise ValueError(f"Plex scan failed: {response.status} - {error_text}")
                        
                    logger.info(f"Plex scan initiated successfully for section {section_id}")
                    scan_response = await response.text()
                    logger.info(f"Scan response: {scan_response}")
                    
                return {
                    "message": "Scan initiated",
                    "section": section_id,
                    "path": scan_path,
                    "scan_url": scan_url
                }
                
            except aiohttp.ClientError as e:
                logger.error(f"Plex API error: {str(e)}", exc_info=True)
                raise
            except Exception as e:
                logger.error(f"Error scanning Plex: {str(e)}", exc_info=True)
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
