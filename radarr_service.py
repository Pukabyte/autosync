from typing import Any, Dict, List
from utils import http_get, http_post, get_config, parse_time_string, rewrite_path
from models import RadarrInstance
import logging
import asyncio
from media_server_service import MediaServerScanner
import aiohttp
import json

# Create module logger
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Radarr-Specific Functions
# ------------------------------------------------------------------------------
def get_movie_by_tmdb_id(
    base_url: str, api_key: str, tmdb_id: int
) -> List[Dict[str, Any]]:
    """
    GET /api/v3/movie?tmdbId=<tmdb_id>
    Returns an array of movies (possibly empty).
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/movie?tmdbId={tmdb_id}"
    return http_get(url, api_key)


def add_movie(
    base_url: str,
    api_key: str,
    tmdb_id: int,
    title: str,
    year: int,
    root_folder_path: str,
    quality_profile_id: int,
) -> Dict[str, Any]:
    """
    POST /api/v3/movie to add a new movie. We'll set monitored=true
    but won't automatically search unless we do it separately.
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/movie"
    payload = {
        "tmdbId": tmdb_id,
        "title": title,
        "year": year,
        "qualityProfileId": quality_profile_id,
        "titleSlug": title.replace(" ", "-").lower(),
        "rootFolderPath": root_folder_path,
        "monitored": True,
        "addOptions": {"searchForMovie": False},
    }
    return http_post(url, api_key, payload)


def search_movie(base_url: str, api_key: str, movie_id: int) -> Dict[str, Any]:
    """
    POST /api/v3/command with name=MoviesSearch, movieIds=[<id>]
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/command"
    payload = {"name": "MoviesSearch", "movieIds": [movie_id]}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered search for movieId={movie_id} in Radarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger search for movieId={movie_id}: {str(e)}")
        raise


def refresh_movie(base_url: str, api_key: str, movie_id: int) -> Dict[str, Any]:
    """
    POST /api/v3/command with name=RefreshMovie, movieId=<id>
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/command"
    payload = {"name": "RefreshMovie", "movieId": movie_id}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered refresh for movieId={movie_id} in Radarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger refresh for movieId={movie_id}: {str(e)}")
        raise


def rescan_movie(base_url: str, api_key: str, movie_id: int) -> Dict[str, Any]:
    """
    POST /api/v3/command with name=RescanMovie, movieId=<id>
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/command"
    payload = {"name": "RescanMovie", "movieId": movie_id}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered rescan for movieId={movie_id} in Radarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger rescan for movieId={movie_id}: {str(e)}")
        raise


# ------------------------------------------------------------------------------
# Handle Radarr "On Grab"
# ------------------------------------------------------------------------------
async def handle_radarr_grab(payload: Dict[str, Any], instances: List[RadarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle movie grab by syncing across instances and scanning media servers"""
    movie_data = payload.get("movie", {})
    movie_id = movie_data.get("tmdbId")
    title = movie_data.get("title", "Unknown")
    path = movie_data.get("folderPath") or movie_data.get("path")
    
    results = {
        "status": "ok",
        "event": "Grab",
        "title": title,
        "tmdbId": movie_id,
        "results": []
    }
    
    # Log the grab event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Radarr Grab: Title=\033[1m{title}\033[0m, TMDB=\033[1m{movie_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Process each instance
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Check if movie exists
            existing_movie = await instance.get_movie_by_tmdb_id(movie_id)
            
            if existing_movie:
                logger.debug(f"  ├─ Movie already exists (id={existing_movie['id']}) on \033[1m{instance.name}\033[0m")
                results["results"].append({
                    "instance": instance.name,
                    "existingMovieId": existing_movie["id"]
                })
            else:
                # Add movie to instance
                logger.info(f"  ├─ Adding movie to \033[1m{instance.name}\033[0m")
                new_movie = add_movie(
                    instance.url,
                    instance.api_key,
                    movie_id,
                    title,
                    movie_data.get("year", 0),
                    instance.root_folder_path,
                    instance.quality_profile_id
                )
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for new movie on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_movie(instance.url, instance.api_key, new_movie["id"])
                    logger.info(f"  ├─ Triggered search for movieId=\033[1m{new_movie['id']}\033[0m on \033[1m{instance.name}\033[0m")
                
                results["results"].append({
                    "instance": instance.name,
                    "newMovieId": new_movie["id"]
                })
                
        except Exception as e:
            logger.error(f"  ├─ Failed to process on \033[1m{instance.name}\033[0m: \033[1m{str(e)}\033[0m")
            results["results"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })
    
    # Log final results
    successful_adds = len([r for r in results["results"] if "newMovieId" in r])
    existing_movies = len([r for r in results["results"] if "existingMovieId" in r])
    failed_adds = len([r for r in results["results"] if r.get("status") == "error"])
    
    logger.info(f"  └─ Results:")
    if successful_adds > 0:
        logger.info(f"      ├─ Added to \033[1m{successful_adds}\033[0m instance(s)")
    if existing_movies > 0:
        logger.info(f"      ├─ Already exists in \033[1m{existing_movies}\033[0m instance(s)")
    if failed_adds > 0:
        logger.info(f"      └─ Failed on \033[1m{failed_adds}\033[0m instance(s)")
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    return results


