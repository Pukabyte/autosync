import uvicorn
import yaml
import requests
import logging
import json
import asyncio
import re
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from models import (
    SonarrWebhook,
    RadarrWebhook,
    SonarrInstance,
    RadarrInstance,
    PlexServer,
    JellyfinServer,
    EmbyServer,
)
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
from radarr_service import handle_radarr_grab, handle_radarr_import
from sonarr_service import handle_sonarr_grab, handle_sonarr_import
from utils import load_config, get_config, save_config, parse_time_string
from media_server_service import MediaServerScanner
import random
import string
from pathlib import Path

# Application version - update this when creating new releases
VERSION = "0.0.13"

# Create a logger for this module
logger = logging.getLogger(__name__)
# Remove this initial logging configuration
# config = load_config()
# log_level = config.get("log_level", "INFO")
# logging.basicConfig(level=getattr(logging, log_level.upper()))

# Store instances at module level with proper typing
sonarr_instances: List[SonarrInstance] = []
radarr_instances: List[RadarrInstance] = []

# TODO: Add anime support


# ------------------------------------------------------------------------------
# Load YAML Config and Setup Logging
# ------------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        global sonarr_instances, radarr_instances  # Add this line to use global variables
        
        config = load_config()
        
        # Setup logging based on config
        log_level = getattr(logging, config.get('log_level', 'INFO').upper())
        
        # Configure root logger with improved format
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Use more descriptive level names instead of abbreviations
        logging.addLevelName(logging.INFO, '\033[32mINFO\033[0m')     # Green
        logging.addLevelName(logging.ERROR, '\033[31mERROR\033[0m')   # Red
        logging.addLevelName(logging.WARNING, '\033[33mWARN\033[0m')  # Yellow
        logging.addLevelName(logging.DEBUG, '\033[36mDEBUG\033[0m')   # Cyan
        
        # Ensure all module loggers have the correct level
        for logger_name in ['media_server_service', 'radarr_service', 'sonarr_service']:
            module_logger = logging.getLogger(logger_name)
            module_logger.setLevel(log_level)
            # Ensure the logger has a handler
            if not module_logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
                module_logger.addHandler(handler)
        
        # Build startup messages
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("\033[1mAutosync\033[0m starting up")
        logger.info(f"  ├─ Version: \033[1m{VERSION}\033[0m")
        logger.info(f"  ├─ Server port: \033[1m3536\033[0m")
        logger.info(f"  └─ Log level: \033[1m{config.get('log_level', 'INFO').lower()}\033[0m")
        
        # Convert dict instances to proper types and assign to global variables
        sonarr_instances = [
            SonarrInstance(**inst)
            for inst in config["instances"]
            if inst["type"].lower() == "sonarr"
        ]
        radarr_instances = [
            RadarrInstance(**inst)
            for inst in config["instances"]
            if inst["type"].lower() == "radarr"
        ]
        
        # Get media servers
        media_servers = config.get("media_servers", [])
        # Group servers by type
        server_types = {}
        for server in media_servers:
            if server.get("enabled", True):
                server_type = server["type"].capitalize()
                if server_type not in server_types:
                    server_types[server_type] = 0
                server_types[server_type] += 1
        
        # Build initialization message for instances
        logger.info("Instances configuration:")
        logger.info(f"  ├─ Sonarr: \033[1m{len(sonarr_instances)}\033[0m instance(s)")
        logger.info(f"  └─ Radarr: \033[1m{len(radarr_instances)}\033[0m instance(s)")
        
        # Build initialization message for media servers
        logger.info("Media servers configuration:")
        media_server_types = []
        for server_type, count in server_types.items():
            media_server_types.append(f"{server_type}: \033[1m{count}\033[0m")
        
        if media_server_types:
            for i, server_info in enumerate(media_server_types):
                prefix = "  └─ " if i == len(media_server_types) - 1 else "  ├─ "
                logger.info(f"{prefix}{server_info}")
        else:
            logger.info("  └─ No media servers configured")
        
        # Log version information
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    except Exception as e:
        logger.error(f"Failed to initialize server error=\"{str(e)}\"")
        raise
    yield


app = FastAPI(lifespan=lifespan)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")

def get_template_context(request: Request, **kwargs) -> Dict[str, Any]:
    """Create a template context with common variables."""
    context = {"request": request, "version": VERSION}
    context.update(kwargs)
    return context

