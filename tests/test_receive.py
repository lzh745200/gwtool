# -*- coding: utf-8 -*-
"""收文登记台账（A1）的测试。

覆盖四层：
  · schema —— 建表、索引、从 v3 老库升级（**这层最容易被忽略，却是老用户的实际路径**）；
  · dao —— 增删改查与筛选；
  · 业务逻辑 —— 来文字号宽松解析、字段校验、统计、时限判定；
  · 导出 —— CSV 与 XLSX。

另有一组"常量不漂移"测试：密级/紧急程度/文种必须与 registry 同源，
两处各写一份清单必然日久漂移（改一处忘一处，表现为"发文能选、收文选不到"）。
"""
from __future__ import annotations

import zipfile

import pytest

from gwtool.core import receive, registry
from gwtool.db import connection as dbconn
from gwtool.db import dao
from gwtool.db.schema import SCHEMA_VERSION, init_schema


def _rec(**kw) -> dao.Receive:
    base = dict(reg_no="收〔2026〕1号", incoming_no="×政发〔2026〕12号",
                title="关于做好安全生产工作的通知", doc_type="通知",
                from_org="××市人民政府办公室", main_send="各科室",
                secret_level="公开", urgency="平急",
                receive_date="2026-09-01", doc_date="2026-08-28",
                pages=3, copies=2, propose="请安全科研提意见",
                instruction="同意", handler_dept="安全科", handler="张三",
                due_date="2026-09-10", done_date="", result="",
                status="承办", archive_no="", retention="30年",
                archive_date="", doc_id=0, remark="")
    base.update(kw)
    return dao.Receive(**base)


class TestSchema:
    def test_table_created(self, tmp_db):
        conn = dbconn.get_conn()
        init_schema(conn)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "receive_register" in names

    def test_indexes_created(self, tmp_db):
        conn = dbconn.get_conn()
        init_schema(conn)
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        for name in ("idx_receive_no", "idx_receive_date", "idx_receive_org",
                     "idx_receive_due"):
            assert name in idx, f"缺少索引 {name}"

    def test_upgrade_from_v3_adds_table(self, tmp_db):
        """老库（user_version=3）升级后必须补出收文表。

        这是老用户的真实路径：他们的库是在没有收文功能的版本上建的。
        """
        conn = dbconn.get_conn()
        init_schema(conn)
        conn.execute("DROP TABLE receive_register")      # 退化成 v3
        conn.execute("PRAGMA user_version=3")
        conn.commit()

        init_schema(conn)                                # 再走一次升级
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "receive_register" in names, "从 v3 升级后必须补出收文表"
        # 断言"等于当前版本"而不是硬编码数字 —— 与 test_recycle_bin.py 同一纪律：
        # 硬编码会在每次推进 SCHEMA_VERSION 时误报，而它想守的其实只是
        # "升级链把版本号推到了最新"。
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    def test_upgrade_preserves_existing_data(self, tmp_db):
        """升级不能动既有数据。"""
        conn = dbconn.get_conn()
        init_schema(conn)
        dao.add_document(dao.Document(title="升级前的文档", content_text="内容"))
        conn.execute("DROP TABLE receive_register")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        init_schema(conn)
        assert dao.count_documents() == 1

    def test_indexes_from_v3_have_columns_available(self, tmp_db):
        """索引依赖的列必须已就位（老库升级路径不得报 no such column）。"""
        conn = dbconn.get_conn()
        init_schema(conn)
        conn.execute("DROP TABLE receive_register")
        conn.execute("PRAGMA user_version=3")
        conn.commit()
        init_schema(conn)        # 抛异常即失败
        assert "receive_register" in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}


