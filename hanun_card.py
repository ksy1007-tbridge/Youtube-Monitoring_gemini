# -*- coding: utf-8 -*-
"""여권 유튜브 텔레그램 '한눈 카드' — 규칙 기반 배치 (감 배제)."""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 카드 전용 편중 임계 (리포트 ⚠편중 50%와 별개)
HANUN_BIAS_PCT = 60.0
# 소수 채널 예외: 채널 수가 이 값 미만(=1~2채널)이면 점유율과 무관하게 내부·편중.
# 1~2채널 이슈는 사실상 특정 채널 기획이므로 편중으로 본다. 3채널 이상은
# 최대 채널 점유율 ≥ HANUN_BIAS_PCT(60%) 규칙만 적용한다.
# (예: 김어준 논란 9건·3채널·최대 49.2% → 확산. 10/29 카드 오분류 수정)
HANUN_MIN_CHANNELS_SPREAD = 3
# 인물 급등/급락 표기 임계(전일 대비 %)
HANUN_PERSON_SWING_PCT = 50.0
# 한 줄·칸에 올릴 최대 줄 수
MAX_SPREAD = 4
MAX_INTERNAL = 5
MAX_ILLUSION = 3

# 미디어 스캔들(인물 급등 ≠ 이슈 쟁점) — 내부·편중 쪽 한 줄
MEDIA_SCANDEL_KEYS = ("주진우", "김어준")

# 신규 속보 이름 힌트 (전일 issues 키가 없을 때·보조)
NEW_BREAKING_RE = re.compile(r"속보|구속\s*연장|긴급")

DYNAMIC_TAG = " (자동)"


def strip_auto(name: str) -> str:
    n = str(name or "").strip()
    if n.endswith(DYNAMIC_TAG):
        return n[: -len(DYNAMIC_TAG)].strip()
    return n


def _conc_share(conc) -> Optional[float]:
    if not conc or not isinstance(conc, dict):
        return None
    try:
        return float(conc.get("top_share"))
    except (TypeError, ValueError):
        return None


def is_new_issue(name: str, prev_issues: Optional[dict], *, is_auto: bool = False) -> bool:
    """전일 issues 스냅샷에 없으면 신규. 스냅샷이 없거나 비면(마이그레이션·첫날) 이름 힌트·자동만."""
    key = strip_auto(name)
    if not prev_issues:
        return bool(is_auto and NEW_BREAKING_RE.search(key))
    if key not in prev_issues and name not in prev_issues:
        return True
    return False


def classify_issue(
    *,
    channels: int,
    top_share: Optional[float],
    is_new: bool,
) -> Tuple[str, bool]:
    """반환: ('spread'|'internal', used_new_exception).
    규칙(문서화된 예외 외 없음):
      1) 최대 채널 점유율 ≥ 60% → 내부·편중
      2) 채널 수 < 3 (1~2채널) → 내부·편중 (소수 채널 예외)
      3) 그 외 → 확산
    신규 속보는 1)·2)를 덮어쓰고 ※표기한다."""
    biased = top_share is not None and top_share >= HANUN_BIAS_PCT
    few_channels = channels < HANUN_MIN_CHANNELS_SPREAD
    if biased or few_channels:
        if is_new:
            return "spread", True
        return "internal", False
    return "spread", False


def _fmt_views(v: int) -> str:
    if v >= 10000:
        return f"{v / 10000:.1f}만".replace(".0만", "만")
    return f"{v:,}회"


def _issue_line(row: dict, *, new_exc: bool) -> str:
    name = strip_auto(row["이슈명"])
    name = html.escape(str(name))
    ch = int(row.get("채널수") or 0)
    cnt = int(row.get("건수") or 0)
    views = int(row.get("조회수") or 0)
    share = _conc_share(row.get("concentration"))
    share_s = f"·최대 {share:.0f}%" if share is not None else ""
    exc = " ※신규 속보 예외" if new_exc else ""
    return f"• <b>{name}</b> — {cnt}건·{ch}채널·{_fmt_views(views)}{share_s}{exc}"


def _person_internal_line(p: dict) -> str:
    disp = html.escape(str(p.get("display") or p.get("name") or ""))
    views = int(p.get("views_standalone") or 0)
    share = p.get("top_share")
    ch = p.get("n_ch")
    bits = [f"단독 {_fmt_views(views)}"]
    if ch:
        bits.append(f"{ch}채널")
    if share is not None:
        bits.append(f"최대 {float(share):.0f}%")
    trend = p.get("trend_short") or ""
    if trend:
        bits.append(trend)
    note = p.get("note") or ""
    return f"• <b>{disp}</b> — {'·'.join(bits)}{(' · ' + note) if note else ''}"