# ------------------------------------------------------------------------------
# Frontend Routes
# ------------------------------------------------------------------------------

@app.get("/")
async def index(request: Request):
    """Render the dashboard page."""
    config = get_config()
    
    # Get instances
    sonarr_instances = [
        inst for inst in config.get("instances", [])
        if inst.get("type", "").lower() == "sonarr"
    ]
    
    radarr_instances = [
        inst for inst in config.get("instances", [])
        if inst.get("type", "").lower() == "radarr"
    ]
    
    # Get media servers
    media_servers = config.get("media_servers", [])
    
    return templates.TemplateResponse(
        "dashboard.html",
        get_template_context(
            request, 
            sonarr_instances=sonarr_instances, 
            radarr_instances=radarr_instances, 
            media_servers=media_servers, 
            config=config,
            messages=[]
        )
    )

@app.get("/instances/add")
async def add_instance_form(request: Request, type: str = "sonarr"):
    """Render the add instance form."""
    if type.lower() not in ["sonarr", "radarr"]:
        type = "sonarr"  # Default to sonarr if invalid type
    
    config = get_config()
        
    return templates.TemplateResponse(
        "add_instance.html",
        get_template_context(request, instance_type=type.lower(), config=config, messages=[])
    )

@app.post("/instances/add")
async def add_instance(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    url: str = Form(...),
    api_key: str = Form(...),
    root_folder_path: str = Form(...),
    quality_profile_id: int = Form(...),
    language_profile_id: Optional[int] = Form(None),
    season_folder: Optional[bool] = Form(False),
    search_on_sync: Optional[bool] = Form(False),
    enabled_events: List[str] = Form([])
):
    """Add a new instance to the configuration."""
    global sonarr_instances, radarr_instances
    config = get_config()
    
    # Create instance data
    instance_data = {
        "name": name,
        "type": type,
        "url": url,
        "api_key": api_key,
        "root_folder_path": root_folder_path,
        "quality_profile_id": quality_profile_id,
        "search_on_sync": search_on_sync,
        "enabled_events": enabled_events
    }
    
    # Add Sonarr-specific fields
    if type.lower() == "sonarr":
        instance_data["language_profile_id"] = language_profile_id or 1
        instance_data["season_folder"] = season_folder
    
    # Check if instance with same name and type already exists
    for idx, inst in enumerate(config.get("instances", [])):
        if inst.get("name") == name and inst.get("type") == type:
            # Replace existing instance
            config["instances"][idx] = instance_data
            save_config(config)
            
            # Reload instances
            sonarr_instances = [
                SonarrInstance(**inst)
                for inst in config["instances"]
                if inst["type"].lower() == "sonarr"
            ]
            radarr_instances = [
                RadarrInstance(**inst)
                for inst in config["instances"]
                if inst["type"].lower() == "radarr"
            ]
            
            return RedirectResponse(url="/", status_code=303)
    
    # Add new instance
    if "instances" not in config:
        config["instances"] = []
    
    config["instances"].append(instance_data)
    save_config(config)
    
    # Reload instances
    sonarr_instances = [
        SonarrInstance(**inst)
        for inst in config["instances"]
        if inst["type"].lower() == "sonarr"
    ]
    radarr_instances = [
        RadarrInstance(**inst)
        for inst in config["instances"]
        if inst["type"].lower() == "radarr"
    ]
    
    return RedirectResponse(url="/", status_code=303)

@app.get("/instances/delete/{name}/{type}")
async def delete_instance(request: Request, name: str, type: str):
    """Delete an instance from the configuration."""
    global sonarr_instances, radarr_instances
    config = get_config()
    
    # Find and remove the instance
    config["instances"] = [
        inst for inst in config.get("instances", [])
        if not (inst.get("name") == name and inst.get("type").lower() == type.lower())
    ]
    
    save_config(config)
    
    # Reload instances
    sonarr_instances = [
        SonarrInstance(**inst)
        for inst in config["instances"]
        if inst["type"].lower() == "sonarr"
    ]
    radarr_instances = [
        RadarrInstance(**inst)
        for inst in config["instances"]
        if inst["type"].lower() == "radarr"
    ]
    
    return RedirectResponse(url="/", status_code=303)