class TestCrud:
    def test_add_get(self, tmp_db):
        rid = dao.add_receive(_rec())
        assert rid > 0
        got = dao.get_receive(rid)
        assert got is not None
        assert got.title == "关于做好安全生产工作的通知"
        assert got.from_org == "××市人民政府办公室"
        assert got.created_time, "时间戳应由库自动填充"

    def test_get_missing_returns_none(self, tmp_db):
        assert dao.get_receive(99999) is None

    def test_update(self, tmp_db):
        rid = dao.add_receive(_rec())
        r = dao.get_receive(rid)
        r.status = "已办结"
        r.done_date = "2026-09-08"
        r.result = "已按要求整改完毕"
        dao.update_receive(r)
        got = dao.get_receive(rid)
        assert got.status == "已办结"
        assert got.done_date == "2026-09-08"
        assert got.updated_time

    def test_delete(self, tmp_db):
        rid = dao.add_receive(_rec())
        dao.delete_receive(rid)
        assert dao.get_receive(rid) is None
        assert dao.count_receive() == 0

    def test_keyword_search_covers_org_and_no(self, tmp_db):
        dao.add_receive(_rec())
        dao.add_receive(_rec(incoming_no="国发〔2026〕5号",
                             from_org="国务院办公厅", title="另一份文件"))
        assert len(dao.list_receive(keyword="应急")) == 0
        assert len(dao.list_receive(keyword="国务院")) == 1
        assert len(dao.list_receive(keyword="×政发")) == 1

    def test_filter_by_org_and_status(self, tmp_db):
        dao.add_receive(_rec())
        dao.add_receive(_rec(from_org="国务院办公厅", status="已办结"))
        assert len(dao.list_receive(from_org="国务院办公厅")) == 1
        assert len(dao.list_receive(status="承办")) == 1

    def test_filter_by_year_matches_no(self, tmp_db):
        dao.add_receive(_rec(receive_date="2025-01-01",
                             incoming_no="×政发〔2026〕12号"))
        # 收到日期是 2025，但字号里是 2026 —— 两个年份都该命中
        assert len(dao.list_receive(year="2025")) == 1
        assert len(dao.list_receive(year="2026")) == 1
        assert len(dao.list_receive(year="2027")) == 0

    def test_filter_by_date_range(self, tmp_db):
        dao.add_receive(_rec(receive_date="2026-09-01"))
        dao.add_receive(_rec(receive_date="2026-10-01", title="十月文件"))
        assert len(dao.list_receive(date_from="2026-09-15")) == 1

    def test_stats(self, tmp_db):
        dao.add_receive(_rec())
        dao.add_receive(_rec(from_org="国务院办公厅"))
        rows = dao.receive_stats("from_org")
        assert dict(rows)["国务院办公厅"] == 1

    def test_stats_rejects_unknown_column(self, tmp_db):
        """分组列白名单：绝不能把调用方字符串直接拼进 SQL。"""
        with pytest.raises(ValueError):
            dao.receive_stats("title; DROP TABLE documents")

    def test_monthly_counts_has_twelve_months(self, tmp_db):
        dao.add_receive(_rec(receive_date="2026-09-01"))
        got = dao.receive_monthly_counts("2026")
        assert len(got) == 12
        assert dict(got)["09月"] == 1
        assert dict(got)["01月"] == 0

    def test_years(self, tmp_db):
        dao.add_receive(_rec(receive_date="2026-09-01"))
        dao.add_receive(_rec(receive_date="2025-09-01", title="去年"))
        assert dao.receive_years() == ["2026", "2025"]

    def test_reg_no_serial_increments(self, tmp_db):
        assert dao.max_reg_no_serial("收", "2026") == 0
        dao.add_receive(_rec(reg_no="收〔2026〕7号"))
        assert dao.max_reg_no_serial("收", "2026") == 7
        assert dao.max_reg_no_serial("收", "2027") == 0     # 按年度独立计数

    def test_dispatch_and_receive_are_separate_ledgers(self, tmp_db):
        """两本账互不影响。"""
        dao.add_receive(_rec(reg_no="收〔2026〕9号"))
        assert dao.max_doc_no_serial("收", "2026") == 0
        assert dao.count_dispatch() == 0
        assert dao.count_receive() == 1


class TestParsing:
    def test_standard(self):
        assert receive.parse_incoming_no("×政发〔2026〕12号") == ("×政发", "2026", 12)

    def test_accepts_other_brackets(self):
        # 外单位来文格式不受本单位约束，四种括号都要认
        for text in ("×政发[2026]12号", "×政发［2026］12号", "×政发（2026）12号"):
            assert receive.parse_incoming_no(text) == ("×政发", "2026", 12)

    def test_number_suffix_optional(self):
        assert receive.parse_incoming_no("国发〔2026〕5") == ("国发", "2026", 5)

    def test_free_text_returns_empty(self):
        """识别不出不报错——来文可能是自由文本，强求格式化只会逼用户填假数据。"""
        assert receive.parse_incoming_no("内部明电") == ("", "", 0)
        assert receive.parse_incoming_no("") == ("", "", 0)
        assert receive.parse_incoming_no(None) == ("", "", 0)

    def test_format_and_next(self, tmp_db):
        assert receive.format_reg_no("收", 2026, 12) == "收〔2026〕12号"
        assert receive.next_reg_no("收", 2026) == "收〔2026〕1号"
        dao.add_receive(_rec(reg_no="收〔2026〕1号"))
        assert receive.next_reg_no("收", 2026) == "收〔2026〕2号"


