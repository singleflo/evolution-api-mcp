import json
from unittest import mock

import httpx2
import pytest

from evolution_api_mcp import errors
from evolution_api_mcp.errors import ToolExecutionError
from evolution_api_mcp.tools import groups
from tests.conftest import INSTANCE

GROUP = "120363012345678901@g.us"
GROUP_DIGITS = "120363012345678901"
OWNER = "391110001111@s.whatsapp.net"
CODE = "F1EX5QZxO181L3TMVP31gY"
LINK = f"https://chat.whatsapp.com/{CODE}"


def _path(endpoint: str) -> str:
    return f"/group/{endpoint}/{INSTANCE}"


def _group_row(group_id=GROUP, subject="Team", **extra):
    row = {
        "id": group_id,
        "subject": subject,
        "subjectOwner": OWNER,
        "subjectTime": 1759180000,
        "size": 3,
        "creation": 1759180000,
        "owner": OWNER,
        "desc": "Weekly sync",
        "descId": "ABCDEF",
        "restrict": False,
        "announce": False,
        "isCommunity": False,
        "isCommunityAnnounce": False,
    }
    row.update(extra)
    return row


# --- list_groups -----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_groups_projects_sorts_by_subject_and_asks_without_participants(evo, bound):
    evo.on(
        "GET",
        _path("fetchAllGroups"),
        json=[
            _group_row("120363000000000002@g.us", "beta team", announce=True),
            _group_row("120363000000000001@g.us", "Alpha", restrict=True, pictureUrl="https://pps.example/a.jpg"),
            _group_row("120363000000000003@g.us", "Gamma", owner=None, creation=None, isCommunity=True, size=None),
        ],
    )
    with bound(evo):
        result = json.loads(await groups.list_groups())

    request = evo.last("GET", _path("fetchAllGroups"))
    assert request.params == {"getParticipants": "false"}
    assert result == {
        "groups": [
            {
                "group_id": "120363000000000001@g.us",
                "subject": "Alpha",
                "size": 3,
                "owner": "391110001111",
                "created_at": "2025-09-29T21:06:40Z",
                "announce_only": False,
                "locked": True,
                "is_community": False,
            },
            {
                "group_id": "120363000000000002@g.us",
                "subject": "beta team",
                "size": 3,
                "owner": "391110001111",
                "created_at": "2025-09-29T21:06:40Z",
                "announce_only": True,
                "locked": False,
                "is_community": False,
            },
            {
                "group_id": "120363000000000003@g.us",
                "subject": "Gamma",
                "size": None,
                "owner": None,
                "created_at": None,
                "announce_only": False,
                "locked": False,
                "is_community": True,
            },
        ],
        "total": 3,
        "offset": 0,
        "limit": 50,
        "has_more": False,
    }


@pytest.mark.anyio
async def test_list_groups_pages_after_sorting_and_reports_has_more(evo, bound):
    rows = [_group_row(f"1203630000000000{n:02d}@g.us", f"Group {n:02d}") for n in (5, 1, 4, 2, 3)]
    evo.on("GET", _path("fetchAllGroups"), json=rows)
    with bound(evo):
        first = json.loads(await groups.list_groups(limit=2))
        second = json.loads(await groups.list_groups(limit=2, offset=2))
        last = json.loads(await groups.list_groups(limit=2, offset=4))

    assert [g["subject"] for g in first["groups"]] == ["Group 01", "Group 02"]
    assert (first["total"], first["has_more"]) == (5, True)
    assert [g["subject"] for g in second["groups"]] == ["Group 03", "Group 04"]
    assert second["has_more"] is True
    assert [g["subject"] for g in last["groups"]] == ["Group 05"]
    assert (last["offset"], last["limit"], last["has_more"]) == (4, 2, False)


@pytest.mark.anyio
async def test_list_groups_filters_names_here_case_insensitively_before_paging(evo, bound):
    evo.on(
        "GET",
        _path("fetchAllGroups"),
        json=[_group_row(subject="Sales Italy"), _group_row(subject="Support"), _group_row(subject="sales Spain")],
    )
    with bound(evo):
        result = json.loads(await groups.list_groups(name_contains="SALES"))

    assert [g["subject"] for g in result["groups"]] == ["Sales Italy", "sales Spain"]
    assert result["total"] == 2


