# -*- coding: utf-8 -*-
"""语料采集合规工具（开发期本地运行，**不进产品运行时**）。

为什么单独存在
--------------
`eval_corrector.py --corpus` 需要**真实公文**当负样本，但"从网上抓"和"从别的
系统导出"都涉及两层合规：

1. **站点条款 / robots**：不同站点规则差别极大（实测：gov.cn robots 明确
   `Allow: /1` 并逐条 Disallow；81.cn robots 404；people.com.cn 无 Disallow 但
   `Crawl-delay: 120`）。工具必须逐 URL 判定，而不是"能打开就抓"。
2. **著作权 / 授权**：人民网、求是网、解放军报、国防部网的内容受著作权保护，
   **没有书面授权不得批量采集**。所以本工具把除 gov.cn 之外的站点全部标记为
   `restricted`：没有授权文件一律拒绝（退出码 3），并明确列出需要哪些字段。

本工具只做三件事：按站点配置抓取/导入、把正文清洗成纯文本、把"抓了什么、
依据什么授权、robots 是否允许"写进 `manifest.json`。它**不写产品代码、不接
数据库、不联网做隐藏请求**：每次请求都有间隔、有上限、有 UA、有留痕。

站点配置（内置常量，`--list-url` 可覆盖列表页）
------------------------------------------------
    gov       公开政府信息（robots 允许；默认入库 20 篇）
    people    人民网（restricted；robots Crawl-delay=120，极不推荐批量）
    qstheory  求是网（restricted）
    junbao    解放军报 81.cn（restricted；robots 404 ≠ 可批量转载）
    mod       国防部网（restricted；本机曾不可达）

输出契约
--------
    <out>/<site>/<YYYYMMDD>_<slug>.txt   正文纯文本（UTF-8，一段一行，空行分段）
    <out>/manifest.json                  source_url/site/title/fetched_at/chars/
                                         sha256/authorization_id/license_note/
                                         robots_allowed + 本次参数与 skipped 记录
    <out>/_authorization.json            授权文件副本（给了 --authorization 时才写）

退出码
------
    0 = 成功（含 dry-run / 无正文可存的"跑完"）
    1 = 运行期失败（网络不可用、robots 无法确认、列表页抓不到、无输入文件）
    2 = 用法错误（参数非法、授权文件不是合法 JSON）
    3 = 授权不足（restricted 站点缺授权文件 / sites 不含该站 / doc_ref 为空）

用法
----
    # 只打印计划，不写盘、不抓正文（robots 也不请求时加 --offline）
    python scripts/fetch_corpus.py --site gov --dry-run --offline

    # 真正采集 gov.cn（公开政府信息；robots 允许的 URL 才会被抓）
    python scripts/fetch_corpus.py --site gov --out corpus_out --max-docs 20

    # 受限站点：先拿到书面授权，再按授权文件采集
    python scripts/fetch_corpus.py --site people --out corpus_out \
        --authorization doc/corpus/AUTHORIZATION.example.json --max-docs 5

    # B 路线：把数据库/手工导出的 txt/md 批量导入同一目录结构
    python scripts/fetch_corpus.py --site gov --from-dir D:/导出语料 --out corpus_out
"""
from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.robotparser import RobotFileParser

# ---------------------------------------------------------------- 常量与站点表
DEFAULT_USER_AGENT = (
    "gwtool-corpus-bot/1.0 (+local research use only; robots-aware; "
    "contact: see authorization file)")
DEFAULT_DELAY_S = 2.0
MIN_DELAY_S = 1.0                 # 合规下限：不允许比 1 秒更快
MAX_DOCS_HARD_CAP = 100           # 单次运行的绝对上限，防止误开大批量
# 正文质量闸门（v1.12.0 D 批补充）：gov.cn 的 HTML 不规范（表格嵌套/未闭合），
# 实测 `lxml` 会只解析出 9 个 div 就截断、正文容器直接丢失 —— 于是 23 个链接
# 全被判"正文过短"却没人知道为什么。因此：
#   · 解析器**优先 stdlib html.parser**（慢但容错），lxml 只作回退；
#   · 容器找不到或正文 <200 字 / 无 <p> 段落 → 判"抽取失败"并写进 manifest.failed。
MIN_CRAWL_DOC_CHARS = 200         # 抓取入库的正文下限（含 <p> 段落校验）
MIN_IMPORT_CHARS = 100            # --from-dir 导入的正文下限（手工导出/库导出）
# 标题碎片合并（v1.12.1 D3）：gov.cn 正文容器把标题拆成 2~3 个 <p>
#（如「国务院关于《国家残疾预防行动计划」+「（2026—2030年）》的批复」），
# 采集后标题跨两段 → 书名号跨段不配对，`corpus_check`/`eval` 按段检查时产生
# 假阳性（实测 3.3 万字里 12 处）。默认合并：拼出完整标题后从正文删除。
MEND_MAX_PARAS = 6                # 只检查正文前 6 段（标题碎片只会出现在开头）
MEND_MAX_PIECE = 42               # 单个碎片（去空格后）长度上限，避免误并长段落
CONTENT_PARSERS = ("html.parser", "lxml")   # 解析器回退链（顺序即优先级）
DEFAULT_TIMEOUT_S = 10.0

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_AUTH = 3

# 授权文件必填字段（restricted 站点缺一不可；gov 只记录不强制）
AUTH_FIELDS = ("granted_by", "doc_ref", "granted_at", "sites", "scope", "contact")