class TestValidation:
    def test_clean_record_passes(self):
        assert receive.validate(_rec()) == []

    def test_requires_title_or_number(self):
        got = receive.validate(_rec(title="", incoming_no=""))
        assert any("至少填写一项" in p for p in got)

    def test_title_alone_is_enough(self):
        assert receive.validate(_rec(incoming_no="")) == []

    def test_rejects_bad_secret_level(self):
        assert any("密级" in p for p in receive.validate(_rec(secret_level="超级机密")))

    def test_rejects_bad_status(self):
        assert any("状态" in p for p in receive.validate(_rec(status="随便")))

    def test_rejects_bad_retention(self):
        assert any("保管期限" in p for p in receive.validate(_rec(retention="5年")))

    def test_rejects_bad_date_format(self):
        assert any("收到日期" in p
                   for p in receive.validate(_rec(receive_date="2026/09/01")))

    def test_due_before_receive_is_rejected(self):
        got = receive.validate(_rec(receive_date="2026-09-10",
                                    due_date="2026-09-01"))
        assert any("早于收到日期" in p for p in got)

    def test_negative_counts_rejected(self):
        assert any("不能为负" in p for p in receive.validate(_rec(pages=-1)))

    def test_unknown_doc_type_only_warns(self):
        """非 12 种法定文种的来文仍可保存（仅提示），不阻断。"""
        got = receive.validate(_rec(doc_type="内部通报"))
        assert any("法定文种" in p for p in got)


class TestDeadline:
    def test_days_left(self):
        r = _rec(due_date="2026-09-10")
        assert receive.days_left(r, today="2026-09-08") == 2
        assert receive.days_left(r, today="2026-09-12") == -2

    def test_closed_has_no_deadline(self):
        assert receive.days_left(_rec(status="已办结"), today="2026-09-01") is None
        assert receive.days_left(_rec(done_date="2026-09-05"),
                                 today="2026-09-01") is None

    def test_no_due_date_returns_none(self):
        assert receive.days_left(_rec(due_date=""), today="2026-09-01") is None

    def test_overdue(self):
        assert receive.is_overdue(_rec(due_date="2026-09-01"), today="2026-09-05")
        assert not receive.is_overdue(_rec(due_date="2026-09-10"),
                                      today="2026-09-05")

    def test_pending_receive_lists_due_and_overdue(self, tmp_db):
        dao.add_receive(_rec(due_date="2026-09-01", status="承办"))       # 逾期
        dao.add_receive(_rec(due_date="2026-09-30", status="承办",
                             title="还早"))                                # 未到期
        dao.add_receive(_rec(due_date="2026-09-01", status="已办结",
                             done_date="2026-09-01", title="已办结"))      # 不算
        got = dao.pending_receive(due_within_days=2, today="2026-09-03")
        assert len(got) == 1
        assert got[0].title == "关于做好安全生产工作的通知"

    def test_pending_without_due_date_is_ignored(self, tmp_db):
        """没有期限的收文不该被算法替用户判定为"该催办了"。"""
        dao.add_receive(_rec(due_date="", status="承办"))
        assert dao.pending_receive(today="2026-09-03") == []


class TestSummarize:
    def test_counts_and_overdue(self):
        rows = [_rec(), _rec(status="已办结", done_date="2026-09-02",
                             from_org="国务院办公厅"),
                _rec(due_date="2026-08-01", status="承办")]
        summ = receive.summarize(rows, today="2026-09-03")
        assert summ["total"] == 3
        assert summ["copies"] == 6
        assert summ["pending"] == 2          # 已办结那件不算待办
        assert summ["overdue"] == 1          # 只有 2026-08-01 那件逾期
        assert summ["by_org"]["国务院办公厅"] == 1
        assert "未填写" not in summ["by_org"], "本组数据都填了来文机关"

    def test_blank_org_grouped_as_unfilled(self):
        summ = receive.summarize([_rec(from_org="")])
        assert summ["by_org"]["未填写"] == 1

    def test_empty_rows(self):
        summ = receive.summarize([])
        assert summ["total"] == 0 and summ["overdue"] == 0


