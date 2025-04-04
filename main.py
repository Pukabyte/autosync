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
from radarr_service import (
    handle_radarr_grab,
    handle_radarr_import,
    handle_radarr_delete,
    handle_radarr_movie_add,
)
from sonarr_service import handle_sonarr_grab, handle_sonarr_import, handle_sonarr_series_add
from utils import load_config, get_config, save_config, parse_time_string
from media_server_service import MediaServerScanner
import random
import string
from pathlib import Path
import aiohttp

# Application version - update this when creating new releases
VERSION = "0.0.34"

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

# Mount static files with HTTPS configuration
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")

def get_template_context(request: Request, **kwargs) -> Dict[str, Any]:
    """Create a template context with common variables."""
    # Get the forwarded protocol (https if forwarded, otherwise use request scheme)
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    
    # Build base URL using the correct scheme
    base_url = str(request.base_url)
    if base_url.startswith("http:") and scheme == "https":
        base_url = "https:" + base_url[5:]
    
    context = {
        "request": request,
        "version": VERSION,
        "base_url": base_url
    }
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
        SonarrInstance(**inst)
        for inst in config.get("instances", [])
        if inst.get("type", "").lower() == "sonarr"
    ]
    
    radarr_instances = [
        RadarrInstance(**inst)
        for inst in config.get("instances", [])
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
    enabled: Optional[bool] = Form(True),
    rewrite_from: Optional[List[str]] = Form([]),
    rewrite_to: Optional[List[str]] = Form([])
):
    """Add a new media server to the configuration."""
    config = get_config()
    
    # Check if server with same name already exists
    for server in config.get("media_servers", []):
        if server.get("name") == name:
            return templates.TemplateResponse(
                "add_media_server.html",
                get_template_context(request, messages=[{"type": "danger", "text": "A server with this name already exists"}])
            )
    
    # Create server data
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
    
    # Add rewrite rules if any
    if rewrite_from and rewrite_to:
        server_data["rewrite"] = [
            {"from_path": f, "to_path": t}
            for f, t in zip(rewrite_from, rewrite_to)
            if f and t  # Only add rules where both from and to are provided
        ]
    
    # Add server to config
    if "media_servers" not in config:
        config["media_servers"] = []
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
    new_name: str = Form(...),
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
        "name": new_name,
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
    new_name: str = Form(...),
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
        "name": new_name,
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

@app.post("/test-connection")
async def test_connection(
    type: str = Form(...),
    url: str = Form(...),
    api_key: Optional[str] = Form(None),
    token: Optional[str] = Form(None)
) -> Dict[str, Any]:
    """Test connection to a Sonarr/Radarr instance or media server."""
    try:
        # Log the connection attempt
        logger.debug(f"Testing connection to {type} at {url}")
        
        # Add http:// if not present
        if not url.startswith(('http://', 'https://')):
            url = f"http://{url}"
            logger.debug(f"Added http:// protocol to URL: {url}")
            
        if type.lower() in ["sonarr", "radarr"]:
            # Test Sonarr/Radarr connection
            test_url = f"{url}/api/v3/system/status"
            headers = {"X-Api-Key": api_key}
            
            logger.debug(f"Attempting to connect to {test_url}")
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(test_url, headers=headers, timeout=10, ssl=False) as response:
                        if response.status == 200:
                            data = await response.json()
                            logger.info(f"Successfully connected to {type} instance")
                            return {
                                "status": "success",
                                "message": f"Successfully connected to {type}",
                                "version": data.get("version", "unknown")
                            }
                        else:
                            error_text = await response.text()
                            logger.error(f"Connection failed with status {response.status}: {error_text}")
                            return {
                                "status": "error",
                                "message": f"Failed to connect to {type}: {error_text}"
                            }
                except aiohttp.ClientError as e:
                    logger.error(f"Connection error: {str(e)}")
                    return {
                        "status": "error",
                        "message": f"Connection error: {str(e)}"
                    }
        elif type.lower() in ["plex", "jellyfin", "emby"]:
            # Test media server connection
            if type.lower() == "plex":
                test_url = f"{url}/library/sections"
                headers = {"X-Plex-Token": token}
            else:  # Jellyfin or Emby
                test_url = f"{url}/Library/SelectableMediaFolders"
                headers = {"X-MediaBrowser-Token": api_key}
            
            logger.debug(f"Attempting to connect to {test_url}")
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(test_url, headers=headers, timeout=10, ssl=False) as response:
                        if response.status == 200:
                            logger.info(f"Successfully connected to {type}")
                            return {
                                "status": "success",
                                "message": f"Successfully connected to {type}"
                            }
                        else:
                            error_text = await response.text()
                            logger.error(f"Connection failed with status {response.status}: {error_text}")
                            return {
                                "status": "error",
                                "message": f"Failed to connect to {type}: {error_text}"
                            }
                except aiohttp.ClientError as e:
                    logger.error(f"Connection error: {str(e)}")
                    return {
                        "status": "error",
                        "message": f"Connection error: {str(e)}"
                    }
        else:
            logger.error(f"Unsupported type: {type}")
            return {
                "status": "error",
                "message": f"Unsupported type: {type}"
            }
    except Exception as e:
        logger.error(f"Connection test failed: {str(e)}")
        return {
            "status": "error",
            "message": f"Connection test failed: {str(e)}"
        }

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

