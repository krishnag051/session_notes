"""Fix Round (2026-10-05), "non-determinism fix": the backend's review-
result cache (Issue 2 of that round) must never serve a stale verdict after
rules.json or any rule-checking/prompt module changes -- pipeline_version_
fingerprint() is the one function the backend calls to detect that. These
tests confirm it's stable for unchanged inputs and actually reacts to a real
change in each covered source, using a real temp copy of each file rather
than asserting against the hash algorithm in the abstract.
"""
import hashlib
from pathlib import Path

from ..pipeline import api as api_module


def test_fingerprint_is_stable_across_repeated_calls():
    assert api_module.pipeline_version_fingerprint() == api_module.pipeline_version_fingerprint()


def test_fingerprint_changes_when_rules_json_bytes_change(tmp_path, monkeypatch):
    real_bytes = api_module.RULES_PATH.read_bytes()
    before = api_module.pipeline_version_fingerprint()

    fake_rules_path = tmp_path / "rules.json"
    fake_rules_path.write_bytes(real_bytes + b" ")  # one real byte different, nothing else touched
    monkeypatch.setattr(api_module, "RULES_PATH", fake_rules_path)

    after = api_module.pipeline_version_fingerprint()
    assert before != after


def test_fingerprint_changes_when_a_fingerprinted_module_file_changes(tmp_path, monkeypatch):
    before = api_module.pipeline_version_fingerprint()

    real_fields_path = Path(api_module.fields_module.__file__)
    fake_fields_path = tmp_path / "fields.py"
    fake_fields_path.write_bytes(real_fields_path.read_bytes() + b"\n# a real code change\n")
    patched_paths = [
        fake_fields_path if p == real_fields_path else p for p in api_module._FINGERPRINTED_MODULE_PATHS
    ]
    monkeypatch.setattr(api_module, "_FINGERPRINTED_MODULE_PATHS", patched_paths)

    after = api_module.pipeline_version_fingerprint()
    assert before != after


def test_fingerprint_is_a_sha256_hex_digest():
    value = api_module.pipeline_version_fingerprint()
    assert len(value) == 64
    int(value, 16)  # raises if not valid hex