class TestExport:
    def test_csv_is_utf8_bom(self, tmp_db, tmp_path):
        out = tmp_path / "收文.csv"
        n = receive.export_csv([_rec()], str(out))
        assert n == 1
        assert "收文登记号" in out.read_text(encoding="utf-8-sig")

    def test_csv_empty(self, tmp_path):
        assert receive.export_csv([], str(tmp_path / "e.csv")) == 0

    def test_xlsx(self, tmp_path):
        out = tmp_path / "收文.xlsx"
        n = receive.export_xlsx([_rec()], str(out))
        assert n == 1
        with zipfile.ZipFile(str(out)) as zf:
            assert "xl/worksheets/sheet1.xml" in zf.namelist()

    def test_xlsx_with_stats_has_two_sheets(self, tmp_path):
        out = tmp_path / "收文.xlsx"
        receive.export_xlsx([_rec()], str(out), with_stats=True)
        with zipfile.ZipFile(str(out)) as zf:
            assert "xl/worksheets/sheet2.xml" in zf.namelist()

    def test_xlsx_forces_code_columns_to_text(self, tmp_path):
        out = tmp_path / "收文.xlsx"
        receive.export_xlsx([_rec(incoming_no="5012345678901234")], str(out))
        with zipfile.ZipFile(str(out)) as zf:
            sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "5012345678901234" in sheet
        assert 't="inlineStr"' in sheet, "编码列未按文本写入，会被 Excel 转成科学计数法"


class TestConstantsDoNotDrift:
    """三组取值必须与 registry 同源，否则会出现"发文能选、收文选不到"。"""

    def test_secret_levels_shared(self):
        assert receive.SECRET_LEVELS is registry.SECRET_LEVELS

    def test_urgency_shared(self):
        assert receive.URGENCY_LEVELS is registry.URGENCY_LEVELS

    def test_doc_types_shared(self):
        assert receive.doc_types_available() == registry.doc_types()

    def test_receive_statuses_differ_from_dispatch(self):
        """收文与发文是两条不同的流程，状态集合不应重合到混用。"""
        assert set(receive.STATUSES) != set(registry.STATUSES)
        assert "拟稿" in registry.STATUSES and "拟稿" not in receive.STATUSES
        assert "签收" in receive.STATUSES and "签收" not in registry.STATUSES


class TestDialogSmoke:
    """界面层冒烟：字段名拼错、布局用错、私有属性名写错，
    都只在**构造时**才暴露，不会让任何逻辑测试变红。
    """

    def test_dialog_constructs(self, qapp, tmp_db):
        from gwtool.ui.receive_dialog import _COLUMNS, ReceiveDialog
        dlg = ReceiveDialog()
        assert dlg.tbl.columnCount() == len(_COLUMNS)
        assert dlg.lbl_count.text().startswith("共")

    def test_form_constructs_and_prefills_today(self, qapp, tmp_db):
        from datetime import date
        from gwtool.ui.receive_dialog import ReceiveForm
        form = ReceiveForm()
        assert form.value().receive_date == date.today().isoformat(), (
            "新建收文应预填当天收到日期")

    def test_form_does_not_override_existing_date(self, qapp, tmp_db):
        from gwtool.ui.receive_dialog import ReceiveForm
        rid = dao.add_receive(_rec(receive_date="2026-01-15"))
        form = ReceiveForm(None, dao.get_receive(rid))
        assert form.value().receive_date == "2026-01-15"

    def test_dialog_lists_existing_rows(self, qapp, tmp_db):
        from gwtool.ui.receive_dialog import ReceiveDialog
        dao.add_receive(_rec())
        dlg = ReceiveDialog()
        assert dlg.tbl.rowCount() == 1

    def test_overdue_row_is_marked(self, qapp, tmp_db):
        from gwtool.ui.receive_dialog import _COLUMNS, ReceiveDialog
        dao.add_receive(_rec(due_date="2020-01-01", status="承办"))
        dlg = ReceiveDialog()
        col = [k for k, _label in _COLUMNS].index("due_date")
        assert "已逾期" in dlg.tbl.item(0, col).text()

    def test_stats_page_renders_counts(self, qapp, tmp_db):
        from gwtool.ui.receive_dialog import ReceiveDialog
        dao.add_receive(_rec())
        dlg = ReceiveDialog()
        assert "合计" in dlg.lbl_stats.text()

    def test_auto_number_uses_receive_ledger(self, qapp, tmp_db):
        """取号必须走收文这本账，不能被发文流水干扰。"""
        from gwtool.ui.receive_dialog import ReceiveForm
        dao.add_receive(_rec(reg_no="收〔2026〕7号"))
        form = ReceiveForm()
        form._fields["receive_date"].setText("2026-09-01")
        form._auto_number()
        assert form.ed_reg_no.text() == "收〔2026〕8号"