async def handle_sonarr_delete(payload: Dict[str, Any], instances: List[SonarrInstance], sync_interval: float, config: Dict[str, Any]):
    """Handle series or episode deletion by syncing across instances and scanning media servers"""
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
        
        # Handle different event types
        if event_type == "Download":
            # For downloads, use the episode file path to get season folder
            episode_file = payload.get("episodeFile", {})
            file_path = episode_file.get("path", "")
            if file_path:
                scan_path = file_path
                logger.debug(f"  ├─ Using episode file path for scanning: \033[1m{scan_path}\033[0m")
            else:
                scan_path = path
                logger.debug(f"  ├─ Using series path for scanning: \033[1m{scan_path}\033[0m")
        elif event_type == "EpisodeFileDelete":
            # For episode deletions, use the episode file path to get season folder
            episode_file = payload.get("episodeFile", {})
            file_path = episode_file.get("path", "")
            if file_path:
                scan_path = file_path
                logger.debug(f"  ├─ Using episode file path for scanning: \033[1m{scan_path}\033[0m")
            else:
                scan_path = path
                logger.debug(f"  ├─ Using series path for scanning: \033[1m{scan_path}\033[0m")
        elif event_type == "SeriesDelete":
            # For series deletions, use the series path
            scan_path = path
            logger.debug(f"  ├─ Using series path for scanning: \033[1m{scan_path}\033[0m")
        else:
            # For other events, use the series path
            scan_path = path
            logger.debug(f"  ├─ Using series path for scanning: \033[1m{scan_path}\033[0m")
        
        scan_results = []
        if scan_path:
            logger.info(f"  ├─ Initiating scan for path: \033[1m{scan_path}\033[0m")
            scan_results = await scanner.scan_path(scan_path, is_series=True)
            
            result = {
                "status": "ok",
                "message": f"Successfully scanned {len(scan_results)} media server(s)",
                "scanResults": scan_results,
                "scannedPath": scan_path
            }
        else:
            result = {"status": "ignored", "reason": f"No instances configured for {event_type}"}
        
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
    
    return result

