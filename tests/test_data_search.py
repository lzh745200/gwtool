# -*- coding: utf-8 -*-
"""数据层/检索/写作参考/备份 测试。"""
import time
from pathlib import Path

from gwtool.core import reference
from gwtool.core.model import Block, DocTree
from gwtool.db import dao


def _add(title, text, cat=0, tags=""):
    tree = DocTree(title=title, blocks=[Block(text=text)])
    d = dao.Document(title=title, content_text=tree.plain_text(),
                     blocks_json=tree.to_json(), tags=tags, category_id=cat)
    return dao.add_document(d)


def test_document_crud_and_dedup(tmp_db):
    did = _add("测试一", "乡村振兴战略实施意见的内容。")
    assert did > 0
    # 相同标题+内容视为重复（对应同一文件重复导入）
    assert _add("测试一", "乡村振兴战略实施意见的内容。") == -1  # 去重
    dao.update_document_content(did, "测试一", "修改后的内容。")
    d = dao.get_document(did)
    assert d.content_text == "修改后的内容。"
    # 删除 = 移入回收站（软删除）：行还在，但常规列表与检索都看不到
    dao.delete_document(did)
    assert dao.get_document(did) is not None
    assert dao.list_documents() == []
    assert [x.id for x in dao.list_deleted_documents()] == [did]
    # 彻底删除才真删行
    dao.purge_document(did)
    assert dao.get_document(did) is None


def test_categories(tmp_db):
    root = dao.add_category("政策法规")
    child = dao.add_category("中央文件", root)
    cats = dao.list_categories()
    assert {c.name for c in cats} >= {"政策法规", "中央文件"}
    did = _add("文件一", "内容。", cat=child)
    dao.delete_category(root)
    d = dao.get_document(did)
    assert d.category_id == 0  # 归入未分类


def test_fts_search_speed_and_snippet(tmp_db):
    for i in range(200):
        _add(f"材料{i}", f"这是第{i}份材料。" + "乡村振兴" * 20 if i % 5 == 0 else f"普通材料{i}。")
    t0 = time.time()
    results = dao.search_documents("乡村振兴")
    elapsed = time.time() - t0
    assert results, "应能检索到关键词"
    assert elapsed < 1.0, f"检索耗时 {elapsed:.2f}s（要求1秒内）"
    assert "乡村振兴" in results[0].snippet or "【" in results[0].snippet


def test_dictionary_lookup(tmp_db):
    dao.add_dictionary_entry("示范词", "shi fan ci", "用于测试的释义。")
    rows = dao.lookup_dictionary("示范词")
    assert rows and rows[0]["definition"] == "用于测试的释义。"


def test_reference_lookup(tmp_db):
    _add("领导讲话", "要坚持以人民为中心的发展思想，扎实推进共同富裕。")
    dao.add_phrase("会议强调，要压实责任、狠抓落实。", tag="金句")
    dao.add_dictionary_entry("发展", "fa zhan", "释义内容。")
    items = reference.lookup("发展")
    assert items, "写作参考应返回结果"
    # 相关度排序：rank 递减
    ranks = [it.rank for it in items]
    assert ranks == sorted(ranks, reverse=True)


def test_error_pairs_user_add(tmp_db):
    dao.add_error_pair("自定义错误", "自定义正确")
    pairs = dao.all_error_pairs()
    assert any(p.wrong == "自定义错误" for p in pairs)


def test_templates(tmp_db):
    from gwtool.core.template import default_template
    tpl = default_template()
    dao.save_template("标准公文", tpl.to_json(), is_default=True)
    dao.save_template("自定义A", default_template().clone("自定义A").to_json())
    assert dao.default_template_config()
    names = {t["name"] for t in dao.list_templates()}
    assert {"标准公文", "自定义A"} <= names


def test_backup_restore(tmp_db, tmp_path, monkeypatch):
    import gwtool.core.backup as bk

    def fake_backup_dir():
        d = tmp_path / "backups"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(bk.paths, "backup_dir", fake_backup_dir)
    _add("备份测试", "备份内容。")
    z = bk.create_backup(note="测试备份")
    assert Path(z).exists()
    # 破坏数据再恢复
    from gwtool.db.connection import get_conn
    get_conn().execute("DELETE FROM documents")
    get_conn().commit()
    bk.restore_backup(z)
    d = dao.get_document(1)
    assert d is not None and "备份内容" in d.content_text

# ------------------------------------------------------------ MATCH 构造（第 6 轮回归）
def test_match_query_grouping_and_syntax(tmp_db):
    """变体进 OR 分组、分组间显式 AND：两词以上查询不得因拆分变体漏检。

    修复前：lcut_for_search 的拆分片与主词并列 AND——"钉钉子精神"生成
    '"钉子" "钉钉子" "精神"'，索引里没有"钉子"就整条落空；带括号分组后
    分组间隐式 AND 又是 FTS5 语法错误（被静默吞掉，多词查询全部返回空）。
    """
    from gwtool.db.tokenize import build_match_query
    from gwtool.db import dao

    dao.add_phrase("我们要以钉钉子精神抓好落实。", context="作风")
    mq = build_match_query("钉钉子精神")
    assert " OR " in mq, f"变体应进 OR 分组: {mq}"
    assert " AND " in mq, f"分组间应显式 AND: {mq}"
    hits = dao.search_phrases("钉钉子精神")
    assert hits, f"词组查询不得漏检（MATCH={mq}）"

    # 纯符号查询：无词干，返回空表达式
    assert build_match_query("！@#￥%") == ""

    # 注入防御：引号剥除后无法闭合出 OR 注入
    mq2 = build_match_query('x" OR "1')
    assert mq2.count('"') % 2 == 0

    # 多词文档查询在真实 FTS 上命中
    dao.add_document(dao.Document(title="乡村振兴实施方案",
                                  content_text="产业振兴 人才振兴。"))
    rs = dao.search_documents("乡村振兴")
    assert rs and rs[0].table == "documents"
