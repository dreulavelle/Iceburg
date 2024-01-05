from fastapi import APIRouter, Request
import requests
from utils.settings import settings_manager


router = APIRouter(
    responses={404: {"description": "Not found"}},
)


@router.get("/")
async def root():
    return {
        "success": True,
        "message": "Iceburg is running!",
    }


@router.get("/health")
async def health():
    return {
        "success": True,
        "message": "Iceburg is running!",
    }


@router.get("/user")
async def get_rd_user():
    api_key = settings_manager.get("real_debrid.api_key")
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get(
        "https://api.real-debrid.com/rest/1.0/user", headers=headers
    )
    return response.json()


@router.get("/services")
async def get_services(request: Request):
    data = {}
    if hasattr(request.app.program, "core_manager"):
        for service in request.app.program.core_manager.services:
            data[service.key] = service.initialized
            if getattr(service, "sm", False):
                for sub_service in service.sm.services:
                    data[sub_service.key] = sub_service.initialized
    if hasattr(request.app.program, "extras_manager"):
        for service in request.app.program.extras_manager.services:
            data[service.key] = service.initialized
            if getattr(service, "sm", False):
                for sub_service in service.sm.services:
                    data[sub_service.key] = sub_service.initialized
    return {
        "success": True,
        "data": data
    }