@pytest.mark.anyio
async def test_list_groups_with_no_groups_is_an_empty_page(evo, bound):
    evo.on("GET", _path("fetchAllGroups"), json=[])
    with bound(evo):
        result = json.loads(await groups.list_groups())

    assert result == {"groups": [], "total": 0, "offset": 0, "limit": 50, "has_more": False}


# --- get_group -------------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_group_projects_details_and_participants(evo, bound):
    row = _group_row(
        subject="Team",
        announce=True,
        pictureUrl="https://pps.example/g.jpg",
        participants=[
            {"id": OWNER, "admin": "superadmin"},
            {"id": "392220002222@s.whatsapp.net", "admin": "admin", "lid": "111@lid"},
            {"id": "393330003333@s.whatsapp.net", "admin": None},
            {"id": "98765@lid", "phoneNumber": "394440004444@s.whatsapp.net", "admin": None},
            {"id": "55555@lid", "admin": None},
        ],
        size=5,
    )
    evo.on("GET", _path("findGroupInfos"), json=row)
    with bound(evo):
        result = json.loads(await groups.get_group(GROUP_DIGITS))

    assert evo.last("GET", _path("findGroupInfos")).params == {"groupJid": GROUP}
    assert result == {
        "group_id": GROUP,
        "subject": "Team",
        "description": "Weekly sync",
        "owner": "391110001111",
        "created_at": "2025-09-29T21:06:40Z",
        "size": 5,
        "announce_only": True,
        "locked": False,
        "picture_url": "https://pps.example/g.jpg",
        "is_community": False,
        "participants": [
            {"chat_id": OWNER, "phone": "391110001111", "admin": "superadmin"},
            {"chat_id": "392220002222@s.whatsapp.net", "phone": "392220002222", "admin": "admin"},
            {"chat_id": "393330003333@s.whatsapp.net", "phone": "393330003333", "admin": None},
            {"chat_id": "98765@lid", "phone": "394440004444", "admin": None},
            {"chat_id": "55555@lid", "phone": None, "admin": None},
        ],
    }


@pytest.mark.anyio
async def test_get_group_reports_ephemeral_duration_when_evolution_returns_it(evo, bound):
    evo.on("GET", _path("findGroupInfos"), json=_group_row(participants=[], ephemeralDuration=604800))
    with bound(evo):
        result = json.loads(await groups.get_group(GROUP))

    assert result["ephemeral_seconds"] == 604800


@pytest.mark.anyio
async def test_get_group_caps_participants_at_500_and_flags_it(evo, bound):
    participants = [{"id": f"39333{n:07d}@s.whatsapp.net", "admin": None} for n in range(520)]
    evo.on("GET", _path("findGroupInfos"), json=_group_row(participants=participants, size=520))
    with bound(evo), mock.patch.object(errors, "MAX_RESULT_CHARS", 10_000_000):
        result = json.loads(await groups.get_group(GROUP))

    assert len(result["participants"]) == 500
    assert result["participants"][-1]["chat_id"] == "393330000499@s.whatsapp.net"
    assert result["participants_truncated"] is True
    assert result["size"] == 520


@pytest.mark.anyio
async def test_get_group_with_exactly_500_participants_is_not_flagged(evo, bound):
    participants = [{"id": f"39333{n:07d}@s.whatsapp.net", "admin": None} for n in range(500)]
    evo.on("GET", _path("findGroupInfos"), json=_group_row(participants=participants, size=500))
    with bound(evo), mock.patch.object(errors, "MAX_RESULT_CHARS", 10_000_000):
        result = json.loads(await groups.get_group(GROUP))

    assert len(result["participants"]) == 500
    assert "participants_truncated" not in result


@pytest.mark.anyio
async def test_get_group_answering_null_says_the_group_was_not_found(evo, bound):
    evo.on("GET", _path("findGroupInfos"), text="null")
    with bound(evo), pytest.raises(ToolExecutionError, match=f"Group {GROUP} was not found. Use list_groups"):
        await groups.get_group(GROUP)