@app.get("/media-servers/add")
async def add_media_server_form(request: Request):
    """Render the add media server form."""
    config = get_config()
    
    return templates.TemplateResponse(
        "add_media_server.html",
        get_template_context(request, config=config, messages=[])
    )

@app.post("/media-servers/add")
async def add_media_server(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    url: str = Form(...),
    token: Optional[str] = Form(None),
    api_key: Optional[str] = Form(None),
    enabled: Optional[bool] = Form(True)
):
    """Add a new media server to the configuration."""
    config = get_config()
    
    # Create media server data
    server_data = {
        "name": name,
        "type": type,
        "url": url,
        "enabled": enabled
    }
    
    # Add type-specific fields
    if type.lower() == "plex":
        if not token:
            return templates.TemplateResponse(
                "add_media_server.html",
                get_template_context(request, messages=[{"type": "danger", "text": "Plex token is required"}])
            )
        server_data["token"] = token
    else:
        if not api_key:
            return templates.TemplateResponse(
                "add_media_server.html",
                get_template_context(request, messages=[{"type": "danger", "text": "API key is required"}])
            )
        server_data["api_key"] = api_key
    
    # Check if server with same name already exists
    if "media_servers" not in config:
        config["media_servers"] = []
        
    for idx, server in enumerate(config.get("media_servers", [])):
        if server.get("name") == name:
            # Replace existing server
            config["media_servers"][idx] = server_data
            save_config(config)
            return RedirectResponse(url="/", status_code=303)
    
    # Add new server
    config["media_servers"].append(server_data)
    save_config(config)
    
    return RedirectResponse(url="/", status_code=303)

@app.get("/media-servers/delete/{name}")
async def delete_media_server(request: Request, name: str):
    """Delete a media server from the configuration."""
    config = get_config()
    
    # Find and remove the server
    config["media_servers"] = [
        server for server in config.get("media_servers", [])
        if server.get("name") != name
    ]
    
    save_config(config)
    
    return RedirectResponse(url="/", status_code=303)

@app.get("/instances/edit/{name}/{type}")
async def edit_instance_form(request: Request, name: str, type: str):
    """Render the edit instance form."""
    config = get_config()
    
    # Find the instance
    instance = None
    for inst in config["instances"]:
        if inst["name"] == name and inst["type"].lower() == type.lower():
            instance = inst
            break
    
    if not instance:
        return RedirectResponse(url="/", status_code=303)
    
    return templates.TemplateResponse(
        "edit_instance.html",
        get_template_context(request, instance=instance, config=config, messages=[])
    )

@app.post("/instances/edit/{name}/{type}")
async def edit_instance(
    request: Request,
    name: str,
    type: str,
    url: str = Form(...),
    api_key: str = Form(...),
    root_folder_path: str = Form(...),
    quality_profile_id: int = Form(...),
    language_profile_id: Optional[int] = Form(None),
    season_folder: Optional[bool] = Form(False),
    search_on_sync: Optional[bool] = Form(False),
    enabled_events: List[str] = Form([]),
    rewrite_from: Optional[List[str]] = Form([]),
    rewrite_to: Optional[List[str]] = Form([])
):
    """Update an existing instance in the configuration."""
    global sonarr_instances, radarr_instances
    config = get_config()
    
    # Create updated instance data
    instance_data = {
        "name": name,
        "type": type,
        "url": url,
        "api_key": api_key,
        "root_folder_path": root_folder_path,
        "quality_profile_id": quality_profile_id,
        "search_on_sync": search_on_sync,
        "enabled_events": enabled_events
    }
    
    # Add Sonarr-specific fields
    if type.lower() == "sonarr":
        instance_data["language_profile_id"] = language_profile_id or 1
        instance_data["season_folder"] = season_folder
    
    # Add rewrite rules if any
    if rewrite_from and rewrite_to:
        instance_data["rewrite"] = [
            {"from_path": f, "to_path": t}
            for f, t in zip(rewrite_from, rewrite_to)
            if f and t  # Only add rules where both from and to are provided
        ]
    
    # Find and update the instance
    for idx, inst in enumerate(config.get("instances", [])):
        if inst.get("name") == name and inst.get("type").lower() == type.lower():
            config["instances"][idx] = instance_data
            save_config(config)
            
            # Reload instances
            sonarr_instances = [
                SonarrInstance(**inst)
                for inst in config["instances"]
                if inst["type"].lower() == "sonarr"
            ]
            radarr_instances = [
                RadarrInstance(**inst)
                for inst in config["instances"]
                if inst["type"].lower() == "radarr"
            ]
            
            break
    
    return RedirectResponse(url="/", status_code=303)

