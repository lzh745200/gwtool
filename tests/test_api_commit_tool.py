# -*- coding: utf-8 -*-
"""api_commit 扫描逻辑的回归：必须只认 git 可见文件（尊重 .gitignore）。

事故背景（2026-10-24）：旧的 `os.walk` + 人工跳过名单把 `.gitignore` 排除的
`doc/corpus/**` 推上了公开仓库（85 个语料文件 + AUTHORIZATION.json）。
本测试用临时 git 仓库钉死"忽略文件绝不上传"。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import api_commit


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True)


def test_scan_local_respects_gitignore(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text("data/\n*.secret\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("tracked", encoding="utf-8")
    (repo / "untracked.txt").write_text("untracked", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "corpus.txt").write_text("sensitive", encoding="utf-8")
    (repo / "x.secret").write_text("secret", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-m", "init")

    files = api_commit.scan_local(repo)
    assert set(files) == {".gitignore", "tracked.txt", "untracked.txt"}
    assert "data/corpus.txt" not in files
    assert "x.secret" not in files


def test_scan_local_ignores_skipped_dirs_even_if_tracked(tmp_path):
    """双保险：即使被 git 跟踪，.venv/build 等构建目录也不上传。"""
    repo = tmp_path / "repo2"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "ok.txt").write_text("ok", encoding="utf-8")
    (repo / ".venv64").mkdir()
    (repo / ".venv64" / "lib.txt").write_text("env", encoding="utf-8")
    _git(repo, "add", "-f", "ok.txt", ".venv64/lib.txt")
    _git(repo, "commit", "-m", "init")

    files = api_commit.scan_local(repo)
    assert "ok.txt" in files
    assert ".venv64/lib.txt" not in files
