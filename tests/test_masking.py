import copy

from copilot.privacy.masking import mask_users

USERS = [
    {
        "id": "4150868000000225013",
        "full_name": "Jane Fake",
        "email": "jane@example.com",
        "phone": "+1 555 0100",
        "mobile": "+1 555 0101",
        "zuid": "123456",
        "status": "active",
        "confirm": True,
        "role": {"id": "r1", "name": "CEO", "extra": "x"},
        "profile": {"id": "p1", "name": "Administrator", "extra": "y"},
    },
    {"id": "4150868000000225014", "full_name": "Bob Fake", "email": "bob@example.com"},
]


def test_only_allowlisted_keys_survive():
    out = mask_users(USERS)
    assert out[0] == {
        "alias": "user_1",
        "id": "4150868000000225013",
        "status": "active",
        "confirm": True,
        "role": {"id": "r1", "name": "CEO"},
        "profile": {"id": "p1", "name": "Administrator"},
    }


def test_missing_keys_are_absent_not_none():
    out = mask_users(USERS)
    assert out[1] == {"alias": "user_2", "id": "4150868000000225014"}


def test_no_pii_anywhere_in_output():
    text = repr(mask_users(USERS))
    for leaked in ("Jane", "Bob", "example.com", "555", "123456"):
        assert leaked not in text


def test_input_not_mutated():
    before = copy.deepcopy(USERS)
    mask_users(USERS)
    assert before == USERS


def test_empty_list():
    assert mask_users([]) == []
