"""issue_tracker (두 저장소 공통, 오프라인: LLM·네트워크 호출 없음)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from issue_tracker import (merge_auto_into_fixed, check_issue_name, update_history, is_sensational,  # noqa: E402
                           persistent_candidates, normalize_history)

FIXED = {"조희대/사법부": ["조희대", "대법원장"], "김어준 논란": ["김어준"], "부동산·증시": ["부동산", "코스피"]}


def test_auto_merges_into_fixed_by_keyword_overlap():
    auto = {"조희대 탄핵 (자동)": ["조희대 탄핵", "탄핵집회"], "오세훈 무죄 (자동)": ["오세훈", "2심 무죄"]}
    remaining, absorbed = merge_auto_into_fixed(auto, FIXED)
    assert "조희대 탄핵 (자동)" not in remaining
    assert absorbed == {"조희대/사법부": ["조희대 탄핵 (자동)"]}
    assert remaining == {"오세훈 무죄 (자동)": ["오세훈", "2심 무죄"]}
    assert FIXED["조희대/사법부"] == ["조희대", "대법원장"]  # 고정 키워드는 늘리지 않음


def test_auto_auto_overlap_merged():
    auto = {"장동혁 녹취 (자동)": ["장동혁 녹취"], "장동혁 욕설 (자동)": ["장동혁 욕설", "녹취"]}
    remaining, absorbed = merge_auto_into_fixed(auto, FIXED)
    assert list(remaining) == ["장동혁 녹취 (자동)"]
    assert absorbed == {"장동혁 녹취 (자동)": ["장동혁 욕설 (자동)"]}


def test_issue_name_passes_person_facts():
    assert check_issue_name("국민의힘 장동혁 대표 녹취 (자동)") == "국민의힘 장동혁 대표 녹취"
    # 직함 오류 이름은 키워드명으로 교체
    # 직함 오류 이름은 키워드명으로 교체하고, 직함이 빠진 인물명은 preferred 표기로 보정
    assert check_issue_name("국민의힘 최고위원 장동혁 녹취", ["장동혁 녹취"]) == "국민의힘 장동혁 대표 녹취"
    assert check_issue_name("국민의힘 최고위원 장동혁", []) is None


def _hist(days):
    h = None
    for d, items in days:
        h = update_history(h, d, items)
    return h


def test_candidate_after_more_than_3_days_with_name_drift():
    h = _hist([
        ("2026-10-21", {"오세훈 재판": {"keywords": ["오세훈"], "cnt": 3}}),
        ("2026-10-22", {"오세훈 2심": {"keywords": ["오세훈 2심"], "cnt": 4}}),
        ("2026-10-23", {"오세훈 무죄": {"keywords": ["오세훈"], "cnt": 5}}),
    ])
    assert persistent_candidates(h, "2026-10-23") == []  # 3일은 후보 아님
    h = update_history(h, "2026-10-24", {"오세훈 무죄 (자동)": {"keywords": ["오세훈", "2심 무죄"], "cnt": 6}})
    c = persistent_candidates(h, "2026-10-24")
    assert c and c[0]["name"] == "오세훈 무죄" and c[0]["days"] == 4


def test_history_prunes_and_tolerates_bad_input():
    assert normalize_history(None) == {"version": 1, "days": {}}
    assert normalize_history({"days": "x"}) == {"version": 1, "days": {}}
    h = update_history({"days": {"2026-09-01": {"a": {}}}}, "2026-10-24", {})
    assert list(h["days"]) == ["2026-10-24"]


def test_missing_title_corrected_in_issue_name():
    assert check_issue_name("장동혁 욕설 (자동)", ["욕설"]) == "국민의힘 장동혁 대표 욕설"
    assert check_issue_name("국민의힘 장동혁 욕설", []) == "국민의힘 장동혁 대표 욕설"
    assert check_issue_name("장동혁 대표 욕설", []) == "장동혁 대표 욕설"
    assert check_issue_name("오세훈 무죄", ["무죄"]) == "오세훈 무죄"   # preferred 없는 인물은 그대로


def test_sensational_issue_name_blocked():
    for n in ("추미애 무릎", "“추미애가 결국 무릎을 꿇었다?”", "장동혁 몰락", "충격 폭로", "오세훈 무죄?"):
        assert is_sensational(n), n
    assert not is_sensational("오세훈 2심 무죄")
    assert check_issue_name("추미애 무릎 (자동)", ["추미애", "무릎"]) is None
    assert check_issue_name("추미애 무릎", ["추미애", "사퇴 청원"]) == "사퇴 청원"


def test_person_only_name_not_issue():
    assert check_issue_name("추미애", ["추미애"]) is None
    assert check_issue_name("탁현민", [], person_names=["탁현민"]) is None
