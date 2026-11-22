import keyring
import pytest
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError(username)
        del self.store[(service, username)]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real keychain or the real org registry in tests."""
    kr = MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(kr)
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    yield kr
    keyring.set_keyring(previous)