def _has_batchim(word: str) -> bool:
    """마지막 한글 음절의 받침 유무. 숫자·영문은 읽는 소리 기준으로 근사."""
    w = re.sub(r"[\s\)\]\}\"'”’]+$", "", str(word or ""))
    if not w:
        return False
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return ch in "013678"  # 영·일·삼·육·칠·팔 받침 있음
    return ch.lower() in "lmnr"


def josa(word: str, pair: str) -> str:
    """word + 맞는 조사. pair 예: '이/가', '은/는', '을/를', '과/와'."""
    with_b, without_b = pair.split("/")
    return f"{word}{with_b if _has_batchim(word) else without_b}"


def person_swings(person_rows: Sequence[dict]) -> Tuple[List[dict], List[dict]]:
    """전일 대비 ±50% 이상 인물. (급등, 급락)."""
    ups, downs = [], []
    for p in person_rows:
        pct = p.get("pct_change")
        if pct is None:
            continue
        pct = float(pct)
        if pct >= HANUN_PERSON_SWING_PCT:
            ups.append(p)
        elif pct <= -HANUN_PERSON_SWING_PCT:
            downs.append(p)
    # 급등은 '조회 견인' 판단용이므로 단독 조회수 큰 순(동률 시 변동폭)
    ups.sort(key=lambda p: (int(p.get("views_standalone") or 0), float(p["pct_change"])), reverse=True)
    downs.sort(key=lambda p: float(p["pct_change"]))
    return ups, downs


def _person_swing_phrase(p: dict) -> str:
    disp = strip_auto(p.get("display") or p.get("name") or "")
    pct = float(p["pct_change"])
    share = p.get("top_share")
    s = f"{disp} {pct:+.0f}%"
    if share is not None and float(share) >= HANUN_BIAS_PCT:
        s += f"(편중 {float(share):.0f}%)"
    return s


def build_one_liner(
    spread: Sequence[dict],
    person_rows: Sequence[dict] = (),
) -> str:
    """담백한 한 줄. 이슈 문장과 인물 급등 문장을 분리한다.
    - 이슈: 확산 상위 최대 2개 '넓게 깔린다' (조회 견인 표현 쓰지 않음)
    - 인물: 전일 대비 +50% 이상만 '조회 급등'으로, 최대 채널 ≥60%면 (편중 N%) 표기
      → 편중 급등만 있으면 '특정 채널 몰림'으로 서술해 과장 방지
    - 특정 인물(예: 김어준)을 이슈 이름만으로 '조회를 끌어올린다'고 쓰지 않는다."""
    names = [strip_auto(r["이슈명"]) for r in spread[:2] if r.get("이슈명")]
    parts = []
    if names:
        if len(names) == 2:
            subj = f"{josa(names[0], '과/와')} {names[1]}"
        else:
            subj = names[0]
        parts.append(f"여권 유튜브는 {josa(subj, '이/가')} 넓게 깔린다.")
    ups, _downs = person_swings(person_rows)
    if ups:
        top = ups[:2]
        phr = ", ".join(_person_swing_phrase(p) for p in top)
        all_biased = all(
            p.get("top_share") is not None and float(p["top_share"]) >= HANUN_BIAS_PCT for p in top
        )
        tail = "특정 채널 몰림이다" if all_biased else "조회가 뛰었다"
        parts.append(f"인물은 전일 대비 {phr}로 {tail}.")
    if parts:
        return " ".join(parts)
    return "오늘 여권 유튜브에서 뚜렷한 확산 이슈는 없습니다."


def collect_illusion_lines(df_top_rows: Sequence[dict], unclassified_rows: Sequence[dict]) -> List[str]:
    """종합방송 TOP·미분류 코너 클립 착시."""
    lines = []
    for r in df_top_rows:
        if str(r.get("프레임") or "") == "종합방송":
            title = html.escape(str(r.get("제목") or "")[:40])
            ch = html.escape(str(r.get("채널명") or ""))
            lines.append(f"• 종합방송 {_fmt_views(int(r.get('조회수') or 0))} [{ch}] {title}")
        if len(lines) >= 2:
            break
    for r in unclassified_rows[:2]:
        title = str(r.get("제목") or "")
        if any(k in title for k in ("코너별", "다시보기", "중간선거", "프랑스", "알고 보면")):
            lines.append(
                f"• 미분류 클립 {_fmt_views(int(r.get('조회수') or 0))} {html.escape(title[:40])}"
            )
        if len(lines) >= MAX_ILLUSION:
            break
    return lines[:MAX_ILLUSION]


