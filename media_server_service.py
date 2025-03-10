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
        logger.info(f"Initializing MediaServerScanner with \033[1m{len(servers)}\033[0m server(s)")
        
        if not servers:
            logger.warning("No media servers provided for scanning")
            return
            
        logger.info("Media server details:")
        for idx, server in enumerate(servers):
            server_name = server.get('name', 'Unknown')
            server_type = server.get('type', 'Unknown')
            server_enabled = server.get('enabled', True)
            
            status_color = "\033[32m" if server_enabled else "\033[31m"  # Green for enabled, red for disabled
            status_text = f"{status_color}{'enabled' if server_enabled else 'disabled'}\033[0m"
            
            prefix = "  └─ " if idx == len(servers) - 1 else "  ├─ "
            logger.info(f"{prefix}\033[1m{server_name}\033[0m ({server_type}): {status_text}")
            
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
            logger.info(f"Scanning path: \033[1m{path}\033[0m")
            logger.debug(f"  ├─ Parent path: \033[1m{parent_path}\033[0m")
            logger.debug(f"  └─ Content type: \033[1m{'Series' if is_series else 'Movie'}\033[0m")
            
            if is_series:
                scan_path = path
                library_type = "Series"
            else:
                scan_path = path  # Use exact path for movies
                library_type = "Movies"
                await asyncio.sleep(2)

            # Remove redundant path logging and consolidate path details
            logger.debug(f"Path details:")
            logger.debug(f"  ├─ Scan path: \033[1m{scan_path}\033[0m")
            logger.debug(f"  ├─ Library type: \033[1m{library_type}\033[0m")
            logger.debug(f"  ├─ Absolute path: \033[1m{Path(scan_path).absolute()}\033[0m")
            logger.debug(f"  ├─ Path exists: \033[1m{Path(scan_path).exists() if Path(scan_path).is_absolute() else 'Unknown (relative path)'}\033[0m")
            logger.debug(f"  └─ Path type: \033[1m{'Directory' if Path(scan_path).is_dir() else 'File' if Path(scan_path).is_file() else 'Unknown'}\033[0m")

            if not self.servers:
                logger.error("No media servers configured or all servers are disabled")
                return [{"status": "error", "error": "No media servers configured"}]

            results = []
            active_servers = [s for s in self.servers if s.enabled]
            logger.debug(f"Media servers: \033[1m{len(active_servers)}\033[0m enabled out of \033[1m{len(self.servers)}\033[0m total")
            
            if not active_servers:
                logger.error("All media servers are disabled")
                return [{"status": "error", "error": "All media servers are disabled"}]

            for server in self.servers:
                if not server.enabled:
                    logger.debug(f"Skipping disabled server: \033[1m{server.name}\033[0m")
                    continue
                    
                try:
                    logger.debug(f"Processing server: name=\033[1m{server.name}\033[0m, type=\033[1m{server.type}\033[0m, url=\033[1m{server.url}\033[0m")
                    if server.type == "plex":
                        result = await self._scan_plex(server, scan_path, library_type)
                        logger.info(f"Scan completed for \033[1m{server.name}\033[0m (Plex)")
                        results.append({
                            "server": server.name,
                            "type": server.type,
                            "status": "success",
                            "result": result
                        })
                    elif server.type == "jellyfin":
                        result = await self._scan_jellyfin(server, scan_path)
                        logger.info(f"Scan completed for \033[1m{server.name}\033[0m (Jellyfin)")
                        results.append({
                            "server": server.name,
                            "type": server.type,
                            "status": "success",
                            "result": result
                        })
                    elif server.type == "emby":
                        result = await self._scan_emby(server, scan_path)
                        logger.info(f"Scan completed for \033[1m{server.name}\033[0m (Emby)")
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
                    logger.error(f"Failed to scan \033[1m{server.type}\033[0m target=\033[1m{server.name}\033[0m error=\"\033[1m{error_msg}\033[0m\"", exc_info=True)
                    results.append({
                        "server": server.name,
                        "type": server.type,
                        "status": "error",
                        "error": error_msg
                    })

            if not results:
                logger.error("No scan results were generated. Check server configurations.")
                return [{"status": "error", "error": "No scan results were generated"}]
                
            # Add detailed logging of scan results in the exact format requested
            logger.info(f"Scan results summary:")
            for idx, result in enumerate(results):
                prefix = "  └─ " if idx == len(results) - 1 else "  ├─ "
                server_name = result.get('server', 'Unknown')
                server_type = result.get('type', 'unknown')
                status = result.get('status', 'unknown')
                
                # Add color to status - green for success, red for error
                status_colored = f"\033[32m{status}\033[0m" if status == "success" else f"\033[31m{status}\033[0m"
                
                # Log the summary line at INFO level with bold formatting for server name and type
                logger.info(f"{prefix}\033[1m{server_name}\033[0m (\033[1m{server_type}\033[0m): {status_colored}")
                
                # Log the details at DEBUG level with the exact indentation requested
                if result.get('status') == 'success' and 'result' in result:
                    result_data = result['result']
                    section_id = result_data.get('section', 'N/A')
                    scan_url = result_data.get('scan_url', 'N/A')
                    
                    # Use proper DEBUG level logs with bold formatting
                    logger.debug(f"        └─ Section: \033[1m{section_id}\033[0m")
                    logger.debug(f"        └─ Scan URL: \033[1m{scan_url}\033[0m")
                elif result.get('status') != 'success' and 'error' in result:
                    logger.debug(f"        └─ Error: \033[1m{result['error']}\033[0m")
            
            return results
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Unexpected error in scan_path: \033[1m{error_msg}\033[0m", exc_info=True)
            return [{"status": "error", "error": f"Scan path failed: {error_msg}"}]

    async def _scan_plex(self, server: PlexServer, path: str, library_type: str) -> Dict[str, Any]:
        headers = {
            "X-Plex-Token": server.token,
            "Accept": "application/json"
        }
        
        logger.info(f"Starting Plex scan for server \033[1m{server.name}\033[0m")
        logger.debug(f"  ├─ URL: \033[1m{server.url}\033[0m")
        logger.debug(f"  ├─ Library type: \033[1m{library_type}\033[0m")
        logger.debug(f"  └─ Path: \033[1m{path}\033[0m")
        
        async with aiohttp.ClientSession() as session:
            try:
                # First get all library sections
                sections_url = urljoin(server.url, "library/sections")
                logger.debug(f"Fetching Plex library sections from: \033[1m{sections_url}\033[0m")
                async with session.get(sections_url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to get Plex sections. Status: {response.status}, Response: {error_text}")
                        raise ValueError(f"Failed to get Plex sections: {response.status} - {error_text}")
                    
                    sections = await response.json()
                    section_count = len(sections.get('MediaContainer', {}).get('Directory', []))
                    logger.debug(f"Retrieved \033[1m{section_count}\033[0m library sections from Plex")

                # Find the section containing our path
                section_id = None
                matching_sections = []

                # First pass: collect all sections of the correct type
                for section in sections["MediaContainer"]["Directory"]:
                    section_type = section["type"]
                    section_title = section["title"]
                    
                    if (library_type == "Movies" and section_type == "movie") or \
                       (library_type == "Series" and section_type == "show"):
                        for location in section["Location"]:
                            location_path = location["path"]
                            matching_sections.append((section, location_path))

                if not matching_sections:
                    error_msg = f"No \033[1m{library_type}\033[0m libraries found in Plex"
                    logger.error(error_msg)
                    raise ValueError(error_msg)

                logger.debug(f"Found \033[1m{len(matching_sections)}\033[0m potential matching sections")

                # Second pass: find best matching section
                for idx, (section, location_path) in enumerate(matching_sections):
                    # Normalize both paths for comparison
                    normalized_scan_path = Path(path).as_posix()
                    normalized_location = Path(location_path).as_posix()
                    
                    # Try different path matching strategies
                    if normalized_scan_path.startswith(normalized_location):
                        section_id = section["key"]
                        logger.info(f"Found \033[32mExact match\033[0m in section: {section['title']} (id: {section_id})")
                        break
                    elif normalized_location in normalized_scan_path:
                        section_id = section["key"]
                        logger.info(f"Found \033[33mPartial match\033[0m in section: {section['title']} (id: {section_id})")
                        break

                if not section_id:
                    error_msg = f"No matching library section found for path: \033[1m{path}\033[0m"
                    logger.error(f"{error_msg}. Available {library_type} libraries: {[s[0]['title'] for s in matching_sections]}")
                    raise ValueError(error_msg)

                # Log detailed section information
                for section, location_path in matching_sections:
                    if section["key"] == section_id:
                        logger.debug(f"Selected section details:")
                        logger.debug(f"  ├─ Section ID: \033[1m{section_id}\033[0m")
                        logger.debug(f"  ├─ Section Title: \033[1m{section['title']}\033[0m")
                        logger.debug(f"  ├─ Section Type: \033[1m{section['type']}\033[0m")
                        logger.debug(f"  └─ Section Path: \033[1m{location_path}\033[0m")
                        break

                # Use the exact movie folder path for scanning
                scan_path = path  # This is the exact movie folder path we want to scan
                logger.debug(f"Using exact folder path for scan: \033[1m{scan_path}\033[0m")

                # Properly encode the path parameter
                from urllib.parse import quote
                encoded_path = quote(scan_path)
                
                # Construct the scan URL with the correct format
                scan_url = f"{server.url}/library/sections/{section_id}/refresh?path={encoded_path}"
                logger.debug(f"Scan details:")
                logger.debug(f"  ├─ Scan URL: \033[1m{scan_url}\033[0m")
                logger.debug(f"  ├─ Server URL: \033[1m{server.url}\033[0m")
                logger.debug(f"  ├─ Section ID: \033[1m{section_id}\033[0m")
                logger.debug(f"  └─ Encoded Path: \033[1m{encoded_path}\033[0m")
                
                # Remove duplicate INFO logs
                # logger.info(f"SCAN DETAILS - URL: \033[1m{scan_url}\033[0m")
                # logger.info(f"SCAN DETAILS - Section: {section_id} (Title: {section.get('title', 'Unknown')})")
                
                async with session.get(scan_url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Plex scan failed. Status: {response.status}, Response: {error_text}")
                        raise ValueError(f"Plex scan failed: {response.status} - {error_text}")
                        
                    logger.info(f"Plex scan initiated successfully for section {section_id}")
                    scan_response = await response.text()
                    logger.debug(f"Scan response: {scan_response}")
                    
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