@app.post("/webhook")
async def webhook_handler(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """
    Handle webhooks from Sonarr and Radarr with proper validation.
    """
    try:
        # Get sync timing settings
        config = get_config()
        sync_delay = parse_time_string(config.get("sync_delay", "0"))
        sync_interval = parse_time_string(config.get("sync_interval", "0"))
        
        event_type = payload.get("eventType")
        if not event_type:
            raise ValueError("Webhook payload missing eventType")

        # Generate a unique ID for this webhook
        webhook_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
        
        # Log webhook receipt and acknowledge it
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"Received webhook: \033[1m{event_type}\033[0m (ID: {webhook_id})")
        
        # Return immediate acknowledgment
        response = {
            "status": "received",
            "webhook_id": webhook_id,
            "event_type": event_type,
            "message": "Webhook received, processing will begin after sync delay"
        }
        
        # Start background task for processing
        asyncio.create_task(process_webhook(payload, event_type, webhook_id, sync_delay, sync_interval))
        
        return response

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

async def process_webhook(payload: Dict[str, Any], event_type: str, webhook_id: str, sync_delay: float, sync_interval: float):
    """Process webhook payload with proper timing."""
    try:
        # Get config for event validation
        config = get_config()
        
        # Apply sync delay before processing
        if sync_delay > 0:
            logger.info(f"  ├─ Applying sync delay of {sync_delay} seconds before processing")
            await asyncio.sleep(sync_delay)
        
        # Handle manual scan requests
        if event_type == "ManualScan":
            try:
                path = payload.get("path")
                content_type = payload.get("contentType")
                
                if not path or not content_type:
                    raise ValueError("Manual scan requires path and contentType")
                
                logger.info(f"  ├─ Manual scan requested for path: \033[1m{path}\033[0m")
                logger.info(f"  ├─ Content type: \033[1m{content_type}\033[0m")
                
                media_servers = config.get("media_servers", [])
                
                if not media_servers:
                    logger.error("  ├─ No media servers configured")
                    raise HTTPException(status_code=400, detail="No media servers configured")

                active_servers = [s for s in media_servers if s.get("enabled", False)]
                if not active_servers:
                    logger.error("  ├─ No active media servers found")
                    raise HTTPException(status_code=400, detail="No active media servers found")
                    
                logger.info(f"  ├─ Found \033[1m{len(active_servers)}\033[0m active media server(s)")
                
                # Validate content type
                if content_type not in ["series", "movie"]:
                    logger.error(f"  ├─ Invalid content type: {content_type}")
                    raise HTTPException(status_code=400, detail="Content type must be either 'series' or 'movie'")
                
                # Initialize scanner and perform scan
                scanner = MediaServerScanner(media_servers)
                scan_results = await scanner.scan_path(path, is_series=(content_type == "series"))
                
                # Check if any scans were successful
                successful_scans = [r for r in scan_results if r.get("status") == "success"]
                if not successful_scans:
                    logger.warning("  ├─ No successful scans completed")
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
                logger.error(f"  ├─ Manual scan failed: {str(e)}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        # Try to parse as Sonarr webhook first
        if "series" in payload:
            # Validate event type
            if event_type not in config.get("webhook_events", {}).get("sonarr", []):
                logger.info(f"  ├─ Ignoring unsupported Sonarr event={event_type}")
                return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}

            # Map "Download" to "Import" for consistency
            if event_type == "Download":
                event_type = "Import"
                
            # Log webhook receipt
            path = payload.get("series", {}).get("path", "")
            logger.info(f"  ├─ Processing Sonarr webhook: \033[1m{event_type}\033[0m")
            logger.info(f"  └─ Series path: \033[1m{path}\033[0m")

            webhook_data = SonarrWebhook(**payload)
            
            # Filter instances that have this event type enabled
            valid_instances = [
                inst for inst in sonarr_instances 
                if event_type.lower() in [e.lower() for e in inst.enabled_events]
            ]
            
            logger.debug(f"  ├─ Found {len(valid_instances)} Sonarr instances for event {event_type}")
            
            if not valid_instances:
                logger.info(f"  ├─ No Sonarr instances configured for event={event_type}")
                
                # Even if no instances are configured, we should still scan media servers for Import events
                if event_type == "Import":
                    # Get paths from payload for scanning
                    series_data = payload.get("series", {})
                    episode_file = payload.get("episodeFile", {})
                    series_path = series_data.get("path", "")
                    file_path = episode_file.get("path", "")
                    
                    # Initialize scanner with media servers from config
                    media_servers = config.get("media_servers", [])
                    logger.debug(f"  ├─ Found {len(media_servers)} media server(s) to scan")
                    
                    scanner = MediaServerScanner(media_servers)
                    
                    # Try to scan using the most specific path available
                    scan_path = None
                    if file_path:  # Use the full episode file path
                        scan_path = file_path
                        logger.debug(f"  ├─ Using episode file path for scanning: {scan_path}")
                    elif series_path:  # Fallback to series path if file path not available
                        scan_path = series_path
                        logger.debug(f"  ├─ Using series path for scanning: {scan_path}")
                    
                    scan_results = []
                    if scan_path:
                        logger.info(f"  ├─ Initiating scan for path: \033[1m{scan_path}\033[0m")
                        scan_results = await scanner.scan_path(scan_path, is_series=True)
                        
                        result = {
                            "status": "ok",
                            "message": "No Sonarr instances configured, but media servers were scanned",
                            "scanResults": scan_results,
                            "scannedPath": scan_path
                        }
                    else:
                        result = {"status": "ignored", "reason": f"No instances configured for {event_type}"}
                else:
                    result = {"status": "ignored", "reason": f"No instances configured for {event_type}"}
            else:
                if event_type == "Grab":
                    result = await handle_sonarr_grab(payload, valid_instances, sync_interval, config)
                elif event_type == "Import":
                    result = await handle_sonarr_import(payload, valid_instances, sync_interval, config)
                elif event_type in ["SeriesDelete", "EpisodeFileDelete"]:
                    logger.info(f"  ├─ Received {event_type} event, syncing deletion and scanning media servers")
                    result = await handle_sonarr_delete(payload, valid_instances, sync_interval, config)
                elif event_type == "SeriesAdd":
                    logger.info(f"  ├─ Received {event_type} event, syncing series addition across instances")
                    result = await handle_sonarr_series_add(payload, valid_instances, sync_interval, config)
                else:
                    logger.info(f"  ├─ Unhandled Sonarr event type: {event_type}")
                    result = {"status": "ignored", "reason": f"Unhandled event type: {event_type}"}

            return result

        # Try to parse as Radarr webhook
        elif "movie" in payload:
            # Validate event type
            if event_type not in config.get("webhook_events", {}).get("radarr", []):
                logger.info(f"  ├─ Ignoring unsupported Radarr event={event_type}")
                return {"status": "ignored", "reason": f"Unsupported event type: {event_type}"}

            # Map "Download" to "Import" for consistency
            if event_type == "Download":
                event_type = "Import"
                
            # Get paths from payload for logging
            movie_data = payload.get("movie", {})
            movie_file = payload.get("movieFile", {})
            folder_path = movie_data.get("folderPath", "")
            file_path = movie_file.get("path", "")
            
            logger.info(f"  ├─ Processing Radarr webhook: \033[1m{event_type}\033[0m")
            logger.info(f"  ├─ Movie: \033[1m{movie_data.get('title', 'Unknown')}\033[0m")
            logger.info(f"  ├─ Folder path: {folder_path}")
            logger.info(f"  └─ File path: {file_path}")

            webhook_data = RadarrWebhook(**payload)
            
            # Filter instances that have this event type enabled
            valid_instances = [
                inst for inst in radarr_instances 
                if event_type.lower() in [e.lower() for e in inst.enabled_events]
            ]
            
            logger.debug(f"  ├─ Found {len(valid_instances)} Radarr instances for event {event_type}")
            
            if not valid_instances:
                logger.info(f"  ├─ No Radarr instances configured for event={event_type}")
                
                # Even if no instances are configured, we should still scan media servers for Import events
                if event_type == "Import":
                    # Get paths from payload for scanning
                    movie_data = payload.get("movie", {})
                    movie_file = payload.get("movieFile", {})
                    folder_path = movie_data.get("folderPath", "")
                    file_path = movie_file.get("path", "")
                    
                    # Initialize scanner with media servers from config
                    media_servers = config.get("media_servers", [])
                    logger.debug(f"  ├─ Found {len(media_servers)} media server(s) to scan")
                    
                    scanner = MediaServerScanner(media_servers)
                    
                    # Try to scan using the most specific path available
                    scan_path = None
                    if file_path:  # Use movie file path to get movie folder
                        scan_path = str(Path(file_path).parent)  # Get movie folder path
                        logger.debug(f"  ├─ Using movie file path for scanning: {file_path}")
                        logger.debug(f"  ├─ Using movie folder path for scanning: {scan_path}")
                    elif folder_path:  # Fallback to movie folder path if file path not available
                        scan_path = folder_path
                        logger.debug(f"  ├─ Using movie folder path for scanning: {scan_path}")
                    
                    scan_results = []
                    if scan_path:
                        logger.info(f"  ├─ Initiating scan for path: \033[1m{scan_path}\033[0m")
                        scan_results = await scanner.scan_path(scan_path, is_series=False)
                        
                        result = {
                            "status": "ok",
                            "message": "No Radarr instances configured, but media servers were scanned",
                            "scanResults": scan_results,
                            "scannedPath": scan_path
                        }
                    else:
                        result = {"status": "ignored", "reason": f"No instances configured for {event_type}"}
                else:
                    result = {"status": "ignored", "reason": f"No instances configured for {event_type}"}
            else:
                if event_type == "Grab":
                    result = await handle_radarr_grab(payload, valid_instances, sync_interval, config)
                elif event_type == "Import":
                    result = await handle_radarr_import(payload, valid_instances, sync_interval, config)
                elif event_type in ["MovieDelete", "MovieFileDelete"]:
                    logger.info(f"  ├─ Received {event_type} event, syncing deletion and scanning media servers")
                    result = await handle_radarr_delete(payload, valid_instances, sync_interval, config)
                elif event_type == "MovieAdded":
                    logger.info(f"  ├─ Received {event_type} event, syncing movie addition across instances")
                    result = await handle_radarr_movie_add(payload, valid_instances, sync_interval, config)
                else:
                    logger.info(f"  ├─ Unhandled Radarr event type: {event_type}")
                    result = {"status": "ignored", "reason": f"Unhandled event type: {event_type}"}

            return result

        else:
            logger.warning("  ├─ Unknown webhook type")
            raise ValueError("Webhook must contain either 'series' or 'movie' data")

    except ValueError as e:
        logger.warning(f"  ├─ Invalid webhook format: {str(e)}")
        return {"status": "error", "reason": f"Invalid webhook format: {str(e)}"}

    except Exception as e:
        logger.error(f"  ├─ Failed to process webhook: {str(e)}")
        return {"status": "error", "reason": f"Internal server error: {str(e)}"}