SITES: dict[str, dict] = {
    "gov": {
        "label": "中国政府网（公开政府信息）",
        "restricted": False,
        "list_url": "https://www.gov.cn/zhengce/",
        # 注：`/zhengce/zuixin/` 是 JS 渲染页（静态 HTML 里没有正文链接，实测 48 个
        # <a> 全是导航），默认改用同频道的静态列表页；可用 --list-url 覆盖。
        "list_url_note": "默认列表页为静态可解析页；zuixin 页需 JS，静态抓取拿不到链接",
        "robots_user_agent": DEFAULT_USER_AGENT,
        "min_delay_s": DEFAULT_DELAY_S,
        "max_pages_default": 10,
        "max_docs_default": 20,
        "link_pattern": r"https?://www\.gov\.cn/(?:zhengce/content|1\d{5})/",
        "content_selectors": ("#UCAP-CONTENT", ".pages_content",
                              "div.trs_editor_view"),
        "license_note": ("公开政府信息；遵循 robots 与转载规范，仅作本地内部评测语料，"
                         "不对外分发、不再许可"),
        "robots_note": ("www.gov.cn/robots.txt 实测 200：Allow: /1，并逐条 Disallow "
                        "/2016gov/、/2016shuju/ 等；必须逐 URL 判定"),
    },
    "people": {
        "label": "人民网",
        "restricted": True,
        "list_url": "http://www.people.com.cn/",
        "robots_user_agent": DEFAULT_USER_AGENT,
        "min_delay_s": 120.0,     # robots: Crawl-delay: 120
        "max_pages_default": 5,
        "max_docs_default": 5,
        "link_pattern": r"https?://[a-z0-9.]*people\.com\.cn/",
        "content_selectors": ("div.rm_txt_con", "div#rwb_zw", "div.content",
                              "article"),
        "license_note": ("人民网内容受著作权保护；须取得书面授权（doc_ref）；"
                         "robots Crawl-delay=120，极不推荐批量采集"),
        "robots_note": "people.com.cn/robots.txt：Disallow 为空，但 Crawl-delay: 120",
    },
    "qstheory": {
        "label": "求是网",
        "restricted": True,
        "list_url": "http://www.qstheory.cn/",
        "robots_user_agent": DEFAULT_USER_AGENT,
        "min_delay_s": DEFAULT_DELAY_S,
        "max_pages_default": 5,
        "max_docs_default": 5,
        "link_pattern": r"https?://www\.qstheory\.cn/",
        "content_selectors": ("div#content", "div.content", "div.TRS_Editor",
                              "article"),
        "license_note": ("求是网内容受著作权保护；须取得书面授权（doc_ref），"
                         "仅作本地内部评测语料"),
        "robots_note": "qstheory.cn/robots.txt 实测 301（跟随到 https 后取用）",
    },
    "junbao": {
        "label": "中国军网 / 解放军报（81.cn）",
        "restricted": True,
        "list_url": "http://www.81.cn/",
        "robots_user_agent": DEFAULT_USER_AGENT,
        "min_delay_s": DEFAULT_DELAY_S,
        "max_pages_default": 5,
        "max_docs_default": 5,
        "link_pattern": r"https?://www\.81\.cn/",
        "content_selectors": ("div.article", "div#article", "div.TRS_Editor",
                              "article"),
        "license_note": ("解放军报/中国军网内容受著作权保护；须取得书面授权（doc_ref）。"
                         "robots.txt 404（无声明）不等于可以批量转载"),
        "robots_note": "81.cn/robots.txt 实测 404（无 robots 声明）——仍须授权",
    },
    "mod": {
        "label": "国防部网",
        "restricted": True,
        "list_url": "http://www.mod.gov.cn/",
        "robots_user_agent": DEFAULT_USER_AGENT,
        "min_delay_s": DEFAULT_DELAY_S,
        "max_pages_default": 5,
        "max_docs_default": 5,
        "link_pattern": r"https?://www\.mod\.gov\.cn/",
        "content_selectors": ("div.TRS_Editor", "div#content", "article"),
        "license_note": ("国防部网内容受著作权保护；须使用前取得书面授权（doc_ref），"
                         "仅作本地内部评测语料"),
        "robots_note": "mod.gov.cn/robots.txt：本机曾不可达（000），以实际探测为准",
    },
}


class FetchError(RuntimeError):
    """网络层可预期失败（HTTP 错误、超时、连接失败）。"""


# ---------------------------------------------------------------- 网络（可注入）
def _decode_bytes(raw: bytes) -> str:
    """按 UTF-8 / GB18030 顺序解码（政府站点常见两种编码）。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _http_get(url: str, *, timeout: float = DEFAULT_TIMEOUT_S,
              user_agent: str = DEFAULT_USER_AGENT) -> tuple[int, str]:
    """GET 一个 URL，返回 (状态码, 文本)。

    测试通过 monkeypatch 本函数即可完全离线；工具其它部分不直接碰网络。
    HTTP 4xx 也抛 `FetchError`（调用方从异常文本判断是否 404 —— 对 robots
    来说 404 表示"没有声明"，对正文页来说表示"抓不到"）。
    """
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}") from exc
    except Exception as exc:                       # URLError / timeout / SSL
        raise FetchError(str(exc)) from exc
    if status >= 400:
        raise FetchError(f"HTTP {status}")
    return status, _decode_bytes(raw)


# ---------------------------------------------------------------- 限速
class RateLimiter:
    """固定最小间隔的限速器（clock/sleep 可注入，便于离线测试）。

    合规要求：相邻两次网络请求的**间隔**不小于 delay 秒。首次请求不等待。
    """

    def __init__(self, delay_s: float, *, clock=None, sleep=None):
        self.delay_s = max(0.0, float(delay_s))
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._last: float | None = None
        self.waited_s = 0.0

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remain = self.delay_s - (now - self._last)
            if remain > 0:
                self._sleep(remain)
                self.waited_s += remain
                now = self._clock()
        self._last = now


def effective_delay_s(cli_delay: float, site_cfg: dict,
                      robots_crawl_delay: "int | float | None" = None) -> float:
    """实际间隔 = max(命令行、站点下限、robots 声明的 crawl-delay)。"""
    delay = max(float(cli_delay), float(site_cfg.get("min_delay_s") or 0.0))
    if robots_crawl_delay:
        delay = max(delay, float(robots_crawl_delay))
    return delay


# ---------------------------------------------------------------- 授权闸门
def load_authorization(path: str) -> dict:
    """读授权 JSON；不是合法 JSON 或不是对象时抛 ValueError（上层判 2）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("授权文件必须是 JSON 对象")
    return data


def authorization_gap(site_name: str, auth: "dict | None") -> str:
    """返回授权不足之处（空串 = 通过）。restricted 站点的唯一放行依据。"""
    required = "、".join(AUTH_FIELDS)
    if auth is None:
        return (f"缺少授权文件（{site_name} 为受限站点）。需要 --authorization <json>，"
                f"且文件包含字段：{required}；其中 sites 必须包含 \"{site_name}\"，"
                "doc_ref 不能为空")
    sites = auth.get("sites")
    if not isinstance(sites, list) or site_name not in sites:
        return (f"授权文件的 sites 未包含 \"{site_name}\"（当前：{sites!r}）。"
                f"需要字段：{required}")
    doc_ref = auth.get("doc_ref")
    if not isinstance(doc_ref, str) or not doc_ref.strip():
        return f"授权文件的 doc_ref 为空或不是字符串。需要字段：{required}"
    return ""