@pytest.mark.anyio
async def test_get_group_passes_evolutions_refusal_through(evo, bound):
    evo.on(
        "GET",
        _path("findGroupInfos"),
        status=404,
        json={"status": 404, "error": "Not Found", "response": {"message": ["Error fetching group"]}},
    )
    with (
        bound(evo),
        pytest.raises(ToolExecutionError, match=r"Evolution refused the request \(404\): Error fetching group"),
    ):
        await groups.get_group(GROUP)


@pytest.mark.anyio
async def test_group_tools_refuse_ids_that_are_not_groups_before_calling_evolution(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="Unsupported group id 'not a group'"):
        await groups.get_group("not a group")
    assert evo.requests == []


# --- invite links ----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_group_invite_link_reads_the_code_from_the_query(evo, bound):
    evo.on("GET", _path("inviteCode"), json={"inviteUrl": LINK, "inviteCode": CODE})
    with bound(evo):
        result = json.loads(await groups.get_group_invite_link(GROUP))

    assert evo.last("GET", _path("inviteCode")).params == {"groupJid": GROUP}
    assert result == {"group_id": GROUP, "invite_url": LINK, "invite_code": CODE}


@pytest.mark.anyio
@pytest.mark.parametrize("invite", [LINK, f"{LINK}?mode=r", CODE, f"  {CODE}  "])
async def test_get_invite_info_accepts_a_link_or_a_bare_code(evo, bound, invite):
    evo.on(
        "GET",
        _path("inviteInfo"),
        json={
            "id": GROUP_DIGITS,
            "subject": "Team",
            "size": 42,
            "creation": 1700000000,
            "owner": OWNER,
            "desc": "Weekly sync",
            "participants": [],
        },
    )
    with bound(evo):
        result = json.loads(await groups.get_invite_info(invite))

    assert evo.last("GET", _path("inviteInfo")).params == {"inviteCode": CODE}
    assert result == {
        "group_id": GROUP,
        "subject": "Team",
        "description": "Weekly sync",
        "size": 42,
        "owner": "391110001111",
        "created_at": "2023-11-14T22:13:20Z",
    }


@pytest.mark.anyio
async def test_get_invite_info_refuses_a_malformed_invite_without_calling_evolution(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="Unsupported invite 'https://example.com/x'"):
        await groups.get_invite_info("https://example.com/x")
    assert evo.requests == []


@pytest.mark.anyio
async def test_join_group_by_invite_uses_get_with_the_code_and_returns_the_group(evo, bound):
    evo.on("GET", _path("acceptInviteCode"), json={"accepted": True, "groupJid": GROUP})
    with bound(evo):
        result = json.loads(await groups.join_group_by_invite(LINK))

    request = evo.last("GET", _path("acceptInviteCode"))
    assert request.params == {"inviteCode": CODE}
    assert request.json is None
    assert result == {"joined": True, "group_id": GROUP}


@pytest.mark.anyio
async def test_join_group_by_invite_read_timeout_is_uncertain(evo, bound):
    evo.fail("GET", _path("acceptInviteCode"), httpx2.ReadTimeout("no answer"))
    with bound(evo), pytest.raises(ToolExecutionError, match="UNCERTAIN.*Do NOT repeat"):
        await groups.join_group_by_invite(CODE)


@pytest.mark.anyio
async def test_revoke_group_invite_link_returns_the_new_link(evo, bound):
    new_code = "Zz9YyXxWwVvUuTtSsRrQqP"
    evo.on("POST", _path("revokeInviteCode"), status=201, json={"revoked": True, "inviteCode": new_code})
    with bound(evo):
        result = json.loads(await groups.revoke_group_invite_link(GROUP))

    assert evo.last("POST", _path("revokeInviteCode")).json == {"groupJid": GROUP}
    assert result == {
        "group_id": GROUP,
        "invite_code": new_code,
        "invite_url": f"https://chat.whatsapp.com/{new_code}",
    }