async def handle_sonarr_rename(payload: Dict[str, Any], instances: List[SonarrInstance], sync_interval: float, config: Dict[str, Any]):
    """Handle series rename by syncing across instances and scanning media servers"""
    series_data = payload.get("series", {})
    series_id = series_data.get("tvdbId")
    title = series_data.get("title", "Unknown")
    path = series_data.get("path")
    
    results = {
        "status": "ok",
        "event": "Rename",
        "title": title,
        "tvdbId": series_id,
        "renames": [],
        "scanResults": []
    }
    
    # Log the rename event
    logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    logger.info(f"Processing Sonarr Rename: Title=\033[1m{title}\033[0m, TVDB=\033[1m{series_id}\033[0m")
    if path:
        logger.info(f"  ├─ Path: \033[1m{path}\033[0m")
    
    # Sync rename across instances
    for i, instance in enumerate(instances):
        try:
            # Apply sync interval between instances (but not before the first one)
            if i > 0 and sync_interval > 0:
                logger.info(f"  ├─ Waiting {sync_interval} seconds before processing next instance")
                await asyncio.sleep(sync_interval)
            
            # Get the series from the instance
            series = await instance.get_series_by_tvdb_id(series_id)
            if series:
                # Trigger series refresh to update filenames
                response = await instance.refresh_series(series['id'])
                logger.info(f"  ├─ Refreshed series in \033[1m{instance.name}\033[0m")
                results["renames"].append({
                    "instance": instance.name,
                    "status": "success"
                })
            else:
                logger.warning(f"  ├─ Series not found in \033[1m{instance.name}\033[0m")
                results["renames"].append({
                    "instance": instance.name,
                    "status": "skipped",
                    "reason": "Series not found"
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

@app.get("/api/root-folders")
async def get_root_folders(type: str, url: str, api_key: str) -> Dict[str, Any]:
    """Get root folders from a Sonarr/Radarr instance."""
    try:
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = f"http://{url}"
            
        # Test connection first
        test_url = f"{url}/api/v3/system/status"
        headers = {"X-Api-Key": api_key}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, headers=headers, timeout=10, ssl=False) as response:
                if response.status != 200:
                    return {
                        "status": "error",
                        "message": "Failed to connect to instance"
                    }
        
        # Get root folders
        folders_url = f"{url}/api/v3/rootFolder"
        async with aiohttp.ClientSession() as session:
            async with session.get(folders_url, headers=headers, timeout=10, ssl=False) as response:
                if response.status == 200:
                    folders = await response.json()
                    return {
                        "status": "success",
                        "folders": folders
                    }
                else:
                    error_text = await response.text()
                    return {
                        "status": "error",
                        "message": f"Failed to get root folders: {error_text}"
                    }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }

