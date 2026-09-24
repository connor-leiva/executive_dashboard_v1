"""Re-saving a connection must not destroy the workspace's configuration for that source.

On 2026-09-24 the ULRG Recruiting sync failed, Connor did the reasonable thing -- opened Settings
and re-saved the connection to clear the error -- and the connect form replaced `integration.config`
wholesale. The pipeline id and the whole stage-group mapping went with it. The next sync reported
"ok, 0 records, 0.0 seconds" and the tab went on saying the first sync had not finished, so nothing
anywhere said a configuration had just been deleted.

The form had kept exactly one key, `fub_state`, added after FUB was bitten by the same thing. That
is the shape of the bug: an allow-list of what to preserve, one entry per past incident, never
updated by the feature that adds the next key. These tests are therefore written to be INCAPABLE of
that -- they assert over arbitrary keys the production code has never heard of, so a feature added
next year is covered without anybody remembering this file exists.
"""
import re
from pathlib import Path

from app.routers.integrations import _ACCOUNT_KEY, _merge_config


class _Integ:
    """Just enough Integration to carry a config."""
    def __init__(self, config):
        self.config = config


def test_keys_the_form_does_not_send_survive_a_re_save():
    """The bug itself. The key names here are deliberately invented: the rule is about ownership,
    not about any particular feature's settings."""
    stored = {"location_id": "loc_1", "pipeline_id": "pipe_1",
              "recruiting_stage_groups": [["Met", "team_leader", ["st_1"]]],
              "some_future_feature_setting": {"deeply": ["nested", 1, None]},
              "another_one_nobody_has_written_yet": "keep me"}
    out = _merge_config(_Integ(dict(stored)), "ghl_recruiting", {"location_id": "loc_1"})
    for key, value in stored.items():
        assert key in out, f"re-saving the connection dropped {key!r}"
        assert out[key] == value, f"re-saving the connection changed {key!r}"


def test_the_form_still_wins_for_the_keys_it_sends():
    """Preserving everything must not make the form read-only: what it sends is what it owns."""
    out = _merge_config(_Integ({"location_id": "old", "pipeline_id": "pipe_1"}),
                        "ghl_recruiting", {"location_id": "old", "base_url": "https://x"})
    assert out["base_url"] == "https://x"
    assert out["pipeline_id"] == "pipe_1"

    out = _merge_config(_Integ({"base_url": "https://old"}), "fub", {"base_url": "https://new"})
    assert out["base_url"] == "https://new"


def test_a_different_account_does_not_inherit_the_old_mapping():
    """The one thing the old code was right about. A mapping names stage ids, pipeline ids and
    calendar ids that exist in ONE location; pointing it at another location is worse than blank,
    because it looks configured."""
    stored = {"location_id": "loc_1", "pipeline_id": "pipe_1", "recruiting_stage_groups": [["Met"]]}
    out = _merge_config(_Integ(dict(stored)), "ghl_recruiting", {"location_id": "loc_2"})
    assert out == {"location_id": "loc_2"}, "a new location kept the old location's mapping"

    # ...but only when it really changed, and only when we can tell.
    same = _merge_config(_Integ(dict(stored)), "ghl_recruiting", {"location_id": "loc_1"})
    assert same["pipeline_id"] == "pipe_1"
    unsaid = _merge_config(_Integ(dict(stored)), "ghl_recruiting", {"gci_field_id": "f1"})
    assert unsaid["pipeline_id"] == "pipe_1", "a save that never mentioned the location reset it"


def test_fub_state_is_kept_without_being_named():
    """FUB's sync bookkeeping used to be preserved by name. It must still survive -- now because
    it is ordinary config, not because it is on a list."""
    out = _merge_config(_Integ({"fub_state": {"last_run": {"people": 130000}}}), "fub",
                        {"base_url": "https://api.fub.com"})
    assert out["fub_state"] == {"last_run": {"people": 130000}}


def test_connecting_something_for_the_first_time_is_just_the_form():
    assert _merge_config(None, "ghl", {"location_id": "loc_1"}) == {"location_id": "loc_1"}
    assert _merge_config(_Integ(None), "sisu", {"lender_names": ["a"]}) == {"lender_names": ["a"]}


def test_every_provider_with_an_account_key_uses_the_key_it_is_identified_by():
    """A provider in the reset table has to name a key its form actually sends, or the reset
    never fires and a re-point silently inherits the old mapping."""
    src = Path(__file__).resolve().parents[1] / "app" / "routers" / "integrations.py"
    text = src.read_text(encoding="utf-8")
    for provider, key in _ACCOUNT_KEY.items():
        assert key == "location_id", f"{provider}: unexpected account key {key!r}"
        assert f'"{provider}"' in text


def test_the_connect_endpoint_never_replaces_config_outright():
    """The guard. Three call sites had this bug and were fixed together; a fourth added later
    must not reintroduce it, and nobody will remember to re-read this file."""
    src = Path(__file__).resolve().parents[1] / "app" / "routers" / "integrations.py"
    code = "\n".join(l for l in src.read_text(encoding="utf-8").splitlines()
                     if not l.strip().startswith("#"))
    rhs = [m.strip() for m in re.findall(r"\.config\s*=\s*(.+)", code)]
    # A local dict built up and assigned (`cfg = dict(integ.config or {}) ... = cfg`) is fine;
    # taking the request body, or a dict literal built from it, is not.
    offenders = [r for r in rhs
                 if ("body" in r or "incoming" in r) and not r.startswith("_merge_config(")]
    assert not offenders, f"config assigned straight from the request body: {offenders}"
