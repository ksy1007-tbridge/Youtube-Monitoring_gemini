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
    return lambda api_key, **kw: types.SimpleNamespace(models=models)


def test_pipeline_auto_only_and_no_double_count(monkeypatch):
    # 조희대 탄핵 3채널이 80%에 안 걸리도록 조회를 고르게
    rows = [
        ("고발뉴스TV", "조희대 탄핵 국면 분석", 40000),
        ("MBC 라디오 시사", "오세훈 2심 무죄, 거대한 파장 / ‘조희대 탄핵’ 국면에 무리수", 35000),
        ("서울의소리 VoiceOfSeoul", "국힘 “조희대 탄핵, 해볼 테면 해봐라”", 30000),
        ("스픽스", "조희대 탄핵 청원 폭주", 25000),
        ("뉴탐사 NewTamsa", "생중계 판결 뒷이야기", 3000),
        ("[팟빵] 최욱의 매불쇼", "허접한 판결을 생중계로 보여준 이유", 4000),
    ]
    df = _df(rows)
    mock = ('{"issues":[{"name":"조희대 탄핵","keywords":["조희대 탄핵"]},'
            '{"name":"생중계 판결","keywords":["생중계", "판결"]}],"persons":[]}')
    monkeypatch.setattr(main, "GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(main.genai, "Client", _fake_client(mock))
    pool = df["프레임"] != "종합방송"
    cands = main.extract_dynamic_entities(df["제목"].tolist(), df.loc[pool, "제목"].tolist(), [])
    dyn, _, absorbed = main.filter_dynamic_entities(df, cands, pool)
    assert any("조희대" in k for k in dyn)
    issues, _ = main.extract_major_issues(df, dyn, absorbed)
    by = {r["이슈명"]: r for _, r in issues.iterrows()}
    assert "조희대/사법부" in by
    assert by["조희대/사법부"]["건수"] >= 3
    # 생중계 판결은 2건 2채널이라 제목 3건 하한으로 탈락 (고조회 면제 꺼짐)
    assert "생중계 판결" not in by and "생중계 판결 (자동)" not in by
    section, _, ai, hist = main.build_issue_section(issues, {"persons": {}, "frames": {}}, "2026-10-24", dyn)
    assert "조희대/사법부" in section
    assert "조희대/사법부" in hist["days"]["2026-10-24"]
    assert "구조" not in section + ai  # 금지어 미사용 확인 (원 테스트와 동일 취지)


def test_one_video_one_issue():
    df = _df(ROWS)
    dyn = {"조희대 탄핵 (자동)": ["조희대"], "탄핵 공방 (자동)": ["탄핵"]}
    issues, _ = main.extract_major_issues(df, dyn)
    if issues is not None and not issues.empty:
        assert int(issues["건수"].sum()) <= len(df[df["프레임"] != "종합방송"])


def test_candidate_line_after_4_days():
    hist = None
    for d in ("2026-10-21", "2026-10-22", "2026-10-23"):
        hist = main.update_history(hist, d, {"오세훈 무죄": {"keywords": ["오세훈"], "cnt": 3}})
    issues = pd.DataFrame([{"이슈명": "오세훈 무죄 (자동)", "건수": 5, "채널수": 3, "조회수": 100, "mbc_tag": "",
                            "concentration": None, "is_auto": True, "merged": []}])
    section, _, _, _ = main.build_issue_section(issues, {"issue_history": hist}, "2026-10-24",
                                                {"오세훈 무죄 (자동)": ["오세훈"]})
    assert "이름 사전 별칭 추가 제안" in section and "오세훈 무죄(4일)" in section


def test_extract_without_key_makes_no_call(monkeypatch):
    monkeypatch.setattr(main, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main.genai, "Client", lambda **kw: (_ for _ in ()).throw(AssertionError("no call")))
    assert main.extract_dynamic_entities(["a"], ["a"], []) == {"issues": [], "persons": []}


# ---- 10/26 점검 반영 ----
def _df2(rows):
    df = pd.DataFrame(rows, columns=["채널명", "제목", "조회수", "길이초"])
    df["제목_정제"] = df["제목"]
    df["프레임"] = [main.classify_frame(t, d) for t, d in zip(df["제목"], df["길이초"])]
    return df


ROWS_1026 = [
    ("MBC 라디오 시사", "[엠스피드] 오세훈 판결의 의미? 장동혁 망한거죠", 4892, 900),
    ("MBC 라디오 시사", "국힘 막말 정국, 여야 대치 격화", 230000, 1500),
    ("서울의소리 VoiceOfSeoul", "장동혁 욕설 녹취 공개", 5000, 700),
    ("스픽스", "장동혁 욕설 파문 확산", 4046, 800),
    ("뉴탐사 NewTamsa", "장동혁 막말 녹취 후폭풍", 3000, 800),
    ("MBC 라디오 시사", "오세훈 2심 무죄 의미", 50000, 900),
    ("스픽스", "오세훈 무죄 이후 서울시는", 30000, 900),
    ("서울의소리 VoiceOfSeoul", "오세훈 무죄 판결 비판", 20000, 900),
    ("MBC 라디오 시사", "오세훈 무슨 직업 갖나?", 90000, 900),
    ("[팟빵] 김어준의 겸손은힘들다 뉴스공장", "[풀영상] 뉴스공장 2026년 10월 26일 월요일", 938713, 9000),
    ("스픽스", "[🔴본방송][역전의용사들] 추미애가 결국 무릎을 꿇었다?", 186262, 2596),
    ("스픽스", "[역전의용사들] 추미애가 결국 무릎을 꿇었다?", 17818, 2596),
]


def test_person_issue_requires_person_and_keyword():
    df = _df2(ROWS_1026)
    cands = {"issues": [{"name": "장동혁 욕설", "keywords": ["장동혁", "욕설", "녹취", "막말", "국힘"]},
                        {"name": "오세훈 무죄", "keywords": ["오세훈", "무죄"]}], "persons": []}
    pool = df["프레임"] != "종합방송"
    dyn, _, absorbed = main.filter_dynamic_entities(df, cands, pool)
    assert "국민의힘 장동혁 대표 욕설 (자동)" in dyn            # 직함 보정
    assert main.AUTO_ISSUE_MUST["국민의힘 장동혁 대표 욕설 (자동)"] == ["장동혁"]
    issues, unc = main.extract_major_issues(df, dyn, absorbed)
    by = {r["이슈명"]: r for _, r in issues.iterrows()}
    jang = by["국민의힘 장동혁 대표 욕설"]
    assert jang["건수"] == 3 and jang["조회수"] == 12046            # '국힘 막말 정국'(MBC 23만)은 장동혁이 없어 제외
    oh = by["오세훈 무죄"]
    assert oh["건수"] == 3                                         # '오세훈 무슨 직업' 제외 (오세훈 AND 무죄)
    assert "뉴스공장" not in " ".join(unc["제목"])                    # 미분류에서 종합방송 제외
    assert "오세훈 무슨 직업 갖나?" in list(unc["제목"])


def test_sensational_auto_issue_dropped():
    df = _df2(ROWS_1026)
    cands = {"issues": [{"name": "추미애 무릎", "keywords": ["추미애", "무릎"]}], "persons": []}
    dyn, _, _ = main.filter_dynamic_entities(df, cands, None)
    assert dyn == {}


def test_dedupe_live_and_reupload():
    df = _df2(ROWS_1026)
    out, log = main.dedupe_reuploads(df)
    assert len(out) == len(df) - 1 and log[0][2] == 17818
    assert 186262 in out["조회수"].values and 17818 not in out["조회수"].values
    # 길이가 다르면 다른 영상
    df2 = _df2([("스픽스", "[🔴본방송] A 방송", 10, 600), ("스픽스", "A 방송", 5, 900)])
    assert len(main.dedupe_reuploads(df2)[0]) == 2


def test_high_share_goes_internal_not_dropped(capsys):
    rows = [("스픽스", f"쟁점X {i}", 100000, 900) for i in range(4)] + [("MBC 라디오 시사", "쟁점X 반론", 1000, 900),
            ("MBC 라디오 시사", "쟁점Y 1", 2000, 900), ("서울의소리 VoiceOfSeoul", "쟁점Y 2", 2000, 900),
            ("스픽스", "쟁점Y 3", 2000, 900)]
    df = _df2(rows)
    main.AUTO_ISSUE_MUST.clear()
    issues, _ = main.extract_major_issues(df, {"쟁점X (자동)": ["쟁점X"], "쟁점Y (자동)": ["쟁점Y"]})
    names = list(issues["이슈명"]) if issues is not None and not issues.empty else []
    assert any("쟁점X" in n for n in names)  # 99.8%라도 탈락하지 않음
    assert any("쟁점Y" in n for n in names)
    rowx = issues[issues["이슈명"].astype(str).str.contains("쟁점X")].iloc[0]
    share = (rowx.get("concentration") or {}).get("top_share") if isinstance(rowx.get("concentration"), dict) else None
    assert share is None or share >= 80
    log = capsys.readouterr().out
    assert "내부·편중" in log and "쟁점X" in log
    assert "80% 규칙만으로 탈락" not in log


def test_choo_oh_are_fixed_persons():
    assert main.TRACK_PERSONS["추미애"] == ["추미애"] and main.TRACK_PERSONS["오세훈"] == ["오세훈"]


def test_trump_only_added_to_track_persons():
    assert "트럼프" in main.TRACK_PERSONS
    assert "Trump" in main.TRACK_PERSONS["트럼프"]
    assert "한덕수" not in main.TRACK_PERSONS
    assert "윤석열" not in main.TRACK_PERSONS