@pytest.mark.anyio
async def test_send_group_invite_sends_the_message_to_normalized_numbers(evo, bound):
    evo.on("POST", _path("sendInvite"), json={"send": True, "inviteUrl": LINK})
    with bound(evo):
        result = json.loads(
            await groups.send_group_invite(
                GROUP, ["+39 333 123 4567", "393331234567", "392220002222"], "Join our team group"
            )
        )

    assert evo.last("POST", _path("sendInvite")).json == {
        "groupJid": GROUP,
        "description": "Join our team group",
        "numbers": ["393331234567", "392220002222"],
    }
    assert result == {"sent": True, "invite_url": LINK}


# --- create_group ----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_group_posts_normalized_participants_and_returns_the_new_group(evo, bound):
    evo.on(
        "POST",
        _path("create"),
        status=201,
        json=_group_row(subject="Launch", participants=[{"id": OWNER}, {"id": "393331234567@s.whatsapp.net"}], size=2),
    )
    with bound(evo):
        result = json.loads(
            await groups.create_group("Launch", ["+39 333 123 4567", "391110001111", "393331234567"], "Launch crew")
        )

    assert evo.last("POST", _path("create")).json == {
        "subject": "Launch",
        "participants": ["393331234567", "391110001111"],
        "description": "Launch crew",
    }
    assert result == {"group_id": GROUP, "subject": "Launch", "size": 2}


@pytest.mark.anyio
async def test_create_group_without_description_sends_no_description_key(evo, bound):
    evo.on("POST", _path("create"), status=201, json=_group_row(subject="Launch", size=1))
    with bound(evo):
        await groups.create_group("Launch", ["393331234567"])

    assert evo.last("POST", _path("create")).json == {"subject": "Launch", "participants": ["393331234567"]}


@pytest.mark.anyio
async def test_create_group_refuses_a_participant_that_is_not_a_phone_number(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="is not a phone number"):
        await groups.create_group("Launch", [GROUP])
    assert evo.requests == []