@app.get("/api/quality-profiles")
async def get_quality_profiles(type: str, url: str, api_key: str) -> Dict[str, Any]:
    """Get quality profiles from a Sonarr/Radarr instance."""
    try:
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = f"http://{url}"
            
        # Test connection first
        test_url = f"{url}/api/v3/system/status"
        headers = {"X-Api-Key": api_key}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, headers=headers, timeout=10, ssl=False) as response:
                if response.status != 200:
                    return {
                        "status": "error",
                        "message": "Failed to connect to instance"
                    }
        
        # Get quality profiles
        profiles_url = f"{url}/api/v3/qualityprofile"
        async with aiohttp.ClientSession() as session:
            async with session.get(profiles_url, headers=headers, timeout=10, ssl=False) as response:
                if response.status == 200:
                    profiles = await response.json()
                    return {
                        "status": "success",
                        "profiles": profiles
                    }
                else:
                    error_text = await response.text()
                    return {
                        "status": "error",
                        "message": f"Failed to get quality profiles: {error_text}"
                    }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }

@app.get("/api/language-profiles")
async def get_language_profiles(type: str, url: str, api_key: str) -> Dict[str, Any]:
    """Get language profiles from a Sonarr instance."""
    try:
        if type.lower() != "sonarr":
            return {
                "status": "error",
                "message": "Language profiles are only available for Sonarr"
            }
            
        # Ensure URL has protocol
        if not url.startswith(('http://', 'https://')):
            url = f"http://{url}"
            
        # Test connection first
        test_url = f"{url}/api/v3/system/status"
        headers = {"X-Api-Key": api_key}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, headers=headers, timeout=10, ssl=False) as response:
                if response.status != 200:
                    return {
                        "status": "error",
                        "message": "Failed to connect to instance"
                    }
        
        # Get language profiles
        profiles_url = f"{url}/api/v3/languageprofile"
        async with aiohttp.ClientSession() as session:
            async with session.get(profiles_url, headers=headers, timeout=10, ssl=False) as response:
                if response.status == 200:
                    profiles = await response.json()
                    return {
                        "status": "success",
                        "profiles": profiles
                    }
                else:
                    error_text = await response.text()
                    return {
                        "status": "error",
                        "message": f"Failed to get language profiles: {error_text}"
                    }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }

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
            "multipart": {"level": "WARNING", "propagate": False},  # Suppress multipart debug logs
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
