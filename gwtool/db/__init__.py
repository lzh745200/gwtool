# -*- coding: utf-8 -*-
"""数据层包。

这里的子模块导入是**刻意的再导出**（`from gwtool.db import dao` 等调用方式），
不是"未使用的导入"。用 `X as X` 冗余别名表达这个意图：静态检查（F401）认可
这种写法，比 `# noqa: F401` 更稳 —— 不会因为 lint 规则集调整而反复报错。
"""
from . import connection as connection
from . import dao as dao
from . import schema as schema
from . import tokenize as tokenize