def build_hanun_card(
    *,
    now_str: str,
    issue_rows: Sequence[dict],
    prev_issues: Optional[dict],
    person_rows: Sequence[dict],
    df_top_rows: Sequence[dict] = (),
    unclassified_rows: Sequence[dict] = (),
) -> str:
    """텔레그램 HTML 한눈 카드."""
    # 이슈 정렬: 채널 수 > 조회수
    issues = sorted(
        list(issue_rows),
        key=lambda r: (int(r.get("채널수") or 0), int(r.get("조회수") or 0)),
        reverse=True,
    )

    spread, internal = [], []
    for row in issues:
        share = _conc_share(row.get("concentration"))
        is_new = is_new_issue(row.get("이슈명", ""), prev_issues, is_auto=bool(row.get("is_auto")))
        bucket, new_exc = classify_issue(
            channels=int(row.get("채널수") or 0),
            top_share=share,
            is_new=is_new,
        )
        item = {**row, "_new_exc": new_exc}
        (spread if bucket == "spread" else internal).append(item)

    # 인물: 미디어 스캔들·한동훈·±50% 급변
    person_internal = []
    handong_seen = False
    for p in person_rows:
        key = strip_auto(p.get("name") or "")
        disp = p.get("display") or key
        pct = p.get("pct_change")  # float or None
        top_share = p.get("top_share")
        n_ch = int(p.get("n_ch") or 0)
        note = ""
        kind = "person"

        if any(k in key for k in MEDIA_SCANDEL_KEYS):
            kind = "media"
            note = "미디어 스캔들"
            person_internal.append({**p, "display": disp, "note": note, "kind": kind})
        elif "한동훈" in key:
            handong_seen = True
            biased = top_share is not None and float(top_share) >= HANUN_BIAS_PCT
            few = n_ch and n_ch < HANUN_MIN_CHANNELS_SPREAD
            if biased or few or p.get("from_issue"):
                person_internal.append({**p, "display": disp, "note": note, "kind": kind})
            elif pct is not None and abs(pct) >= 50:
                person_internal.append({**p, "display": disp, "note": note, "kind": kind})
        elif pct is not None and abs(pct) >= 50:
            sign = "+" if pct >= 0 else ""
            if pct <= -50:
                person_internal.append({
                    **p, "display": disp,
                    "note": f"열기 식는 중({sign}{pct:.0f}%)",
                    "kind": kind,
                })

    # 체크리스트: 한동훈이 이슈/인물에 있으면 반드시 한 줄
    if not handong_seen:
        for p in person_rows:
            if "한동훈" in strip_auto(p.get("name") or ""):
                person_internal.append({**p, "display": p.get("display") or "한동훈", "note": "", "kind": "person"})
                handong_seen = True
                break
        for row in issues:
            if "한동훈" in strip_auto(row.get("이슈명") or ""):
                # 이슈로만 있으면 internal에 이미 분류됐을 수 있음 — 없으면 강제
                if not any("한동훈" in strip_auto(x.get("이슈명") or "") for x in spread + internal):
                    internal.append({**row, "_new_exc": False})
                handong_seen = True
                break

    # 중복 인물 줄 제거
    seen_p = set()
    deduped = []
    for p in person_internal:
        k = strip_auto(p.get("name") or p.get("display") or "")
        if k in seen_p:
            continue
        seen_p.add(k)
        deduped.append(p)
    person_internal = deduped

    one = html.escape(build_one_liner(spread[:2], person_rows))

    lines = [
        f"<b>[여권 유튜브 한눈 카드]</b>",
        f"▪ 기준 시각: KST {html.escape(now_str)}",
        "",
        f"<b>한 줄</b>",
        one,
        "",
        f"<b>확산 (쓸 이슈)</b>",
    ]
    if spread:
        for row in spread[:MAX_SPREAD]:
            lines.append(_issue_line(row, new_exc=bool(row.get("_new_exc"))))
    else:
        lines.append("• 해당 없음")

    lines += ["", f"<b>내부·편중 (참고만)</b>"]
    body_internal = []
    for row in internal[:MAX_INTERNAL]:
        body_internal.append(_issue_line(row, new_exc=False))
    for p in person_internal[:MAX_INTERNAL]:
        body_internal.append(_person_internal_line(p))
    lines.extend(body_internal[:MAX_INTERNAL] or ["• 해당 없음"])

    illusions = collect_illusion_lines(df_top_rows, unclassified_rows)
    lines += ["", f"<b>무시해도 되는 착시</b>"]
    if illusions:
        lines.extend(illusions)
    else:
        lines.append("• 종합방송 풀버전·코너 클립 조회는 이슈 순위가 아닙니다.")

    lines += ["", "※ 아래 메시지에 원문 리포트가 이어집니다."]
    return "\n".join(lines)


def issues_snapshot(issue_rows: Sequence[dict]) -> Dict[str, Any]:
    """previous_data.json용 이슈 스냅샷 (다음날 신규 판정)."""
    out = {}
    for row in issue_rows:
        key = strip_auto(row.get("이슈명") or "")
        if not key:
            continue
        share = _conc_share(row.get("concentration"))
        out[key] = {
            "cnt": int(row.get("건수") or 0),
            "channels": int(row.get("채널수") or 0),
            "views": int(row.get("조회수") or 0),
            "top_share": share,
            "is_auto": bool(row.get("is_auto")),
        }
    return out