def authorization_id(auth: "dict | None") -> str:
    """给人看的授权标识：`granted_by:doc_ref`（无授权时为空串）。"""
    if not auth:
        return ""
    doc_ref = str(auth.get("doc_ref") or "").strip()
    granted_by = str(auth.get("granted_by") or "").strip()
    if granted_by:
        return f"{granted_by}:{doc_ref}"
    return doc_ref


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- robots
def fetch_robots(list_url: str, *, user_agent: str,
                 timeout: float = DEFAULT_TIMEOUT_S,
                 limiter: "RateLimiter | None" = None) -> dict:
    """取同源 robots.txt 并解析。

    返回 dict：url / state(ok|missing|unreachable) / parser / crawl_delay / note。
    - ok：拿到并解析；
    - missing：HTTP 4xx（按 RFC 9309 视为"没有声明"，允许抓取）；
    - unreachable：网络错误/5xx（真实采集会终止，dry-run 仅提示）。
    """
    parts = urllib.parse.urlsplit(list_url)
    robots_url = urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, "/robots.txt", "", ""))
    if limiter is not None:
        limiter.wait()
    try:
        _status, text = _http_get(robots_url, timeout=timeout, user_agent=user_agent)
    except FetchError as exc:
        message = str(exc)
        missing = "HTTP 4" in message
        return {"url": robots_url,
                "state": "missing" if missing else "unreachable",
                "parser": None, "crawl_delay": None,
                "note": ("未找到 robots.txt（4xx）→ 视为无声明"
                         if missing else f"无法获取 robots.txt：{message}")}
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    try:
        crawl_delay = parser.crawl_delay(user_agent)
    except Exception:
        crawl_delay = None
    return {"url": robots_url, "state": "ok", "parser": parser,
            "crawl_delay": crawl_delay,
            "note": f"robots 已获取（Crawl-delay={crawl_delay}）"
                    if crawl_delay else "robots 已获取"}


def robots_allows(robots: dict, user_agent: str, url: str) -> bool:
    """按 robots 判定单个 URL 是否可抓（无声明/解析失败时放行并留痕）。"""
    parser = robots.get("parser")
    if parser is None:
        return True
    try:
        return bool(parser.can_fetch(user_agent, url))
    except Exception:
        return True


# ---------------------------------------------------------------- HTML → 纯文本
# 教训（v1.12.0 D 批，主控实测）：gov.cn 正文页 HTML 不规范（表格嵌套/未闭合）。
# `BeautifulSoup(html, "lxml")` 会**只解析出 9 个 div 就截断**，`#UCAP-CONTENT`
# 容器随之消失 → 23 个链接全被判"正文过短"、0 篇入库。当时只有长度兜底，
# 失败原因不可见。因此这里做成三条硬约束：
#   ① 解析器优先 stdlib `html.parser`（容错好），`lxml` 仅作回退；
#   ② 正文容器按**多级候选**匹配，最后有"最大文本块"兜底；
#   ③ 抽取质量必须校验（正文 ≥200 字且 <p> 段落 >0），不达标**显式失败**，
#      写进 manifest.failed —— 绝不静默当成"正文过短"跳过。
def _load_soup():
    """bs4 是运行时既有依赖；缺失时返回 None（走纯 re 兜底，不新增依赖）。"""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    return BeautifulSoup


def _soup_parsers() -> "tuple[str, ...]":
    """解析器回退链（顺序即优先级；html.parser 在前，见上方教训）。"""
    return CONTENT_PARSERS


_BLOCK_TAGS = ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
               "tr", "section", "article", "blockquote", "pre", "table", "td")