@app.get("/media-servers/edit/{name}")
async def edit_media_server_form(request: Request, name: str):
    """Render the edit media server form."""
    config = get_config()
    
    # Find the server
    server = None
    for srv in config["media_servers"]:
        if srv["name"] == name:
            server = srv
            break
    
    if not server:
        return RedirectResponse(url="/", status_code=303)
    
    return templates.TemplateResponse(
        "edit_media_server.html",
        get_template_context(request, server=server, config=config, messages=[])
    )

@app.post("/media-servers/edit/{name}")
async def edit_media_server(
    request: Request,
    name: str,
    type: str = Form(...),
    url: str = Form(...),
    token: Optional[str] = Form(None),
    api_key: Optional[str] = Form(None),
    enabled: Optional[bool] = Form(True),
    rewrite_from: Optional[List[str]] = Form([]),
    rewrite_to: Optional[List[str]] = Form([])
):
    """Update an existing media server in the configuration."""
    config = get_config()
    
    # Create updated server data
    server_data = {
        "name": name,
        "type": type,
        "url": url,
        "enabled": enabled
    }
    
    # Add type-specific fields
    if type.lower() == "plex":
        if not token:
            return templates.TemplateResponse(
                "edit_media_server.html",
                get_template_context(request, server=server_data, messages=[{"type": "danger", "text": "Plex token is required"}])
            )
        server_data["token"] = token
    else:
        if not api_key:
            return templates.TemplateResponse(
                "edit_media_server.html",
                get_template_context(request, server=server_data, messages=[{"type": "danger", "text": "API key is required"}])
            )
        server_data["api_key"] = api_key
    
    # Add rewrite rules if any
    if rewrite_from and rewrite_to:
        server_data["rewrite"] = [
            {"from_path": f, "to_path": t}
            for f, t in zip(rewrite_from, rewrite_to)
            if f and t  # Only add rules where both from and to are provided
        ]
    
    # Find and update the server
    for idx, server in enumerate(config.get("media_servers", [])):
        if server.get("name") == name:
            config["media_servers"][idx] = server_data
            save_config(config)
            break
    
    return RedirectResponse(url="/", status_code=303)

@app.get("/settings")
async def settings_form(request: Request):
    """Render the settings form."""
    config = get_config()
    
    return templates.TemplateResponse(
        "settings.html",
        get_template_context(request, config=config, messages=[])
    )

@app.post("/settings")
async def update_settings(
    request: Request,
    log_level: str = Form(...),
    sync_delay: str = Form(...),
    sync_interval: str = Form(...)
):
    """Update application settings."""
    config = get_config()
    
    # Update settings
    config["log_level"] = log_level
    config["sync_delay"] = sync_delay
    config["sync_interval"] = sync_interval
    
    # Validate time formats
    try:
        parse_time_string(sync_delay)
        parse_time_string(sync_interval)
    except Exception as e:
        return templates.TemplateResponse(
            "settings.html",
            get_template_context(
                request, 
                config=config, 
                messages=[{"type": "danger", "text": f"Invalid time format: {str(e)}"}]
            ),
            status_code=400
        )
    
    # Save config
    if save_config(config):
        # Update logging level
        logging.getLogger().setLevel(getattr(logging, log_level.upper()))
        
        # Redirect to dashboard with success message
        return RedirectResponse(
            url="/",
            status_code=303,
            headers={"HX-Trigger": json.dumps({"showMessage": {"type": "success", "text": "Settings updated successfully"}})}
        )
    else:
        # Show error
        return templates.TemplateResponse(
            "settings.html",
            get_template_context(
                request, 
                config=config, 
                messages=[{"type": "danger", "text": "Failed to save settings"}]
            ),
            status_code=500
        )

@app.get("/manual-scan")
async def manual_scan_form(request: Request):
    """Render the manual scan page."""
    config = get_config()
    return templates.TemplateResponse(
        "manual_scan.html",
        get_template_context(request, config=config, messages=[])
    )

