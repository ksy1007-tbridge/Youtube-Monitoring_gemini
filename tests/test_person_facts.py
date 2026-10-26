"""person_facts 여야·직함 점검기 오프라인 테스트 (API 호출 없음).
실행: python tests/test_person_facts.py  또는  pytest tests/test_person_facts.py"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from person_facts import (audit_party_camp, audit_person_titles, load_person_facts,  # noqa: E402
                          format_audit_footer, append_footer_to_chunks, TELEGRAM_MAX_LEN,
                          audit_missing_titles, apply_preferred_title)

FACTS = load_person_facts()


def _q(fn, text):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(text, "test", FACTS)


def _any(text):
    return _q(audit_party_camp, text) + _q(audit_person_titles, text)


CAMP_WARN = ["더불어민주당 등 야권에서 조희대 탄핵 추진", "여당 국민의힘 지도부", "야권 조국혁신당", "여권 개혁신당",
             "야당인 더불어민주당이 반발", "조국혁신당 등 야당", "국민의힘 등 여권 인사들", "민주당 등 야당은 반발",
             "국힘 등 여권", "더불어민주당은 야당으로서 견제", "국민의힘을 비롯한 여권", "여당 조국혁신당", "야당 개혁신당",
             "개혁신당 등 여권", "여권인 국민의힘",
             # 신규: 범여권
             "범여권 국민의힘", "개혁신당 등 범여권"]
CAMP_OK = ["여권 내 비판이 거세다", "추미애 경기도지사는 같은 여권 내에서도 비판이 비등하다", "국민의힘 등 야권", "조국혁신당 등 여권",
           "개혁신당 등 야권", "여당인 더불어민주당", "더불어민주당 등 여권", "더불어민주당과 야당", "더불어민주당이 야당을 비판",
           "야권의 더불어민주당 비판", "여당 더불어민주당", "야당 국민의힘 최고위원", "더불어민주당 야당 시절 발언",
           "국민의힘 여당 독주 비판", "민주당과 국민의힘, 여야 공방", "이재명 정부 등 여권", "여권 이재명 정부", "야권 개혁신당",
           "여권 조국혁신당", "더불어민주당. 야권은 반발",
           # 신규: 범여권
           "범여권 조국혁신당", "조국혁신당 등 범여권", "범여권인 더불어민주당"]
TITLE_WARN = ["추미애 의원", "김민석 의원", "한동훈 전 대표", "국민의힘 추미애", "추미애 전 장관", "야당 김민석", "이재명 대표",
              "국민의힘 한동훈", "더불어민주당 양향자",
              # 장동혁 = 국민의힘 대표
              "장동혁 의원", "장동혁 최고위원", "국민의힘 인사 장동혁", "국민의힘 인사인 장동혁", "장동혁 전 대표",
              "국민의힘 최고위원 장동혁 의원", "국힘 인사 장동혁", "국민의힘 최고위원 장동혁"]
TITLE_OK = ["더불어민주당 추미애 경기도지사", "추미애 경기도지사", "여권 추미애 경기도지사", "국민의힘 오세훈 서울시장", "오세훈 서울시장",
            "추미애 경기도지사는 같은 여권 내에서도 비판이 비등하다", "여당 김민석 대표", "야당 최고위원 양향자", "양향자 국민의힘 최고위원",
            "한동훈 의원", "한동훈 무소속 국회의원", "이재명 대통령", "추미애 지사", "김민석 더불어민주당 대표",
            # 장동혁 = 국민의힘 대표
            "국민의힘 장동혁 대표", "장동혁 대표", "야권 인사인 장동혁 대표", "최고위원회의를 주재한 장동혁 대표",
            "장동혁 대표와 배현진 의원", "장동혁 대표는 최고위원들과 회의했다", "국민의힘 인사인 장동혁 대표",
            "배현진 의원과 장동혁 대표", "장동혁 대표 체제의 최고위원 회의", "국민의힘 장동혁 대표, 배현진 의원", "국민의힘 대표 장동혁", "야당 최고위원 양향자", "국민의힘 최고위원 양향자"]

# 인물별 진영 (어느 점검기든 경고하면 '경고'로 본다)
PERSON_WARN = [
    "야권 인사인 추미애 경기도지사",
    "한겨레TV는 야권 인사인 추미애 경기도지사의 발언을 다뤘다",
    "야당 인사 추미애",
    "야권 소속 추미애 경기도지사",
    "야권이자 경기도지사인 추미애",  # 사이말 '인' 뒤 직함 → 창 안
    "야권 경기도지사 추미애",
    "야당 한동훈",                    # 무소속: 여당/야당(정당 표현) 해당 없음
    "야당 인사인 한동훈 국회의원",
    "여권 인사인 한동훈 국회의원",
    "범여권 인사 한동훈",
    "여당 국회의원 한동훈",
    "야권 인사인 이재명 대통령",
    "여권 인사인 오세훈 서울시장",
    "범여권 양향자",
    "여권 인사 장동혁 대표",
    "여당 장동혁 대표",
]
PERSON_OK = [
    "여권 인사인 추미애 경기도지사",
    "범여권 인사인 추미애 경기도지사",
    "여당 소속 추미애 경기도지사",
    "야권 인사인 한동훈 국회의원",
    "김어준의 겸손은힘들다는 야권 인사인 한동훈 국회의원을 다뤘다",
    "야권 한동훈",
    "범여권 조국혁신당",
    "추미애 경기도지사는 같은 여권 내에서도",
    "야권 인사인 오세훈 서울시장",
    "여권 인사 이재명 대통령",
    "야권 인사인 장동혁 대표",
    "야당 국민의힘 장동혁 대표",
    # 사이말 창이 문장·절을 넘지 않는지
    "야권은 반발했다. 추미애 경기도지사는 침묵했다",
    "야권 인사들이 모였고, 추미애 경기도지사도 언급됐다",
    "야당이 추미애 경기도지사를 비판했다",
    "야권은 추미애 경기도지사를 겨냥했다",
    "야권 인사인 한동훈 국회의원과 추미애 경기도지사",
    "야당 국민의힘은 추미애 경기도지사 발언에 반발",
    "야권 인사들은 추미애를 비판했다",
    "야당 측에서는 한동훈 국회의원 행보를 주시",
]

GROUPS = [
    ("camp warn", lambda t: _q(audit_party_camp, t), CAMP_WARN, True),
    ("camp ok", lambda t: _q(audit_party_camp, t), CAMP_OK, False),
    ("title warn", lambda t: _q(audit_person_titles, t), TITLE_WARN, True),
    ("title ok", lambda t: _q(audit_person_titles, t), TITLE_OK, False),
    ("person warn", _any, PERSON_WARN, True),
    ("person ok", _any, PERSON_OK, False),
]


def _run():
    fails, total = [], 0
    for label, fn, cases, expect in GROUPS:
        for t in cases:
            total += 1
            r = fn(t)
            if bool(r) != expect:
                fails.append((label, t, r))
    return total, fails


def test_all_cases():
    total, fails = _run()
    assert not fails, "\n".join(f"{l}: {t!r} -> {r}" for l, t, r in fails)


def test_warn_only_does_not_modify_text():
    text = "야권 인사인 추미애 경기도지사"
    _any(text)
    assert text == "야권 인사인 추미애 경기도지사"


# ---- 텔레그램 점검 꼬리말 ----
def _footer(text, **kw):
    return format_audit_footer(_q(audit_person_titles, text), _q(audit_party_camp, text), **kw)


def test_footer_single_warning():
    f = _footer("국민의힘 장동혁 의원이 발언했다")
    assert f == "⚠ 직함 점검: 장동혁 의원 → 국민의힘 대표 확인 필요"


def test_footer_multiple_dedup_cap():
    text = ("장동혁 의원. 장동혁 의원. 국민의힘 인사인 장동혁. 추미애 의원. "
            "야권 인사인 추미애 경기도지사. 야당인 더불어민주당이 반발")
    f = _footer(text)
    assert f.startswith("⚠ 직함 점검 · 여야 점검: ")
    assert f.count("장동혁 의원") == 1          # 중복 제거
    assert f.count("확인 필요") == 3 and f.endswith("외 2건")
    assert len(f) <= 300


def test_footer_none_when_no_warnings():
    assert _footer("국민의힘 장동혁 대표와 배현진 의원, 추미애 경기도지사") == ""
    assert format_audit_footer([], []) == "" and format_audit_footer(None, None) == ""


def test_footer_no_long_quote_or_secret():
    long_q = "야권 인사인 " + "가" * 200
    f = format_audit_footer(["'장동혁 의원' (현재 직함: 국민의힘 대표)"], [f"'{long_q}' (추미애 진영 여권 → 여권/여당)"])
    assert len(f) <= 300 and "가" * 30 not in f
    f2 = format_audit_footer(["'AIzaSyA1234567890abcdefghij' (현재 직함: x)"], [])
    assert "AIza" not in f2


def test_footer_max_len_and_escape():
    ws = [f"'인물{i} 의원' (현재 직함: 대표)" for i in range(50)]
    f = format_audit_footer(ws, [], max_len=80)
    assert len(f) <= 80 and "외 " in f
    assert "<" not in format_audit_footer(["'<b>장동혁</b> 의원' (현재 직함: 대표)"], [], mode="html").replace("&lt;", "")
    assert "_" not in format_audit_footer(["'장_동혁 의원' (현재 직함: 대표)"], [], mode="markdown")


def test_append_footer_no_footer_identical():
    chunks = ["a" * 10, "b" * 20]
    assert append_footer_to_chunks(chunks, "") == chunks
    assert append_footer_to_chunks([], "") == []


def test_append_footer_fits_last_chunk():
    out = append_footer_to_chunks(["x" * 100], "⚠ f", max_len=200)
    assert out == ["x" * 100 + "\n\n⚠ f"]


def test_append_footer_exact_boundary():
    foot = "⚠ " + "f" * 8                       # 10자
    exact = "x" * (200 - 2 - len(foot))          # 마지막 조각 + '\n\n' + 꼬리말 = 정확히 200
    assert append_footer_to_chunks([exact], foot, 200) == [exact + "\n\n" + foot]
    over = exact + "x"                            # 1자 초과 → 별도 조각
    assert append_footer_to_chunks([over], foot, 200) == [over, foot]


def test_append_footer_oversized_footer_truncated():
    out = append_footer_to_chunks(["x" * 50], "f" * 500, max_len=100)
    assert all(len(c) <= 100 for c in out) and out[0] == "x" * 50


def test_telegram_limit_constant():
    assert TELEGRAM_MAX_LEN == 4096


def test_missing_title_audit_and_fix():
    for t in ("장동혁 욕설", "국민의힘 장동혁 욕설", "국힘 장동혁 녹취"):
        assert _q(audit_missing_titles, t), t
    for t in ("국민의힘 장동혁 대표 욕설", "장동혁 대표 녹취", "국민의힘 대표 장동혁 녹취", "오세훈 무죄", "추미애 사퇴 청원"):
        assert not _q(audit_missing_titles, t), t
    assert apply_preferred_title("장동혁 욕설", FACTS) == "국민의힘 장동혁 대표 욕설"
    assert apply_preferred_title("국힘 장동혁 녹취", FACTS) == "국민의힘 장동혁 대표 녹취"
    assert apply_preferred_title("장동혁 대표 욕설", FACTS) == "장동혁 대표 욕설"


def test_tracked_persons_choo_oh():
    by = {p["name"]: p for p in FACTS["persons"]}
    assert by["추미애"]["title"] == "경기도지사" and by["추미애"]["camp"] == "여권" and by["추미애"].get("tracked")
    assert by["오세훈"]["title"] == "서울시장" and by["오세훈"]["show_party"] is False and by["오세훈"].get("tracked")
    assert not _any("오세훈 서울시장") and not _any("추미애 경기도지사")


if __name__ == "__main__":
    total, fails = _run()
    for l, t, r in fails:
        print(f"FAIL {l:11s} {t!r} -> {r}")
    extra = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v) and k != "test_all_cases"]
    for fn in extra:
        try:
            fn()
        except AssertionError as e:
            fails.append(("unit", fn.__name__, str(e)))
    print(f"TOTAL {total} cases + {len(extra)} unit tests, FAILS {len(fails)}")
    sys.exit(1 if fails else 0)