def _regex_html_to_text(fragment: str) -> str:
    """纯 re 兜底：去 script/style/注释/标签，块级标签转换行。"""
    text = re.sub(r"(?is)<!--.*?-->", " ", fragment)
    text = re.sub(r"(?is)<(script|style|noscript|iframe|svg)\b[^>]*>.*?</\1>",
                  " ", text)
    for tag in _BLOCK_TAGS:
        text = re.sub(r"(?is)</?%s\b[^>]*>" % tag, "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return text


def _regex_select(html: str, selector: str) -> str:
    """纯 re 兜底的极简选择器：只支持 `#id` / `.class` / 标签名。"""
    for part in [s.strip() for s in (selector or "").split(",") if s.strip()]:
        if part.startswith("#"):
            node_id = re.escape(part[1:])
            match = re.search(
                r"(?is)<([a-zA-Z][\w-]*)\b[^>]*\bid=[\"']%s[\"'][^>]*>(.*?)</\1>"
                % node_id, html)
        elif part.startswith("."):
            node_cls = re.escape(part[1:])
            match = re.search(
                r"(?is)<([a-zA-Z][\w-]*)\b[^>]*\bclass=[\"'][^\"']*%s[^\"']*[\"']"
                r"[^>]*>(.*?)</\1>" % node_cls, html)
        else:
            tag = re.escape(part)
            match = re.search(r"(?is)<(%s)\b[^>]*>(.*?)</\1>" % tag, html)
        if match:
            return match.group(2)
    return ""


def _normalize_text(text: str) -> str:
    """正文归一：统一换行、去行尾空白、空行折叠成段落分隔。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    lines: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if line:
            lines.append(line)
        elif lines and lines[-1] != "":
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _squash_spaces(text: str) -> str:
    """去掉所有空白（比较"标题碎片/正文标题"时用）。"""
    return re.sub(r"\s+", "", text or "")


def count_paragraphs(text: str) -> int:
    """正文段数（非空行数）。"""
    return len([ln for ln in (text or "").split("\n") if ln.strip()])


def mend_title_fragments(text: str, title: str, *,
                         enabled: bool = True) -> "tuple[str, dict]":
    """合并被正文容器拆成多段的标题；拼回完整标题后从正文删除。

    gov.cn 会把标题拆成 2~3 个 ``<p>``（书名号/破折号被拆开），采集后标题跨段，
    按段检查时书名号不配对、产生假阳性。判定刻意保守：

      · 只看正文前 ``MEND_MAX_PARAS`` 段；
      · 每个碎片去空格后必须 < ``MEND_MAX_PIECE`` 字，且**逐段拼接是标题前缀**；
      · 拼出的结果与标题（去空格）**完全相同**才动手；
      · 只删这几段标题碎片，其它段落一字不动；``--from-dir`` 不调用本函数。
    """
    paras = [ln for ln in (text or "").split("\n") if ln.strip()]
    before = len(paras)
    info = {"title_mended": False, "body_paras_before": before,
            "body_paras_after": before}
    title_norm = _squash_spaces(title)
    if not enabled or not paras or not title_norm:
        return text, info
    merged = ""
    used = 0
    for line in paras[:MEND_MAX_PARAS]:
        piece = _squash_spaces(line)
        if not piece or len(piece) >= MEND_MAX_PIECE:
            break
        candidate = merged + piece
        if not title_norm.startswith(candidate):
            break
        merged = candidate
        used += 1
        if merged == title_norm:
            break
    if not used or merged != title_norm:
        return text, info
    remain = paras[used:]
    info["title_mended"] = True
    info["body_paras_after"] = len(remain)
    return "\n".join(remain), info


def _pick_container(soup, selectors: "tuple[str, ...]"):
    """多级容器匹配：候选选择器 → 最大文本块兜底。返回 (node, 命中方式)。"""
    for selector in selectors or ():
        try:
            node = soup.select_one(selector)
        except Exception:                      # 非法/不支持的选择器
            node = None
        if node is not None and node.get_text(strip=True):
            return node, selector
    best, best_len = None, 0
    for tag_name in ("article", "main", "div", "section", "td"):
        try:
            nodes = soup.find_all(tag_name)
        except Exception:
            continue
        for node in nodes:
            try:
                length = len(node.get_text(" ", strip=True))
            except Exception:
                continue
            if length > best_len:
                best, best_len = node, length
    if best is not None and best_len:
        return best, "largest-block"
    return None, ""


def _quality_ok(text: str, p_count: int) -> bool:
    return len(text) >= MIN_CRAWL_DOC_CHARS and p_count > 0


def _finish_quality(cand: dict) -> dict:
    """给未通过质量闸门的抽取结果补上显式失败原因（进 manifest.failed）。"""
    bits = []
    if len(cand.get("text", "")) < MIN_CRAWL_DOC_CHARS:
        bits.append(f"正文过短（{len(cand.get('text', ''))} 字 < "
                    f"{MIN_CRAWL_DOC_CHARS}）")
    if int(cand.get("p_count") or 0) <= 0:
        bits.append("未找到 <p> 段落（正文容器可能丢失/解析被截断）")
    cand["ok"] = False
    cand["chars"] = len(cand.get("text", ""))
    cand["reason"] = "抽取失败：" + ("；".join(bits) if bits else "正文未通过质量校验")
    return cand


def extract_content(html: str, selectors: "tuple[str, ...] | str" = ()) -> dict:
    """抽取正文并做质量判定。

    返回 dict：text / ok / chars / parser / container / p_count / reason。
    bs4 不可用时自动走纯 re 兜底；仍不达标则 ok=False（调用方计入 manifest.failed）。
    """
    if isinstance(selectors, str):
        selectors = (selectors,) if selectors else ()
    selectors = tuple(selectors)
    soup_cls = _load_soup()
    best: dict = {"text": "", "parser": "", "container": "", "p_count": 0}
    if soup_cls is not None:
        for parser in _soup_parsers():
            try:
                soup = soup_cls(html or "", parser)
            except Exception:                  # 解析器不可用/实现缺失 → 换下一个
                continue
            for bad in soup(["script", "style", "noscript", "iframe", "svg"]):
                bad.decompose()
            node, how = _pick_container(soup, selectors)
            if node is None:
                text, p_count = "", 0
            else:
                text = _normalize_text(node.get_text("\n"))
                try:
                    p_count = len(node.find_all("p"))
                except Exception:
                    p_count = 0
            cand = {"text": text, "parser": parser, "container": how,
                    "p_count": p_count}
            if _quality_ok(text, p_count):
                cand.update(ok=True, chars=len(text), reason="")
                return cand
            if len(text) > len(best["text"]):
                best = cand
        return _finish_quality(best)

    fragment = ""
    used = ""
    for selector in selectors or ("",):
        fragment = _regex_select(html or "", selector) if selector else ""
        if fragment.strip():
            used = selector
            break
    if not fragment.strip():
        fragment = html or ""
        used = "regex-whole"
    text = _normalize_text(_regex_html_to_text(fragment))
    p_count = len(re.findall(r"(?is)<p\b", fragment))
    cand = {"text": text, "parser": "regex", "container": used,
            "p_count": p_count}
    if _quality_ok(text, p_count):
        cand.update(ok=True, chars=len(text), reason="")
        return cand
    return _finish_quality(cand)


def html_to_text(html: str, selector: str = "") -> str:
    """兼容旧接口：只返回正文文本（不做质量判定；供单测/一次性检查用）。"""
    return extract_content(html, selector)["text"]


def _sanitize_slug(text: str, fallback: str = "doc") -> str:
    """标题/文件名 → 安全 slug（保留中文、字母、数字；其余压成连字符）。"""
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (text or "").strip())
    slug = slug.strip("-_")
    return (slug[:40].strip("-_") or fallback)


def extract_links(html: str, base_url: str, pattern: str,
                  max_links: int) -> list[dict]:
    """从列表页抽取候选正文链接（保留出现顺序、去重、按 pattern 过滤）。

    bs4 解析异常时自动回退纯 re（列表页同样可能不规范，不能因此丢整页链接）。
    """
    found: list[tuple[str, str]] = []
    soup_cls = _load_soup()
    parsed = False
    if soup_cls is not None:
        try:
            soup = soup_cls(html or "", _soup_parsers()[0])
            for anchor in soup.find_all("a", href=True):
                found.append((str(anchor.get("href")),
                              anchor.get_text(" ", strip=True)))
            parsed = True
        except Exception:
            parsed = False
    if not parsed:
        for href, inner in re.findall(
                r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html or ""):
            found.append((href, html_lib.unescape(re.sub(r"(?s)<[^>]+>", " ", inner))))

    rx = re.compile(pattern) if pattern else None
    out: list[dict] = []
    seen: set[str] = set()
    for href, title in found:
        url = urllib.parse.urljoin(base_url, href.strip())
        if url in seen:
            continue
        if rx is not None and not rx.search(url):
            continue
        seen.add(url)
        out.append({"url": url, "title": (title or "").strip()})
        if len(out) >= max_links:
            break
    return out


# ---------------------------------------------------------------- 输出与留痕
def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_text(path: Path, text: str) -> None:
    """写 UTF-8 文本。

    Python 3.9 的 ``Path.write_text`` 不支持传换行参数，这里显式以 UTF-8 +
    ``newline="\\n"`` 打开文件写入，保证跨平台行尾一致。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=1) + "\n")