async def handle_radarr_import(payload: Dict[str, Any], instances: List[RadarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle movie import by syncing across instances and scanning media servers"""
    movie_data = payload.get("movie", {})
    movie_id = movie_data.get("tmdbId")
    title = movie_data.get("title", "Unknown")
    path = movie_data.get("folderPath") or movie_data.get("path")
    
    results = {
        "status": "ok",
        "event": "Import",
        "title": title,
        "tmdbId": movie_id,
        "imports": [],
        "scanResults": []
    }
    
    # Log the import event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Radarr import: Title=\033[1m{title}\033[0m, TMDB=\033[1m{movie_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync import across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Apply path rewriting if configured
            rewritten_path = rewrite_path(path, instance.rewrite)
            
            # Get the movie from the instance
            movie = await instance.get_movie_by_tmdb_id(movie_id)
            if movie:
                # Import movie to instance
                response = await instance.import_movie(movie_id, rewritten_path)
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for movie on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_movie(instance.url, instance.api_key, movie_id)
                    logger.info(f"  ├─ Triggered search for movieId=\033[1m{movie_id}\033[0m on \033[1m{instance.name}\033[0m")
                
                results["imports"].append({
                    "instance": instance.name,
                    "status": "success"
                })
            else:
                logger.warning(f"  ├─ Movie not found in \033[1m{instance.name}\033[0m")
                results["imports"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Movie not found"
                })
        except Exception as e:
            logger.error(f"  ├─ Failed to import to \033[1m{instance.name}\033[0m: \033[1m{str(e)}\033[0m")
            results["imports"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })

    # Log import results
    successful_imports = len([i for i in results["imports"] if i["status"] == "success"])
    skipped_imports = len([i for i in results["imports"] if i["status"] == "skipped"])
    failed_imports = len([i for i in results["imports"] if i["status"] == "error"])
    
    logger.info(f"  ├─ Import results:")
    if successful_imports > 0:
        logger.info(f"  │   ├─ Imported to \033[1m{successful_imports}\033[0m instance(s)")
    if skipped_imports > 0:
        logger.info(f"  │   ├─ Skipped \033[1m{skipped_imports}\033[0m instance(s)")
    if failed_imports > 0:
        logger.info(f"  │   └─ Failed on \033[1m{failed_imports}\033[0m instance(s)")

    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["imports"]:
            logger.info(f"  ├─ Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        
        # Get the instance that triggered the import
        source_instance = next((inst for inst in instances if inst.url == payload.get("instanceUrl")), None)
        plex_library_id = source_instance.plex_library_id if source_instance else None
        
        scan_results = await scanner.scan_path(path, is_series=False, plex_library_id=plex_library_id)
        results["scanResults"] = scan_results
        
        # Log scan results
        successful_scans = [s for s in scan_results if s.get("status") == "success"]
        failed_scans = [s for s in scan_results if s.get("status") == "error"]
        
        logger.info(f"  └─ Scan results:")
        if successful_scans:
            for scan in successful_scans[:-1]:
                logger.info(f"      ├─ Scanned \033[1m{scan['server']}\033[0m ({scan['type']})")
            if successful_scans:
                logger.info(f"      └─ Scanned \033[1m{successful_scans[-1]['server']}\033[0m ({successful_scans[-1]['type']})")
        if failed_scans:
            for scan in failed_scans[:-1]:
                logger.info(f"      ├─ Failed on \033[1m{scan['server']}\033[0m: {scan.get('message', 'Unknown error')}")
            if failed_scans:
                logger.info(f"      └─ Failed on \033[1m{failed_scans[-1]['server']}\033[0m: {failed_scans[-1].get('message', 'Unknown error')}")
    else:
        logger.info("  └─ No path provided for media server scanning")
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    return results


async def handle_radarr_movie_add(payload: Dict[str, Any], instances: List[RadarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle movie addition by syncing across instances."""
    movie_data = payload.get("movie", {})
    movie_id = movie_data.get("tmdbId")
    title = movie_data.get("title", "Unknown")
    path = movie_data.get("folderPath") or movie_data.get("path")
    
    results = {
        "status": "ok",
        "event": "MovieAdded",
        "title": title,
        "tmdbId": movie_id,
        "adds": []
    }
    
    # Log the movie add event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Radarr MovieAdded: Title=\033[1m{title}\033[0m, TMDB=\033[1m{movie_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync addition across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Check if movie exists
            existing_movie = await instance.get_movie_by_tmdb_id(movie_id)
            
            if existing_movie:
                logger.debug(f"  ├─ Movie already exists (id={existing_movie['id']}) on \033[1m{instance.name}\033[0m")
                results["adds"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Movie already exists"
                })
            else:
                # Add movie to instance
                logger.info(f"  ├─ Adding movie to \033[1m{instance.name}\033[0m")
                new_movie = add_movie(
                    instance.url,
                    instance.api_key,
                    movie_id,
                    title,
                    movie_data.get("year", 0),
                    instance.root_folder_path,
                    instance.quality_profile_id
                )
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for new movie on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_movie(instance.url, instance.api_key, new_movie["id"])
                    logger.info(f"  ├─ Triggered search for movieId=\033[1m{new_movie['id']}\033[0m on \033[1m{instance.name}\033[0m")
                
                results["adds"].append({
                    "instance": instance.name,
                    "status": "success",
                    "movieId": new_movie["id"]
                })
                
        except Exception as e:
            logger.error(f"  ├─ Failed to add to \033[1m{instance.name}\033[0m: \033[1m{str(e)}\033[0m")
            results["adds"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })
    
    # Log final results
    successful_adds = len([a for a in results["adds"] if a["status"] == "success"])
    skipped_adds = len([a for a in results["adds"] if a["status"] == "skipped"])
    failed_adds = len([a for a in results["adds"] if a["status"] == "error"])
    
    logger.info(f"  └─ Results:")
    if successful_adds > 0:
        logger.info(f"      ├─ Added to \033[1m{successful_adds}\033[0m instance(s)")
    if skipped_adds > 0:
        logger.info(f"      ├─ Skipped \033[1m{skipped_adds}\033[0m instance(s)")
    if failed_adds > 0:
        logger.info(f"      └─ Failed on \033[1m{failed_adds}\033[0m instance(s)")
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    return results


