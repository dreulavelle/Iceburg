import json

import pytest

from program.services.downloaders import alldebrid
from program.services.downloaders.alldebrid import (
    AllDebridDownloader,
    AllDebridAPI
)
from program.settings.manager import settings_manager as settings


@pytest.fixture
def downloader(instant, upload, status, delete):
    """Instance of AllDebridDownloader with API calls mocked"""
    # mock API calls
    _request = AllDebridAPI._request
    def get(self, method, url, **params):
        match url:
            case "user":
                return { "user": { "isPremium": True, "premiumUntil": 1735514599, } }
            case "magnet/instant":
                return instant(url, **params)
            case "magnet/upload":
                return upload(url, **params)
            case "magnet/delete":
                return delete(url, **params)
            case "magnet/status":
                if params.get("id", False):
                    return status(url, **params)
            case _:
                raise Exception("unmatched api call")
    AllDebridAPI._request = get

    alldebrid_settings = settings.settings.downloaders.all_debrid
    alldebrid_settings.enabled = True
    alldebrid_settings.api_key = "key"

    downloader = AllDebridDownloader()
    assert downloader.initialized
    yield downloader

    # tear down mock
    AllDebridAPI._request = _request


## API parsing tests
def test_get_instant_availability(instant, downloader):
    AllDebridAPI._request = instant
    infohashes = [UBUNTU]
    availability = downloader.get_instant_availability(infohashes)
    assert len(availability[0].get("files", [])) == 2


def test_get_instant_availability_unavailable(instant_unavailable, downloader):
    AllDebridAPI._request = instant_unavailable
    infohashes = [UBUNTU]
    availability = downloader.get_instant_availability(infohashes)
    assert availability[0]["hash"] == UBUNTU


def test_add_torrent(upload, downloader):
    AllDebridAPI._request = upload
    torrent_id = downloader.add_torrent(UBUNTU)
    assert torrent_id == 251993753


def test_add_torrent_cached(upload_ready, downloader):
    AllDebridAPI._request = upload_ready
    torrent_id = downloader.add_torrent(UBUNTU)
    assert torrent_id == 251993753


def test_get_status(status, downloader):
    AllDebridAPI._request = status
    torrent_status = downloader.get_status(251993753)
    assert torrent_status["filename"] == "Ubuntu 24.04"


def test_get_status_unfinished(status_downloading, downloader):
    AllDebridAPI._request = status_downloading
    torrent_info = downloader.get_torrent_info(251993753)
    assert torrent_info["status"] == "Downloading"


def test_delete(delete):
    AllDebridAPI._request = delete
    delete(123)


# Example requests - taken from real API calls
UBUNTU = "3648baf850d5930510c1f172b534200ebb5496e6"
MAGNET_ID = "251993753"
@pytest.fixture
def instant():
    """GET /magnet/instant?magnets[0]=infohash (torrent available)"""
    with open("src/tests/test_data/alldebrid_magnet_instant.json") as f:
        body = json.load(f)
    return lambda self, method, url, **params: body

@pytest.fixture
def instant_unavailable():
    """GET /magnet/instant?magnets[0]=infohash (torrent unavailable)"""
    with open("src/tests/test_data/alldebrid_magnet_instant_unavailable.json") as f:
        body = json.load(f)
    return lambda self, method, url, **params: body

@pytest.fixture
def upload():
    """GET /magnet/upload?magnets[]=infohash (torrent not ready yet)"""
    with open("src/tests/test_data/alldebrid_magnet_upload_not_ready.json") as f:
        body = json.load(f)
    return lambda self, method, url, **params: body

@pytest.fixture
def upload_ready():
    """GET /magnet/upload?magnets[]=infohash (torrent ready)"""
    with open("src/tests/test_data/alldebrid_magnet_upload_ready.json") as f:
        body = json.load(f)
    return lambda self, method, url, **params: body

@pytest.fixture
def status():
    """GET /magnet/status?id=123 (debrid links ready)"""
    with open("src/tests/test_data/alldebrid_magnet_status_one_ready.json") as f:
        body = json.load(f)
    return lambda self, method, url, **params: body

@pytest.fixture
def status_downloading():
    """GET /magnet/status?id=123 (debrid links not ready yet)"""
    with open("src/tests/test_data/alldebrid_magnet_status_one_downloading.json") as f:
        body = json.load(f)
    return lambda self, method, url, **params: body

@pytest.fixture
def delete():
    """GET /delete"""
    with open("src/tests/test_data/alldebrid_magnet_delete.json") as f:
        body = json.load(f)
    return lambda url, **params: body