def _doc_entry(*, source_url: str, site: str, title: str, rel_path: str,
               text: str, robots_allowed: "bool | None", auth_id: str,
               license_note: str, mode: str) -> dict:
    return {
        "source_url": source_url,
        "site": site,
        "title": title,
        "path": rel_path,
        "chars": len(text),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "fetched_at": _now_iso(),
        "robots_allowed": robots_allowed,
        "authorization_id": auth_id,
        "license_note": license_note,
        "mode": mode,
    }


def _manifest_base(args, site_cfg: dict, site_name: str,
                   auth: "dict | None", auth_sha: str) -> dict:
    return {
        "tool": "fetch_corpus",
        "version": 1,
        "site": site_name,
        "site_label": site_cfg["label"],
        "mode": "from-dir" if args.from_dir else "crawl",
        "generated_at": _now_iso(),
        "license_note": site_cfg["license_note"],
        "robots_note": site_cfg["robots_note"],
        "authorization": ({
            "authorization_id": authorization_id(auth),
            "granted_by": auth.get("granted_by", ""),
            "doc_ref": auth.get("doc_ref", ""),
            "sites": auth.get("sites", []),
            "sha256": auth_sha,
        } if auth is not None else None),
        "docs": [],
        "failed": [],
        "skipped": [],
    }


def _finish_output(out_dir: Path, manifest: dict, auth_path: str,
                   auth_sha: str) -> None:
    """写 manifest，并在有授权文件时复制一份到输出目录留档。"""
    if auth_path:
        target = out_dir / "_authorization.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(auth_path, str(target))
        manifest["authorization_copy"] = "_authorization.json"
        manifest["authorization_sha256"] = auth_sha
    _write_json(out_dir / "manifest.json", manifest)


# ---------------------------------------------------------------- 规划/校验
def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="语料采集合规工具（受限站点必须有授权文件；默认低速率、小批量）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("退出码：0 成功 / 1 运行期失败 / 2 用法错误 / "
                "3 授权不足（restricted 站点缺授权）"))
    ap.add_argument("--site", required=True, choices=tuple(SITES),
                    help="站点：" + " / ".join(SITES))
    ap.add_argument("--out", default="corpus_out", help="输出根目录（默认 corpus_out）")
    ap.add_argument("--from-dir", default="",
                    help="B 路线：把已导出的 .txt/.md 目录批量导入（不抓网）")
    ap.add_argument("--list-url", default="",
                    help="覆盖站点列表页 URL（默认用站点配置）")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="最多尝试的候选正文页数（默认取站点配置）")
    ap.add_argument("--max-docs", type=int, default=None,
                    help=f"最多入库篇数（默认取站点配置，硬上限 {MAX_DOCS_HARD_CAP}）")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_S,
                    help=f"每请求最小间隔秒数（默认 {DEFAULT_DELAY_S}，"
                         f"不得低于 {MIN_DELAY_S}，且不低于站点下限）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                    help="单次请求超时秒数")
    ap.add_argument("--robot-user-agent", default="",
                    help="robots 判定与请求使用的 UA（默认取站点配置）")
    ap.add_argument("--mend-titles", dest="mend_titles", action="store_true",
                    default=True,
                    help="合并被正文容器拆成多段的标题，并在正文重复时删除"
                         "（默认开启；对应 gov.cn 标题跨段问题）")
    ap.add_argument("--no-mend-titles", dest="mend_titles", action="store_false",
                    help="关闭标题碎片合并（保留原始分段，便于对比/取证）")
    ap.add_argument("--authorization", default="",
                    help="授权文件 JSON（restricted 站点必需；格式见 "
                         "doc/corpus/AUTHORIZATION.example.json）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印计划与 robots 判定：不写盘、不抓正文")
    ap.add_argument("--probe", action="store_true",
                    help="配合 --dry-run：请求列表页与正文做**容器命中/失败统计**，"
                         "用于判断站点模板是否变化（仍不写盘）")
    ap.add_argument("--offline", action="store_true",
                    help="不发起任何网络请求（仅与 --dry-run 同用）")
    return ap


def _validate_args(args, ap: argparse.ArgumentParser, site_cfg: dict) -> None:
    if args.delay < MIN_DELAY_S:
        ap.error(f"--delay 不得低于 {MIN_DELAY_S} 秒（合规下限；站点另有更严下限）")
    if args.offline and not args.dry_run:
        ap.error("--offline 只能与 --dry-run 同用（否则无法采集）")
    if args.probe and not args.dry_run:
        ap.error("--probe 必须与 --dry-run 同用（只做统计、不写盘）")
    if args.probe and args.offline:
        ap.error("--offline 与 --probe 冲突：probe 需要请求正文才能统计容器命中")
    max_docs = args.max_docs if args.max_docs is not None else site_cfg["max_docs_default"]
    max_pages = args.max_pages if args.max_pages is not None else site_cfg["max_pages_default"]
    if max_docs <= 0 or max_pages <= 0:
        ap.error("--max-docs / --max-pages 必须为正整数")
    if max_docs > MAX_DOCS_HARD_CAP:
        ap.error(f"--max-docs 超过硬上限 {MAX_DOCS_HARD_CAP}（防止误开大批量采集）")
    if args.timeout <= 0:
        ap.error("--timeout 必须为正数")