async def handle_radarr_delete(payload: Dict[str, Any], instances: List[RadarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle movie deletion by syncing across instances and scanning media servers"""
    movie_data = payload.get("movie", {})
    movie_id = movie_data.get("tmdbId")
    title = movie_data.get("title", "Unknown")
    path = movie_data.get("folderPath") or movie_data.get("path")
    event_type = payload.get("eventType")
    
    results = {
        "status": "ok",
        "event": event_type,
        "title": title,
        "tmdbId": movie_id,
        "deletions": [],
        "scanResults": []
    }
    
    # Log the delete event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Radarr {event_type}: Title=\033[1m{title}\033[0m, TMDB=\033[1m{movie_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync deletion across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
                
            if event_type == "MovieDelete":
                # Delete movie from instance
                response = await instance.delete_movie(movie_id)
                logger.info(f"  ├─ Deleted movie from \033[1m{instance.name}\033[0m")
            elif event_type == "MovieFileDelete":
                # Delete movie file from instance
                movie_file_id = payload.get("movieFile", {}).get("id")
                response = await instance.delete_movie_file(movie_file_id)
                logger.info(f"  ├─ Deleted movie file from \033[1m{instance.name}\033[0m")
            
            results["deletions"].append({
                "instance": instance.name,
                "status": "success"
            })
        except Exception as e:
            error_msg = str(e)
            if "message" in error_msg:
                try:
                    error_json = json.loads(error_msg)
                    error_msg = error_json.get("message", error_msg)
                except:
                    pass
            logger.error(f"  ├─ Failed to delete from \033[1m{instance.name}\033[0m: \033[1m{error_msg}\033[0m")
            results["deletions"].append({
                "instance": instance.name,
                "status": "error",
                "error": error_msg
            })

    # Log deletion results
    successful_deletes = len([d for d in results["deletions"] if d["status"] == "success"])
    failed_deletes = len([d for d in results["deletions"] if d["status"] == "error"])
    
    logger.info(f"  ├─ Deletion results:")
    if successful_deletes > 0:
        logger.info(f"  │   ├─ Deleted from \033[1m{successful_deletes}\033[0m instance(s)")
    if failed_deletes > 0:
        logger.info(f"  │   └─ Failed on \033[1m{failed_deletes}\033[0m instance(s)")

    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["deletions"]:
            logger.info(f"  ├─ Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        
        # Get the instance that triggered the delete
        source_instance = next((inst for inst in instances if inst.url == payload.get("instanceUrl")), None)
        plex_library_id = source_instance.plex_library_id if source_instance else None
        
        scan_results = await scanner.scan_path(path, is_series=False, plex_library_id=plex_library_id)
        results["scanResults"] = scan_results
        
        # Log scan results
        successful_scans = [s for s in scan_results if s.get("status") == "success"]
        failed_scans = [s for s in scan_results if s.get("status") == "error"]
        
        logger.info(f"  └─ Scan results:")
        if successful_scans:
            for scan in successful_scans[:-1]:
                logger.info(f"      ├─ Scanned \033[1m{scan['server']}\033[0m ({scan['type']})")
            if successful_scans:
                logger.info(f"      └─ Scanned \033[1m{successful_scans[-1]['server']}\033[0m ({successful_scans[-1]['type']})")
        if failed_scans:
            for scan in failed_scans[:-1]:
                logger.info(f"      ├─ Failed on \033[1m{scan['server']}\033[0m: {scan.get('message', 'Unknown error')}")
            if failed_scans:
                logger.info(f"      └─ Failed on \033[1m{failed_scans[-1]['server']}\033[0m: {failed_scans[-1].get('message', 'Unknown error')}")
    else:
        logger.info("  └─ No path provided for media server scanning")
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    return results


async def handle_radarr_rename(payload: Dict[str, Any], instances: List[RadarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle movie rename by refreshing instances and scanning media servers"""
    movie_data = payload.get("movie", {})
    movie_id = movie_data.get("tmdbId")
    title = movie_data.get("title", "Unknown")
    path = movie_data.get("folderPath") or movie_data.get("path")
    
    results = {
        "status": "ok",
        "event": "Rename",
        "title": title,
        "tmdbId": movie_id,
        "renames": [],
        "scanResults": []
    }
    
    # Log the rename event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Radarr Rename: Title=\033[1m{title}\033[0m, TMDB=\033[1m{movie_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync rename across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Get the movie from the instance
            movie = await instance.get_movie_by_tmdb_id(movie_id)
            if movie:
                # Trigger movie refresh to update filenames
                response = await instance.refresh_movie(movie['id'])
                logger.info(f"  ├─ Refreshed movie in \033[1m{instance.name}\033[0m")
                results["renames"].append({
                    "instance": instance.name,
                    "status": "success"
                })
            else:
                logger.warning(f"  ├─ Movie not found in \033[1m{instance.name}\033[0m")
                results["renames"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Movie not found"
                })
        except Exception as e:
            error_msg = str(e)
            if "message" in error_msg:
                try:
                    error_json = json.loads(error_msg)
                    error_msg = error_json.get("message", error_msg)
                except:
                    pass
            logger.error(f"  ├─ Failed to rename in \033[1m{instance.name}\033[0m: \033[1m{error_msg}\033[0m")
            results["renames"].append({
                "instance": instance.name,
                "status": "error",
                "error": error_msg
            })

    # Log rename results
    successful_renames = len([r for r in results["renames"] if r["status"] == "success"])
    skipped_renames = len([r for r in results["renames"] if r["status"] == "skipped"])
    failed_renames = len([r for r in results["renames"] if r["status"] == "error"])
    
    logger.info(f"  ├─ Rename results:")
    if successful_renames > 0:
        logger.info(f"  │   ├─ Refreshed in \033[1m{successful_renames}\033[0m instance(s)")
    if skipped_renames > 0:
        logger.info(f"  │   ├─ Skipped \033[1m{skipped_renames}\033[0m instance(s)")
    if failed_renames > 0:
        logger.info(f"  │   └─ Failed on \033[1m{failed_renames}\033[0m instance(s)")

    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["renames"]:
            logger.info(f"  ├─ Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        
        # Get the instance that triggered the rename
        source_instance = next((inst for inst in instances if inst.url == payload.get("instanceUrl")), None)
        plex_library_id = source_instance.plex_library_id if source_instance else None
        
        scan_results = await scanner.scan_path(path, is_series=False, plex_library_id=plex_library_id)
        results["scanResults"] = scan_results
        
        # Log scan results
        successful_scans = [s for s in scan_results if s.get("status") == "success"]
        failed_scans = [s for s in scan_results if s.get("status") == "error"]
        
        logger.info(f"  └─ Scan results:")
        if successful_scans:
            for scan in successful_scans[:-1]:
                logger.info(f"      ├─ Scanned \033[1m{scan['server']}\033[0m ({scan['type']})")
            if successful_scans:
                logger.info(f"      └─ Scanned \033[1m{successful_scans[-1]['server']}\033[0m ({successful_scans[-1]['type']})")
        if failed_scans:
            for scan in failed_scans[:-1]:
                logger.info(f"      ├─ Failed on \033[1m{scan['server']}\033[0m: {scan.get('message', 'Unknown error')}")
            if failed_scans:
                logger.info(f"      └─ Failed on \033[1m{failed_scans[-1]['server']}\033[0m: {failed_scans[-1].get('message', 'Unknown error')}")
    else:
        logger.info("  └─ No path provided for media server scanning")
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    return results
