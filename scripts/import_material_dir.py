"""素材包批量导入：把一个目录树里的 .txt 素材文件批量写入资料库。

目录约定（一级子目录名 = 资料分类名）：

    <root>/
        党建组织/
            01-金句集.txt
            ...
        乡村振兴/
            ...

素材文件格式约定：

    第 1 行  # 标题【文种·类型】主题：副标题
    第 2 行  @TAGS 来源类型|年份区间|适用场景     （可选，竖线分隔）
    其余行  正文（导入后自动切段，供段落参考检索）

用法：

    python scripts/import_material_dir.py <素材根目录> [--dry-run]

入库走 dao.add_document 官方通道（text_hash 去重、FTS 与段落索引同步维护），
重复导入同一份素材会按 hash 跳过，可安全重跑。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gwtool.db import connection as dbconn
from gwtool.db import dao


def parse_material(path: Path) -> tuple[str, str, str] | None:
    """解析素材文件，返回 (title, tags, body)；无 # 标题行返回 None。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    title = ""
    tags = ""
    body_start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#") and not title:
            title = s.lstrip("#").strip()
            body_start = i + 1
        elif s.startswith("@TAGS"):
            tags = s[len("@TAGS"):].strip().replace("|", ",")
            body_start = i + 1
        elif s:
            break
    if not title:
        return None
    body = "\n".join(lines[body_start:]).strip()
    return title, tags, body


def ensure_category(name: str) -> int:
    """按名取分类 id，不存在则创建（只在一级查找，素材分类树为单层）。"""
    for c in dao.list_categories():
        if c.name == name and c.parent_id == 0:
            return c.id
    return dao.add_category(name, parent_id=0)


def main() -> int:
    ap = argparse.ArgumentParser(description="批量导入素材目录到资料库")
    ap.add_argument("root", help="素材根目录（一级子目录名=分类名）")
    ap.add_argument("--dry-run", action="store_true", help="只解析统计，不入库")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"目录不存在: {root}")
        return 2

    files = sorted(p for p in root.rglob("*.txt") if p.is_file())
    if not files:
        print(f"未找到 .txt 素材: {root}")
        return 2

    ok = skipped_no_title = dup = imported = 0
    fail: list[str] = []
    per_cat: dict[str, int] = {}

    if not args.dry_run:
        dbconn.current_db_file()   # 未配置时按默认路径（paths.db_path）初始化

    for f in files:
        cat_name = f.parent.relative_to(root).parts[0]
        parsed = parse_material(f)
        if parsed is None:
            skipped_no_title += 1
            print(f"[跳过·无标题] {f.name}")
            continue
        title, tags, body = parsed
        if not body:
            skipped_no_title += 1
            print(f"[跳过·空正文] {f.name}")
            continue
        if args.dry_run:
            print(f"[dry] {cat_name} / {title} ({len(body)} 字)")
            imported += 1
            per_cat[cat_name] = per_cat.get(cat_name, 0) + 1
            continue
        cat_id = ensure_category(cat_name)
        doc = dao.Document(
            title=title, content_text=body, blocks_json=None,
            file_path=str(f), file_type="txt", tags=tags, category_id=cat_id)
        try:
            doc_id = dao.add_document(doc)
        except Exception as exc:  # 单篇失败不中断整批
            fail.append(f"{f.name}: {type(exc).__name__}: {exc}")
            continue
        if doc_id < 0:
            dup += 1
            print(f"[跳过·重复] {title[:40]}")
        else:
            imported += 1
            per_cat[cat_name] = per_cat.get(cat_name, 0) + 1
            ok += 1

    print("=" * 46)
    print(f"总计 {len(files)} 文件 | 入库 {imported} | 重复跳过 {dup} | "
          f"格式跳过 {skipped_no_title} | 失败 {len(fail)}")
    for cat, n in sorted(per_cat.items()):
        print(f"  {cat}: {n} 篇")
    for line in fail:
        print(f"  [失败] {line}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