def _print_authorization_block(site_name: str, gap: str) -> None:
    site_cfg = SITES[site_name]
    print("=" * 72)
    print("授权不足（退出码 3）：拒绝采集")
    print("=" * 72)
    print(f"站点：{site_name}（{site_cfg['label']}）为**受限站点**：{gap}")
    print("")
    print("需要一份书面授权，并以 JSON 文件提供（--authorization <json>）：")
    print("  {")
    print('    "granted_by": "授权方（单位/部门 + 联系人）",')
    print('    "doc_ref": "授权文号或函件编号（不能为空）",')
    print('    "granted_at": "YYYY-MM-DD",')
    print(f'    "sites": ["{site_name}"],')
    print('    "scope": "用途与边界（仅本地评测、不得再分发）",')
    print('    "contact": "授权方联系方式"')
    print("  }")
    print("模板：doc/corpus/AUTHORIZATION.example.json")
    print(f"许可说明：{site_cfg['license_note']}")


# ---------------------------------------------------------------- crawl 模式
def _print_dry_run(args, site_cfg: dict, site_name: str, *, robots: dict,
                   delay_s: float, max_pages: int, max_docs: int,
                   auth: "dict | None") -> None:
    list_url = args.list_url or site_cfg["list_url"]
    out_dir = Path(args.out)
    print("=" * 72)
    print("语料采集计划（dry-run：不写盘、不抓正文）")
    print("=" * 72)
    print(f"站点：{site_name}（{site_cfg['label']}）")
    print(f"列表页：{list_url}")
    print(f"输出目录：{out_dir / site_name}/（文件名 <YYYYMMDD>_<slug>.txt）")
    print(f"限速：每请求间隔 ≥{delay_s:.1f}s（站点下限 {site_cfg['min_delay_s']}s）")
    print(f"数量：候选页 ≤{max_pages}、入库 ≤{max_docs}")
    if args.offline:
        print("robots：跳过（--offline：本次不发起任何网络请求）")
    else:
        print(f"robots：{robots['url']} → {robots['state']}（{robots['note']}）")
        if robots.get("parser") is not None:
            allowed = robots_allows(robots, _user_agent(args, site_cfg), list_url)
            print(f"        列表页 robots 判定：{'允许' if allowed else '禁止'}")
    auth_text = (authorization_id(auth) if auth is not None
                 else ("无需授权（公开政府文本，仅受 robots 约束）"
                       if not site_cfg["restricted"] else "缺失"))
    print(f"授权：{auth_text}")
    print(f"许可说明：{site_cfg['license_note']}")
    print("正文容器候选：" + " → ".join(site_cfg["content_selectors"])
          + " → 最大文本块兜底")
    print(f"正文质量闸门：≥{MIN_CRAWL_DOC_CHARS} 字且 <p> 段落 >0；"
          "不达标计入 manifest.failed（不静默跳过）")
    mend_state = "开启" if getattr(args, "mend_titles", True) else "关闭"
    print(f"标题碎片合并：{mend_state}（前 {MEND_MAX_PARAS} 段内可拼回完整标题"
          f"则从正文删除；--no-mend-titles 关闭）")
    print("解析器链：" + " → ".join(_soup_parsers()) + "（html.parser 容错优先）")
    print("-" * 72)
    print("真实运行将：取列表页 → 解析候选链接 → 逐 URL 过 robots → 抓正文 "
          "→ 清洗为纯文本 → 写 manifest.json")
    print("本次 dry-run 不创建输出目录、不写任何文件、不抓取正文。")
    print("提示：加 --probe 可实际请求列表页与正文，输出"
          "「容器命中/抽取失败」计数（仍不写盘），用于判断站点模板是否变化。")
    print("=" * 72)


def _user_agent(args, site_cfg: dict) -> str:
    return args.robot_user_agent or site_cfg["robots_user_agent"]


def _fetch_and_extract(args, site_cfg: dict, user_agent: str,
                       limiter: "RateLimiter", robots: dict,
                       cand: dict) -> dict:
    """抓一页并抽取正文。

    status：
      ok             抽取通过质量闸门（正文 ≥200 字且有 <p> 段落）
      robots-denied  robots 禁止（不请求）
      fetch-failed   网络/HTTP 失败
      extract-failed 抓到了但抽不出合格正文 → 进 manifest.failed（不静默跳过）
    """
    url = cand["url"]
    base: dict = {"url": url, "title": cand.get("title") or ""}
    if not robots_allows(robots, user_agent, url):
        base.update(status="robots-denied", reason="robots 禁止")
        return base
    try:
        limiter.wait()
        _status, page_html = _http_get(url, timeout=args.timeout,
                                       user_agent=user_agent)
    except FetchError as exc:
        base.update(status="fetch-failed", reason=f"抓取失败：{exc}")
        return base
    result = extract_content(page_html, site_cfg["content_selectors"])
    result.update(base)
    if result.get("ok"):
        # 标题碎片合并（默认开启；--no-mend-titles 关闭）：
        # gov.cn 把标题拆成 2~3 个 <p>，不合并会跨段、书名号不配对。
        text, info = mend_title_fragments(
            result["text"], base["title"],
            enabled=bool(getattr(args, "mend_titles", True)))
        result.update(info)
        result["text"] = text
    result["status"] = "ok" if result.get("ok") else "extract-failed"
    return result


def _probe_stats(results: "list[dict]", candidates: int) -> dict:
    """汇总"候选数 / 容器命中 / 抽取失败 / 跳过"统计（probe 与实跑共用）。"""
    hits: dict[str, int] = {}
    failed_reasons: dict[str, int] = {}
    ok = failed = skipped = 0
    for res in results:
        status = res.get("status")
        if status == "ok":
            ok += 1
        elif status == "extract-failed":
            failed += 1
            key = ("正文过短" if "过短" in (res.get("reason") or "")
                   else "无 <p> 段落/容器丢失")
            failed_reasons[key] = failed_reasons.get(key, 0) + 1
        else:
            skipped += 1
        container = res.get("container") or "(未命中)"
        hits[container] = hits.get(container, 0) + 1
    return {"candidates": candidates, "processed": len(results),
            "ok": ok, "failed": failed,
            "skipped": skipped, "container_hits": hits,
            "failed_reasons": failed_reasons}