# ------------------------------------------------------------------------------
# API Routes
# ------------------------------------------------------------------------------

@app.post("/debug-webhook")
async def debug_webhook(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    Debug endpoint that simply logs and returns the received webhook payload.
    """
    logger.info("========================")
    logger.info("Received webhook on debug endpoint")
    logger.info("Headers:")
    for name, value in request.headers.items():
        logger.info(f"{name}: {value}")
    logger.info("Payload:")
    logger.info(json.dumps(payload, indent=2))
    logger.info("========================")
    return {
        "status": "received",
        "eventType": payload.get("eventType", "unknown"),
        "payload": payload,
    }

async def handle_sonarr_delete(payload: Dict[str, Any], instances: List[SonarrInstance]):
    """Handle series or episode deletion by syncing across instances and scanning media servers"""
    series_id = payload.get("series", {}).get("tvdbId")
    path = payload.get("series", {}).get("path")
    event_type = payload.get("eventType")
    
    results = {
        "status": "ok",
        "event": event_type,
        "deletions": [],
        "scanResults": []
    }
    
    # Get sync interval from config
    config = get_config()
    sync_interval = parse_time_string(config.get("sync_interval", "0"))
    
    # Sync deletion across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
                
            if event_type == "SeriesDelete":
                # Delete series from instance
                response = await instance.delete_series(series_id)
            elif event_type == "EpisodeFileDelete":
                # Delete episode file from instance
                episode_id = payload.get("episodeFile", {}).get("id")
                response = await instance.delete_episode(episode_id)
            
            results["deletions"].append({
                "instance": instance.name,
                "status": "success"
            })
        except Exception as e:
            logger.error(f"Failed to delete from {instance.name}: {str(e)}")
            results["deletions"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })

    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["deletions"]:
            logger.info(f"Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        results["scanResults"] = await scanner.scan_path(path, is_series=True)
    
    return results

async def handle_radarr_delete(payload: Dict[str, Any], instances: List[RadarrInstance]):
    """Handle movie or movie file deletion by syncing across instances and scanning media servers"""
    tmdb_id = payload.get("movie", {}).get("tmdbId")
    path = payload.get("movie", {}).get("folderPath")
    event_type = payload.get("eventType")
    
    results = {
        "status": "ok",
        "event": event_type,
        "deletions": [],
        "scanResults": []
    }
    
    # Get sync interval from config
    config = get_config()
    sync_interval = parse_time_string(config.get("sync_interval", "0"))
    
    # Sync deletion across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
                
            if event_type == "MovieDelete":
                # Delete movie from instance
                response = await instance.delete_movie(tmdb_id)
            elif event_type == "MovieFileDelete":
                # Delete movie file from instance
                movie_file_id = payload.get("movieFile", {}).get("id")
                response = await instance.delete_movie_file(movie_file_id)
            
            results["deletions"].append({
                "instance": instance.name,
                "status": "success"
            })
        except Exception as e:
            logger.error(f"Failed to delete from {instance.name}: {str(e)}")
            results["deletions"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })

    # Scan media servers if path exists
    if path:
        # Apply sync interval before media server scanning
        if sync_interval > 0 and results["deletions"]:
            logger.info(f"Waiting {sync_interval} seconds before scanning media servers")
            await asyncio.sleep(sync_interval)
            
        scanner = MediaServerScanner(config.get("media_servers", []))
        results["scanResults"] = await scanner.scan_path(path, is_series=False)
    
    return results

@app.post("/webhook")
async def webhook_handler(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    Handle webhooks from Sonarr and Radarr with proper validation.
    """
    try:
        event_type = payload.get("eventType")
        if not event_type:
            raise ValueError("Webhook payload missing eventType")

        # Handle manual scan requests
        if event_type == "ManualScan":
            try:
                path = payload.get("path")
                content_type = payload.get("contentType")
                
                if not path or not content_type:
                    raise ValueError("Manual scan requires path and contentType")
                
                logger.info(f"Manual scan requested for path: \033[1m{path}\033[0m")
                logger.info(f"Content type: \033[1m{content_type}\033[0m")
                
                config = get_config()
                media_servers = config.get("media_servers", [])
                
                if not media_servers:
                    logger.error("No media servers configured")
                    raise HTTPException(status_code=400, detail="No media servers configured")
                
                active_servers = [s for s in media_servers if s.get("enabled", False)]
                if not active_servers:
                    logger.error("No active media servers found")
                    raise HTTPException(status_code=400, detail="No active media servers found")
                    
                logger.info(f"Found \033[1m{len(active_servers)}\033[0m active media server(s)")
                
                # Validate content type
                if content_type not in ["series", "movie"]:
                    logger.error(f"Invalid content type: {content_type}")
                    raise HTTPException(status_code=400, detail="Content type must be either 'series' or 'movie'")
                
                # Initialize scanner and perform scan
                scanner = MediaServerScanner(media_servers)
                scan_results = await scanner.scan_path(path, is_series=(content_type == "series"))
                
                # Check if any scans were successful
                successful_scans = [r for r in scan_results if r.get("status") == "success"]
                if not successful_scans:
                    logger.warning("No successful scans completed")
                    return {
                        "status": "warning",
                        "message": "No successful scans completed",
                        "path": path,
                        "content_type": content_type,
                        "scan_results": scan_results
                    }
                
                return {
                    "status": "ok",
                    "message": f"Successfully scanned {len(successful_scans)} media server(s)",
                    "path": path,
                    "content_type": content_type,
                    "scan_results": scan_results
                }
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Manual scan failed: {str(e)}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        # Get config for event validation
        config = get_config()

        # Generate a unique ID for this webhook
        webhook_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))

        # Get sync timing settings
        sync_delay = parse_time_string(config.get("sync_delay", "0"))
        sync_interval = parse_time_string(config.get("sync_interval", "0"))
        
        if sync_delay > 0:
            logger.info(f"Delaying webhook processing for {sync_delay} seconds")
            await asyncio.sleep(sync_delay)

        # Try to parse as Sonarr webhook first
        if "series" in payload:
            # Validate event type
            if event_type not in config.get("webhook_events", {}).get("sonarr", []):
                logger.info(f"Ignoring unsupported Sonarr event={event_type}")
                return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}

            # Map "Download" to "Import" for consistency
            if event_type == "Download":
                event_type = "Import"
                
            # Log webhook receipt
            path = payload.get("series", {}).get("path", "")
            logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"Processing Sonarr webhook: \033[1m{event_type}\033[0m (ID: {webhook_id})")
            logger.info(f"  └─ Series path: \033[1m{path}\033[0m")

            webhook_data = SonarrWebhook(**payload)
            
            # Filter instances that have this event type enabled
            valid_instances = [
                inst for inst in sonarr_instances 
                if event_type.lower() in [e.lower() for e in inst.enabled_events]
            ]
            
            logger.debug(f"Found {len(valid_instances)} Sonarr instances for event {event_type}")
            
            if not valid_instances:
                logger.info(f"No Sonarr instances configured for event={event_type}")
                
                # Even if no instances are configured, we should still scan media servers for Import events
                if event_type == "Import":
                    # Get config for sync timing
                    config = get_config()
                    sync_interval = parse_time_string(config.get("sync_interval", "0"))
                    
                    # Get paths from payload for scanning
                    series_data = payload.get("series", {})
                    episode_file = payload.get("episodeFile", {})
                    series_path = series_data.get("path", "")
                    file_path = episode_file.get("path", "")
                    
                    # Initialize scanner with media servers from config
                    media_servers = config.get("media_servers", [])
                    logger.debug(f"Found {len(media_servers)} media server(s) to scan")
                    
                    # Apply sync interval before media server scanning
                    if sync_interval > 0:
                        logger.info(f"Waiting {sync_interval} seconds before scanning media servers")
                        await asyncio.sleep(sync_interval)
                    
                    scanner = MediaServerScanner(media_servers)
                    
                    # Try to scan using the most specific path available
                    scan_path = None
                    if series_path:  # Use series path for better Plex library scanning
                        scan_path = series_path
                        logger.debug(f"Using series path for scanning: {scan_path}")
                    elif file_path:  # Fallback to file path if series path not available
                        scan_path = file_path
                        logger.debug(f"Using episode file path for scanning: {scan_path}")
                    
                    scan_results = []
                    if scan_path:
                        logger.info(f"Initiating scan for path: \033[1m{scan_path}\033[0m")
                        scan_results = await scanner.scan_path(scan_path, is_series=True)
                        
                        return {
                            "status": "ok",
                            "message": "No Sonarr instances configured, but media servers were scanned",
                            "scanResults": scan_results,
                            "scannedPath": scan_path
                        }
                
                return {"status": "ignored", "reason": f"No instances configured for {event_type}"}
            
            if event_type == "Grab":
                return await handle_sonarr_grab(payload, valid_instances)
            elif event_type == "Import":
                # Add sync interval to the import handler context
                result = await handle_sonarr_import(payload, valid_instances)
                logger.info(f"Import result: {result}")
                return result
            elif event_type in ["SeriesDelete", "EpisodeFileDelete"]:
                logger.info(f"Received {event_type} event, syncing deletion and scanning media servers")
                return await handle_sonarr_delete(payload, valid_instances)
            else:
                logger.info(f"Unhandled Sonarr event type: {event_type}")
                return {"status": "ignored", "reason": f"Unhandled event type: {event_type}"}

        # Try to parse as Radarr webhook
        elif "movie" in payload:
            # Validate event type
            if event_type not in config.get("webhook_events", {}).get("radarr", []):
                logger.info(f"Ignoring unsupported Radarr event={event_type}")
                return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}

            # Map "Download" to "Import" for consistency
            if event_type == "Download":
                event_type = "Import"
                
            # Get paths from payload for logging
            movie_data = payload.get("movie", {})
            movie_file = payload.get("movieFile", {})
            folder_path = movie_data.get("folderPath", "")
            file_path = movie_file.get("path", "")
            
            logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            logger.info(f"Processing Radarr webhook: \033[1m{event_type}\033[0m (ID: {webhook_id})")
            logger.info(f"  ├─ Movie: \033[1m{movie_data.get('title', 'Unknown')}\033[0m")
            logger.info(f"  ├─ Folder path: {folder_path}")
            logger.info(f"  └─ File path: {file_path}")

            webhook_data = RadarrWebhook(**payload)
            
            # Filter instances that have this event type enabled
            valid_instances = [
                inst for inst in radarr_instances 
                if event_type.lower() in [e.lower() for e in inst.enabled_events]
            ]
            
            logger.debug(f"Found {len(valid_instances)} Radarr instances for event {event_type}")
            
            if not valid_instances:
                logger.info(f"No Radarr instances configured for event={event_type}")
                
                # Even if no instances are configured, we should still scan media servers for Import events
                if event_type == "Import":
                    # Get config for sync timing
                    config = get_config()
                    sync_interval = parse_time_string(config.get("sync_interval", "0"))
                    
                    # Get paths from payload for scanning
                    movie_data = payload.get("movie", {})
                    movie_file = payload.get("movieFile", {})
                    folder_path = movie_data.get("folderPath", "")
                    file_path = movie_file.get("path", "")
                    
                    # Initialize scanner with media servers from config
                    media_servers = config.get("media_servers", [])
                    logger.debug(f"Found {len(media_servers)} media server(s) to scan")
                    
                    # Apply sync interval before media server scanning
                    if sync_interval > 0:
                        logger.info(f"Waiting {sync_interval} seconds before scanning media servers")
                        await asyncio.sleep(sync_interval)
                    
                    scanner = MediaServerScanner(media_servers)
                    
                    # Try to scan using the most specific path available
                    scan_path = None
                    if folder_path:  # Use folder path for better Plex library scanning
                        scan_path = folder_path
                        logger.debug(f"Using movie folder path for scanning: {scan_path}")
                    elif file_path:  # Fallback to file path if folder path not available
                        scan_path = file_path
                        logger.debug(f"Using movie file path for scanning: {scan_path}")
                    
                    scan_results = []
                    if scan_path:
                        logger.info(f"Initiating scan for path: \033[1m{scan_path}\033[0m")
                        scan_results = await scanner.scan_path(scan_path, is_series=False)
                        
                        return {
                            "status": "ok",
                            "message": "No Radarr instances configured, but media servers were scanned",
                            "scanResults": scan_results,
                            "scannedPath": scan_path
                        }
                
                return {"status": "ignored", "reason": f"No instances configured for {event_type}"}
            
            if event_type == "Grab":
                return await handle_radarr_grab(payload, valid_instances)
            elif event_type == "Import":
                result = await handle_radarr_import(payload, valid_instances)
                logger.info(f"Import result: {result}")
                return result
            elif event_type in ["MovieDelete", "MovieFileDelete"]:
                logger.info(f"Received {event_type} event, syncing deletion and scanning media servers")
                return await handle_radarr_delete(payload, valid_instances)
            else:
                logger.info(f"Unhandled Radarr event type: {event_type}")
                return {"status": "ignored", "reason": f"Unhandled event type: {event_type}"}

        else:
            logger.warning("Unknown webhook type")
            raise ValueError("Webhook must contain either 'series' or 'movie' data")

    except ValueError as e:
        logger.warning(f"Invalid webhook format: {str(e)}")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "reason": f"Invalid webhook format: {str(e)}"},
        )

    except Exception as e:
        logger.error(f"Failed to process webhook: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "reason": f"Internal server error: {str(e)}"},
        )

