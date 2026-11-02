# -*- coding: utf-8 -*-
import os, sys, json, unittest
from datetime import datetime
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import issue_defs
from issue_defs import build_defs, publish_defs, summarize_one_liner, kv_key, KST

NOW = datetime(2026, 11, 2, 9, 21, tzinfo=KST)
V_OMNI = {"채널명": "A", "제목": "풀버전", "조회수": 900000, "링크": "https://youtu.be/omni111", "프레임": "종합방송",
          "설명": "종합방송 설명은 근거로 쓰면 안 되는 문장입니다."}
V_TOP = {"채널명": "채널A", "제목": "김어준 발언 정리", "조회수": 50000, "링크": "https://youtu.be/abc123",
         "프레임": "기타", "설명": "#태그\nhttps://x.y\n김어준의 방송 발언을 두고 비판이 이어지고 있습니다. 둘째 문장."}
V_LOW = {"채널명": "B", "제목": "저조회", "조회수": 10, "링크": "https://youtu.be/low999", "프레임": "기타",
         "설명": "저조회 영상 설명 문장입니다."}
NONE = lambda *_a, **_k: None


class BuildDefs(unittest.TestCase):
    def test_description_first_and_standalone_top(self):
        d = build_defs([{"이슈명": "김어준 논란"}], {"김어준 논란": [V_LOW, V_OMNI, V_TOP]},
                       now=NOW, caption=NONE, news=NONE, aliases={"김어준 논란": ["김어준"]})
        self.assertEqual((d["date"], d["source"]), ("2026-11-02", "yeogwon"))
        it = d["issues"][0]
        self.assertEqual(it["evidence_kind"], "description")
        self.assertEqual(it["evidence_url"], "https://youtu.be/abc123")
        self.assertTrue(it["definition_one_liner"].startswith("김어준의 방송 발언"))
        self.assertEqual(it["aliases"], ["김어준"])
        self.assertEqual(it["generated_at"], "2026-11-02T09:21:00+09:00")

    def test_caption_then_news_then_skip(self):
        nodesc = dict(V_TOP, 설명="")
        with mock.patch.dict(os.environ, {"YOUTUBE_API_KEY": ""}):
            d = build_defs([{"이슈명": "X (자동)"}], {"X (자동)": [nodesc]}, now=NOW,
                           caption=lambda v: "자막 앞부분에서 나온 정의 문장입니다.", news=NONE)
            self.assertEqual(d["issues"][0]["evidence_kind"], "caption")
            self.assertEqual(d["issues"][0]["issue_name"], "X")
            d = build_defs([{"이슈명": "X"}], {"X": [nodesc]}, now=NOW, caption=NONE,
                           news=lambda n: {"channel": "○○일보", "title": "보도 제목", "url": "https://n", "text": "보도 기준 정의 문장입니다."})
            self.assertEqual(d["issues"][0]["evidence_kind"], "news")
            d = build_defs([{"이슈명": "X"}], {"X": [nodesc]}, now=NOW, caption=NONE, news=NONE)
            self.assertEqual(d["issues"], [])

    def test_one_liner_len(self):
        s = summarize_one_liner("가" * 200)
        self.assertLessEqual(len(s), 60)
        self.assertIsNone(summarize_one_liner("짧음\n#tag"))


class Publish(unittest.TestCase):
    DEFS = {"date": "2026-11-02", "source": "yeogwon", "issues": [{"issue_name": "x"}]}

    def test_noop_without_env(self):
        s = mock.Mock()
        with mock.patch.dict(os.environ, {"CF_ACCOUNT_ID": "", "CF_KV_NAMESPACE_ID": "", "CF_API_TOKEN": ""}):
            publish_defs(self.DEFS, session=s)
        s.put.assert_not_called()

    def test_put_and_no_secret_in_logs(self):
        s = mock.Mock(); s.put.return_value = mock.Mock(status_code=200)
        env = {"CF_ACCOUNT_ID": "acc", "CF_KV_NAMESPACE_ID": "ns", "CF_API_TOKEN": "SECRET_TOKEN_123"}
        with mock.patch.dict(os.environ, env), mock.patch("builtins.print") as p:
            publish_defs(self.DEFS, session=s)
            s.put.side_effect = RuntimeError("boom Bearer SECRET_TOKEN_123")
            publish_defs(self.DEFS, session=s)  # 예외 전파 없음
        url = s.put.call_args[0][0]
        self.assertTrue(url.endswith("/accounts/acc/storage/kv/namespaces/ns/values/defs:2026-11-02:yeogwon"))
        self.assertEqual(s.put.call_args[1]["params"]["expiration_ttl"], 7 * 24 * 3600)
        self.assertEqual(json.loads(s.put.call_args[1]["data"].decode())["source"], "yeogwon")
        self.assertNotIn("SECRET_TOKEN_123", " ".join(str(c) for c in p.call_args_list))
        self.assertEqual(kv_key(self.DEFS), "defs:2026-11-02:yeogwon")


if __name__ == "__main__":
    unittest.main()
