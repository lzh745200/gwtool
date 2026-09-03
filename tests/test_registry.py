# -*- coding: utf-8 -*-
"""发文登记台账：发文字号解析与自动取号、校验、统计聚合、CSV 导出。"""
from datetime import date

import pytest

from gwtool.core import registry
from gwtool.db import dao


def _dispatch(**kw) -> dao.Dispatch:
    base = dict(title="关于加强安全生产工作的通知", doc_type="通知",
                org="××市人民政府办公室", status="已印发")
    base.update(kw)
    return dao.Dispatch(**base)


# ------------------------------------------------------------ 发文字号
@pytest.mark.parametrize("raw,prefix,year,serial", [
    ("×政办发〔2026〕12号", "×政办发", "2026", 12),
    ("国发〔2025〕1号", "国发", "2025", 1),
    ("京政发[2026]108号", "京政发", "2026", 108),   # 方括号写法也要认
    ("×政办发（2026）7号", "×政办发", "2026", 7),     # 圆括号写法也要认
    ("  ×政办发〔2026〕3号  ", "×政办发", "2026", 3),  # 首尾空白容错
])
def test_parse_doc_no(raw, prefix, year, serial):
    assert registry.parse_doc_no(raw) == (prefix, year, serial)


@pytest.mark.parametrize("bad", ["", "随便一段文字", "×政办发2026年12号",
                                 "〔2026〕12号", "×政办发〔2026〕号"])
def test_parse_doc_no_rejects_malformed(bad):
    assert registry.parse_doc_no(bad) == ("", "", 0)


def test_format_doc_no_uses_standard_brackets():
    assert registry.format_doc_no("×政办发", 2026, 12) == "×政办发〔2026〕12号"


def test_next_serial_auto_increments(tmp_db):
    """自动取号：同机关同年度递增，跨年度/跨机关各自从 1 起。"""
    assert registry.next_serial("×政办发", 2026) == 1
    dao.add_dispatch(_dispatch(doc_no="×政办发〔2026〕5号", sign_date="2026-03-01"))
    assert registry.next_serial("×政办发", 2026) == 6
    dao.add_dispatch(_dispatch(doc_no="×政办发〔2026〕9号", sign_date="2026-04-01"))
    assert registry.next_serial("×政办发", 2026) == 10
    # 序号取最大值而非记录条数：中间缺号也不会撞号
    assert registry.next_serial("×政办发", 2027) == 1
    assert registry.next_serial("京政发", 2026) == 1


def test_next_doc_no_defaults_to_current_year(tmp_db):
    no = registry.next_doc_no("×政办发")
    assert no == f"×政办发〔{date.today().year}〕1号"


# ------------------------------------------------------------ 校验
def test_validate_accepts_good_record():
    assert registry.validate(_dispatch(doc_no="×政办发〔2026〕12号",
                                       sign_date="2026-03-01",
                                       print_date="2026-03-05",
                                       pages=4, copies=120)) == []


def test_validate_catches_common_mistakes():
    problems = registry.validate(_dispatch(
        title="", doc_no="格式不对", sign_date="2026年3月1日",
        print_date="2026-02-01", secret_level="超密", pages=-1))
    joined = " ".join(problems)
    assert "标题不能为空" in joined
    assert "发文字号格式不规范" in joined
    assert "成文日期" in joined
    assert "印发日期早于成文日期" in joined
    assert "密级取值异常" in joined
    assert "不能为负" in joined


# ------------------------------------------------------------ DAO 与统计
def test_dispatch_crud_roundtrip(tmp_db):
    did = dao.add_dispatch(_dispatch(doc_no="×政办发〔2026〕1号",
                                     sign_date="2026-01-10", pages=3, copies=50))
    assert did > 0
    got = dao.get_dispatch(did)
    assert got.doc_no == "×政办发〔2026〕1号"
    assert got.pages == 3 and got.copies == 50
    assert got.created_time, "应自动写入登记时间"

    got.status = "已归档"
    got.remark = "已归档备查"
    dao.update_dispatch(got)
    assert dao.get_dispatch(did).status == "已归档"
    assert dao.get_dispatch(did).remark == "已归档备查"

    dao.delete_dispatch(did)
    assert dao.get_dispatch(did) is None
    assert dao.count_dispatch() == 0


