"""person_facts 여야·직함 점검기 오프라인 테스트 (API 호출 없음).
실행: python tests/test_person_facts.py  또는  pytest tests/test_person_facts.py"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from person_facts import audit_party_camp, audit_person_titles, load_person_facts  # noqa: E402

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


if __name__ == "__main__":
    total, fails = _run()
    for l, t, r in fails:
        print(f"FAIL {l:11s} {t!r} -> {r}")
    print(f"TOTAL {total} cases, FAILS {len(fails)}")
    sys.exit(1 if fails else 0)
