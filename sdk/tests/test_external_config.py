"""The configuration an external run is given: what it accepts, and what it will not read.

`EM3C-E5-CLASSIFY-0001` is why this file exists. The fifth external run was told where the gateway
was through a shell, and a shell had opinions. So the values live in a document now, and a
document can be checked -- which is what is checked here.

Two habits run through it. Nothing is defaulted: a configuration missing a field is refused rather
than completed, because a value nobody wrote is a value nobody decided. And nothing is ignored: a
field this version does not know is refused rather than skipped, because a setting that is quietly
dropped is one whose author believes it took effect.
"""
from __future__ import annotations

import json

import pytest

from agentnode_sdk.verification import config


def a_document(**changes) -> dict:
    document = {
        "version": config.CONFIG_VERSION,
        "client_home": "C:/Users/x/em3c",
        "agentnode": "C:/Users/x/em3c/venv/Scripts/agentnode.exe",
        "work": "C:/Users/x/em3c/work",
        "evidence": "C:/Users/x/em3c/record.jsonl",
        "ssh_key": "C:/Users/x/.ssh/em3c",
        "server": "root@a-gateway-machine",
        "gateway_bin": "/home/em3ce1/venv/bin/agentnode",
        "gateway_state": "/home/em3ce1/em3c-state-e6",
        "gateway_log": "/home/em3ce1/gateway.log",
        "gateway_user": "a-service-account",
        "gateway_port": "8099",
    }
    document.update(changes)
    return document


class TestARemotePathIsOne:
    """Every refusal here is a shape a Linux path cannot have, and most of them are shapes a
    Linux path acquires when something converts it."""

    @pytest.mark.parametrize("value,because", [
        ("C:/Program Files/Git/home/em3ce1/em3c-state-e5", "drive letter"),   # the real E5 value
        ("C:/home/em3ce1/state", "drive letter"),
        ("c:/home/em3ce1/state", "drive letter"),
        ("C:" + chr(92) + "home", "backslash"),
        (chr(92) * 2 + "server" + chr(92) + "share", "backslash"),
        ("/home/em3ce1/state" + chr(92) + "sub", "backslash"),
        ("//server/share/state", "UNC"),
        ("home/em3ce1/state", "relative"),
        ("./state", "relative"),
        ("", "empty"),
        (" /home/em3ce1/state", "space around it"),
        ("/home/em3ce1/state ", "space around it"),
        ("/home/../etc/shadow", "climbs out of itself"),
        ("/home/em3ce1/state/", "normal form"),
        ("/home//em3ce1/state", "normal form"),
        ("/home/./em3ce1/state", "normal form"),
        ("/usr/bin/home/em3ce1", "/usr/bin/"),
        ("/mingw64/home/em3ce1", "/mingw64/"),
        ("/Program Files/Git/home/x", "program files/git/"),
        ("/home/em3ce1/sta" + chr(10) + "te", "control character"),
        ("/home/em3ce1/sta" + chr(0) + "te", "control character"),
    ])
    def test_what_a_far_path_may_not_look_like(self, value, because):
        with pytest.raises(config.ConfigError) as caught:
            config.check_remote_path("gateway_state", value)
        assert because in str(caught.value)

    @pytest.mark.parametrize("value", [
        "/home/em3ce1/em3c-state-e6",
        "/home/em3ce1/venv/bin/agentnode",
        "/home/em3ce1/gateway.log",
        "/opt/agentnode/state",
        "/srv/a name with spaces/state",
        "/",
    ])
    def test_and_what_one_does_look_like(self, value):
        assert config.check_remote_path("gateway_state", value) == value

    def test_a_path_that_is_not_text_is_not_a_path(self):
        for value in (None, 12, ["/home/x"], {"p": "/home/x"}):
            with pytest.raises(config.ConfigError) as caught:
                config.check_remote_path("gateway_state", value)
            assert "a path is text" in str(caught.value)

    def test_the_refusal_says_which_field_and_what_it_saw(self):
        """A refusal a person cannot act on costs the same as no refusal."""
        with pytest.raises(config.ConfigError) as caught:
            config.check_remote_path("gateway_bin", "C:/Program Files/Git/home/x")
        message = str(caught.value)
        assert "gateway_bin" in message and "C:/Program Files/Git/home/x" in message


