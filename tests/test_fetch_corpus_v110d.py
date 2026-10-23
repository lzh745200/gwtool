# -*- coding: utf-8 -*-
"""D 批：语料采集合规工具（scripts/fetch_corpus.py）离线回归。

**全部离线**：不访问任何真实站点。网络层统一 monkeypatch `fetch_corpus._http_get`，
限速器用无休眠桩替换（真实限速逻辑用假 clock 单测），robots 用固定文本喂给
`urllib.robotparser`。

覆盖验收点：
  ① 受限站点无授权拒绝（exit 3，且提示需要哪些字段）；
  ② 授权文件 sites/doc_ref 校验（sites 不含该站、doc_ref 为空 → 3）；
  ③ robots 禁止的页面跳过并记入 manifest.skipped；
  ④ `--dry-run` 零写盘、不请求正文；
  ⑤ `--from-dir` 导入 .txt/.md + manifest（受限站点同样要授权）；
  ⑥ 限速器：间隔不足会补 sleep，且站点下限/robots crawl-delay 取最大值；
  ⑦ HTML→文本清洗（去 script/style/标签、保留段落），bs4 与纯 re 两条路径都测；
  ⑧ 授权文件副本、manifest 字段、`--help`、参数校验。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_corpus.py"
EXAMPLE_AUTH = ROOT / "doc" / "corpus" / "AUTHORIZATION.example.json"
CHECKLIST = ROOT / "doc" / "corpus" / "采集合规检查表.md"

LIST_URL = "https://www.gov.cn/zhengce/"
ROBOTS_URL = "https://www.gov.cn/robots.txt"
OK_URL = "https://www.gov.cn/zhengce/content/ok.htm"
BLOCKED_URL = "https://www.gov.cn/zhengce/content/blocked.htm"

ROBOTS_GOV = ("User-agent: *\nAllow: /\n"
              "Disallow: /zhengce/content/blocked.htm\n")
LIST_HTML = (
    "<html><body><ul>"
    '<li><a href="/zhengce/content/ok.htm">关于进一步加强安全生产工作的通知</a></li>'
    '<li><a href="/zhengce/content/blocked.htm">robots 禁止的页面</a></li>'
    '<li><a href="/zhengce/content/ok.htm">重复链接</a></li>'
    "</ul></body></html>")
PARA_ONE = ("为深入贯彻落实上级关于安全生产工作的部署要求，现就有关事项通知如下，"
            "请各单位结合实际认真抓好贯彻落实，确保各项任务按期完成，"
            "并将贯彻落实情况及时报告上级主管部门。")
PARA_TWO = ("各单位要高度重视此项工作，严格落实责任制，加强协调配合，形成工作合力，"
            "共同推动各项措施落地见效，切实防范化解重大安全风险隐患。")
PARA_THREE = ("要坚持问题导向，深入基层开展调查研究，切实解决群众反映的突出问题，"
              "并按规定时限报送工作总结和相关材料，确保信息真实准确完整。")
PAGE_OK = (f"<html><head><style>p{{color:red}}</style></head><body>"
           f'<div id="UCAP-CONTENT"><p>{PARA_ONE}</p><p>{PARA_TWO}</p>'
           f"<p>{PARA_THREE}</p>"
           "<script>alert('跟踪脚本');</script></div>"
           "<div>导航噪声</div></body></html>")
PAGE_BLOCKED = f"<html><body><div id='UCAP-CONTENT'><p>{PARA_ONE}</p></div></body></html>"
# 正文容器存在但质量不合格：只有 1 个短段落（模拟"容器找错/被截断"）
PAGE_SHORT = '<html><body><div id="UCAP-CONTENT"><p>太短了。</p></div></body></html>'
# 容器里没有 <p> 段落（很多老站用 <br> 排版）→ 质量闸门必须判失败
PAGE_NO_P = ('<html><body><div id="UCAP-CONTENT"><span>'
             + PARA_ONE + PARA_TWO + PARA_THREE
             + "</span></div></body></html>")
# 多级容器：没有 #UCAP-CONTENT，只有 .pages_content（站点模板变化时的回退）
PAGE_SECOND_SELECTOR = (f'<html><body><div class="pages_content">'
                        f"<p>{PARA_ONE}</p><p>{PARA_TWO}</p><p>{PARA_THREE}</p>"
                        "</div></body></html>")

# ---- 标题碎片合并（D3）：gov.cn 正文容器把标题拆成 2~3 个 <p> ----
MEND_TITLE = "国务院关于《国家残疾预防行动计划（2026—2030年）》的批复"
MEND_PART1 = "国务院关于《国家残疾预防行动计划"
MEND_PART2 = "（2026—2030年）》的批复"
MEND_URL = "https://www.gov.cn/zhengce/content/mend.htm"
MEND_LIST_HTML = ('<html><body><ul><li>'
                  '<a href="/zhengce/content/mend.htm">'
                  + MEND_TITLE + "</a></li></ul></body></html>")
MEND_PAGE = ('<html><body><div id="UCAP-CONTENT">'
             f"<p>{MEND_PART1}</p><p>{MEND_PART2}</p>"
             f"<p>{PARA_ONE}</p><p>{PARA_TWO}</p><p>{PARA_THREE}</p>"
             "</div></body></html>")


# ---------------------------------------------------------------- helpers
def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_corpus_mod", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


class FakeClock:
    """可注入的时钟：sleep 推进时间，便于断言限速间隔。"""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 6))
        self.t += seconds


class NoSleepLimiter:
    """抓取流程里替换限速器：不真等（限速逻辑另有单测）。"""

    instances: list["NoSleepLimiter"] = []

    def __init__(self, delay_s, **_kwargs):
        self.delay_s = delay_s
        self.waits = 0
        NoSleepLimiter.instances.append(self)

    def wait(self) -> None:
        self.waits += 1


def _install_fake_net(mod, monkeypatch, *, robots=ROBOTS_GOV,
                      list_html=LIST_HTML,
                      pages: "dict[str, str] | None" = None,
                      robots_error: "Exception | None" = None):
    """替换网络层：返回记录到的请求 URL 列表（顺序即请求顺序）。"""
    pages = pages if pages is not None else {OK_URL: PAGE_OK,
                                             BLOCKED_URL: PAGE_BLOCKED}
    calls: list[str] = []

    def fake_get(url, *, timeout=10.0, user_agent=""):
        calls.append(url)
        if url == ROBOTS_URL:
            if robots_error is not None:
                raise robots_error
            return 200, robots
        if url == LIST_URL:
            return 200, list_html
        if url in pages:
            return 200, pages[url]
        raise mod.FetchError("HTTP 404")

    monkeypatch.setattr(mod, "_http_get", fake_get)
    monkeypatch.setattr(mod, "RateLimiter", NoSleepLimiter)
    return calls


def _write_auth(tmp_path: Path, **overrides) -> Path:
    data = {
        "granted_by": "测试单位 宣传处",
        "doc_ref": "测试单位〔2026〕第 9 号 关于同意用于本地评测的函",
        "granted_at": "2026-11-01",
        "sites": ["people"],
        "scope": "仅用于本地纠错引擎评测，不得再分发",
        "contact": "测试联系人",
    }
    data.update(overrides)
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _capture(capsys) -> str:
    got = capsys.readouterr()
    return got.out + got.err


# ---------------------------------------------------------------- 站点配置
def test_site_table_matches_compliance_recon(mod):
    """站点表必须带合规侦察到的关键事实（机器人 UA/限速/上限/许可/受限标记）。"""
    assert set(mod.SITES) == {"gov", "people", "qstheory", "junbao", "mod"}
    for name, cfg in mod.SITES.items():
        for key in ("label", "restricted", "list_url", "robots_user_agent",
                    "min_delay_s", "max_pages_default", "max_docs_default",
                    "license_note", "link_pattern", "content_selectors"):
            assert key in cfg, f"{name} 缺少配置项 {key}"
        assert cfg["min_delay_s"] >= mod.MIN_DELAY_S
        assert isinstance(cfg["content_selectors"], tuple)
    assert mod.SITES["gov"]["restricted"] is False
    for name in ("people", "qstheory", "junbao", "mod"):
        assert mod.SITES[name]["restricted"] is True, f"{name} 必须是受限站点"
    # people 的 robots 声明 Crawl-delay: 120，站点下限必须不小于它
    assert mod.SITES["people"]["min_delay_s"] >= 120
    assert mod.SITES["gov"]["list_url"].startswith("https://www.gov.cn/")
    # gov.cn 实测教训：正文容器 id 与解析器链都写进配置/常量
    assert "#UCAP-CONTENT" in mod.SITES["gov"]["content_selectors"]
    assert mod._soup_parsers()[0] == "html.parser", (
        "解析器必须优先 stdlib html.parser：lxml 在 gov.cn 不规范 HTML 上会截断")
    assert mod.MIN_CRAWL_DOC_CHARS == 200


# ---------------------------------------------------------------- 授权闸门
def test_restricted_site_without_authorization_refused(mod, capsys, tmp_path):
    out = tmp_path / "out"
    rc = mod.main(["--site", "people", "--dry-run", "--offline", "--out", str(out)])
    text = _capture(capsys)
    assert rc == 3, f"受限站点无授权必须 exit 3，实际 {rc}\n{text}"
    assert "受限站点" in text and "授权" in text
    for field in ("granted_by", "doc_ref", "granted_at", "sites", "scope", "contact"):
        assert field in text, f"拒绝提示里应列出需要字段：{field}"
    assert not out.exists(), "授权不足时不得创建输出目录"


def test_restricted_site_authorization_must_list_site(mod, capsys, tmp_path):
    auth = _write_auth(tmp_path, sites=["qstheory"])
    rc = mod.main(["--site", "people", "--authorization", str(auth),
                   "--dry-run", "--offline"])
    text = _capture(capsys)
    assert rc == 3, text
    assert "sites" in text and "people" in text


def test_restricted_site_doc_ref_must_not_be_blank(mod, capsys, tmp_path):
    auth = _write_auth(tmp_path, sites=["people"], doc_ref="   ")
    rc = mod.main(["--site", "people", "--authorization", str(auth),
                   "--dry-run", "--offline"])
    text = _capture(capsys)
    assert rc == 3, text
    assert "doc_ref" in text


def test_restricted_site_with_valid_authorization_passes(mod, capsys, tmp_path):
    auth = _write_auth(tmp_path)
    out = tmp_path / "out"
    rc = mod.main(["--site", "people", "--authorization", str(auth),
                   "--dry-run", "--offline", "--out", str(out)])
    text = _capture(capsys)
    assert rc == 0, text
    assert "测试单位" in text or "授权" in text
    assert not out.exists(), "dry-run 不得写盘"


def test_invalid_authorization_json_is_usage_error(mod, capsys, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not a json", encoding="utf-8")
    rc = mod.main(["--site", "people", "--authorization", str(bad),
                   "--dry-run", "--offline"])
    text = _capture(capsys)
    assert rc == 2, f"授权文件不是合法 JSON 应 exit 2，实际 {rc}\n{text}"

    missing = tmp_path / "nope.json"
    rc = mod.main(["--site", "people", "--authorization", str(missing),
                   "--dry-run", "--offline"])
    assert rc == 2, "授权文件不存在应 exit 2"


# ---------------------------------------------------------------- 参数/帮助
def test_help_exits_zero(mod):
    with pytest.raises(SystemExit) as exc:
        mod.main(["--help"])
    assert exc.value.code == 0


def test_delay_below_minimum_rejected(mod):
    with pytest.raises(SystemExit) as exc:
        mod.main(["--site", "gov", "--delay", "0.5"])
    assert exc.value.code == 2


def test_offline_requires_dry_run(mod):
    with pytest.raises(SystemExit) as exc:
        mod.main(["--site", "gov", "--offline"])
    assert exc.value.code == 2


def test_max_docs_over_hard_cap_rejected(mod):
    with pytest.raises(SystemExit) as exc:
        mod.main(["--site", "gov", "--max-docs", str(mod.MAX_DOCS_HARD_CAP + 1)])
    assert exc.value.code == 2


# ---------------------------------------------------------------- dry-run
def test_dry_run_writes_nothing_and_skips_body_requests(mod, monkeypatch,
                                                        capsys, tmp_path):
    calls = _install_fake_net(mod, monkeypatch)
    out = tmp_path / "dry_out"
    rc = mod.main(["--site", "gov", "--dry-run", "--out", str(out)])
    text = _capture(capsys)
    assert rc == 0, text
    assert not out.exists(), "dry-run 必须零写盘（连目录都不建）"
    assert calls == [ROBOTS_URL], f"dry-run 只允许请求 robots，实际 {calls}"
    assert "dry-run" in text and LIST_URL in text
    assert "不写任何文件" in text or "不创建输出目录" in text


def test_dry_run_offline_never_touches_network(mod, monkeypatch, capsys, tmp_path):
    def boom(*_a, **_k):
        raise AssertionError("--offline 下不允许发起任何网络请求")

    monkeypatch.setattr(mod, "_http_get", boom)
    rc = mod.main(["--site", "gov", "--dry-run", "--offline",
                   "--out", str(tmp_path / "offline_out")])
    text = _capture(capsys)
    assert rc == 0, text
    assert "--offline" in text
    assert not (tmp_path / "offline_out").exists()


# ---------------------------------------------------------------- robots
def test_robots_denied_page_is_skipped_and_manifested(mod, monkeypatch,
                                                      capsys, tmp_path):
    calls = _install_fake_net(mod, monkeypatch)
    out = tmp_path / "crawl_out"
    rc = mod.main(["--site", "gov", "--out", str(out), "--max-docs", "5",
                   "--max-pages", "5"])
    text = _capture(capsys)
    assert rc == 0, text
    assert BLOCKED_URL not in calls, "robots 禁止的 URL 不得请求正文"
    assert OK_URL in calls

    docs = sorted((out / "gov").glob("*.txt"))
    assert len(docs) == 1, f"应只入库 1 篇，实际 {[p.name for p in docs]}"
    assert re.match(r"^\d{8}_.+\.txt$", docs[0].name), docs[0].name
    body = docs[0].read_text(encoding="utf-8")
    assert PARA_ONE in body and PARA_TWO in body
    assert "alert" not in body and "<script" not in body and "<p>" not in body

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["site"] == "gov" and manifest["mode"] == "crawl"
    assert manifest["robots"]["state"] == "ok"
    assert len(manifest["docs"]) == 1
    entry = manifest["docs"][0]
    for key in ("source_url", "site", "title", "fetched_at", "chars",
                "sha256", "robots_allowed", "authorization_id",
                "license_note", "path"):
        assert key in entry, f"manifest 文档条目缺少 {key}"
    assert entry["source_url"] == OK_URL
    assert entry["robots_allowed"] is True
    assert entry["authorization_id"] == ""
    assert entry["chars"] == len(body)
    assert entry["sha256"] == mod.hashlib.sha256(
        body.encode("utf-8")).hexdigest()
    assert any(s["url"] == BLOCKED_URL and "robots" in s["reason"]
               for s in manifest["skipped"]), manifest["skipped"]
    assert manifest["failed"] == [], manifest["failed"]
    stats = manifest["stats"]
    assert stats["candidates"] == 2 and stats["docs"] == 1
    assert stats["failed"] == 0 and stats["skipped"] == 1
    assert stats["container_hits"].get("#UCAP-CONTENT") == 1, stats
    assert stats["avg_chars"] > 0
    assert manifest["authorization"] is None
    assert not (out / "_authorization.json").exists()


def test_robots_missing_treated_as_no_declaration(mod, monkeypatch):
    def fake_get(url, *, timeout=10.0, user_agent=""):
        raise mod.FetchError("HTTP 404")

    monkeypatch.setattr(mod, "_http_get", fake_get)
    robots = mod.fetch_robots(LIST_URL, user_agent="ua")
    assert robots["state"] == "missing"
    assert robots["parser"] is None
    assert mod.robots_allows(robots, "ua", OK_URL) is True


def test_robots_unreachable_aborts_real_run(mod, monkeypatch, capsys, tmp_path):
    _install_fake_net(mod, monkeypatch, robots_error=mod.FetchError("timed out"))
    out = tmp_path / "robo_out"
    rc = mod.main(["--site", "gov", "--out", str(out)])
    text = _capture(capsys)
    assert rc == 1, f"robots 无法确认时真实采集必须终止，实际 {rc}\n{text}"
    assert "robots" in text
    assert not out.exists()


# ---------------------------------------------------------------- 限速
def test_rate_limiter_enforces_minimum_interval(mod):
    """start-to-start 间隔：相邻两次请求的**发起时刻**至少相隔 delay。"""
    clock = FakeClock()
    limiter = mod.RateLimiter(2.0, clock=clock.now, sleep=clock.sleep)
    limiter.wait()                       # 首次不等待（t=0）
    assert clock.sleeps == []
    clock.t += 0.5
    limiter.wait()                       # 距上次发起 0.5s → 补睡 1.5s，t=2.0
    assert clock.sleeps == [1.5]
    limiter.wait()                       # 紧接着再发 → 仍要补满 2.0s，t=4.0
    assert clock.sleeps == [1.5, 2.0]
    clock.t += 0.2
    limiter.wait()                       # 距上次发起 0.2s → 补睡 1.8s
    assert clock.sleeps == [1.5, 2.0, 1.8]


def test_effective_delay_uses_max_of_cli_site_and_robots(mod):
    assert mod.effective_delay_s(60.0, mod.SITES["gov"]) == 60.0
    assert mod.effective_delay_s(2.0, mod.SITES["gov"]) == 2.0
    # people 站点下限 120（robots Crawl-delay）
    assert mod.effective_delay_s(2.0, mod.SITES["people"]) == 120.0
    # robots 声明更大时取 robots 值
    assert mod.effective_delay_s(2.0, mod.SITES["gov"], 30) == 30.0


# ---------------------------------------------------------------- HTML 清洗
def test_html_to_text_removes_scripts_and_keeps_paragraphs(mod):
    text = mod.html_to_text(PAGE_OK, "#UCAP-CONTENT")
    assert PARA_ONE in text and PARA_TWO in text
    assert "alert" not in text and "<script" not in text
    assert "color:red" not in text and "导航噪声" not in text
    assert "<" not in text and ">" not in text
    lines = [ln for ln in text.split("\n") if ln.strip()]
    assert len(lines) >= 2, "两段正文应保留为两个段落"


def test_html_to_text_regex_fallback(mod, monkeypatch):
    monkeypatch.setattr(mod, "_load_soup", lambda: None)
    text = mod.html_to_text(PAGE_OK, "#UCAP-CONTENT")
    assert PARA_ONE in text and PARA_TWO in text
    assert "alert" not in text and "<script" not in text
    assert "<" not in text and "color:red" not in text
    # 选择器之外的内容（导航噪声）不应进入正文
    assert "导航噪声" not in text


def test_extract_links_filters_and_dedupes(mod):
    links = mod.extract_links(LIST_HTML, LIST_URL, mod.SITES["gov"]["link_pattern"],
                              10)
    assert [item["url"] for item in links] == [OK_URL, BLOCKED_URL]
    assert links[0]["title"] == "关于进一步加强安全生产工作的通知"
    # 上限生效
    assert len(mod.extract_links(LIST_HTML, LIST_URL,
                                 mod.SITES["gov"]["link_pattern"], 1)) == 1


# ---------------------------------------------------------------- 抽取质量闸门
def test_multilevel_container_selectors(mod):
    """主容器缺失时按多级候选回退（gov.cn 模板变化/解析截断时的第二道防线）。"""
    res = mod.extract_content(PAGE_SECOND_SELECTOR,
                              mod.SITES["gov"]["content_selectors"])
    assert res["ok"] is True
    assert res["container"] == ".pages_content", res
    assert res["parser"] == "html.parser"
    assert PARA_ONE in res["text"]


def test_quality_gate_rejects_short_or_no_paragraph(mod):
    """正文 <200 字 或 无 <p> 段落 → ok=False 且原因明确（进 manifest.failed）。"""
    short = mod.extract_content(PAGE_SHORT, mod.SITES["gov"]["content_selectors"])
    assert short["ok"] is False
    assert "过短" in short["reason"], short["reason"]

    no_p = mod.extract_content(PAGE_NO_P, mod.SITES["gov"]["content_selectors"])
    assert no_p["ok"] is False
    assert "<p>" in no_p["reason"], no_p["reason"]
    # 文本本身够长，说明失败不是"没抓到"，而是"容器/结构不对"
    assert no_p["chars"] >= mod.MIN_CRAWL_DOC_CHARS


def test_parser_fallback_chain(mod, monkeypatch):
    """解析器回退链：首个解析器失败时自动换下一个（gov.cn 用 lxml 会截断）。"""
    real_soup = mod._load_soup()
    calls: list[str] = []

    class FakeSoup:
        def __new__(cls, markup, parser):
            calls.append(parser)
            if parser == "lxml":
                raise RuntimeError("模拟 lxml 截断")
            return real_soup(markup, parser)

    monkeypatch.setattr(mod, "_load_soup", lambda: FakeSoup)
    monkeypatch.setattr(mod, "_soup_parsers", lambda: ("lxml", "html.parser"))
    res = mod.extract_content(PAGE_OK, mod.SITES["gov"]["content_selectors"])
    assert res["ok"] is True, res
    assert res["parser"] == "html.parser"
    assert calls == ["lxml", "html.parser"], calls


def test_extract_failure_is_recorded_not_silently_skipped(
        mod, monkeypatch, capsys, tmp_path):
    """抽取质量不达标 → manifest.failed（带原因），绝不静默当"没抓到"。"""
    _install_fake_net(mod, monkeypatch,
                      pages={OK_URL: PAGE_SHORT, BLOCKED_URL: PAGE_BLOCKED})
    out = tmp_path / "quality_out"
    rc = mod.main(["--site", "gov", "--out", str(out), "--max-pages", "5"])
    text = _capture(capsys)
    assert rc == 0, text
    assert "[fail]" in text
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["docs"] == []
    assert len(manifest["failed"]) == 1, manifest["failed"]
    entry = manifest["failed"][0]
    assert entry["url"] == OK_URL
    assert "抽取失败" in entry["reason"] and "过短" in entry["reason"]
    assert entry["chars"] < mod.MIN_CRAWL_DOC_CHARS
    assert manifest["stats"]["failed"] == 1
    assert not list((out / "gov").glob("*.txt"))


def test_probe_reports_container_hits_without_writing(mod, monkeypatch,
                                                      capsys, tmp_path):
    """--dry-run --probe：报告容器命中/失败计数，但仍零写盘。"""
    _install_fake_net(mod, monkeypatch)
    out = tmp_path / "probe_out"
    rc = mod.main(["--site", "gov", "--dry-run", "--probe", "--out", str(out)])
    text = _capture(capsys)
    assert rc == 0, text
    assert not out.exists(), "--probe 必须零写盘"
    assert "容器命中" in text and "#UCAP-CONTENT=1" in text
    assert "抽取成功 1" in text and "跳过 1" in text
    assert "未写任何文件" in text


def test_probe_and_offline_are_mutually_exclusive(mod):
    with pytest.raises(SystemExit) as exc:
        mod.main(["--site", "gov", "--probe", "--dry-run", "--offline"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        mod.main(["--site", "gov", "--probe"])       # probe 必须配 dry-run
    assert exc.value.code == 2


# ---------------------------------------------------------------- 标题碎片合并（D3）
def test_mend_split_title_into_one_paragraph(mod):
    """标题被拆成 2 段 → 拼回完整标题后从正文删除（段数 4 → 2）。"""
    text = "\n".join([MEND_PART1, MEND_PART2, PARA_ONE, PARA_TWO])
    body, info = mod.mend_title_fragments(text, MEND_TITLE)
    assert info["title_mended"] is True
    assert info["body_paras_before"] == 4
    assert info["body_paras_after"] == 2
    assert body == "\n".join([PARA_ONE, PARA_TWO])


def test_mend_three_fragments(mod):
    """拆成 3 段也能拼回（gov.cn 实测确有 3 段的情况）。"""
    parts = ["国务院关于《国家残疾预防", "行动计划（2026—2030年）》", "的批复"]
    text = "\n".join(parts + [PARA_ONE, PARA_TWO])
    body, info = mod.mend_title_fragments(text, MEND_TITLE)
    assert info["title_mended"] is True
    assert info["body_paras_before"] == 5
    assert info["body_paras_after"] == 2
    assert body.startswith(PARA_ONE)


def test_mend_removes_duplicate_title_paragraph(mod):
    """正文开头单独一段就是完整标题 → 从正文删除（避免正文重复标题）。"""
    text = "\n".join([MEND_TITLE, PARA_ONE])
    body, info = mod.mend_title_fragments(text, MEND_TITLE)
    assert info["title_mended"] is True
    assert info["body_paras_before"] == 2 and info["body_paras_after"] == 1
    assert body == PARA_ONE


def test_mend_leaves_normal_paragraphs_untouched(mod):
    """普通段落（不匹配标题）一个字都不动。"""
    text = "\n".join([PARA_ONE, PARA_TWO, PARA_THREE])
    body, info = mod.mend_title_fragments(text, MEND_TITLE)
    assert body == text
    assert info["title_mended"] is False
    assert info["body_paras_before"] == info["body_paras_after"] == 3


def test_mend_stops_at_oversized_or_nonmatching_piece(mod):
    """碎片 ≥42 字或拼接后不再是标题前缀 → 立即放弃，绝不误删。"""
    long_title = "甲" * 45
    text = "\n".join(["甲" * 45, PARA_ONE])
    body, info = mod.mend_title_fragments(text, long_title)
    assert info["title_mended"] is False and body == text

    text2 = "\n".join([MEND_PART1, "无关段落内容", PARA_ONE])
    body2, info2 = mod.mend_title_fragments(text2, MEND_TITLE)
    assert info2["title_mended"] is False and body2 == text2


def test_mend_disabled_keeps_original(mod):
    text = "\n".join([MEND_PART1, MEND_PART2, PARA_ONE])
    body, info = mod.mend_title_fragments(text, MEND_TITLE, enabled=False)
    assert body == text
    assert info["title_mended"] is False
    assert info["body_paras_before"] == info["body_paras_after"] == 3


def test_crawl_mends_titles_by_default_and_manifests(mod, monkeypatch,
                                                     capsys, tmp_path):
    """默认开启：采集入库后标题碎片消失，manifest 记录合并信息。"""
    _install_fake_net(mod, monkeypatch, list_html=MEND_LIST_HTML,
                      pages={MEND_URL: MEND_PAGE})
    out = tmp_path / "mend_out"
    rc = mod.main(["--site", "gov", "--out", str(out), "--max-pages", "3"])
    text = _capture(capsys)
    assert rc == 0, text
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["params"]["mend_titles"] is True
    entry = manifest["docs"][0]
    assert entry["title_mended"] is True
    assert entry["body_paras_before"] == 5
    assert entry["body_paras_after"] == 3
    saved = (out / entry["path"]).read_text(encoding="utf-8")
    assert saved.splitlines()[0] == PARA_ONE, "正文开头仍残留标题碎片"
    assert MEND_PART1 not in saved and MEND_PART2 not in saved
    assert "标题碎片已合并删除" in text


def test_crawl_no_mend_titles_keeps_original(mod, monkeypatch, capsys, tmp_path):
    """--no-mend-titles：保持原始分段（用于对比取证），manifest 标 false。"""
    _install_fake_net(mod, monkeypatch, list_html=MEND_LIST_HTML,
                      pages={MEND_URL: MEND_PAGE})
    out = tmp_path / "nomend_out"
    rc = mod.main(["--site", "gov", "--out", str(out), "--max-pages", "3",
                   "--no-mend-titles"])
    text = _capture(capsys)
    assert rc == 0, text
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["params"]["mend_titles"] is False
    entry = manifest["docs"][0]
    assert entry["title_mended"] is False
    assert entry["body_paras_before"] == entry["body_paras_after"] == 5
    lines = (out / entry["path"]).read_text(encoding="utf-8").splitlines()
    assert lines[0] == MEND_PART1 and lines[1] == MEND_PART2


def test_from_dir_does_not_mend_titles(mod, capsys, tmp_path):
    """--from-dir 导入的外部文本不做任何改写（要求 4）。"""
    src = tmp_path / "export_mend"
    src.mkdir()
    (src / "手写标题样例.txt").write_text(
        "\n".join([MEND_TITLE, PARA_ONE, PARA_TWO]), encoding="utf-8")
    out = tmp_path / "from_mend"
    rc = mod.main(["--site", "gov", "--from-dir", str(src), "--out", str(out)])
    assert rc == 0, _capture(capsys)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["params"]["mend_titles"] is False
    entry = manifest["docs"][0]
    assert entry["title_mended"] is False
    assert entry["body_paras_before"] == entry["body_paras_after"] == 3
    lines = (out / entry["path"]).read_text(encoding="utf-8").splitlines()
    assert lines[0] == MEND_TITLE, "from-dir 不得删除/改写首段"


def test_help_lists_mend_flags(mod):
    help_text = mod._build_parser().format_help()
    assert "--mend-titles" in help_text
    assert "--no-mend-titles" in help_text


# ---------------------------------------------------------------- from-dir
def _make_export_dir(tmp_path: Path) -> Path:
    src = tmp_path / "export"
    src.mkdir()
    (src / "长文一.md").write_text(
        "# 关于安全生产的通知\n\n" + PARA_ONE + "\n\n" + PARA_TWO, encoding="utf-8")
    (src / "长文二.txt").write_text(PARA_ONE + "\n\n" + PARA_TWO, encoding="utf-8")
    (src / "短文.txt").write_text("太短了。", encoding="utf-8")
    return src


def test_from_dir_imports_and_writes_manifest(mod, capsys, tmp_path):
    src = _make_export_dir(tmp_path)
    out = tmp_path / "from_out"
    rc = mod.main(["--site", "gov", "--from-dir", str(src), "--out", str(out)])
    text = _capture(capsys)
    assert rc == 0, text
    files = sorted((out / "gov").glob("*.txt"))
    assert len(files) == 2, [p.name for p in files]
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "from-dir"
    assert manifest["robots"]["state"] == "not-applicable"
    assert len(manifest["docs"]) == 2
    assert manifest["docs"][0]["robots_allowed"] is None
    assert manifest["docs"][0]["source_url"].endswith((".md", ".txt"))
    assert any("过短" in f["reason"] for f in manifest["failed"]), manifest["failed"]
    assert manifest["stats"]["docs"] == 2
    assert manifest["stats"]["failed"] == 1


def test_from_dir_restricted_requires_authorization(mod, capsys, tmp_path):
    src = _make_export_dir(tmp_path)
    out = tmp_path / "from_restricted"
    rc = mod.main(["--site", "people", "--from-dir", str(src), "--out", str(out)])
    text = _capture(capsys)
    assert rc == 3, f"受限站点的 from-dir 导入同样要授权，实际 {rc}\n{text}"
    assert not out.exists()

    auth = _write_auth(tmp_path)
    rc = mod.main(["--site", "people", "--from-dir", str(src),
                   "--authorization", str(auth), "--out", str(out)])
    assert rc == 0, _capture(capsys)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["authorization"]["doc_ref"].startswith("测试单位")
    assert manifest["docs"][0]["authorization_id"].startswith("测试单位")
    # 授权文件副本 + 哈希留档
    copied = out / "_authorization.json"
    assert copied.exists() and copied.read_bytes() == auth.read_bytes()
    assert mod.hashlib.sha256(auth.read_bytes()).hexdigest() == \
        manifest["authorization"]["sha256"]


def test_from_dir_dry_run_writes_nothing(mod, capsys, tmp_path):
    src = _make_export_dir(tmp_path)
    out = tmp_path / "from_dry"
    rc = mod.main(["--site", "gov", "--from-dir", str(src),
                   "--dry-run", "--out", str(out)])
    text = _capture(capsys)
    assert rc == 0, text
    assert not out.exists()
    assert "dry-run" in text


def test_from_dir_missing_or_empty_is_error(mod, capsys, tmp_path):
    out = tmp_path / "from_bad"
    empty = tmp_path / "empty_export"
    empty.mkdir()
    rc = mod.main(["--site", "gov", "--from-dir", str(empty), "--out", str(out)])
    assert rc == 1, _capture(capsys)
    rc = mod.main(["--site", "gov", "--from-dir", str(tmp_path / "nope"),
                   "--out", str(out)])
    assert rc == 2, _capture(capsys)


# ---------------------------------------------------------------- 独立性/文档
def test_script_does_not_import_product_modules():
    """采集工具是独立脚本：不得 import gwtool 产品模块（避免打包/运行时耦合）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    hits = re.findall(r"^\s*(?:from|import)\s+gwtool\b.*$", text, flags=re.M)
    assert not hits, f"采集工具不应依赖产品模块：{hits}"


def test_compliance_docs_exist_and_cover_required_sections():
    assert EXAMPLE_AUTH.exists(), "缺少授权文件示例"
    auth = json.loads(EXAMPLE_AUTH.read_text(encoding="utf-8"))
    for field in ("granted_by", "doc_ref", "granted_at", "sites", "scope",
                  "contact"):
        assert auth.get(field), f"授权示例缺少字段 {field}"

    assert CHECKLIST.exists(), "缺少采集合规检查表"
    text = CHECKLIST.read_text(encoding="utf-8")
    for needle in ("robots", "Crawl-delay", "120", "授权", "退出码",
                   "doc_ref", "manifest", "脱敏", "不得再分发", "签字"):
        assert needle in text, f"合规检查表缺少关键内容：{needle}"
