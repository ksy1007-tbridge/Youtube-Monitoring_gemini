"""gemini 텔레그램 분할 + 점검 꼬리말 (오프라인: 전송 없음, requests.post 모킹)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import main  # noqa: E402


def _old_split(message, MAX_LEN=3800):
    if len(message) <= MAX_LEN:
        return [message]
    chunks = []
    while len(message) > MAX_LEN:
        i = message.rfind("\n\n", 0, MAX_LEN)
        if i == -1:
            i = MAX_LEN
        chunks.append(message[:i])
        message = message[i:].lstrip()
    if message:
        chunks.append(message)
    return chunks


SAMPLES = ["", "짧은 리포트", "x" * 3800, "y" * 3801, ("단락\n\n" * 1500), "z" * 9000]


def test_no_footer_identical_to_before():
    for m in SAMPLES:
        assert main.split_telegram_chunks(m) == _old_split(m)


def test_footer_on_last_chunk_within_limit():
    for m in SAMPLES[1:]:
        out = main.split_telegram_chunks(m, "⚠ 직함 점검: x")
        assert out[-1].endswith("⚠ 직함 점검: x") and all(len(c) <= 3800 for c in out)


def test_footer_own_chunk_when_full():
    out = main.split_telegram_chunks("x" * 3800, "⚠ 직함 점검: x")
    assert out == ["x" * 3800, "⚠ 직함 점검: x"]


def test_send_passes_footer(monkeypatch):
    sent = []

    class R:
        def json(self):
            return {"ok": True}

    monkeypatch.setattr(main, "TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setattr(main, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(main.requests, "post", lambda url, json=None, data=None, timeout=None, **k: sent.append((json or data)["text"]) or R())
    main.send_telegram_message("<b>본문</b>", "⚠ 직함 점검: 장동혁 의원 → 국민의힘 대표 확인 필요")
    assert sent == ["<b>본문</b>\n\n⚠ 직함 점검: 장동혁 의원 → 국민의힘 대표 확인 필요"]
    sent.clear()
    main.send_telegram_message("<b>본문</b>")
    assert sent == ["<b>본문</b>"]
