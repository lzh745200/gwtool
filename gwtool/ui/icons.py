# -*- coding: utf-8 -*-
"""内嵌 SVG 工具栏图标：替代 emoji 前缀，深浅背景均可辨识。

 QIcon 从 SVG 数据渲染（PySide6 自带 qsvg 图像插件，打包时随 imageformats 分发）。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QImage, QPixmap

from . import theme

_WRAP = ("<?xml version='1.0' encoding='utf-8'?>"
         "<svg xmlns='http://www.w3.org/2000/svg' width='24' height='24' "
         "viewBox='0 0 24 24' fill='none' stroke='{c}' stroke-width='1.8' "
         "stroke-linecap='round' stroke-linejoin='round'>{body}</svg>")

# 24x24 线性图标（material 风格简化路径）
_SVGS = {
    "new_doc": "<path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/>"
               "<path d='M14 2v6h6'/>",
    "import": "<path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/>"
              "<path d='M7 10l5 5 5-5'/><path d='M12 15V3'/>",
    "registry": "<rect x='4' y='3' width='16' height='18' rx='2'/>"
                "<path d='M8 8h8M8 12h8M8 16h5'/>",
    "compile": "<rect x='5' y='3' width='14' height='7' rx='1'/>"
               "<rect x='5' y='14' width='14' height='7' rx='1'/>"
               "<path d='M8 6.5h8M8 17.5h8'/>",
    "template": "<rect x='3' y='3' width='7' height='7' rx='1'/>"
                "<rect x='14' y='3' width='7' height='7' rx='1'/>"
                "<rect x='3' y='14' width='7' height='7' rx='1'/>"
                "<rect x='14' y='14' width='7' height='7' rx='1'/>",
    "check": "<circle cx='11' cy='11' r='7'/><path d='M21 21l-4.3-4.3'/>"
             "<path d='M8 11l2 2 4-4'/>",
    "inspect": "<path d='M12 3l8 3v6c0 4.4-3.2 7.7-8 9-4.8-1.3-8-4.6-8-9V6z'/>"
               "<path d='M9 12l2 2 4-4'/>",
    "tts": "<path d='M11 5L6 9H3v6h3l5 4z'/>"
           "<path d='M15.5 8.5a5 5 0 0 1 0 7'/><path d='M18.5 6a9 9 0 0 1 0 12'/>",
    "clipboard": "<rect x='8' y='2' width='8' height='4' rx='1'/>"
                 "<path d='M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6"
                 "a2 2 0 0 1 2-2h2'/><path d='M9 12l2 2 4-4'/>",
    "cleanup": "<path d='M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z'/>"
               "<path d='M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z'/>",
    "compare": "<rect x='3' y='5' width='8' height='14' rx='1'/>"
               "<rect x='13' y='5' width='8' height='14' rx='1'/>"
               "<path d='M6 12h2M16 12h2'/>",
    "book": "<path d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20'/>"
            "<path d='M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'/>",
    "backup": "<rect x='3' y='3' width='18' height='18' rx='2'/>"
              "<path d='M7 3v6h10V3'/><rect x='7' y='13' width='10' height='5' rx='1'/>",
}


def icon(name: str) -> QIcon:
    """按名取图标；未知名称返回空图标。"""
    body = _SVGS.get(name)
    if not body:
        return QIcon()
    svg = _WRAP.format(c=theme.ICON, body=body)
    img = QImage.fromData(QByteArray(svg.encode("utf-8")), "SVG")
    if img.isNull():
        return QIcon()
    return QIcon(QPixmap.fromImage(img))