def test_list_dispatch_filters(tmp_db):
    dao.add_dispatch(_dispatch(doc_no="×政办发〔2026〕1号", doc_type="通知",
                               org="甲机关", status="已印发", sign_date="2026-01-10"))
    dao.add_dispatch(_dispatch(doc_no="×政办发〔2026〕2号", doc_type="报告",
                               org="乙机关", status="拟稿", sign_date="2026-05-20"))
    dao.add_dispatch(_dispatch(doc_no="×政办发〔2025〕9号", doc_type="通知",
                               org="甲机关", status="已归档", sign_date="2025-11-03"))

    assert len(dao.list_dispatch()) == 3
    assert len(dao.list_dispatch(doc_type="通知")) == 2
    assert len(dao.list_dispatch(org="甲机关")) == 2
    assert len(dao.list_dispatch(status="拟稿")) == 1
    assert len(dao.list_dispatch(year="2026")) == 2
    assert len(dao.list_dispatch(keyword="乙机关")) == 1
    got = dao.list_dispatch(date_from="2026-01-01", date_to="2026-12-31")
    assert len(got) == 2
    # 成文日期倒序
    assert [d.sign_date for d in dao.list_dispatch(year="2026")] == \
           ["2026-05-20", "2026-01-10"]


def test_dispatch_stats_grouping(tmp_db):
    for dt, org in (("通知", "甲"), ("通知", "甲"), ("报告", "乙"), ("函", "甲")):
        dao.add_dispatch(_dispatch(doc_type=dt, org=org, sign_date="2026-06-01"))
    assert dao.dispatch_stats("doc_type")[0] == ("通知", 2)
    assert dict(dao.dispatch_stats("org")) == {"甲": 3, "乙": 1}


def test_dispatch_stats_rejects_unknown_column(tmp_db):
    """分组列必须走白名单，不能把调用方字符串拼进 SQL。"""
    with pytest.raises(ValueError):
        dao.dispatch_stats("doc_no; DROP TABLE dispatch_register")


def test_dispatch_stats_blank_grouped_as_unfilled(tmp_db):
    dao.add_dispatch(_dispatch(doc_type="", sign_date="2026-06-01"))
    assert dao.dispatch_stats("doc_type") == [("未填写", 1)]


def test_monthly_counts_covers_twelve_months(tmp_db):
    dao.add_dispatch(_dispatch(sign_date="2026-03-15"))
    dao.add_dispatch(_dispatch(sign_date="2026-03-20"))
    dao.add_dispatch(_dispatch(sign_date="2026-11-01"))
    counts = dao.dispatch_monthly_counts("2026")
    assert len(counts) == 12
    assert counts[2] == ("03月", 2)
    assert counts[10] == ("11月", 1)
    assert counts[0] == ("01月", 0)


def test_summarize(tmp_db):
    rows = [_dispatch(doc_type="通知", status="已印发", pages=3, copies=50,
                      sign_date="2026-01-01"),
            _dispatch(doc_type="报告", status="拟稿", pages=5, copies=0,
                      sign_date="2025-06-01")]
    s = registry.summarize(rows)
    assert s["total"] == 2
    assert s["pages"] == 8 and s["copies"] == 50
    assert s["by_type"] == {"通知": 1, "报告": 1}
    assert s["years"] == ["2026", "2025"]


# ------------------------------------------------------------ 导出
def test_export_csv_is_utf8_bom_for_excel(tmp_db, tmp_path):
    """台账导出基本都要拿去 Excel 看：无 BOM 的 UTF-8 会显示成乱码。"""
    rows = [_dispatch(doc_no="×政办发〔2026〕12号", main_send="各区人民政府",
                      sign_date="2026-03-01", pages=4, copies=120)]
    out = tmp_path / "台账.csv"
    n = registry.export_csv(rows, str(out))
    assert n == 1

    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "缺少 UTF-8 BOM，Excel 打开会乱码"

    import csv
    with open(out, encoding="utf-8-sig", newline="") as fh:
        table = list(csv.reader(fh))
    assert table[0][0] == "发文字号"
    assert "标题" in table[0] and "主送" in table[0] and "状态" in table[0]
    assert table[1][0] == "×政办发〔2026〕12号"
    assert len(table[0]) == len(table[1]) == len(registry.EXPORT_COLUMNS)


def test_export_csv_empty(tmp_path):
    out = tmp_path / "空台账.csv"
    assert registry.export_csv([], str(out)) == 0
    assert out.exists()


def test_doc_types_reuses_skeletons():
    """文种清单必须与 15 种法定文种骨架同源，避免两处各写一份而漂移。"""
    kinds = registry.doc_types()
    assert len(kinds) == 15
    for expected in ("决议", "决定", "命令（令）", "公报", "公告", "通告", "意见",
                     "通知", "通报", "报告", "请示", "批复", "议案", "函", "纪要"):
        assert expected in kinds