@pytest.mark.anyio
async def test_create_group_on_read_timeout_is_uncertain_and_rereads_groups_with_that_subject(evo, bound):
    evo.fail("POST", _path("create"), httpx2.ReadTimeout("no answer"))
    evo.on(
        "GET",
        _path("fetchAllGroups"),
        json=[
            _group_row("120363000000000001@g.us", "Other"),
            _group_row("120363000000000009@g.us", "Launch", size=2),
        ],
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await groups.create_group("Launch", ["393331234567"])

    message = str(caught.value)
    assert message.startswith("UNCERTAIN: Evolution did not confirm the result")
    assert 'Verified state: [{"group_id": "120363000000000009@g.us", "subject": "Launch", "size": 2}]' in message
    assert "Do NOT repeat the call" in message
    assert evo.last("GET", _path("fetchAllGroups")).params == {"getParticipants": "false"}


@pytest.mark.anyio
async def test_create_group_refusal_says_nothing_was_changed(evo, bound):
    evo.on(
        "POST",
        _path("create"),
        status=400,
        json={"status": 400, "error": "Bad Request", "response": {"message": ["Subject is invalid"]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError, match=r"refused the request \(400\).*Nothing was changed"):
        await groups.create_group("Launch", ["393331234567"])


# --- subject, description, picture -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_update_group_subject(evo, bound):
    evo.on("POST", _path("updateGroupSubject"), status=201, json={"update": "success"})
    with bound(evo):
        result = json.loads(await groups.update_group_subject(GROUP_DIGITS, "New name"))

    assert evo.last("POST", _path("updateGroupSubject")).json == {"groupJid": GROUP, "subject": "New name"}
    assert result == {"group_id": GROUP, "updated": "subject"}


@pytest.mark.anyio
async def test_update_group_description(evo, bound):
    evo.on("POST", _path("updateGroupDescription"), status=201, json={"update": "success"})
    with bound(evo):
        result = json.loads(await groups.update_group_description(GROUP, "Fresh description"))

    assert evo.last("POST", _path("updateGroupDescription")).json == {
        "groupJid": GROUP,
        "description": "Fresh description",
    }
    assert result == {"group_id": GROUP, "updated": "description"}


@pytest.mark.anyio
async def test_update_group_picture_sends_the_url_as_image(evo, bound):
    evo.on("POST", _path("updateGroupPicture"), status=201, json={"update": "success"})
    with bound(evo):
        result = json.loads(await groups.update_group_picture(GROUP, "https://cdn.example/logo.png"))

    assert evo.last("POST", _path("updateGroupPicture")).json == {
        "groupJid": GROUP,
        "image": "https://cdn.example/logo.png",
    }
    assert result == {"group_id": GROUP, "updated": "picture"}


@pytest.mark.anyio
async def test_update_group_subject_when_evolution_answers_500_is_uncertain(evo, bound):
    evo.on(
        "POST",
        _path("updateGroupSubject"),
        status=500,
        json={"status": 500, "error": "Internal Server Error", "response": {"message": "Error updating group subject"}},
    )
    with bound(evo), pytest.raises(ToolExecutionError, match="UNCERTAIN.*HTTP 500.*Do NOT repeat"):
        await groups.update_group_subject(GROUP, "New name")


# --- update_group_settings -------------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kwargs", "actions", "applied"),
    [
        ({"only_admins_send": True}, ["announcement"], {"only_admins_send": True}),
        ({"only_admins_send": False}, ["not_announcement"], {"only_admins_send": False}),
        ({"only_admins_edit_info": True}, ["locked"], {"only_admins_edit_info": True}),
        ({"only_admins_edit_info": False}, ["unlocked"], {"only_admins_edit_info": False}),
        (
            {"only_admins_send": True, "only_admins_edit_info": False},
            ["announcement", "unlocked"],
            {"only_admins_send": True, "only_admins_edit_info": False},
        ),
    ],
)
async def test_update_group_settings_maps_each_flag_to_its_evolution_action(evo, bound, kwargs, actions, applied):
    evo.on("POST", _path("updateSetting"), status=201, json={"updateSetting": None})
    with bound(evo):
        result = json.loads(await groups.update_group_settings(GROUP, **kwargs))

    sent = [r.json for r in evo.requests if r.path == _path("updateSetting")]
    assert sent == [{"groupJid": GROUP, "action": action} for action in actions]
    assert result == {"group_id": GROUP, **applied}


@pytest.mark.anyio
async def test_update_group_settings_needs_at_least_one_setting(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="Give at least one of only_admins_send"):
        await groups.update_group_settings(GROUP)
    assert evo.requests == []


@pytest.mark.anyio
async def test_update_group_settings_tells_when_the_first_setting_was_applied_before_the_second_failed(evo, bound):
    def handler(request):
        if request.json["action"] == "announcement":
            return 201, {"updateSetting": None}
        return 400, {"status": 400, "response": {"message": ["Error updating setting"]}}

    evo.on("POST", _path("updateSetting"), handler=handler)
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await groups.update_group_settings(GROUP, only_admins_send=True, only_admins_edit_info=True)

    message = str(caught.value)
    assert "Error updating setting" in message
    assert "Already applied before this failure: only_admins_send=True." in message


@pytest.mark.anyio
async def test_update_group_settings_first_failure_is_a_plain_refusal(evo, bound):
    evo.on(
        "POST",
        _path("updateSetting"),
        status=400,
        json={"status": 400, "response": {"message": ["Error updating setting"]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await groups.update_group_settings(GROUP, only_admins_send=True)

    assert "Already applied" not in str(caught.value)
    assert "Nothing was changed" in str(caught.value)


# --- set_disappearing_messages ---------------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(("duration", "expiration"), [("off", 0), ("24h", 86400), ("7d", 604800), ("90d", 7776000)])
async def test_set_disappearing_messages_maps_durations_to_seconds(evo, bound, duration, expiration):
    evo.on("POST", _path("toggleEphemeral"), status=201, json={"success": True})
    with bound(evo):
        result = json.loads(await groups.set_disappearing_messages(GROUP, duration))

    assert evo.last("POST", _path("toggleEphemeral")).json == {"groupJid": GROUP, "expiration": expiration}
    assert result == {"group_id": GROUP, "duration": duration}


# --- participants ----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_group_participants_maps_every_status_code(evo, bound):
    evo.on(
        "POST",
        _path("updateParticipant"),
        status=201,
        json={
            "updateParticipants": [
                {"status": "200", "jid": "393331234567@s.whatsapp.net", "content": {"tag": "participant"}},
                {"status": "403", "jid": "392220002222@s.whatsapp.net"},
                {"status": "408", "jid": "393330003333@s.whatsapp.net"},
                {"status": "409", "jid": "394440004444@s.whatsapp.net"},
                {"status": "500", "jid": "395550005555@s.whatsapp.net"},
                {"status": "404", "jid": "77777@lid"},
            ]
        },
    )
    with bound(evo):
        result = json.loads(
            await groups.add_group_participants(
                GROUP,
                [
                    "+39 333 123 4567",
                    "392220002222",
                    "393330003333",
                    "394440004444",
                    "395550005555",
                    "393331234567",
                ],
            )
        )

    assert evo.last("POST", _path("updateParticipant")).json == {
        "groupJid": GROUP,
        "action": "add",
        "participants": ["393331234567", "392220002222", "393330003333", "394440004444", "395550005555"],
    }
    assert result == {
        "group_id": GROUP,
        "action": "add",
        "results": [
            {"phone": "393331234567", "status_code": 200, "status": "done"},
            {"phone": "392220002222", "status_code": 403, "status": "needs an invite (privacy settings)"},
            {"phone": "393330003333", "status_code": 408, "status": "left recently"},
            {"phone": "394440004444", "status_code": 409, "status": "already in group"},
            {"phone": "395550005555", "status_code": 500, "status": "failed"},
            {"phone": "77777@lid", "status_code": 404, "status": "failed"},
        ],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call", "action"),
    [
        (lambda: groups.remove_group_participants(GROUP, ["393331234567"]), "remove"),
        (lambda: groups.update_group_admins(GROUP, ["393331234567"], "promote"), "promote"),
        (lambda: groups.update_group_admins(GROUP, ["393331234567"], "demote"), "demote"),
    ],
)
async def test_membership_tools_send_their_action(evo, bound, call, action):
    evo.on(
        "POST",
        _path("updateParticipant"),
        status=201,
        json={"updateParticipants": [{"status": "200", "jid": "393331234567@s.whatsapp.net"}]},
    )
    with bound(evo):
        result = json.loads(await call())

    assert evo.last("POST", _path("updateParticipant")).json == {
        "groupJid": GROUP,
        "action": action,
        "participants": ["393331234567"],
    }
    assert result == {
        "group_id": GROUP,
        "action": action,
        "results": [{"phone": "393331234567", "status_code": 200, "status": "done"}],
    }


@pytest.mark.anyio
async def test_participant_update_with_no_result_rows_reports_an_empty_list(evo, bound):
    evo.on("POST", _path("updateParticipant"), status=201, json={"updateParticipants": []})
    with bound(evo):
        result = json.loads(await groups.remove_group_participants(GROUP, ["393331234567"]))

    assert result["results"] == []


@pytest.mark.anyio
async def test_participant_tools_refuse_group_ids_as_participants(evo, bound):
    with bound(evo), pytest.raises(ToolExecutionError, match="is not a phone number"):
        await groups.add_group_participants(GROUP, ["120363000000000001@g.us"])
    assert evo.requests == []


@pytest.mark.anyio
async def test_participant_update_bad_request_is_a_refusal_with_evolutions_reason(evo, bound):
    evo.on(
        "POST",
        _path("updateParticipant"),
        status=400,
        json={"status": 400, "error": "Bad Request", "response": {"message": ["Error updating participants"]}},
    )
    with bound(evo), pytest.raises(ToolExecutionError) as caught:
        await groups.update_group_admins(GROUP, ["393331234567"], "promote")

    assert str(caught.value) == "Evolution refused the request (400): Error updating participants. Nothing was changed."


# --- leave_group -----------------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_leave_group_uses_delete_with_the_group_in_the_query(evo, bound):
    evo.on("DELETE", _path("leaveGroup"), json={"groupJid": GROUP, "leave": True})
    with bound(evo):
        result = json.loads(await groups.leave_group(GROUP_DIGITS))

    request = evo.last("DELETE", _path("leaveGroup"))
    assert request.params == {"groupJid": GROUP}
    assert request.json is None
    assert result == {"left": True, "group_id": GROUP}