async def handle_sonarr_rename(payload: Dict[str, Any], instances: List[SonarrInstance]):
    """Handle series rename by syncing across instances and scanning media servers"""
    series_id = payload.get("series", {}).get("tvdbId")
    path = payload.get("series", {}).get("path")
    
    results = {
        "status": "ok",
        "event": "Rename",
        "renames": [],
        "scanResults": []
    }
    
    # Sync rename across instances
    for instance in instances:
        try:
            # Get the series from the instance
            series = await instance.get_series_by_tvdb_id(series_id)
            if series:
                # Trigger series refresh to update filenames
                response = await instance.refresh_series(series['id'])
                results["renames"].append({
                    "instance": instance.name,
                    "status": "success"
                })
            else:
                logger.warning(f"Series not found in {instance.name}")
                results["renames"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Series not found"
                })
        except Exception as e:
            logger.error(f"Failed to rename in {instance.name}: {str(e)}")
            results["renames"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })

    # Scan media servers if path exists
    if path:
        config = get_config()
        scanner = MediaServerScanner(config.get("media_servers", []))
        results["scanResults"] = await scanner.scan_path(path, is_series=True)
    
    return results

async def handle_radarr_rename(payload: Dict[str, Any], instances: List[RadarrInstance]):
    """Handle movie rename by syncing across instances and scanning media servers"""
    movie_id = payload.get("movie", {}).get("tmdbId")
    path = payload.get("movie", {}).get("folderPath") or payload.get("movie", {}).get("path")
    event_type = payload.get("eventType")
    
    results = {
        "status": "ok",
        "event": "Rename",
        "renames": [],
        "scanResults": []
    }
    
    # Sync rename across instances
    for instance in instances:
        try:
            # Get the movie from the instance
            movie = await instance.get_movie_by_tmdb_id(movie_id)
            if movie:
                # Trigger movie refresh to update filenames
                response = await instance.refresh_movie(movie['id'])
                results["renames"].append({
                    "instance": instance.name,
                    "status": "success"
                })
            else:
                logger.warning(f"Movie not found in {instance.name}")
                results["renames"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Movie not found"
                })
        except Exception as e:
            logger.error(f"Failed to rename in {instance.name}: {str(e)}")
            results["renames"].append({
                "instance": instance.name,
                "status": "error",
                "error": str(e)
            })

    # Scan media servers if path exists
    if path:
        config = get_config()
        scanner = MediaServerScanner(config.get("media_servers", []))
        results["scanResults"] = await scanner.scan_path(path, is_series=False)
    
    return results

# ------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # Configure uvicorn logging to match our format
    log_config = {
        "version": 1,
        "disable_existing_loggers": True,  # Changed to True to prevent duplicates
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(asctime)s [%(levelname)s] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
        },
    }
    
    # Add root logger configuration to ensure all module loggers inherit the correct level
    config = load_config()
    log_level = config.get('log_level', 'INFO').upper()
    log_config["root"] = {"handlers": ["default"], "level": log_level}
    
    # Add specific configuration for our module loggers
    log_config["loggers"]["media_server_service"] = {"level": log_level, "handlers": ["default"], "propagate": False}
    log_config["loggers"]["radarr_service"] = {"level": log_level, "handlers": ["default"], "propagate": False}
    log_config["loggers"]["sonarr_service"] = {"level": log_level, "handlers": ["default"], "propagate": False}
    
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=3536, 
        reload=False,
        log_config=log_config,
        access_log=False  # Disable access logging
    )
