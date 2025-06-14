import logging
import time
import asyncio
import json
from utils import http_get, http_post, http_put, get_config, parse_time_string, rewrite_path
from typing import Dict, Any, List, Optional
from models import (
    SonarrSeries,
    SonarrEpisode,
    SonarrAddSeriesOptions,
    WebhookPayload,
    SonarrMonitorTypes,
    SonarrInstance,
    Season,
)
from media_server_service import MediaServerScanner
from pathlib import Path

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Sonarr-Specific Functions
# ------------------------------------------------------------------------------
def get_series_by_tvdbid(base_url, api_key, tvdb_id) -> List[Dict[str, Any]]:
    """
    GET /api/v3/series?tvdbId=<tvdb_id>
    Returns an array of series (possibly empty).
    """
    url = f"{base_url}/api/v3/series?tvdbId={tvdb_id}"
    return http_get(url, api_key)


def add_series(
    base_url: str,
    api_key: str,
    tvdb_id: int,
    title: str,
    year: int,
    root_folder_path: str,
    quality_profile_id: int,
    language_profile_id: Optional[int] = None,
    season_folder: bool = True
) -> Dict[str, Any]:
    """Add a new series to Sonarr."""
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/series"
    payload = {
        "tvdbId": tvdb_id,
        "title": title,
        "year": year,
        "qualityProfileId": quality_profile_id,
        "languageProfileId": language_profile_id,
        "seasonFolder": season_folder,
        "rootFolderPath": root_folder_path,
        "monitored": True,
        "addOptions": {"searchForMissingEpisodes": False}
    }
    return http_post(url, api_key, payload)