def _print_probe_stats(stats: dict) -> None:
    print("  " + "-" * 68)
    print(f"  候选 {stats['candidates']}（已处理 {stats.get('processed', stats['candidates'])}）"
          f"　抽取成功 {stats['ok']}　抽取失败 {stats['failed']}　"
          f"跳过 {stats['skipped']}")
    hits = stats.get("container_hits") or {}
    if hits:
        print("  容器命中：" + "　".join(f"{k}={v}" for k, v in sorted(hits.items())))
    reasons = stats.get("failed_reasons") or {}
    if reasons:
        print("  失败原因：" + "　".join(f"{k}={v}" for k, v in sorted(reasons.items())))
    if stats["candidates"] and not stats["ok"]:
        print("  警告：0 篇抽取成功 —— 站点模板可能已变化，请核对正文容器候选。")


def _run_probe(args, site_cfg: dict, site_name: str, *,
               robots: dict, limiter: "RateLimiter", user_agent: str,
               max_pages: int) -> int:
    """dry-run --probe：请求列表页与正文，输出容器命中/失败计数，零写盘。"""
    list_url = args.list_url or site_cfg["list_url"]
    print("=" * 72)
    print("站点模板探针（dry-run --probe：会请求页面做统计，但不写盘）")
    print("=" * 72)
    print(f"站点：{site_name}　列表页：{list_url}")
    if robots["state"] == "unreachable":
        print(f"错误：无法确认 robots（{robots['note']}）。合规优先，终止。",
              file=sys.stderr)
        return EXIT_RUNTIME
    try:
        limiter.wait()
        _status, list_html = _http_get(list_url, timeout=args.timeout,
                                       user_agent=user_agent)
    except FetchError as exc:
        print(f"错误：列表页抓取失败（{list_url}）：{exc}", file=sys.stderr)
        return EXIT_RUNTIME
    candidates = extract_links(list_html, list_url, site_cfg["link_pattern"],
                               max_pages)
    results = [_fetch_and_extract(args, site_cfg, user_agent, limiter, robots, cand)
               for cand in candidates]
    stats = _probe_stats(results, len(candidates))
    _print_probe_stats(stats)
    print("  本次 --probe 未写任何文件。")
    print("=" * 72)
    return EXIT_OK


def _run_crawl(args, site_cfg: dict, site_name: str,
               auth: "dict | None", auth_sha: str) -> int:
    max_docs = args.max_docs if args.max_docs is not None else site_cfg["max_docs_default"]
    max_pages = args.max_pages if args.max_pages is not None else site_cfg["max_pages_default"]
    list_url = args.list_url or site_cfg["list_url"]
    user_agent = _user_agent(args, site_cfg)
    # 全程共用一个限速器：robots、列表页、正文页之间的间隔都由它兜底。
    limiter = RateLimiter(max(args.delay, float(site_cfg.get("min_delay_s") or 0.0)))

    robots: dict = {"url": "", "state": "skipped", "parser": None,
                    "crawl_delay": None, "note": "--offline：未请求 robots"}
    if not args.offline:
        robots = fetch_robots(list_url, user_agent=user_agent,
                              timeout=args.timeout, limiter=limiter)
    delay_s = effective_delay_s(args.delay, site_cfg, robots.get("crawl_delay"))
    limiter.delay_s = delay_s

    if args.dry_run:
        if args.probe:
            return _run_probe(args, site_cfg, site_name, robots=robots,
                              limiter=limiter, user_agent=user_agent,
                              max_pages=max_pages)
        _print_dry_run(args, site_cfg, site_name, robots=robots, delay_s=delay_s,
                       max_pages=max_pages, max_docs=max_docs, auth=auth)
        return EXIT_OK

    if robots["state"] == "unreachable":
        print(f"错误：无法确认 robots（{robots['note']}）。合规优先，终止采集。",
              file=sys.stderr)
        return EXIT_RUNTIME

    out_dir = Path(args.out)
    manifest = _manifest_base(args, site_cfg, site_name, auth, auth_sha)
    manifest["params"] = {
        "list_url": list_url, "delay_s": args.delay,
        "effective_delay_s": delay_s, "max_pages": max_pages,
        "max_docs": max_docs, "timeout_s": args.timeout,
        "user_agent": user_agent, "dry_run": False,
        "mend_titles": bool(getattr(args, "mend_titles", True)),
    }
    manifest["robots"] = {"url": robots["url"], "state": robots["state"],
                          "crawl_delay": robots.get("crawl_delay"),
                          "note": robots["note"]}

    try:
        limiter.wait()
        _status, list_html = _http_get(list_url, timeout=args.timeout,
                                       user_agent=user_agent)
    except FetchError as exc:
        print(f"错误：列表页抓取失败（{list_url}）：{exc}", file=sys.stderr)
        return EXIT_RUNTIME

    candidates = extract_links(list_html, list_url, site_cfg["link_pattern"],
                               max_pages)
    print(f"站点 {site_name}：列表页解析出 {len(candidates)} 个候选链接"
          f"（上限 {max_pages}）")
    today = _today_str()
    used_names: set[str] = set()
    results: list[dict] = []
    for cand in candidates:
        res = _fetch_and_extract(args, site_cfg, user_agent, limiter, robots, cand)
        results.append(res)
        if res["status"] in ("robots-denied", "fetch-failed"):
            manifest["skipped"].append({"url": res["url"], "reason": res["reason"]})
            print(f"  [skip] {res['reason']}：{res['url']}")
            continue
        if res["status"] != "ok":
            manifest["failed"].append({
                "url": res["url"], "title": res["title"],
                "reason": res["reason"], "chars": res.get("chars", 0),
                "parser": res.get("parser", ""),
                "container": res.get("container", ""),
                "p_count": res.get("p_count", 0)})
            print(f"  [fail] {res['reason']}：{res['url']}")
            continue
        text = res["text"]
        title = res["title"] or "未命名"
        slug = _sanitize_slug(title, fallback=f"doc{len(manifest['docs']) + 1}")
        rel_path = f"{site_name}/{today}_{slug}.txt"
        while rel_path in used_names:
            slug = f"{slug}-{len(used_names)}"
            rel_path = f"{site_name}/{today}_{slug}.txt"
        used_names.add(rel_path)
        _write_text(out_dir / rel_path, text)
        entry = _doc_entry(
            source_url=res["url"], site=site_name, title=title, rel_path=rel_path,
            text=text, robots_allowed=True, auth_id=authorization_id(auth),
            license_note=site_cfg["license_note"], mode="crawl")
        entry.update({
            "title_mended": bool(res.get("title_mended")),
            "body_paras_before": int(res.get("body_paras_before") or 0),
            "body_paras_after": int(res.get("body_paras_after") or 0),
        })
        manifest["docs"].append(entry)
        mended = "，标题碎片已合并删除" if entry["title_mended"] else ""
        print(f"  [ok]   {rel_path}（{len(text)} 字，容器 {res.get('container')}，"
              f"解析器 {res.get('parser')}{mended}）")
        if len(manifest["docs"]) >= max_docs:
            break

    stats = _probe_stats(results, len(candidates))
    stats["docs"] = len(manifest["docs"])
    stats["avg_chars"] = round(
        sum(d["chars"] for d in manifest["docs"]) / len(manifest["docs"]), 1
    ) if manifest["docs"] else 0.0
    manifest["stats"] = stats
    _finish_output(out_dir, manifest, args.authorization, auth_sha)
    print(f"完整采集：候选 {stats['candidates']}（已处理 {stats['processed']}）/ "
          f"入库 {stats['docs']}（平均 {stats['avg_chars']} 字）/ "
          f"抽取失败 {stats['failed']} / 跳过 {stats['skipped']}；"
          f"manifest：{out_dir / 'manifest.json'}")
    _print_probe_stats(stats)
    return EXIT_OK