class TestTheDocumentIsClosed:

    def test_a_good_one_is_read(self):
        settings = config.parse(a_document())
        assert settings.gateway_state == "/home/em3ce1/em3c-state-e6"
        assert settings.version == config.CONFIG_VERSION

    def test_a_field_this_version_does_not_know_is_refused(self):
        with pytest.raises(config.ConfigError) as caught:
            config.parse(a_document(gateway_timeout="30"))
        assert "gateway_timeout" in str(caught.value)
        assert "believes it took effect" in str(caught.value)

    def test_several_unknown_fields_are_all_named(self):
        with pytest.raises(config.ConfigError) as caught:
            config.parse(a_document(one="1", two="2"))
        assert "'one'" in str(caught.value) and "'two'" in str(caught.value)

    def test_a_missing_field_is_refused_rather_than_defaulted(self):
        for name in config.FIELD_NAMES:
            document = a_document()
            document.pop(name)
            with pytest.raises(config.ConfigError) as caught:
                config.parse(document)
            assert name in str(caught.value)

    def test_a_field_of_the_wrong_type_is_refused(self):
        with pytest.raises(config.ConfigError) as caught:
            config.parse(a_document(gateway_port=8099))
        assert "int" in str(caught.value) and "str" in str(caught.value)
        with pytest.raises(config.ConfigError):
            config.parse(a_document(version="1"))

    def test_true_is_not_a_version(self):
        """`True == 1` in Python, and a schema that believes that is one field wide open."""
        with pytest.raises(config.ConfigError) as caught:
            config.parse(a_document(version=True))
        assert "bool" in str(caught.value)

    def test_another_version_is_refused_rather_than_guessed_at(self):
        with pytest.raises(config.ConfigError) as caught:
            config.parse(a_document(version=config.CONFIG_VERSION + 1))
        assert "refused rather than guessed" in str(caught.value)

    def test_a_document_that_is_not_one(self):
        for value in ([], "a string", 3, None):
            with pytest.raises(config.ConfigError):
                config.parse(value)

    def test_an_empty_local_setting_is_refused(self):
        for name in ("server", "gateway_user", "gateway_port", "agentnode", "ssh_key"):
            with pytest.raises(config.ConfigError) as caught:
                config.parse(a_document(**{name: "   "}))
            assert name in str(caught.value)

    def test_the_schema_is_the_dataclass(self):
        """One list of fields, so a field cannot be added to the reader and not to the check."""
        assert set(config.FIELD_NAMES) == set(a_document())
        assert set(config.REMOTE_PATHS) <= set(config.FIELD_NAMES)


