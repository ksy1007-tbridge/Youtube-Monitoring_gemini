"""11/10: google-genai 3.x 기본 API 버전 v1 전환 대비 — 모든 Client가 v1beta를 명시하는지 (오프라인)."""
import os
import re
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main  # noqa: E402

SRC = open(os.path.join(os.path.dirname(main.__file__), "main.py"), encoding="utf-8").read()


def test_every_client_pins_v1beta():
    calls = re.findall(r"genai\.Client\((.*)\)", SRC)
    assert calls and all('api_version="v1beta"' in c for c in calls), calls


def test_extract_dynamic_entities_passes_v1beta(monkeypatch):
    seen = {}
    resp = types.SimpleNamespace(text='{"issues":[],"persons":[]}', usage_metadata=None)

    def _client(api_key, **kw):
        seen.update(kw)
        return types.SimpleNamespace(models=types.SimpleNamespace(generate_content=lambda **k: resp))
    monkeypatch.setattr(main, "GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(main.genai, "Client", _client)
    main.extract_dynamic_entities(["제목 하나"], [], [])
    assert seen["http_options"].api_version == "v1beta"


def test_requirements_cap_below_3():
    req = open(os.path.join(os.path.dirname(main.__file__), "requirements.txt"), encoding="utf-8").read()
    assert re.search(r"^google-genai<3\s*$", req, re.M)