# ---------------------------------------------------------------- from-dir 模式
def _run_from_dir(args, site_cfg: dict, site_name: str,
                  auth: "dict | None", auth_sha: str) -> int:
    src = Path(args.from_dir)
    if not src.is_dir():
        print(f"错误：--from-dir 不是目录：{src}", file=sys.stderr)
        return EXIT_USAGE
    files = sorted(p for p in src.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".txt", ".md"))
    max_docs = args.max_docs if args.max_docs is not None else site_cfg["max_docs_default"]
    if args.dry_run:
        print("=" * 72)
        print("语料导入计划（dry-run：不写盘）")
        print("=" * 72)
        print(f"站点：{site_name}（{site_cfg['label']}）")
        print(f"导入源：{src}　找到 {len(files)} 个 .txt/.md，最多入库 {max_docs}")
        print(f"输出目录：{Path(args.out) / site_name}/")
        auth_text = authorization_id(auth) or ("无需授权" if not site_cfg["restricted"] else "缺失")
        print(f"授权：{auth_text}")
        print(f"许可说明：{site_cfg['license_note']}")
        print("本次 dry-run 不写任何文件。")
        print("=" * 72)
        return EXIT_OK
    if not files:
        print(f"错误：{src} 下没有 .txt/.md 文件", file=sys.stderr)
        return EXIT_RUNTIME

    out_dir = Path(args.out)
    manifest = _manifest_base(args, site_cfg, site_name, auth, auth_sha)
    manifest["params"] = {"from_dir": str(src), "max_docs": max_docs,
                          "delay_s": args.delay, "dry_run": False,
                          "mend_titles": False}   # 导入的外部文本不做任何改写
    manifest["robots"] = {"url": "", "state": "not-applicable",
                          "note": "from-dir 导入不涉及网络，robots 不适用"}
    today = _today_str()
    used_names: set[str] = set()
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            manifest["skipped"].append({"url": str(path),
                                        "reason": f"读取失败：{exc}"})
            continue
        text = _normalize_text(raw)
        if len(text) < MIN_IMPORT_CHARS:
            manifest["failed"].append({
                "url": str(path), "title": path.stem,
                "reason": f"导入失败：正文过短（{len(text)} 字 < "
                          f"{MIN_IMPORT_CHARS}）",
                "chars": len(text), "parser": "from-dir", "container": "",
                "p_count": 0})
            continue
        title = path.stem
        slug = _sanitize_slug(title, fallback=f"doc{len(manifest['docs']) + 1}")
        rel_path = f"{site_name}/{today}_{slug}.txt"
        while rel_path in used_names:
            slug = f"{slug}-{len(used_names)}"
            rel_path = f"{site_name}/{today}_{slug}.txt"
        used_names.add(rel_path)
        _write_text(out_dir / rel_path, text)
        entry = _doc_entry(
            source_url=str(path), site=site_name, title=title, rel_path=rel_path,
            text=text, robots_allowed=None, auth_id=authorization_id(auth),
            license_note=site_cfg["license_note"], mode="from-dir")
        # 要求：--from-dir 不做标题碎片合并（导入的外部文本必须原样）
        entry.update({"title_mended": False,
                      "body_paras_before": count_paragraphs(text),
                      "body_paras_after": count_paragraphs(text)})
        manifest["docs"].append(entry)
        print(f"  [ok]   {rel_path}（{len(text)} 字）")
        if len(manifest["docs"]) >= max_docs:
            break

    stats = {"candidates": len(files), "docs": len(manifest["docs"]),
             "failed": len(manifest["failed"]),
             "skipped": len(manifest["skipped"]),
             "avg_chars": round(
                 sum(d["chars"] for d in manifest["docs"])
                 / len(manifest["docs"]), 1) if manifest["docs"] else 0.0}
    manifest["stats"] = stats
    _finish_output(out_dir, manifest, args.authorization, auth_sha)
    print(f"完成：导入 {stats['docs']} 篇（平均 {stats['avg_chars']} 字）、"
          f"失败 {stats['failed']} 篇、跳过 {stats['skipped']} 篇"
          f"；manifest：{out_dir / 'manifest.json'}")
    return EXIT_OK


# ---------------------------------------------------------------- 入口
def main(argv: "list[str] | None" = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)
    site_name = args.site
    site_cfg = SITES[site_name]
    _validate_args(args, ap, site_cfg)

    auth: "dict | None" = None
    auth_sha = ""
    if args.authorization:
        try:
            auth = load_authorization(args.authorization)
            auth_sha = _sha256_file(Path(args.authorization))
        except (OSError, ValueError) as exc:
            print(f"错误：授权文件不可用（{args.authorization}）：{exc}",
                  file=sys.stderr)
            return EXIT_USAGE

    gap = authorization_gap(site_name, auth)
    if site_cfg["restricted"] and gap:
        _print_authorization_block(site_name, gap)
        return EXIT_AUTH

    if args.from_dir:
        return _run_from_dir(args, site_cfg, site_name, auth, auth_sha)
    return _run_crawl(args, site_cfg, site_name, auth, auth_sha)


if __name__ == "__main__":
    sys.exit(main())
