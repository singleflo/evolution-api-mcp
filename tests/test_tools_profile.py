import json

import pytest

from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools.profile import (
    get_my_profile,
    get_privacy_settings,
    remove_profile_picture,
    update_privacy_settings,
    update_profile_about,
    update_profile_name,
    update_profile_picture,
)
from tests.conftest import INSTANCE, TOKEN

FETCH_INSTANCES = "/instance/fetchInstances"
FETCH_PROFILE = f"/chat/fetchProfile/{INSTANCE}"
FETCH_PRIVACY = f"/chat/fetchPrivacySettings/{INSTANCE}"
UPDATE_PRIVACY = f"/chat/updatePrivacySettings/{INSTANCE}"

OWNER = "393331234567@s.whatsapp.net"
CURRENT_PRIVACY = {
    "readreceipts": "all",
    "profile": "contacts",
    "status": "contacts",
    "online": "all",
    "last": "contacts",
    "groupadd": "contacts",
}


def _instance_row(owner=OWNER):
    return {
        "name": INSTANCE,
        "connectionStatus": "open",
        "ownerJid": owner,
        "profileName": "Shop",
        "integration": "WHATSAPP-BAILEYS",
        "token": TOKEN,
    }


# --- get_my_profile --------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_my_profile_looks_up_the_owner_number_and_projects_the_answer(evo, bound):
    evo.on("GET", FETCH_INSTANCES, json=[_instance_row()])
    evo.on(
        "POST",
        FETCH_PROFILE,
        json={
            "wuid": OWNER,
            "name": "Shop",
            "numberExists": True,
            "picture": "https://pps.whatsapp.net/me.jpg",
            "status": "Open 9-18",
            "isBusiness": True,
            "email": "shop@example.com",
            "description": "Bakery",
            "website": "https://shop.example.com",
        },
    )
    with bound(evo):
        result = json.loads(await get_my_profile())

    assert evo.last("GET", FETCH_INSTANCES).params == {"instanceName": INSTANCE}
    assert evo.last("POST", FETCH_PROFILE).json == {"number": "393331234567"}
    assert result == {
        "phone_number": "393331234567",
        "name": "Shop",
        "about": "Open 9-18",
        "picture_url": "https://pps.whatsapp.net/me.jpg",
        "is_business": True,
        "email": "shop@example.com",
        "description": "Bakery",
        "website": "https://shop.example.com",
    }
    assert TOKEN not in json.dumps(result)


@pytest.mark.anyio
async def test_get_my_profile_before_pairing_points_to_start_pairing(evo, bound):
    evo.on("GET", FETCH_INSTANCES, json=[_instance_row(owner=None)])
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await get_my_profile()

    assert str(caught.value) == "The instance has no linked phone number yet; start_pairing links one."
    assert [r.path for r in evo.requests] == [FETCH_INSTANCES]


@pytest.mark.anyio
async def test_get_my_profile_when_the_instance_vanished(evo, bound):
    evo.on("GET", FETCH_INSTANCES, json=[{"name": "someone-else", "ownerJid": OWNER}])
    with bound(evo), pytest.raises(ToolExecutionError, match="Evolution no longer lists this instance"):
        await get_my_profile()


@pytest.mark.anyio
async def test_get_my_profile_reports_a_rejected_token(evo, bound):
    evo.on("GET", FETCH_INSTANCES, status=401, json={"status": 401, "error": "Unauthorized", "message": "Unauthorized"})
    with bound(evo), pytest.raises(ToolExecutionError, match=r"Evolution rejected the instance token \(401"):
        await get_my_profile()


# --- privacy settings ------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_privacy_settings_renames_evolution_keys(evo, bound):
    evo.on("GET", FETCH_PRIVACY, json=CURRENT_PRIVACY)
    with bound(evo):
        result = json.loads(await get_privacy_settings())

    assert result == {
        "read_receipts": "all",
        "profile_photo": "contacts",
        "about": "contacts",
        "online": "all",
        "last_seen": "contacts",
        "groups_add": "contacts",
    }


@pytest.mark.anyio
async def test_update_privacy_settings_overlays_the_given_values_on_the_current_ones(evo, bound):
    evo.on("GET", FETCH_PRIVACY, json=CURRENT_PRIVACY)
    evo.on("POST", UPDATE_PRIVACY, json={"update": "success"})
    with bound(evo):
        result = json.loads(await update_privacy_settings(read_receipts="none", last_seen="none"))

    assert evo.last("POST", UPDATE_PRIVACY).json == {
        "readreceipts": "none",
        "profile": "contacts",
        "status": "contacts",
        "online": "all",
        "last": "none",
        "groupadd": "contacts",
    }
    assert result == {
        "read_receipts": "none",
        "profile_photo": "contacts",
        "about": "contacts",
        "online": "all",
        "last_seen": "none",
        "groups_add": "contacts",
    }