class TestReadingItFromDisk:

    def written(self, tmp_path, text) -> str:
        path = tmp_path / "run-config.json"
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
        return str(path)

    def test_a_field_written_twice_is_refused(self, tmp_path):
        """JSON's own rule is last-one-wins, silently. Which of the two was meant is not
        something a reader can decide, so it does not decide it."""
        body = json.dumps(a_document())
        path = self.written(
            tmp_path, body[:-1] + ', "gateway_state": "/home/em3ce1/somewhere-else"}')
        with pytest.raises(config.ConfigError) as caught:
            config.load(path, environ={})
        assert "more than once" in str(caught.value)

    def test_something_that_is_not_json(self, tmp_path):
        path = self.written(tmp_path, "gateway_state = /home/x")
        with pytest.raises(config.ConfigError) as caught:
            config.load(path, environ={})
        assert "not readable as JSON" in str(caught.value)

    def test_something_that_is_not_utf8(self, tmp_path):
        path = self.written(tmp_path, b'{"server": "\xff\xfe"}')
        with pytest.raises(config.ConfigError) as caught:
            config.load(path, environ={})
        assert "not UTF-8" in str(caught.value)

    def test_a_file_that_is_not_there(self, tmp_path):
        with pytest.raises(config.ConfigError) as caught:
            config.load(str(tmp_path / "nothing.json"), environ={})
        assert "could not be read" in str(caught.value)

    def test_the_file_that_was_meant(self, tmp_path):
        path = self.written(tmp_path, json.dumps(a_document()))
        digest = config.digest_of(config.parse(a_document()))
        assert config.load(path, digest, environ={}).gateway_state.endswith("e6")

    def test_and_a_file_that_is_not(self, tmp_path):
        """A path names a file; a digest names its contents. This is why the launcher passes
        both -- the path can survive a boundary the contents could not."""
        digest = config.digest_of(config.parse(a_document()))
        path = self.written(
            tmp_path, json.dumps(a_document(gateway_state="/home/em3ce1/elsewhere")))
        with pytest.raises(config.ConfigError) as caught:
            config.load(path, digest, environ={})
        assert "not the configuration that was meant" in str(caught.value)
        assert "Nothing has run" in str(caught.value)


class TestTheDigest:

    def test_it_is_over_the_whole_configuration(self):
        base = config.digest_of(config.parse(a_document()))
        for name in config.FIELD_NAMES:
            if name == "version":
                continue
            changed = config.digest_of(config.parse(a_document(**{name: "/home/em3ce1/other"
                                                                 if name in config.REMOTE_PATHS
                                                                 else "something-else"})))
            assert changed != base, name

    def test_it_does_not_depend_on_the_order_things_were_written_in(self):
        one = a_document()
        other = {k: one[k] for k in reversed(list(one))}
        assert config.digest_of(config.parse(one)) == config.digest_of(config.parse(other))

    def test_nothing_secret_is_in_what_it_covers(self):
        """The key is named by its path, never by its contents, so the digest can be written
        into the record as it is. `SECRET_FIELDS` is empty and this is why."""
        assert config.SECRET_FIELDS == ()
        covered = config.parse(a_document()).as_dict()
        assert "ssh_key" in covered and "PRIVATE KEY" not in json.dumps(covered)


class TestTheOldWayIsRefused:

    def test_the_environment_the_fifth_run_used_stops_this_one(self):
        for name in config.SUPERSEDED_ENVIRONMENT:
            with pytest.raises(config.ConfigError) as caught:
                config.refuse_environment({name: "/home/em3ce1/state"})
            assert name in str(caught.value)

    def test_it_is_refused_and_not_merely_ignored(self):
        with pytest.raises(config.ConfigError) as caught:
            config.refuse_environment({"EM3C_GATEWAY_STATE": "/home/x"})
        assert "believes it took effect" in str(caught.value)

    def test_an_empty_one_is_not_somebody_trying(self):
        config.refuse_environment({"EM3C_GATEWAY_STATE": ""})

    def test_an_unrelated_environment_is_left_alone(self):
        config.refuse_environment({"PATH": "/usr/bin", "HOME": "/home/x"})

    def test_reading_a_configuration_refuses_the_environment_first(self, tmp_path):
        """Before the file is even opened: if somebody is still setting these, what the file says
        is not what they think is happening."""
        path = tmp_path / "run-config.json"
        path.write_text(json.dumps(a_document()), encoding="utf-8")
        with pytest.raises(config.ConfigError) as caught:
            config.load(str(path), environ={"EM3C_GATEWAY_STATE": "/home/x"})
        assert "EM3C_GATEWAY_STATE" in str(caught.value)


class TestWhatAReaderIsTold:

    def test_the_description_carries_the_three_and_the_digest(self):
        settings = config.parse(a_document())
        lines = config.describe(settings)
        joined = chr(10).join(lines)
        assert config.digest_of(settings) in joined
        for name in config.REMOTE_PATHS:
            assert getattr(settings, name) in joined