def update_episodes(
    base_url: str, api_key: str, episodes: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Update episodes in Sonarr using individual PUT requests."""
    if not episodes:
        return []

    updated_episodes = []
    for episode in episodes:
        try:
            # First get the current episode data
            url = f"{base_url}/api/v3/episode/{episode['id']}"
            current_episode = http_get(url, api_key)

            # Update only the monitored status while preserving other fields
            current_episode["monitored"] = True

            # PUT to the individual episode endpoint
            updated = http_put(url, api_key, current_episode)
            updated_episodes.append(updated)

            logger.info(f"Successfully updated episode {episode['id']}")

        except Exception as e:
            logger.error(f"Failed to update episode {episode['id']}: {str(e)}")
            continue

    return updated_episodes


def search_episodes(
    base_url: str, api_key: str, episode_ids: List[int]
) -> Dict[str, Any]:
    """
    Trigger a search for specific episodes in Sonarr.
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/command"
    payload = {"name": "EpisodeSearch", "episodeIds": episode_ids}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered search for episodeIds={episode_ids} in Sonarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger search for episodes: {str(e)}")
        raise


def search_series(base_url: str, api_key: str, series_id: int) -> Dict[str, Any]:
    """
    Trigger a search for a specific series in Sonarr.
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/command"
    payload = {"name": "SeriesSearch", "seriesId": series_id}
    try:
        response = http_post(url, api_key, payload)
        return response
    except Exception as e:
        logger.error(f"Failed to trigger search for seriesId={series_id}: {str(e)}")
        raise


def refresh_series(base_url: str, api_key: str, series_id: int) -> Dict[str, Any]:
    """
    Trigger a refresh for a specific series in Sonarr.
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/command"
    payload = {"name": "RefreshSeries", "seriesId": series_id}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered refresh for seriesId={series_id} in Sonarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger refresh for seriesId={series_id}: {str(e)}")
        raise


def rescan_series(base_url: str, api_key: str, series_id: int) -> Dict[str, Any]:
    """
    Trigger a rescan for a specific series in Sonarr.
    """
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"http://{base_url}"
        logger.debug(f"Added http:// protocol to URL: {base_url}")
        
    url = f"{base_url}/api/v3/command"
    payload = {"name": "RescanSeries", "seriesId": series_id}
    try:
        response = http_post(url, api_key, payload)
        logger.info(f"Triggered rescan for seriesId={series_id} in Sonarr.")
        return response
    except Exception as e:
        logger.error(f"Failed to trigger rescan for seriesId={series_id}: {str(e)}")
        raise


async def handle_sonarr_grab(payload: Dict[str, Any], instances: List[SonarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle episode grab by syncing across instances and scanning media servers"""
    series_data = payload.get("series", {})
    series_id = series_data.get("tvdbId")
    title = series_data.get("title", "Unknown")
    path = series_data.get("path")
    
    results = {
        "status": "ok",
        "event": "Grab",
        "title": title,
        "tvdbId": series_id,
        "results": []
    }
    
    # Log the grab event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Sonarr Grab: Title=\033[1m{title}\033[0m, TVDB=\033[1m{series_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Process each instance
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Check if series exists
            existing_series = await instance.get_series_by_tvdb_id(series_id)
            
            if existing_series:
                logger.debug(f"  ├─ Series already exists (id={existing_series['id']}) on \033[1m{instance.name}\033[0m")
                results["results"].append({
                    "instance": instance.name,
                    "existingSeriesId": existing_series["id"]
                })
            else:
                # Add series to instance
                logger.info(f"  ├─ Adding series to \033[1m{instance.name}\033[0m")
                new_series = add_series(
                    instance.url,
                    instance.api_key,
                    series_id,
                    title,
                    series_data.get("year", 0),
                    instance.root_folder_path,
                    instance.quality_profile_id,
                    instance.language_profile_id,
                    instance.season_folder
                )
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for new series on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_series(instance.url, instance.api_key, new_series["id"])
                    logger.info(f"  ├─ Triggered search for seriesId=\033[1m{new_series['id']}\033[0m on \033[1m{instance.name}\033[0m")
                
                results["results"].append({
                    "instance": instance.name,
                    "newSeriesId": new_series["id"]
                })
                
        except Exception as e:
            logger.error(f"  ├─ Failed to process on \033[1m{instance.name}\033[0m: \033[1m{str(e)}\033[0m")
            results["results"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })
    
    # Log final results
    successful_adds = len([r for r in results["results"] if "newSeriesId" in r])
    existing_series = len([r for r in results["results"] if "existingSeriesId" in r])
    failed_adds = len([r for r in results["results"] if r.get("status") == "error"])
    
    logger.info(f"  └─ Results:")
    if successful_adds > 0:
        logger.info(f"      ├─ Added to \033[1m{successful_adds}\033[0m instance(s)")
    if existing_series > 0:
        logger.info(f"      ├─ Already exists in \033[1m{existing_series}\033[0m instance(s)")
    if failed_adds > 0:
        logger.info(f"      └─ Failed on \033[1m{failed_adds}\033[0m instance(s)")
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    return results


async def handle_sonarr_import(payload: Dict[str, Any], instances: List[SonarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle episode import by syncing across instances and scanning media servers"""
    series_data = payload.get("series", {})
    series_id = series_data.get("tvdbId")
    title = series_data.get("title", "Unknown")
    path = series_data.get("path")
    
    results = {
        "status": "ok",
        "event": "Import",
        "title": title,
        "tvdbId": series_id,
        "results": []
    }
    
    # Log the import event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Sonarr import: Title=\033[1m{title}\033[0m, TVDB=\033[1m{series_id}\033[0m")
    if path:
        logger.info(f"  ├─ Series path: \033[1m{path}\033[0m")
    
    # Process each instance
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Check if series exists
            existing_series = await instance.get_series_by_tvdb_id(series_id)
            
            if existing_series:
                logger.debug(f"  ├─ Series already exists (id={existing_series['id']}) on \033[1m{instance.name}\033[0m")
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for series on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_series(instance.url, instance.api_key, existing_series["id"])
                    logger.info(f"  ├─ Triggered search for seriesId={existing_series['id']} on \033[1m{instance.name}\033[0m")
                
                results["results"].append({
                    "instance": instance.name,
                    "existingSeriesId": existing_series["id"]
                })
            else:
                # Add series to instance
                logger.info(f"  ├─ Adding series to \033[1m{instance.name}\033[0m")
                new_series = add_series(
                    instance.url,
                    instance.api_key,
                    series_id,
                    title,
                    series_data.get("year", 0),
                    instance.root_folder_path,
                    instance.quality_profile_id,
                    instance.language_profile_id,
                    instance.season_folder
                )
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for new series on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_series(instance.url, instance.api_key, new_series["id"])
                    logger.info(f"  ├─ Triggered search for seriesId={new_series['id']} on \033[1m{instance.name}\033[0m")
                
                results["results"].append({
                    "instance": instance.name,
                    "newSeriesId": new_series["id"]
                })
                
        except Exception as e:
            logger.error(f"  ├─ Failed to process on \033[1m{instance.name}\033[0m: \033[1m{str(e)}\033[0m")
            results["results"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })
    
    # Log import results
    successful_imports = len([r for r in results["results"] if "newSeriesId" in r or "existingSeriesId" in r])
    failed_imports = len([r for r in results["results"] if r.get("status") == "error"])
    
    logger.info(f"  ├─ Import results:")
    if successful_imports > 0:
        logger.info(f"  │   ├─ Imported to \033[1m{successful_imports}\033[0m instance(s)")
    if failed_imports > 0:
        logger.info(f"  │   └─ Failed on \033[1m{failed_imports}\033[0m instance(s)")

    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["results"]:
            logger.info(f"  ├─ Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        scan_results = await scanner.scan_path(path, is_series=True)
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


async def handle_sonarr_series_add(payload: Dict[str, Any], instances: List[SonarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle series addition by syncing across instances."""
    series_data = payload.get("series", {})
    series_id = series_data.get("tvdbId")
    title = series_data.get("title", "Unknown")
    path = series_data.get("path")
    
    results = {
        "status": "ok",
        "event": "SeriesAdded",
        "title": title,
        "tvdbId": series_id,
        "adds": []
    }
    
    # Log the series add event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Sonarr SeriesAdded: Title=\033[1m{title}\033[0m, TVDB=\033[1m{series_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync addition across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Check if series exists
            existing_series = await instance.get_series_by_tvdb_id(series_id)
            
            if existing_series:
                logger.debug(f"  ├─ Series already exists (id={existing_series['id']}) on \033[1m{instance.name}\033[0m")
                results["adds"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Series already exists"
                })
            else:
                # Add series to instance
                logger.info(f"  ├─ Adding series to \033[1m{instance.name}\033[0m")
                new_series = add_series(
                    instance.url,
                    instance.api_key,
                    series_id,
                    title,
                    series_data.get("year", 0),
                    instance.root_folder_path,
                    instance.quality_profile_id,
                    instance.language_profile_id,
                    instance.season_folder
                )
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for new series on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_series(instance.url, instance.api_key, new_series["id"])
                    logger.info(f"  ├─ Triggered search for seriesId=\033[1m{new_series['id']}\033[0m on \033[1m{instance.name}\033[0m")
                
                results["adds"].append({
                    "instance": instance.name,
                    "status": "success",
                    "seriesId": new_series["id"]
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


async def handle_sonarr_delete(payload: Dict[str, Any], instances: List[SonarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle series/episode deletion by syncing across instances and scanning media servers"""
    series_data = payload.get("series", {})
    series_id = series_data.get("tvdbId")
    title = series_data.get("title", "Unknown")
    path = series_data.get("path")
    event_type = payload.get("eventType")
    
    results = {
        "status": "ok",
        "event": event_type,
        "title": title,
        "tvdbId": series_id,
        "deletions": [],
        "scanResults": []
    }
    
    # Log the delete event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Sonarr {event_type}: Title=\033[1m{title}\033[0m, TVDB=\033[1m{series_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync deletion across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
                
            if event_type == "SeriesDelete":
                # Delete series from instance
                response = await instance.delete_series(series_id)
                logger.info(f"  ├─ Deleted series from \033[1m{instance.name}\033[0m")
            elif event_type == "EpisodeFileDelete":
                # Delete episode file from instance
                episode_id = payload.get("episodeFile", {}).get("id")
                response = await instance.delete_episode(episode_id)
                logger.info(f"  ├─ Deleted episode file from \033[1m{instance.name}\033[0m")
            
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
        scan_results = await scanner.scan_path(path, is_series=True)
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


async def handle_sonarr_webhook(payload: Dict[str, Any], instances: List[SonarrInstance]) -> Dict[str, Any]:
    """Handle Sonarr webhook with validated data."""
    try:
        # Validate incoming webhook payload
        webhook_data = WebhookPayload(**payload)

        # Map "Download" to "Import" for consistency
        event_type = webhook_data.eventType
        if event_type == "Download":
            event_type = "Import"

        # Get sync timing settings
        config = get_config()
        sync_delay = parse_time_string(config.get("sync_delay", "0"))
        
        if sync_delay > 0:
            logger.info(f"Delaying webhook processing for {sync_delay} seconds")
            await asyncio.sleep(sync_delay)

        # Filter instances that have this event type enabled
        valid_instances = [
            inst for inst in instances 
            if event_type.lower() in [e.lower() for e in inst.enabled_events]
        ]
        
        logger.debug(f"Found {len(valid_instances)} Sonarr instances for event {event_type}")
        
        if not valid_instances:
            logger.info(f"No Sonarr instances configured for event={event_type}")
            return {"status": "ignored", "reason": f"No instances configured for {event_type}"}
        
        if event_type == "Grab":
            return await handle_sonarr_grab(payload, valid_instances, sync_interval, config)
        elif event_type == "Import":
            return await handle_sonarr_import(payload, valid_instances, sync_interval, config)
        elif event_type in ["SeriesDelete", "EpisodeFileDelete"]:
            logger.info(f"Received {event_type} event, syncing deletion and scanning media servers")
            return await handle_sonarr_delete(payload, valid_instances, sync_interval, config)
        elif event_type == "SeriesAdd":
            logger.info(f"Received {event_type} event, syncing series addition across instances")
            return await handle_sonarr_series_add(payload, valid_instances, sync_interval, config)
        else:
            logger.info(f"Unhandled Sonarr event type: {event_type}")
            return {"status": "ignored", "reason": f"Unhandled event type: {event_type}"}

    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return {"status": "error", "error": str(e)}


async def handle_sonarr_rename(payload: Dict[str, Any], instances: List[SonarrInstance], sync_interval: float, config: Dict[str, Any]) -> Dict[str, Any]:
    """Handle series/episode rename by refreshing instances and scanning media servers"""
    series_data = payload.get("series", {})
    series_id = series_data.get("tvdbId")
    title = series_data.get("title", "Unknown")
    path = series_data.get("path")
    
    results = {
        "status": "ok",
        "event": "SeriesRename",
        "title": title,
        "tvdbId": series_id,
        "renames": []
    }
    
    # Log the rename event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Sonarr SeriesRename: Title=\033[1m{title}\033[0m, TVDB=\033[1m{series_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync rename across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Check if series exists
            existing_series = await instance.get_series_by_tvdb_id(series_id)
            
            if existing_series:
                logger.debug(f"  ├─ Series already exists (id={existing_series['id']}) on \033[1m{instance.name}\033[0m")
                results["renames"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Series already exists"
                })
            else:
                # Add series to instance
                logger.info(f"  ├─ Adding series to \033[1m{instance.name}\033[0m")
                new_series = add_series(
                    instance.url,
                    instance.api_key,
                    series_id,
                    title,
                    series_data.get("year", 0),
                    instance.root_folder_path,
                    instance.quality_profile_id,
                    instance.language_profile_id,
                    instance.season_folder
                )
                
                # Trigger search if enabled
                if instance.search_on_sync:
                    logger.info(f"  ├─ Search enabled for new series on \033[1m{instance.name}\033[0m (search_on_sync=True)")
                    search_series(instance.url, instance.api_key, new_series["id"])
                    logger.info(f"  ├─ Triggered search for seriesId=\033[1m{new_series['id']}\033[0m on \033[1m{instance.name}\033[0m")
                
                results["renames"].append({
                    "instance": instance.name,
                    "status": "success",
                    "seriesId": new_series["id"]
                })
                
        except Exception as e:
            logger.error(f"  ├─ Failed to rename to \033[1m{instance.name}\033[0m: \033[1m{str(e)}\033[0m")
            results["renames"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })
    
    # Log final results
    successful_renames = len([r for r in results["renames"] if r["status"] == "success"])
    skipped_renames = len([r for r in results["renames"] if r["status"] == "skipped"])
    failed_renames = len([r for r in results["renames"] if r["status"] == "error"])
    
    logger.info(f"  └─ Results:")
    if successful_renames > 0:
        logger.info(f"      ├─ Renamed to \033[1m{successful_renames}\033[0m instance(s)")
    if skipped_renames > 0:
        logger.info(f"      ├─ Skipped \033[1m{skipped_renames}\033[0m instance(s)")
    if failed_renames > 0:
        logger.info(f"      └─ Failed on \033[1m{failed_renames}\033[0m instance(s)")
    
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["renames"]:
            logger.info(f"  ├─ Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        scan_results = await scanner.scan_path(path, is_series=True)
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
