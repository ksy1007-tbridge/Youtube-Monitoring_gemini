"""여권 리포트 '주요 이슈 언급' 하이브리드 (오프라인: Gemini 호출 모킹, 전송 없음)."""
import os
import sys
import types

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main  # noqa: E402


def _df(rows):
    df = pd.DataFrame(rows, columns=["채널명", "제목", "조회수"])
    df["제목_정제"] = df["제목"]
    df["프레임"] = [main.classify_frame(t, 600) for t in df["제목"]]
    return df


ROWS = [
    ("[팟빵] 최욱의 매불쇼", "허접한 판결을 생중계로 보여준 이유", 1040000),
    ("고발뉴스TV", "팀김어준 탄핵집회 10만 동원? 조희대, 추미애 잡을 비책있다", 125490),
    ("MBC 라디오 시사", "오세훈 2심 무죄, 거대한 파장 / ‘조희대 탄핵’ 국면에 무리수", 61813),
    ("서울의소리 VoiceOfSeoul", "국힘 “조희대 탄핵, 해볼 테면 해봐라”", 9615),
    ("스픽스", "조희대 탄핵 청원 폭주", 5000),
    ("뉴탐사 NewTamsa", "생중계 판결 뒷이야기", 3000),
]


def _fake_client(payload):
    resp = types.SimpleNamespace(text=payload, usage_metadata=None)
    models = types.SimpleNamespace(generate_content=lambda **kw: resp)
    return lambda api_key: types.SimpleNamespace(models=models)


def test_pipeline_merges_and_never_double_counts(monkeypatch):
    df = _df(ROWS)
    mock = ('{"issues":[{"name":"조희대 탄핵","keywords":["조희대 탄핵"]},'
            '{"name":"생중계 판결","keywords":["생중계", "판결"]}],"persons":[]}')
    monkeypatch.setattr(main, "GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(main.genai, "Client", _fake_client(mock))
    pool = (~main.fixed_issue_mask(df) | (df["프레임"] == "기타")) & (df["프레임"] != "종합방송")
    cands = main.extract_dynamic_entities(df["제목"].tolist(), df.loc[pool, "제목"].tolist(), [ROWS[0][1]])
    dyn, _, absorbed = main.filter_dynamic_entities(df, cands, pool)
    assert "조희대 탄핵 (자동)" not in dyn and "조희대 탄핵 (자동)" in absorbed["조희대/사법부"]
    assert "생중계 판결 (자동)" in dyn  # 1건이지만 고조회(50만+) 미분류
    issues, _ = main.extract_major_issues(df, {**main.ISSUE_KEYWORDS, **dyn}, absorbed)
    by = {r["이슈명"]: r for _, r in issues.iterrows()}
    assert by["조희대/사법부"]["건수"] == 4 and by["조희대/사법부"]["merged"] == ["조희대 탄핵"]
    assert by["생중계 판결 (자동)"]["건수"] == 2 and by["생중계 판결 (자동)"]["조회수"] == 1043000
    section, _, ai, hist = main.build_issue_section(issues, {"persons": {}, "frames": {}}, "2026-10-24", {**main.ISSUE_KEYWORDS, **dyn})
    assert "(통합: 조희대 탄핵)" in section and "생중계 판결" in hist["days"]["2026-10-24"]
    assert "구조" not in section + ai


def test_auto_does_not_recount_fixed_videos():
    df = _df(ROWS)
    dyn = {"탄핵 공방 (자동)": ["탄핵"]}
    issues, _ = main.extract_major_issues(df, {**main.ISSUE_KEYWORDS, **dyn})
    names = list(issues["이슈명"])
    assert "탄핵 공방 (자동)" not in names  # 탄핵 제목은 모두 고정 이슈가 이미 셈


def test_candidate_line_after_4_days():
    hist = None
    for d in ("2026-10-21", "2026-10-22", "2026-10-23"):
        hist = main.update_history(hist, d, {"오세훈 무죄": {"keywords": ["오세훈"], "cnt": 3}})
    issues = pd.DataFrame([{"이슈명": "오세훈 무죄 (자동)", "건수": 5, "채널수": 3, "조회수": 100, "mbc_tag": "",
                            "concentration": None, "is_auto": True, "merged": []}])
    section, _, _, _ = main.build_issue_section(issues, {"issue_history": hist}, "2026-10-24",
                                                {"오세훈 무죄 (자동)": ["오세훈"]})
    assert "고정 목록 후보" in section and "오세훈 무죄(4일)" in section


def test_extract_without_key_makes_no_call(monkeypatch):
    monkeypatch.setattr(main, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main.genai, "Client", lambda **kw: (_ for _ in ()).throw(AssertionError("no call")))
    assert main.extract_dynamic_entities(["a"], ["a"], []) == {"issues": [], "persons": []}