@pytest.mark.anyio
async def test_update_privacy_settings_maps_every_parameter_to_its_evolution_key(evo, bound):
    evo.on("GET", FETCH_PRIVACY, json=CURRENT_PRIVACY)
    evo.on("POST", UPDATE_PRIVACY, json={"update": "success"})
    with bound(evo):
        await update_privacy_settings(
            read_receipts="none",
            profile_photo="none",
            about="contact_blacklist",
            last_seen="all",
            groups_add="none",
            online="match_last_seen",
        )

    assert evo.last("POST", UPDATE_PRIVACY).json == {
        "readreceipts": "none",
        "profile": "none",
        "status": "contact_blacklist",
        "online": "match_last_seen",
        "last": "all",
        "groupadd": "none",
    }


@pytest.mark.anyio
async def test_update_privacy_settings_needs_at_least_one_setting(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await update_privacy_settings()

    assert str(caught.value) == "Give at least one privacy setting to change. Nothing was changed."
    assert evo.requests == []


@pytest.mark.anyio
async def test_update_privacy_settings_refuses_when_a_current_value_cannot_be_merged(evo, bound):
    evo.on("GET", FETCH_PRIVACY, json={**CURRENT_PRIVACY, "last": None, "online": None})
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await update_privacy_settings(read_receipts="none")

    assert "did not report the current value of online, last_seen" in str(caught.value)
    assert str(caught.value).endswith("Nothing was changed.")
    assert [r.method for r in evo.requests] == ["GET"]


@pytest.mark.anyio
async def test_update_privacy_settings_can_fill_a_missing_value_itself(evo, bound):
    evo.on("GET", FETCH_PRIVACY, json={**CURRENT_PRIVACY, "online": None})
    evo.on("POST", UPDATE_PRIVACY, json={"update": "success"})
    with bound(evo):
        result = json.loads(await update_privacy_settings(online="all"))

    assert result["online"] == "all"
    assert evo.last("POST", UPDATE_PRIVACY).json["online"] == "all"


@pytest.mark.anyio
async def test_update_privacy_settings_server_failure_is_uncertain(evo, bound):
    evo.on("GET", FETCH_PRIVACY, json=CURRENT_PRIVACY)
    evo.on(
        "POST",
        UPDATE_PRIVACY,
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "Error updating privacy"}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await update_privacy_settings(about="none")

    assert str(caught.value).startswith("UNCERTAIN: Evolution did not confirm the result")


# --- name, about, picture --------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_profile_name(evo, bound):
    path = f"/chat/updateProfileName/{INSTANCE}"
    evo.on("POST", path, json={"update": "success"})
    with bound(evo):
        result = json.loads(await update_profile_name("Shop Milano"))

    assert evo.last("POST", path).json == {"name": "Shop Milano"}
    assert result == {"updated": "name"}


@pytest.mark.anyio
async def test_update_profile_about_sends_the_status_key(evo, bound):
    path = f"/chat/updateProfileStatus/{INSTANCE}"
    evo.on("POST", path, json={"update": "success"})
    with bound(evo):
        result = json.loads(await update_profile_about("Open 9-18"))

    assert evo.last("POST", path).json == {"status": "Open 9-18"}
    assert result == {"updated": "about"}


@pytest.mark.anyio
async def test_update_profile_picture_sends_the_url_as_picture(evo, bound):
    path = f"/chat/updateProfilePicture/{INSTANCE}"
    evo.on("POST", path, json={"update": "success"})
    with bound(evo):
        result = json.loads(await update_profile_picture("https://cdn.example.com/logo.jpg"))

    assert evo.last("POST", path).json == {"picture": "https://cdn.example.com/logo.jpg"}
    assert result == {"updated": "picture"}


@pytest.mark.anyio
async def test_remove_profile_picture_deletes(evo, bound):
    path = f"/chat/removeProfilePicture/{INSTANCE}"
    evo.on("DELETE", path, json={"update": "success"})
    with bound(evo):
        result = json.loads(await remove_profile_picture())

    assert evo.last("DELETE", path).method == "DELETE"
    assert result == {"updated": "picture"}


@pytest.mark.anyio
async def test_profile_update_that_fails_on_the_server_is_uncertain(evo, bound):
    path = f"/chat/updateProfileName/{INSTANCE}"
    evo.on(
        "POST",
        path,
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "Error updating profile name"}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await update_profile_name("Shop")

    assert "Do NOT repeat the call" in str(caught.value)
