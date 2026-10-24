# -*- coding: utf-8 -*-
"""技能套件 per-repo 配置的契约测试（setup-matt-pocock-skills 产物守卫）。

这些文件不被业务代码消费，但决定 to-tickets / triage / to-spec / wayfinder
等技能能否正确读写 issue tracker 与标签。用测试守住它们，避免后续重构
（尤其"顺手重写 AGENTS.md"）把标签字符串或文件路径悄悄改坏 ——
标签写错会让 triage 技能在仓库里建出重复标签。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
DOCS = ROOT / "docs" / "agents"

TRIAGE_LABELS = ["needs-triage", "needs-info", "ready-for-agent",
                 "ready-for-human", "wontfix"]
WAYFINDER_LABELS = ["wayfinder:map", "wayfinder:research",
                    "wayfinder:prototype", "wayfinder:grilling",
                    "wayfinder:task"]


class TestAgentsFile:
    def test_exists_at_repo_root(self):
        assert AGENTS.is_file(), "AGENTS.md 必须位于仓库根"

    def test_has_exactly_one_agent_skills_block(self):
        text = AGENTS.read_text(encoding="utf-8")
        assert text.count("## Agent skills") == 1, \
            "只能有且仅有一个 ## Agent skills 块（重复会让技能读到两份互相矛盾的定义）"

    def test_three_required_subsections_in_order(self):
        text = AGENTS.read_text(encoding="utf-8")
        block = text.split("## Agent skills", 1)[1]
        order = ["### Issue tracker", "### Triage labels", "### Domain docs"]
        positions = []
        for head in order:
            assert head in block, f"缺少子块：{head}"
            positions.append(block.index(head))
        assert positions == sorted(positions), "三个子块顺序必须为 Issue tracker → Triage labels → Domain docs"

    def test_each_subsection_points_at_its_doc(self):
        text = AGENTS.read_text(encoding="utf-8")
        for doc in ("docs/agents/issue-tracker.md",
                    "docs/agents/triage-labels.md",
                    "docs/agents/domain.md"):
            assert doc in text, f"AGENTS.md 必须指向 {doc}"

    def test_domain_line_says_single_context(self):
        text = AGENTS.read_text(encoding="utf-8")
        block = text.split("### Domain docs", 1)[1]
        assert "single-context" in block, \
            "Domain docs 一行必须使用 single-context 这个确切措辞"


class TestDocsAgentsFiles:
    def test_all_three_docs_exist_and_non_empty(self):
        for name in ("issue-tracker.md", "triage-labels.md", "domain.md"):
            p = DOCS / name
            assert p.is_file(), f"缺少 {p.relative_to(ROOT)}"
            assert p.stat().st_size > 0, f"{p.name} 不能为空"

    def test_issue_tracker_doc_names_github_and_keeps_pr_surface_off(self):
        text = (DOCS / "issue-tracker.md").read_text(encoding="utf-8")
        assert "GitHub" in text
        assert re.search(r"PRs as a request surface:\s*no", text), \
            "PRs-as-request-surface 必须保持关闭（默认 no）"

    def test_triage_labels_doc_lists_all_five_exact_strings(self):
        text = (DOCS / "triage-labels.md").read_text(encoding="utf-8")
        for label in TRIAGE_LABELS:
            assert f"`{label}`" in text, f"缺少 triage 标签：{label}"

    def test_triage_labels_doc_lists_all_wayfinder_labels(self):
        text = (DOCS / "triage-labels.md").read_text(encoding="utf-8")
        for label in WAYFINDER_LABELS:
            assert f"`{label}`" in text, f"缺少 wayfinder 标签：{label}"

    def test_label_strings_are_exact_not_underscored(self):
        """标签字符串逐字精确：下划线/驼峰变体会让 triage 建出重复标签。

        只检查 markdown 里被反引号包起来的标签 token：直接对全文做子串搜索是错的
        （`needs_triage` 里含有 `needs-triage`？并不含 —— 但 `ready_for_agent`
        与 `ready-for-agent` 的字符集相同，靠肉眼比对容易看漏，所以按 token 校验）。
        """
        text = (DOCS / "triage-labels.md").read_text(encoding="utf-8")
        tokens = set(re.findall(r"`([^`\n]+)`", text))
        bad = {t for t in tokens
               if t.startswith(("needs_", "ready_", "wont_"))
               or "wayfinder_" in t}
        assert not bad, f"出现非法标签变体（必须用连字符）：{sorted(bad)}"

    def test_domain_doc_records_single_context_and_glossary(self):
        text = (DOCS / "domain.md").read_text(encoding="utf-8")
        assert "single-context" in text
        assert "GLOSSARY.md" in text, "新版技能只认 GLOSSARY.md"
        assert "docs/adr/" in text, "架构决定放 docs/adr/"
        assert "CONTEXT.md" not in text, \
            "不得再引用旧名 CONTEXT.md（新版技能只认 GLOSSARY.md）"


class TestNoStaleContextFiles:
    def test_no_legacy_context_files_present(self):
        """旧名 CONTEXT.md / CONTEXT-MAP.md 不存在 → 无需改名迁移。"""
        skip = {".git", ".venv", "node_modules", "dist", "build", "packs",
                "__pycache__", ".ruff_cache", ".pytest_cache"}
        found = []
        for name in ("CONTEXT.md", "CONTEXT-MAP.md"):
            for p in ROOT.rglob(name):
                if skip & set(p.relative_to(ROOT).parts):
                    continue
                found.append(p.relative_to(ROOT).as_posix())
        assert not found, f"发现旧名文件，需迁移为 GLOSSARY.md：{found}"
