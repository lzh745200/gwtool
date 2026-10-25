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


def test_scan_local_keeps_build_prefixed_filenames(tmp_path):
    """文件名以 build 开头的普通文件不能被"构建目录"过滤误伤。

    2026-10-25 收口实测：过滤把文件名也交给 `is_skipped_dir`，导致
    `scripts/build_windows.bat`、`.github/workflows/build.yml`、`build_*.py`
    等 6 个文件被静默排除，每次推送都误报"本地缺失"。
    """
    repo = tmp_path / "repo3"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "build_windows.bat").write_text("echo xy", encoding="utf-8")
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "build.yml").write_text("on: push\n",
                                                             encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    files = api_commit.scan_local(repo)
    assert "scripts/build_windows.bat" in files
    assert ".github/workflows/build.yml" in files


def test_scan_local_reads_index_content_not_raw_disk(tmp_path):
    """已跟踪文件取索引 blob（过 clean 过滤器），不取磁盘原始字节。

    `.gitattributes` 把 .bat 工作区 CRLF 归一化成索引 LF；若读磁盘字节，
    远端会存 CRLF blob，本地 `git status` 永远显示文件被修改。
    """
    repo = tmp_path / "repo4"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitattributes").write_text("*.bat text eol=crlf\n", encoding="utf-8")
    (repo / "run.bat").write_bytes(b"echo a\r\necho b\r\n")
    _git(repo, "add", ".gitattributes", "run.bat")
    _git(repo, "commit", "-m", "init")

    disk = (repo / "run.bat").read_bytes()
    assert b"\r\n" in disk, "工作区应保持 CRLF（eol=crlf）"
    blob = subprocess.run(["git", "cat-file", "blob", ":run.bat"],
                          cwd=str(repo), capture_output=True, check=True).stdout
    files = api_commit.scan_local(repo)
    assert files["run.bat"] == blob, "scan_local 必须返回索引内容（LF）"
    assert files["run.bat"] != disk
