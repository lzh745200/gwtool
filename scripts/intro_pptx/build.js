/* 公文汇编助手 · 系统介绍 PPT 生成脚本（pptxgenjs） */
const pptxgen = require("pptxgenjs");

const p = new pptxgen();
p.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
p.author = "公文汇编助手";
p.title = "公文汇编助手 · 系统介绍";
p.subject = "单机离线公文汇编与写作辅助工具";

/* ---------- 设计常量 ---------- */
const W = 13.33, H = 7.5, M = 0.5;
const FONT = "Microsoft YaHei";
const INK = "26201C";      // 正文墨色
const MUTED = "8C8178";    // 次要文字
const PRIMARY = "9E2B25";  // 公文红（主色）
const PRIM_DK = "701D19";  // 深红
const TINT = "F5EAE7";     // 红色浅底
const PAPER = "F8F4EE";    // 纸色卡片
const ACCENT = "C9A227";   // 金色点缀
const DARK = "1F1917";     // 深墨背景
const DCARD = "2C2521";    // 深色卡片
const DTEXT = "EFE7DE";    // 深底亮字
const DMUTED = "A99C90";   // 深底次要字
const LINE = "E7DFD5";     // 浅色分隔线
const DLINE = "4A3F38";    // 深色分隔线

const shadow = () => ({ type: "outer", color: "3A2E28", blur: 7, offset: 2, angle: 60, opacity: 0.18 });

/* ---------- 通用元件 ---------- */
function seal(s, x, y, size, char, bg) {
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w: size, h: size, fill: { color: bg || PRIMARY }, rectRadius: size * 0.1, line: { type: "none" } });
  s.addText(char || "文", { x, y: y - size * 0.03, w: size, h: size, align: "center", valign: "middle", fontSize: Math.round(size * 36), bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
}
function pageNum(s, n) {
  s.addText(String(n).padStart(2, "0"), { x: W - 1.15, y: H - 0.52, w: 0.65, h: 0.32, align: "right", fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
  s.addText("公文汇编助手 · 系统介绍", { x: M, y: H - 0.52, w: 3.2, h: 0.32, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}
function header(s, kicker, title, n) {
  s.background = { color: BGWHITE };
  seal(s, M, 0.42, 0.38, "文");
  s.addText(kicker, { x: 1.0, y: 0.42, w: 8, h: 0.38, valign: "middle", fontSize: 12, bold: true, color: PRIMARY, charSpacing: 2, fontFace: FONT, margin: 0 });
  s.addText(title, { x: M, y: 0.92, w: W - 2 * M, h: 0.62, fontSize: 26, bold: true, color: INK, fontFace: FONT, margin: 0 });
  pageNum(s, n);
}
function divider(n, num, title, sub, items) {
  const s = p.addSlide();
  s.background = { color: DARK };
  seal(s, M, 0.55, 0.44, "文");
  s.addText("公文汇编助手 · 单机离线版", { x: 1.08, y: 0.55, w: 6, h: 0.44, valign: "middle", fontSize: 12, color: DMUTED, fontFace: FONT, margin: 0 });
  s.addText(num, { x: M, y: 2.05, w: 2.6, h: 1.9, fontSize: 100, bold: true, color: ACCENT, fontFace: FONT, margin: 0 });
  s.addText(title, { x: 3.15, y: 2.42, w: 9.6, h: 0.85, fontSize: 38, bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
  s.addText(sub, { x: 3.18, y: 3.38, w: 9.4, h: 0.5, fontSize: 15, color: DMUTED, fontFace: FONT, margin: 0 });
  let y = 4.35;
  items.forEach(it => {
    s.addShape(p.shapes.RECTANGLE, { x: 3.2, y: y + 0.115, w: 0.12, h: 0.12, fill: { color: ACCENT }, line: { type: "none" } });
    s.addText(it, { x: 3.48, y, w: 9.2, h: 0.36, fontSize: 13.5, color: DTEXT, fontFace: FONT, margin: 0, valign: "middle" });
    y += 0.46;
  });
  pageNum(s, n);
  return s;
}
function chip(s, x, y, w, h, txt, opts = {}) {
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.06, fill: { color: opts.fill || PAPER }, line: opts.lineC ? { color: opts.lineC, width: 1 } : { type: "none" } });
  s.addText(txt, { x: x + 0.08, y, w: w - 0.16, h, align: opts.align || "center", valign: "middle", fontSize: opts.fontSize || 12.5, bold: !!opts.bold, color: opts.color || INK, fontFace: FONT, margin: 0 });
}
function rowItem(s, x, y, w, lead, desc, opts = {}) {
  s.addShape(p.shapes.RECTANGLE, { x, y: y + 0.09, w: 0.11, h: 0.11, fill: { color: opts.mark || PRIMARY }, line: { type: "none" } });
  s.addText([
    { text: lead, options: { bold: true, color: opts.leadColor || INK, fontSize: opts.leadSize || 13.5 } },
    { text: desc ? "　" + desc : "", options: { color: opts.descColor || MUTED, fontSize: opts.descSize || 12.5 } },
  ], { x: x + 0.26, y, w: w - 0.26, h: opts.h || 0.62, fontFace: FONT, margin: 0, valign: "top", lineSpacingMultiple: 1.12 });
}
function arrow(s, x1, y1, x2, y2, opts = {}) {
  s.addShape(p.shapes.LINE, { x: x1, y: y1, w: x2 - x1, h: y2 - y1, line: { color: opts.color || PRIMARY, width: opts.width || 1.75, endArrowType: "triangle" } });
}
const BGWHITE = "FFFFFF";

/* ================= S1 封面 ================= */
{
  const s = p.addSlide();
  s.background = { color: DARK };
  seal(s, M, 0.55, 0.44, "文");
  s.addText("GWTOOL · 单机离线版 v1.2.0", { x: 1.08, y: 0.55, w: 7, h: 0.44, valign: "middle", fontSize: 13, bold: true, color: ACCENT, charSpacing: 3, fontFace: FONT, margin: 0 });
  s.addText("公文汇编助手", { x: M, y: 2.15, w: 9.2, h: 1.25, fontSize: 60, bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
  s.addText("面向党政机关、企事业单位的单机智能公文汇编与写作辅助工具", { x: 0.53, y: 3.5, w: 8.6, h: 0.5, fontSize: 17, color: DMUTED, fontFace: FONT, margin: 0 });
  const chips = ["完全离线 · 零网络请求", "Windows x64 + 麒麟 ARM64", "GB/T 9704 公文格式", "87 项自动化测试"];
  chips.forEach((t, i) => {
    const cx = M + (i % 2) * 4.55, cy = 4.35 + Math.floor(i / 2) * 0.68;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: cx, y: cy, w: 4.3, h: 0.52, rectRadius: 0.08, fill: { color: DCARD }, line: { color: DLINE, width: 1 } });
    s.addText(t, { x: cx, y: cy, w: 4.3, h: 0.52, align: "center", valign: "middle", fontSize: 12.5, color: DTEXT, fontFace: FONT, margin: 0 });
  });
  s.addText("PySide6 + SQLite（FTS5）+ PyMuPDF　|　数据存本机，程序与数据分离", { x: 0.53, y: 6.55, w: 9, h: 0.4, fontSize: 12.5, color: DMUTED, fontFace: FONT, margin: 0 });
  // 大印章
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 9.85, y: 1.95, w: 2.75, h: 2.75, rectRadius: 0.14, fill: { color: PRIMARY }, line: { type: "none" }, shadow: shadow() });
  s.addText("文", { x: 9.85, y: 1.83, w: 2.75, h: 2.75, align: "center", valign: "middle", fontSize: 120, bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
  s.addText("系统介绍", { x: 9.85, y: 4.95, w: 2.75, h: 0.5, align: "center", fontSize: 16, bold: true, color: DTEXT, charSpacing: 6, fontFace: FONT, margin: 0 });
}

/* ================= S2 目录 ================= */
{
  const s = p.addSlide();
  s.background = { color: BGWHITE };
  seal(s, M, 0.42, 0.38, "文");
  s.addText("CONTENTS", { x: 1.0, y: 0.42, w: 6, h: 0.38, valign: "middle", fontSize: 12, bold: true, color: PRIMARY, charSpacing: 3, fontFace: FONT, margin: 0 });
  s.addText("目录", { x: M, y: 0.92, w: 6, h: 0.62, fontSize: 26, bold: true, color: INK, fontFace: FONT, margin: 0 });
  const items = [
    ["01", "认识公文汇编助手", "定位 · 四大痛点 · 关键数据 · 功能全景"],
    ["02", "系统架构", "技术选型 · 三层架构 · 数据层 · 检索 · 线程模型"],
    ["03", "材料导入与资料库", "7 种文档格式 · OCR 回退 · .doc 四级降级 · 资料库管理"],
    ["04", "智能写作辅助", "15 种文种骨架 · 文秘工具箱 · 写作参考 · 三级纠错"],
    ["05", "一键汇编与成品输出", "三步向导 · 国标模板 · Word 引擎 · 两遍渲染 · A3 小册子"],
    ["06", "质检、对比与安全", "格式体检 · 文档对比查重 · 历史快照 · 加密备份"],
    ["07", "交付、质量与生态", "双平台 · 打包矩阵 · CI/CD · 87 项测试与性能验收"],
  ];
  let y = 1.78;
  items.forEach(([n, t, d]) => {
    s.addText(n, { x: M, y, w: 0.85, h: 0.62, fontSize: 24, bold: true, color: PRIMARY, fontFace: FONT, margin: 0, valign: "middle" });
    s.addText([
      { text: t, options: { bold: true, fontSize: 16.5, color: INK, breakLine: true } },
      { text: d, options: { fontSize: 12, color: MUTED } },
    ], { x: 1.5, y: y - 0.02, w: 7.6, h: 0.72, fontFace: FONT, margin: 0, valign: "middle" });
    if (y < 5.7) s.addShape(p.shapes.LINE, { x: 1.5, y: y + 0.7, w: 7.4, h: 0, line: { color: LINE, width: 1 } });
    y += 0.7;
  });
  seal(s, 10.35, 2.6, 1.7, "文");
  s.addText("40 页 · 7 大章节", { x: 9.55, y: 4.55, w: 3.3, h: 0.4, align: "center", fontSize: 13.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
  s.addText("从产品到架构，从算法到交付", { x: 9.3, y: 4.98, w: 3.8, h: 0.4, align: "center", fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
  pageNum(s, 2);
}

/* ================= S3 章节 01 ================= */
divider(3, "01", "认识公文汇编助手", "它是什么、为谁解决什么问题、整体能力有多大", [
  "定位：单机离线的公文汇编与写作辅助工具",
  "四大痛点：材料散乱 · 格式繁琐 · 错字难查 · 写作无参考",
  "一屏看懂：40220 条纠错对 · 12.3 万词典 · 17 个功能模块",
]);

/* ================= S4 定位与痛点 ================= */
{
  const s = p.addSlide();
  header(s, "01 认识公文汇编助手", "定位：把“材料堆”变成“规范公文”的离线工作站", 4);
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 1.85, w: 4.4, h: 4.9, rectRadius: 0.08, fill: { color: PRIMARY }, line: { type: "none" }, shadow: shadow() });
  s.addText([
    { text: "一句话定位", options: { fontSize: 13, bold: true, color: "F2D8A0", breakLine: true } },
    { text: "不联网、不上传，\n一台电脑即可完成\n材料管理、公文写作、\n汇编成册的全部工作。", options: { fontSize: 20, bold: true, color: "FFFFFF", breakLine: true } },
  ], { x: 0.85, y: 2.2, w: 3.7, h: 2.9, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
  s.addText([
    { text: "适用场景", options: { bold: true, fontSize: 13, color: "F2D8A0", breakLine: true } },
    { text: "党政机关办文 · 企事业单位综合部门 · 涉密内网环境 · 麒麟信创终端", options: { fontSize: 12.5, color: "F5E3DC" } },
  ], { x: 0.85, y: 5.3, w: 3.7, h: 1.2, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
  const pains = [
    ["材料收集散乱", "7 种文档格式批量导入，分类标签 + 全文检索，随手可查"],
    ["格式调整繁琐", "GB/T 9704 默认模板 + 一键汇编，红头目录页码自动生成"],
    ["错别字难查", "4 万条纠错库三级流水线，精标对 100% 识别（测试验收）"],
    ["写作无参考", "15 种法定文种骨架 + 三库联合检索，双击即插入"],
  ];
  let y = 1.85;
  pains.forEach(([t, d], i) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 5.25, y, w: 7.55, h: 1.1, rectRadius: 0.07, fill: { color: i % 2 ? BGWHITE : PAPER }, line: { color: LINE, width: 1 } });
    s.addText("痛点 " + (i + 1), { x: 5.5, y: y + 0.12, w: 0.95, h: 0.86, valign: "middle", fontSize: 12, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText([
      { text: t, options: { bold: true, fontSize: 15.5, color: INK, breakLine: true } },
      { text: d, options: { fontSize: 12.5, color: MUTED } },
    ], { x: 6.6, y: y + 0.1, w: 6.0, h: 0.9, fontFace: FONT, margin: 0, valign: "middle", lineSpacingMultiple: 1.18 });
    y += 1.27;
  });
}

/* ================= S5 关键数据 ================= */
{
  const s = p.addSlide();
  header(s, "01 认识公文汇编助手", "一屏看懂：这个系统“有多大、有多快”", 5);
  const tiles = [
    ["40220", "纠错对（条）", "精标 220 + 程序生成 40000"],
    ["123393", "词典词条（条）", "开源 CC-CEDICT，随包分发"],
    ["15", "法定文种骨架", "对应《公文处理工作条例》"],
    ["7+4", "可导入格式", "文档 7 种 + 图片 4 类（OCR）"],
    ["87", "自动化测试用例", "pytest 全量回归 + 性能验收"],
    ["<10s", "50 个文件导入", "后台线程批量解析入库"],
    ["<1s", "全文检索响应", "FTS5 + jieba，20 万字语料"],
    ["0", "网络请求", "全离线设计，打包排除网络组件"],
  ];
  const bw = 2.98, bh = 2.15, gx = 0.13, gy = 0.3;
  tiles.forEach(([num, label, sub], i) => {
    const x = M + (i % 4) * (bw + gx), y = 1.9 + Math.floor(i / 4) * (bh + gy);
    const hot = i === 7;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w: bw, h: bh, rectRadius: 0.08, fill: { color: hot ? PRIMARY : PAPER }, line: hot ? { type: "none" } : { color: LINE, width: 1 }, shadow: shadow() });
    s.addText(num, { x: x + 0.2, y: y + 0.22, w: bw - 0.4, h: 0.95, fontSize: 44, bold: true, color: hot ? "FFFFFF" : PRIMARY, fontFace: FONT, margin: 0 });
    s.addText([
      { text: label, options: { bold: true, fontSize: 14, color: hot ? "FFFFFF" : INK, breakLine: true } },
      { text: sub, options: { fontSize: 11.5, color: hot ? "F0D9CF" : MUTED } },
    ], { x: x + 0.2, y: y + 1.28, w: bw - 0.4, h: 0.75, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
  });
  s.addText("数据来源：gwtool/resources/data/seed.db 实测（15.7 MB）与 tests/ 性能验收用例", { x: M, y: 6.75, w: 11, h: 0.32, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}

/* ================= S6 功能全景 ================= */
{
  const s = p.addSlide();
  header(s, "01 认识公文汇编助手", "功能全景：六大能力域，17 个功能模块", 6);
  const groups = [
    ["材料导入", "批量导入去重 · OCR 识别\n剪贴板入库 · 右键菜单"],
    ["写作辅助", "15 文种骨架 · 文秘工具箱\n写作参考 · 朗读校对"],
    ["质量纠错", "三级纠错流水线 · GB/T 9704 体检\n文档对比 · 相似查重"],
    ["汇编输出", "三步汇编向导 · 批量模式\nA4 PDF · A3 骑马订小册子"],
    ["资料管理", "分类树 + 标签 · FTS 全文检索\n历史快照 30 版 · 排版微调"],
    ["安全保障", "口令锁 · AES 加密备份\n退出/定时自动备份 · 便携模式"],
  ];
  const gx = [0.75, 9.15], gw = 3.45, gy = [1.9, 3.55, 5.2], gh = 1.42;
  groups.forEach(([t, d], i) => {
    const x = gx[Math.floor(i / 3)], y = gy[i % 3];
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w: gw, h: gh, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText([
      { text: t, options: { bold: true, fontSize: 15, color: PRIMARY, breakLine: true } },
      { text: d, options: { fontSize: 12, color: INK } },
    ], { x: x + 0.22, y: y + 0.12, w: gw - 0.44, h: gh - 0.24, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
  });
  // 中心节点 + 连线
  const cx = 5.55, cy = 3.28, cw = 2.25, ch = 1.35;
  const cpts = [[3.0 + gw, 1.9 + 0.3], [3.0 + gw, 3.55 + 0.6], [3.0 + gw, 5.2 + 1.0], [9.15, 1.9 + 0.3], [9.15, 3.55 + 0.6], [9.15, 5.2 + 1.0]];
  [[gx[0] + gw, gy[0] + gh / 2], [gx[0] + gw, gy[1] + gh / 2], [gx[0] + gw, gy[2] + gh / 2],
   [gx[1], gy[0] + gh / 2], [gx[1], gy[1] + gh / 2], [gx[1], gy[2] + gh / 2]].forEach(([px, py]) => {
    s.addShape(p.shapes.LINE, { x: Math.min(px, cx + cw), y: Math.min(py, cy + ch / 2), w: Math.abs(cx + cw - px) || 0.01, h: Math.abs(cy + ch / 2 - py) || 0.01, line: { color: "D9C9BC", width: 1.25, dashType: "dash" } });
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: cx, y: cy, w: cw, h: ch, rectRadius: 0.1, fill: { color: PRIMARY }, line: { type: "none" }, shadow: shadow() });
  s.addText([
    { text: "公文汇编助手", options: { bold: true, fontSize: 17, color: "FFFFFF", breakLine: true } },
    { text: "17 个功能模块", options: { fontSize: 12, color: "F2D8D0" } },
  ], { x: cx, y: cy + 0.12, w: cw, h: ch - 0.2, align: "center", fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
  s.addText("全部功能共享同一本地数据库与模板体系，任何一个模块的产出都可直接被其他模块使用", { x: 4.45, y: 5.55, w: 4.55, h: 1.2, align: "center", valign: "middle", fontSize: 12, color: MUTED, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
}

/* ================= S7 章节 02 ================= */
divider(7, "02", "系统架构", "三层解耦的可测试架构：界面、逻辑、数据各司其职", [
  "纯逻辑层 core/ 不含任何界面代码，可独立单元测试",
  "所有 SQLite 读写收敛到 db/dao.py 一个入口",
  "耗时操作全部走 QThread 工作线程，界面永不卡死",
]);

/* ================= S8 技术选型 ================= */
{
  const s = p.addSlide();
  header(s, "02 系统架构", "技术选型：成熟开源库组合，双平台全离线", 8);
  const rows = [
    ["PySide6 (Qt 6)", "全部界面与内置 PDF 渲染引擎", "跨 Windows / 麒麟双平台，只用本地 GUI 栈"],
    ["SQLite + FTS5", "单文件数据库 + 全文检索", "bm25 排序、零部署；WAL 模式读写不互卡"],
    ["PyMuPDF", "PDF 解析 · 渲染 · 小册拼版", "逐页文本提取 + show_pdf_page 等比重排"],
    ["python-docx", "规范公文 Word 生成", "标准 OOXML + 手写域代码，WPS 打开不跑版"],
    ["jieba", "中文分词", "检索索引 · 纠错词边界保护 · SimHash 特征"],
    ["OpenCC", "简繁转换", "MIT 开源离线词典，无在线服务依赖"],
    ["PyInstaller", "打包为免安装交付目录", "onedir 启动快，排除 17 个网络/重型组件"],
  ];
  let y = 1.95;
  rows.forEach(([a, b, c], i) => {
    s.addText(a, { x: M, y, w: 2.7, h: 0.62, valign: "middle", fontSize: 14.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText(b, { x: 3.3, y, w: 4.3, h: 0.62, valign: "middle", fontSize: 13, color: INK, fontFace: FONT, margin: 0 });
    s.addText(c, { x: 7.75, y, w: 5.05, h: 0.62, valign: "middle", fontSize: 12.5, color: MUTED, fontFace: FONT, margin: 0 });
    if (i < rows.length - 1) s.addShape(p.shapes.LINE, { x: M, y: y + 0.63, w: W - 2 * M, h: 0, line: { color: LINE, width: 1 } });
    y += 0.64;
  });
  s.addText("运行依赖共 17 项（requirements.txt），全部有官方离线安装包，支持 ARM64 离线 wheels 预下载", { x: M, y: 6.6, w: 12, h: 0.32, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}

/* ================= S9 三层架构图 ================= */
{
  const s = p.addSlide();
  header(s, "02 系统架构", "三层架构：约 8900 行 Python，依赖方向清晰", 9);
  // 入口链
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 5.02, y: 1.8, w: 3.3, h: 0.56, rectRadius: 0.07, fill: { color: DARK }, line: { type: "none" } });
  s.addText("main.py（--portable / --import）", { x: 5.02, y: 1.8, w: 3.3, h: 0.56, align: "center", valign: "middle", fontSize: 12, bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
  arrow(s, 6.67, 2.36, 6.67, 2.62);
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 5.02, y: 2.62, w: 3.3, h: 0.56, rectRadius: 0.07, fill: { color: PRIM_DK }, line: { type: "none" } });
  s.addText("app.py 启动装配 · 种子导入 · 口令锁", { x: 5.02, y: 2.62, w: 3.3, h: 0.56, align: "center", valign: "middle", fontSize: 12, bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
  s.addShape(p.shapes.LINE, { x: 6.67, y: 3.18, w: 0, h: 0.22, line: { color: PRIMARY, width: 1.75 } });
  s.addShape(p.shapes.LINE, { x: 2.7, y: 3.4, w: 7.95, h: 0, line: { color: PRIMARY, width: 1.75 } });
  arrow(s, 2.7, 3.4, 2.7, 3.62); arrow(s, 6.67, 3.4, 6.67, 3.62); arrow(s, 10.65, 3.4, 10.65, 3.62);
  const layers = [
    ["ui / 界面层", "main_window 三栏主窗口", "编辑 · 资料库 · 参考三面板", "12 个功能对话框", "workers 后台线程机制"],
    ["core / 纯逻辑层", "parsers 六格式解析", "corrector 纠错 · inspector 体检", "docxgen · pdfrender · booklet", "reference · differ · simhash"],
    ["db / 数据层", "dao.py 唯一数据入口", "9 张业务表 + 3 个 FTS5 虚表", "WAL + 线程本地连接", "schema v2 自动迁移"],
  ];
  layers.forEach(([t, ...its], i) => {
    const x = 0.85 + i * 4.0;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 3.62, w: 3.65, h: 2.6, rectRadius: 0.08, fill: { color: i === 1 ? TINT : PAPER }, line: { color: i === 1 ? "E5C9C2" : LINE, width: 1 } });
    s.addText(t, { x: x + 0.22, y: 3.78, w: 3.2, h: 0.4, fontSize: 15.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText(its.map((tt, j) => ({ text: tt, options: { fontSize: 12, color: INK, breakLine: true } })), { x: x + 0.22, y: 4.28, w: 3.25, h: 1.8, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.45 });
  });
  arrow(s, 4.5, 4.9, 4.85, 4.9); arrow(s, 8.5, 4.9, 8.85, 4.9);
  s.addText([
    { text: "依赖方向 ui → core → db，core 不依赖 ui（可独立测试）；", options: { color: MUTED } },
    { text: " resources/data/seed.db（15.7 MB 词典 + 纠错库）随包分发，首次运行导入用户库。", options: { color: MUTED } },
  ], { x: M, y: 6.5, w: 12.3, h: 0.6, fontSize: 12.5, fontFace: FONT, margin: 0 });
}

/* ================= S10 数据层设计 ================= */
{
  const s = p.addSlide();
  header(s, "02 系统架构", "数据层：9 张业务表，一“室”管全部档案", 10);
  s.addText("业务表（schema.py · 版本 v2）", { x: M, y: 1.85, w: 5, h: 0.4, fontSize: 14, bold: true, color: INK, fontFace: FONT, margin: 0 });
  const tables = [
    ["documents", "文档"], ["categories", "分类树"], ["dictionary", "词典"],
    ["error_pairs", "纠错对"], ["user_phrases", "常用句式"], ["templates", "排版模板"],
    ["settings", "键值设置"], ["snapshots", "历史快照"], ["ignore_words", "忽略名单"],
  ];
  tables.forEach(([en, cn], i) => {
    const x = M + (i % 3) * 1.98, y = 2.38 + Math.floor(i / 3) * 0.72;
    chip(s, x, y, 1.84, 0.58, "", {});
    s.addText([
      { text: en, options: { bold: true, fontSize: 11.5, color: PRIMARY, breakLine: true } },
      { text: cn, options: { fontSize: 10.5, color: MUTED } },
    ], { x: x + 0.1, y: y + 0.045, w: 1.66, h: 0.5, fontFace: FONT, margin: 0, align: "center", lineSpacingMultiple: 1.0 });
  });
  s.addText([
    { text: "全文检索虚表：", options: { bold: true, fontSize: 12.5, color: INK } },
    { text: "documents_fts / phrases_fts（jieba 预分词后入库）；词典 12.3 万条不走 FTS，改 LIKE 三段式避免拖慢首启动", options: { fontSize: 12, color: MUTED } },
  ], { x: M, y: 4.72, w: 5.9, h: 0.95, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 6.85, y: 1.85, w: 5.98, h: 4.95, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
  s.addText("机制要点", { x: 7.1, y: 2.02, w: 3, h: 0.4, fontSize: 14, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
  const mechs = [
    ["线程安全", "每线程独立连接，WAL + synchronous=NORMAL"],
    ["内容去重", "入库即算 SHA-256 指纹，重复内容拒收"],
    ["相似预存", "documents 表同时落 SimHash 64 位指纹"],
    ["平滑升级", "PRAGMA user_version 迁移，迁移前自动 zip 备份"],
    ["快照轮转", "每文档保留最近 30 版，DAO 层自动裁剪"],
    ["FTS 自救", "索引失配时 rebuild_fts 一键全量重建"],
  ];
  let y = 2.5;
  mechs.forEach(([a, b]) => {
    rowItem(s, 7.1, y, 5.5, a, b, { h: 0.62 });
    y += 0.71;
  });
}

/* ================= S11 全文检索 ================= */
{
  const s = p.addSlide();
  header(s, "02 系统架构", "全文检索：中文切词 + FTS5，20 万字语料秒回", 11);
  const steps = ["用户输入\n“乡村振兴 政策”", "jieba 搜索引擎切词\nlcut_for_search ≤24 词", "FTS5 MATCH\n“乡村”AND“振兴”AND“政策”", "bm25() 相关度排序", "snippet 摘要\n命中词高亮返回"];
  let x = M;
  const bw = 2.28, gap = 0.32;
  steps.forEach((t, i) => {
    const two = t.split("\n");
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 2.1, w: bw, h: 1.5, rectRadius: 0.08, fill: { color: i === 4 ? PRIMARY : PAPER }, line: i === 4 ? { type: "none" } : { color: LINE, width: 1 }, shadow: shadow() });
    s.addText([
      { text: two[0], options: { bold: true, fontSize: 12.5, color: i === 4 ? "FFFFFF" : PRIMARY, breakLine: true } },
      { text: two[1] || "", options: { fontSize: 11.5, color: i === 4 ? "F2D8D0" : MUTED } },
    ], { x: x + 0.12, y: 2.2, w: bw - 0.24, h: 1.3, align: "center", valign: "middle", fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
    if (i < 4) arrow(s, x + bw + 0.03, 2.85, x + bw + gap - 0.03, 2.85);
    x += bw + gap;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 4.05, w: 7.3, h: 1.35, rectRadius: 0.08, fill: { color: DARK }, line: { type: "none" } });
  s.addText([
    { text: "查询构造示例（db/tokenize.py）", options: { fontSize: 12, color: DMUTED, breakLine: true } },
    { text: "“乡村振兴 政策” → \"乡村\" \"振兴\" \"政策\"（逐词加引号，隐式 AND）", options: { fontSize: 13.5, bold: true, color: "F2D8A0" } },
  ], { x: 0.8, y: 4.2, w: 6.8, h: 1.05, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.35 });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 8.15, y: 4.05, w: 4.68, h: 1.35, rectRadius: 0.08, fill: { color: TINT }, line: { type: "none" } });
  s.addText([
    { text: "< 1 秒", options: { fontSize: 30, bold: true, color: PRIMARY, breakLine: true } },
    { text: "200 篇文档全文检索实测（test_fts_search_speed_and_snippet）", options: { fontSize: 11.5, color: MUTED } },
  ], { x: 8.45, y: 4.18, w: 4.1, h: 1.1, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
  rowItem(s, M, 5.85, 12.3, "词典检索特殊优化：", "12 万词条不做 FTS，按“精确 0.9 > 前缀 0.8 > 包含 0.7”三段 LIKE 分级，兼顾首启速度与命中质量", { h: 0.5 });
  rowItem(s, M, 6.42, 12.3, "资料库检索：", "结果带上下文摘要（snippet）与相关度排序，回车即查，上限 100 条，F3 一键聚焦检索框", { h: 0.5 });
}

/* ================= S12 后台线程 ================= */
{
  const s = p.addSlide();
  header(s, "02 系统架构", "线程模型：耗时全后台，界面零卡顿", 12);
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 2.1, w: 3.3, h: 3.9, rectRadius: 0.08, fill: { color: DARK }, line: { type: "none" }, shadow: shadow() });
  s.addText([
    { text: "主线程 · UI", options: { bold: true, fontSize: 18, color: "FFFFFF", breakLine: true } },
    { text: "只做界面绘制与交互\n结果经 Signal 回流更新", options: { fontSize: 12.5, color: DMUTED, breakLine: true } },
    { text: "\n600ms 防抖", options: { bold: true, fontSize: 13, color: "F2D8A0", breakLine: true } },
    { text: "大纲树 / 公文预览均防抖重建，输入不抖屏", options: { fontSize: 11.5, color: DMUTED } },
  ], { x: 0.78, y: 2.35, w: 2.75, h: 3.4, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
  const workers = [
    ["FnWorker", "通用函数工作器：纠错 / 体检 / 对比 / 查重 / PDF 页渲染"],
    ["ImportWorker", "批量导入：逐文件进度 + OCR 页级进度，可中途停止"],
    ["CompileWorker 等", "汇编 / 两遍 PDF 渲染 / 小册子拼版，三大输出各一个"],
    ["TTSWorker", "逐句朗读，sentence 信号驱动编辑器同步高亮"],
  ];
  let y = 2.1;
  workers.forEach(([t, d]) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 5.15, y, w: 7.65, h: 0.82, rectRadius: 0.07, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText([
      { text: t + "　", options: { bold: true, fontSize: 13.5, color: PRIMARY } },
      { text: d, options: { fontSize: 12, color: INK } },
    ], { x: 5.4, y, w: 7.2, h: 0.82, valign: "middle", fontFace: FONT, margin: 0 });
    arrow(s, 3.8, y + 0.41, 5.12, y + 0.41, { color: "B9A79A", width: 1.25 });
    y += 1.02;
  });
  s.addText([
    { text: "细节：", options: { bold: true, color: PRIMARY } },
    { text: "QImage 可跨线程传递（QPixmap 不行）；SQLite 每线程独立连接；全文替换保留一次撤销。", options: { color: MUTED } },
  ], { x: M, y: 6.35, w: 12.3, h: 0.7, fontSize: 12.5, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
}

/* ================= S13 章节 03 ================= */
divider(13, "03", "材料导入与资料库", "七种格式进得来，一座资料库管得住", [
  "格式解析全部本地完成，扫描件可选 OCR",
  "老 .doc 二进制格式四级降级解析，尽力提取",
  "分类树 + 标签 + FTS 检索 + 快照版本",
]);

/* ================= S14 导入 ================= */
{
  const s = p.addSlide();
  header(s, "03 材料导入与资料库", "一站式导入：7 种文档格式 + 图片 OCR", 14);
  const exts = ["docx", "doc", "txt", "rtf", "pdf", "md", "html", "png/jpg/bmp/tif"];
  let x = M;
  exts.forEach((e, i) => {
    const w = i === 7 ? 2.5 : 1.18;
    chip(s, x, 1.95, w, 0.62, e, { fill: i === 7 ? TINT : PAPER, bold: true, fontSize: 13, color: i === 7 ? PRIMARY : INK });
    x += w + 0.18;
  });
  s.addText("文档 7 种格式直读（markdown / htm 等同族扩展名均支持）　·　图片 4 类走 OCR（Tesseract 5 + chi_sim，可选功能）", { x: M, y: 2.72, w: 12, h: 0.36, fontSize: 12.5, color: MUTED, fontFace: FONT, margin: 0 });
  const rows = [
    ["拖拽即进", "支持拖入文件或整个文件夹（递归扫描），也可从右键菜单 / 剪贴板一键入库"],
    ["后台解析", "ImportWorker 逐个解析入库，进度条含“OCR 第 x/y 页”页级反馈，可中途停止"],
    ["内容去重", "入库即算 SHA-256 指纹，重复内容自动跳过；完成统计“成功 N / 重复失败 M”"],
    ["失败友好", "扫描版 PDF 无文字层且缺 OCR 中文包时，返回可操作的安装指引而非静默空白"],
    ["标题识别", "复用公文编号正则识别“一、（一）1.”标题层级，首行 ≤50 字提为文档标题"],
  ];
  let y = 3.35;
  rows.forEach(([a, b]) => {
    rowItem(s, M, y, 12.3, a, b, { h: 0.55 });
    y += 0.68;
  });
}

/* ================= S15 .doc 四级降级 ================= */
{
  const s = p.addSlide();
  header(s, "03 材料导入与资料库", "最难啃的 .doc：四级降级解析链，尽力而为", 15);
  const steps = [
    ["① 本机办公软件", "COM 调用 WPS / WPS 个人版 / Word，另存为 DOCX", "效果最佳"],
    ["② LibreOffice", "soffice --headless --convert-to docx 无窗口转换", "次优"],
    ["③ 纯 Python 硬解", "olefile 读 WordDocument 流：FIB → fcClx → 分片表，UTF-16 / cp1252 解码", "约 95% 完整率"],
    ["④ 原始扫描兜底", "2 字节步长扫描 UTF-16 中文片段，连续 ≥8 字符才收录", "保底不空手"],
  ];
  let y = 1.95;
  steps.forEach(([t, d, tag], i) => {
    const isLast = i === 3;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 9.0, h: 0.98, rectRadius: 0.07, fill: { color: isLast ? TINT : PAPER }, line: { color: isLast ? "E5C9C2" : LINE, width: 1 } });
    s.addText([
      { text: t, options: { bold: true, fontSize: 14, color: PRIMARY, breakLine: true } },
      { text: d, options: { fontSize: 12, color: INK } },
    ], { x: 0.75, y: y + 0.07, w: 7.3, h: 0.85, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
    chip(s, 10.05, y + 0.2, 1.35, 0.58, tag, { fill: isLast ? PRIMARY : "EFE8DE", color: isLast ? "FFFFFF" : MUTED, bold: true, fontSize: 11.5 });
    if (i < 3) arrow(s, 0.95, y + 0.99, 0.95, y + 1.14, { color: "B9A79A", width: 1.25 });
    y += 1.15;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 6.42, w: 12.3, h: 0.52, rectRadius: 0.07, fill: { color: DARK }, line: { type: "none" } });
  s.addText([
    { text: "设计哲学：", options: { bold: true, color: "F2D8A0", fontSize: 12 } },
    { text: "老 .doc 不挑环境 —— 装了 WPS 用 WPS，装了 Office 用 Office，什么都没有也能靠纯解析把文字捞出来。", options: { color: DTEXT, fontSize: 12 } },
  ], { x: 0.8, y: 6.42, w: 11.8, h: 0.52, valign: "middle", fontFace: FONT, margin: 0 });
}

/* ================= S16 资料库管理 ================= */
{
  const s = p.addSlide();
  header(s, "03 材料导入与资料库", "资料库：像管档案一样管材料", 16);
  // 左：树示意
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 1.9, w: 4.7, h: 4.85, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
  s.addText("分类树（右键管理）", { x: 0.75, y: 2.05, w: 3.5, h: 0.4, fontSize: 13, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
  const tree = [
    ["▾ 全部文档（128）", 0, true], ["　▾ 办公室", 0, false], ["　　通知 (32)", 1, false],
    ["　　请示 (11)", 1, false], ["　▾ 政策文件", 0, false], ["　　上级来文 (46)", 1, false],
    ["　未分类 (39)", 0, false],
  ];
  let ty = 2.55;
  tree.forEach(([t, lv, hot]) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 0.75 + lv * 0.55, y: ty, w: 3.9 - lv * 0.55, h: 0.5, rectRadius: 0.05, fill: { color: hot ? PRIMARY : (lv === 0 ? "EFE8DE" : "F7F2EA") }, line: { type: "none" } });
    s.addText(t, { x: 0.9 + lv * 0.55, y: ty, w: 3.7 - lv * 0.55, h: 0.5, valign: "middle", fontSize: 12, bold: !!hot, color: hot ? "FFFFFF" : INK, fontFace: FONT, margin: 0 });
    ty += 0.62;
  });
  const feats = [
    ["全文检索", "回车即查，FTS5 + bm25 排序 + 摘要高亮，上限 100 条"],
    ["多级整理", "分类树右键增删改；删除分类时文档自动归入未分类"],
    ["标签与过滤", "条目显示“标题 [类型] 字数 #标签”；按类型动态过滤"],
    ["四种排序", "导入时间 / 标题 / 字数 / 最近更新一键切换"],
    ["批量操作", "多选后批量删除；导出为 TXT；移动到指定分类"],
    ["版本守护", "打开即看历史快照（30 版），差异预览后一键回滚"],
  ];
  let y = 1.98;
  feats.forEach(([a, b]) => {
    rowItem(s, 5.6, y, 7.2, a, b, { h: 0.62 });
    y += 0.82;
  });
}

/* ================= S17 章节 04 ================= */
divider(17, "04", "智能写作辅助", "从“憋公文”到“填公文”：骨架、工具、参考、纠错", [
  "15 种法定文种骨架，填要素即成稿",
  "写作参考三库联合检索，双击插入",
  "三级纠错流水线 + 三道误报防线",
]);

/* ================= S18 文种骨架 ================= */
{
  const s = p.addSlide();
  header(s, "04 智能写作辅助", "新建公文：15 种法定文种骨架全覆盖", 18);
  const kinds = ["决议", "决定", "命令（令）", "公报", "公告", "通告", "意见", "通知", "通报", "报告", "请示", "批复", "议案", "函", "纪要"];
  kinds.forEach((k, i) => {
    const x = M + (i % 5) * 2.5, y = 1.95 + Math.floor(i / 5) * 0.85;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w: 2.32, h: 0.68, rectRadius: 0.07, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText([
      { text: String(i + 1).padStart(2, "0") + "  ", options: { bold: true, fontSize: 11, color: ACCENT } },
      { text: k, options: { bold: true, fontSize: 14.5, color: INK } },
    ], { x: x + 0.14, y, w: 2.1, h: 0.68, valign: "middle", fontFace: FONT, margin: 0 });
  });
  const rules = [
    ["请示", "一文一事 · 主送一个机关 · 不得越级；结束语“妥否，请批示”"],
    ["报告", "不得夹带请示事项（体检自动报 error）；结束语“特此报告”"],
  ];
  let y = 4.62;
  rules.forEach(([a, b]) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 12.3, h: 0.72, rectRadius: 0.07, fill: { color: TINT }, line: { type: "none" } });
    s.addText([
      { text: a + "　", options: { bold: true, fontSize: 13.5, color: PRIMARY } },
      { text: b, options: { fontSize: 12.5, color: INK } },
    ], { x: 0.8, y, w: 11.7, h: 0.72, valign: "middle", fontFace: FONT, margin: 0 });
    y += 0.87;
  });
  s.addText([
    { text: "骨架即规范：", options: { bold: true, color: PRIMARY } },
    { text: "每种骨架自带标题模板、主送机关、分段结构与【】待填要素、（提示）写作提示段 —— 选文种、填要素，初稿即刻成型。", options: { color: MUTED } },
  ], { x: M, y: 6.5, w: 12.3, h: 0.6, fontSize: 12.5, fontFace: FONT, margin: 0 });
}

/* ================= S19 文秘工具箱 ================= */
{
  const s = p.addSlide();
  header(s, "04 智能写作辅助", "文秘工具箱：编辑器右键，随手可得的“老文秘”手艺", 19);
  const rows = [
    ["金额大写", "10050000.30", "人民币壹仟零伍拾万元零叁角整", "按人民银行票据规范：零压缩 · 角分规则 · “整”字处理"],
    ["日期大写", "2026年8月30日", "二〇二六年八月三十日", "另支持编号大写码转换"],
    ["简繁转换", "简体选区", "一键转繁体 / 转回简体", "OpenCC 离线词典（s2t / t2s），缺库优雅降级"],
    ["全半角", "全角 ABC123", "ABC123（或反向）", "0xFF01–0xFF5E 区间平移，选区或全文"],
  ];
  let y = 1.95;
  rows.forEach(([t, src, dst, note]) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 12.3, h: 1.02, rectRadius: 0.07, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText(t, { x: 0.75, y, w: 1.55, h: 1.02, valign: "middle", fontSize: 14.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText([
      { text: src + "  →  ", options: { fontSize: 13, color: MUTED } },
      { text: dst, options: { fontSize: 14, bold: true, color: INK } },
    ], { x: 2.4, y: y + 0.12, w: 6.6, h: 0.5, valign: "middle", fontFace: FONT, margin: 0 });
    s.addText(note, { x: 2.42, y: y + 0.6, w: 7.4, h: 0.36, fontSize: 11.5, color: MUTED, fontFace: FONT, margin: 0 });
    y += 1.18;
  });
  s.addText("同类还有：数字大写码 · 一键排版微调 · 朗读校对（F9）—— 全部离线，选区操作，Ctrl+Z 可撤销", { x: M, y: 6.75, w: 12, h: 0.35, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}

/* ================= S20 写作参考 ================= */
{
  const s = p.addSlide();
  header(s, "04 智能写作辅助", "写作参考：三库联合检索，双击即插入", 20);
  const srcs = [["资料库", "FTS5 + bm25 全文检索\n所有入库材料"], ["词典库", "12.3 万条\nLIKE 三段式分级"], ["常用句式库", "用户沉淀句式\nFTS5 检索"]];
  let y = 1.95;
  srcs.forEach(([t, d]) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 3.0, h: 1.15, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText([
      { text: t, options: { bold: true, fontSize: 14.5, color: PRIMARY, breakLine: true } },
      { text: d, options: { fontSize: 11.5, color: MUTED } },
    ], { x: 0.72, y: y + 0.09, w: 2.6, h: 0.98, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
    arrow(s, 3.55, y + 0.575, 4.4, y + 0.575);
    y += 1.42;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 4.45, y: 1.95, w: 3.9, h: 4.15, rectRadius: 0.08, fill: { color: PRIMARY }, line: { type: "none" }, shadow: shadow() });
  s.addText([
    { text: "相关度融合", options: { bold: true, fontSize: 15.5, color: "FFFFFF", breakLine: true } },
    { text: "各组分数 min-max 归一到 [0,1]", options: { fontSize: 12, color: "F2D8D0", breakLine: true } },
    { text: "标题命中 +0.15 · 标签命中 +0.10", options: { fontSize: 12, color: "F2D8D0", breakLine: true } },
    { text: "同源去重后合并排序", options: { fontSize: 12, color: "F2D8D0" } },
  ], { x: 4.75, y: 1.95, w: 3.35, h: 4.15, valign: "middle", fontFace: FONT, margin: 0, lineSpacingMultiple: 1.35 });
  arrow(s, 8.4, 4.02, 9.25, 4.02);
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 9.3, y: 2.62, w: 3.5, h: 2.8, rectRadius: 0.08, fill: { color: DARK }, line: { type: "none" } });
  s.addText([
    { text: "结果即所得", options: { bold: true, fontSize: 15.5, color: "FFFFFF", breakLine: true } },
    { text: "双击 / 按钮插入光标处", options: { fontSize: 12.5, color: DTEXT, breakLine: true } },
    { text: "好句一键“存为常用句式”", options: { fontSize: 12.5, color: DTEXT, breakLine: true } },
    { text: "同拼音近音词联想", options: { fontSize: 12.5, color: DTEXT } },
  ], { x: 9.6, y: 2.62, w: 2.95, h: 2.8, valign: "middle", fontFace: FONT, margin: 0, lineSpacingMultiple: 1.35 });
  rowItem(s, M, 6.12, 12.3, "怎么用：", "写“关于 XX 的通知”没有思路？输入关键词，同类材料、惯用句式、规范用语一次呈现；好句可“存为常用句式”反哺词库", { h: 0.45 });
}

/* ================= S21 纠错流水线 ================= */
{
  const s = p.addSlide();
  header(s, "04 智能写作辅助", "纠错引擎：三级流水线，像老编辑一样过稿", 21);
  const stages = [
    ["第一级 · 精确匹配", "4 万条错别字/混淆对，首字桶索引 + 最长优先", "内置精标 + 程序生成 + 用户自定义 + 机构沿革（提示级 0.7）"],
    ["第二级 · 上下文规则", "同一个词，不同语境不同结论", "“截止日期”保留；“截止 8 月底”改“截至”；火山爆发 / 山洪暴发"],
    ["第三级 · 标点数字规则", "GB/T 15835 数字与标点规范", "全角标点 · 省略号“……” · 破折号“——” · 汉字年份 · 概数顿号"],
  ];
  let x = M;
  stages.forEach(([t, d1, d2], i) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 2.0, w: 3.85, h: 2.5, rectRadius: 0.08, fill: { color: i === 0 ? PRIMARY : PAPER }, line: i === 0 ? { type: "none" } : { color: LINE, width: 1 }, shadow: shadow() });
    s.addText([
      { text: t, options: { bold: true, fontSize: 15, color: i === 0 ? "FFFFFF" : PRIMARY, breakLine: true } },
      { text: d1, options: { bold: true, fontSize: 12.5, color: i === 0 ? "FFFFFF" : INK, breakLine: true } },
      { text: d2, options: { fontSize: 11.5, color: i === 0 ? "F2D8D0" : MUTED } },
    ], { x: x + 0.24, y: 2.2, w: 3.4, h: 2.1, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
    if (i < 2) arrow(s, x + 3.9, 3.25, x + 4.42, 3.25);
    x += 4.47;
  });
  // 置信度条
  s.addText("置信度决定呈现：", { x: M, y: 4.95, w: 2.2, h: 0.45, valign: "middle", fontSize: 13, bold: true, color: INK, fontFace: FONT, margin: 0 });
  const bands = [["0.85–0.98 精标对", "D32F2F", "确认错误 · 红色"], ["0.55 程序生成对", "E58A2E", "疑似错误 · 橙色"], ["≥0.99 用户自定义", "D32F2F", "用户最高优先"], ["0.70 机构沿革", "8C8178", "提示级 · 灰色"]];
  let bx = 2.75;
  bands.forEach(([v, c, lab]) => {
    chip(s, bx, 4.95, 2.42, 0.45, v, { fill: c, color: "FFFFFF", bold: true, fontSize: 11.5 });
    s.addText(lab, { x: bx, y: 5.44, w: 2.42, h: 0.3, align: "center", fontSize: 10.5, color: MUTED, fontFace: FONT, margin: 0 });
    bx += 2.52;
  });
  rowItem(s, M, 6.05, 12.3, "结果可交互：", "逐条替换 / 全部替换（自动排除数字类）/ 忽略本次 / 永久忽略（写入 ignore_words，下次不再提示）", { h: 0.45 });
  rowItem(s, M, 6.55, 12.3, "即时生效：", "词典管理中新增纠错对立即参与匹配，缓存自动失效，无需重启", { h: 0.45 });
}

/* ================= S22 误报防线 ================= */
{
  const s = p.addSlide();
  header(s, "04 智能写作辅助", "三道误报防线：宁可少报，不可错报", 22);
  const lines = [
    ["防线一 · 词边界保护", "jieba 分词边界判定：命中片段落在词语内部即抑制；低置信命中（<0.7）双重门控，jieba 高频词（词频≥30）例外放行", "“安全生产”内部不误报“全生→全省”"],
    ["防线二 · 负向上下文", "成语与惯用语白名单：命中词位于特定固定搭配中时不提示", "“以德报怨”不纠“报怨”"],
    ["防线三 · 书名号保护", "书名号内是文件标题，按原文引用原则不作纠改", "《关于布署工作的通知》保持原样"],
  ];
  let y = 1.95;
  lines.forEach(([t, d, ex]) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y, w: 12.3, h: 1.32, rectRadius: 0.07, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText(t, { x: 0.75, y: y + 0.14, w: 2.5, h: 1.05, valign: "middle", fontSize: 14.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText(d, { x: 3.45, y: y + 0.12, w: 5.6, h: 1.08, valign: "middle", fontSize: 12, color: INK, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 9.25, y: y + 0.2, w: 3.3, h: 0.92, rectRadius: 0.06, fill: { color: "EFF7EE" }, line: { type: "none" } });
    s.addText([
      { text: "✓ ", options: { bold: true, fontSize: 13, color: "2E7D32" } },
      { text: ex, options: { fontSize: 11.5, color: "2E5B30" } },
    ], { x: 9.42, y: y + 0.2, w: 3.0, h: 0.92, valign: "middle", fontFace: FONT, margin: 0 });
    y += 1.5;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 6.5, w: 12.3, h: 0.55, rectRadius: 0.07, fill: { color: DARK }, line: { type: "none" } });
  s.addText("重叠命中按“置信度降序 → 长度降序”贪心去重，从后往前批量替换，Ctrl+Z 一次还原", { x: 0.8, y: 6.5, w: 11.7, h: 0.55, valign: "middle", fontSize: 12.5, color: DTEXT, fontFace: FONT, margin: 0 });
}

/* ================= S23 纠错知识库 ================= */
{
  const s = p.addSlide();
  header(s, "04 智能写作辅助", "纠错知识库：4 万条规则从哪里来", 23);
  // 左大数字
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 1.95, w: 4.15, h: 4.9, rectRadius: 0.08, fill: { color: PRIMARY }, line: { type: "none" }, shadow: shadow() });
  s.addText([
    { text: "40220", options: { fontSize: 54, bold: true, color: "FFFFFF", breakLine: true } },
    { text: "纠错对总量（seed.db 实测）", options: { fontSize: 13, color: "F2D8D0", breakLine: true } },
    { text: "\n220 条 人工精标", options: { bold: true, fontSize: 15, color: "F2D8A0", breakLine: true } },
    { text: "音近错字 · 易混搭配 · 新华社禁用词", options: { fontSize: 11.5, color: "F2D8D0", breakLine: true } },
    { text: "\n40000 条 程序化生成", options: { bold: true, fontSize: 15, color: "F2D8A0", breakLine: true } },
    { text: "高频双字词 × pypinyin 同音替换，\n错写本身不是常用词才入库", options: { fontSize: 11.5, color: "F2D8D0" } },
  ], { x: 0.85, y: 2.25, w: 3.5, h: 4.3, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.22 });
  const rows = [
    ["新华社禁用词", "建国以来 → 新中国成立以来；残废人 → 残疾人（规范用语提示）"],
    ["机构沿革 52 条", "环境保护部 → 生态环境部；银保监会 → 国家金融监督管理总局"],
    ["易混词分档", "权力/权利 按搭配区分；“反应情况→反映情况”类搭配纠错"],
    ["生成质量把控", "仅保留“错写不是真实常用词”的对，配合词边界门控防误报"],
    ["用户可扩充", "词典管理批量导入 CSV 纠错对，置信度 0.95，立即生效"],
  ];
  let y = 2.0;
  rows.forEach(([a, b]) => {
    rowItem(s, 5.1, y, 7.7, a, b, { h: 0.68 });
    y += 0.85;
  });
  s.addText("验收：精标词库抽 100 对在样句中 100% 识别（test_corrector）；布署→部署、截止→截至 为 e2e 固定验收对", { x: 5.1, y: 6.4, w: 7.7, h: 0.6, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
}

/* ================= S24 章节 05 ================= */
divider(24, "05", "一键汇编与成品输出", "选材料、选模板，剩下的交给程序", [
  "三步向导：选材料排序 → 选模板封面 → 生成",
  "规范 Word（WPS 不跑版）· A4 PDF · A3 骑马订",
  "批量模式：每份材料独立成文，失败不中断",
]);

/* ================= S25 汇编向导 ================= */
{
  const s = p.addSlide();
  header(s, "05 一键汇编与成品输出", "三步向导：从一堆材料到一本汇编", 25);
  const steps = [
    ["STEP 1", "选择材料", "全部文档勾选，默认全选；拖拽或上下移调整合并顺序；可选“材料标题作一级标题”"],
    ["STEP 2", "模板与封面", "下拉选模板（可跳转管理）；填封面标题、汇编单位、落款日期；勾选生成封面页"],
    ["STEP 3", "生成输出", "同名自动加时间戳防覆盖；完成后一键打开输出文件夹"],
  ];
  let x = M;
  steps.forEach(([tag, t, d], i) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 1.95, w: 3.95, h: 2.35, rectRadius: 0.08, fill: { color: i === 2 ? PRIMARY : PAPER }, line: i === 2 ? { type: "none" } : { color: LINE, width: 1 }, shadow: shadow() });
    s.addText([
      { text: tag, options: { bold: true, fontSize: 12, color: i === 2 ? "F2D8A0" : ACCENT, charSpacing: 2, breakLine: true } },
      { text: t, options: { bold: true, fontSize: 19, color: i === 2 ? "FFFFFF" : INK, breakLine: true } },
      { text: d, options: { fontSize: 11.5, color: i === 2 ? "F2D8D0" : MUTED } },
    ], { x: x + 0.26, y: 2.15, w: 3.45, h: 2.0, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
    if (i < 2) arrow(s, x + 4.0, 3.1, x + 4.32, 3.1);
    x += 4.37;
  });
  s.addText("四种输出（可多选）", { x: M, y: 4.62, w: 4, h: 0.4, fontSize: 14, bold: true, color: INK, fontFace: FONT, margin: 0 });
  const outs = [
    ["规范 Word .docx", "标准 OOXML，WPS / Word 通用"],
    ["A4 PDF", "内置渲染器，真实目录页码"],
    ["A3 骑马订小册子", "自动补页 · 页序自动排列"],
    ["批量模式", "每份材料独立成文，失败不中断"],
  ];
  outs.forEach(([t, d], i) => {
    const x = M + i * 3.14;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 5.1, w: 2.98, h: 1.15, rectRadius: 0.08, fill: { color: TINT }, line: { type: "none" } });
    s.addText([
      { text: t, options: { bold: true, fontSize: 13.5, color: PRIMARY, breakLine: true } },
      { text: d, options: { fontSize: 11.5, color: MUTED } },
    ], { x: x + 0.2, y: 5.22, w: 2.6, h: 0.95, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
  });
  s.addText("输出位置：我的文档\\公文汇编输出\\（独立于数据目录，方便直接取用）", { x: M, y: 6.55, w: 12, h: 0.35, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}

/* ================= S26 国标模板 ================= */
{
  const s = p.addSlide();
  header(s, "05 一键汇编与成品输出", "默认模板：GB/T 9704-2012 开箱即用", 26);
  const rows = [
    ["页边距", "上 37 · 下 35 · 左 28 · 右 26 毫米（国标版心）"],
    ["正文", "仿宋_GB2312 · 三号（16pt）· 固定行距 28 磅"],
    ["首行缩进", "2 字符（firstLineChars=200，随字号缩放）"],
    ["标题体系", "一级 黑体 · 二级 楷体 · 三级 仿宋加粗"],
    ["页码", "宋体四号 · 奇数右 / 偶数左（外侧）· “— 1 —”格式"],
    ["红头", "机关标志 方正小标宋 36pt 红字 + 发文字号 + 红色分隔线"],
    ["可选件", "封面 · 目录 · 版记 · 水印 / 密级标注（秘密★1年）"],
  ];
  let y = 1.95;
  rows.forEach(([a, b], i) => {
    s.addText(a, { x: M, y, w: 1.9, h: 0.6, valign: "middle", fontSize: 13.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText(b, { x: 2.5, y, w: 6.7, h: 0.6, valign: "middle", fontSize: 13, color: INK, fontFace: FONT, margin: 0 });
    if (i < rows.length - 1) s.addShape(p.shapes.LINE, { x: M, y: y + 0.61, w: 8.7, h: 0, line: { color: LINE, width: 1 } });
    y += 0.67;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 9.55, y: 1.95, w: 3.28, h: 4.6, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 }, shadow: shadow() });
  // 迷你版式示意
  s.addShape(p.shapes.RECTANGLE, { x: 9.95, y: 2.25, w: 2.48, h: 3.5, fill: { color: "FFFFFF" }, line: { color: "C9BEB2", width: 1 } });
  s.addText("××机关文件", { x: 9.95, y: 2.4, w: 2.48, h: 0.4, align: "center", fontSize: 11, bold: true, color: "D32F2F", fontFace: FONT, margin: 0 });
  s.addText("×政办发〔2026〕12号", { x: 9.95, y: 2.82, w: 2.48, h: 0.3, align: "center", fontSize: 8.5, color: "6B5E54", fontFace: FONT, margin: 0 });
  s.addShape(p.shapes.RECTANGLE, { x: 10.35, y: 3.18, w: 1.68, h: 0.028, fill: { color: "D32F2F" }, line: { type: "none" } });
  s.addShape(p.shapes.RECTANGLE, { x: 10.15, y: 3.45, w: 2.08, h: 0.09, fill: { color: "3B342E" }, line: { type: "none" } });
  for (let i = 0; i < 6; i++) s.addShape(p.shapes.RECTANGLE, { x: 10.15, y: 3.72 + i * 0.26, w: i === 5 ? 1.1 : 2.08, h: 0.055, fill: { color: "B4A99D" }, line: { type: "none" } });
  s.addText("— 1 —", { x: 11.45, y: 5.35, w: 0.95, h: 0.25, align: "right", fontSize: 8.5, color: "6B5E54", fontFace: FONT, margin: 0 });
  s.addText("模板 JSON 实时渲染预览", { x: 9.7, y: 5.95, w: 3.0, h: 0.35, align: "center", fontSize: 11.5, color: MUTED, fontFace: FONT, margin: 0 });
  s.addText("模板管理器三参数页 + 实时首页预览，保存即生效；字体不随包分发（版权），缺失时启动自动提示", { x: M, y: 6.75, w: 12.3, h: 0.35, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}

/* ================= S27 Word 引擎 ================= */
{
  const s = p.addSlide();
  header(s, "05 一键汇编与成品输出", "Word 生成引擎：六项底层细节，换来“不跑版”", 27);
  const rows = [
    ["目录真域代码", "TOC \\o \"1-3\" \\h \\z \\u + settings 写入 w:updateFields，Word/WPS 打开自动刷新页码"],
    ["中文字体不丢", "每个 run 同时设置 ascii / hAnsi / eastAsia 三个字体位"],
    ["缩进随字号缩放", "firstLineChars=200（字符单位）并写 twips 兜底，改字号不乱缩进"],
    ["页码在外侧", "evenAndOddHeaders 奇偶页脚 + PAGE 域，奇数右偶数左"],
    ["红头红线", "红色分隔线用段落底边框（FF0000 · 15/8pt），标题挂 Heading 1–3 供目录抓取"],
    ["水印注入", "页眉 VML 艺术字水印（Word/WPS 标准做法），透明度可调"],
  ];
  let y = 1.95;
  rows.forEach(([a, b]) => {
    rowItem(s, M, y, 8.1, a, b, { h: 0.68 });
    y += 0.78;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 9.0, y: 2.1, w: 3.83, h: 4.0, rectRadius: 0.1, fill: { color: PRIMARY }, line: { type: "none" }, shadow: shadow() });
  s.addText([
    { text: "WPS 打开\n不跑版", options: { bold: true, fontSize: 30, color: "FFFFFF", breakLine: true } },
    { text: "\n纯标准 OOXML：\n域代码 · 字符缩进 ·\n奇偶页脚全部按规范写入，\n永中 / WPS / Word 三端验证", options: { fontSize: 12.5, color: "F2D8D0" } },
  ], { x: 9.32, y: 2.4, w: 3.2, h: 3.5, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
  s.addText("验收：test_compile_pdf 校验 TOC 域 / updateFields / 奇偶页脚 / 28 磅行距 / 重开解析", { x: M, y: 6.75, w: 12.3, h: 0.35, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}

/* ================= S28 两遍渲染 ================= */
{
  const s = p.addSlide();
  header(s, "05 一键汇编与成品输出", "内置 PDF 渲染器：两遍渲染算准目录页码", 28);
  const steps = [
    ["第一遍渲染", "目录页码用“00”占位\n占位宽度≈真实页码\n保证分页与最终一致"],
    ["定位标题页码", "PyMuPDF search_for 全文找标题\n全匹配失败退 12 字前缀\n重名标题按顺序单调消歧"],
    ["第二遍渲染", "以真实页码重排重绘\n前置页数解析式计算\n（封面 1 页 + 目录分片数）"],
    ["页码盖章 + 水印", "PyMuPDF 盖“奇右偶左”页码\n内置中文字体 china-s\n可选密级/文稿水印平铺"],
  ];
  let x = M;
  steps.forEach(([t, d], i) => {
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 2.0, w: 2.82, h: 2.5, rectRadius: 0.08, fill: { color: i % 2 ? PAPER : TINT }, line: { color: i % 2 ? LINE : "E5C9C2", width: 1 } });
    s.addText([
      { text: t, options: { bold: true, fontSize: 14.5, color: PRIMARY, breakLine: true } },
      { text: d, options: { fontSize: 11.5, color: INK } },
    ], { x: x + 0.2, y: 2.18, w: 2.42, h: 2.15, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.32 });
    if (i < 3) arrow(s, x + 2.86, 3.25, x + 3.14, 3.25);
    x += 3.2;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 4.95, w: 6.05, h: 1.75, rectRadius: 0.08, fill: { color: DARK }, line: { type: "none" } });
  s.addText([
    { text: "为什么值得做？", options: { bold: true, fontSize: 14, color: "F2D8A0", breakLine: true } },
    { text: "不依赖 Word / WPS / LibreOffice —— 麒麟内网机器什么办公软件都没装，也能出带准确目录页码的正式 PDF。", options: { fontSize: 12.5, color: DTEXT } },
  ], { x: 0.8, y: 5.15, w: 5.5, h: 1.4, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.35 });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 6.8, y: 4.95, w: 6.03, h: 1.75, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
  s.addText([
    { text: "0", options: { fontSize: 44, bold: true, color: PRIMARY, breakLine: true } },
    { text: "外部办公软件依赖；目录分片（首页 20 行 / 后续每页 22 行）规避 Qt 表格不跨页限制", options: { fontSize: 12, color: MUTED } },
  ], { x: 7.1, y: 5.1, w: 5.5, h: 1.5, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
}

/* ================= S29 A3 小册子 ================= */
{
  const s = p.addSlide();
  header(s, "05 一键汇编与成品输出", "A3 骑马订小册子：页序算法，折对即成册", 29);
  s.addText("8 页手册 → 2 张 A3 纸（每张正反面，A4 两页并排）：", { x: M, y: 1.9, w: 9, h: 0.4, fontSize: 13.5, bold: true, color: INK, fontFace: FONT, margin: 0 });
  const sheets = [
    ["第 1 张 · 正面", "8", "1", "第 1 张 · 背面", "2", "7"],
    ["第 2 张 · 正面", "6", "3", "第 2 张 · 背面", "4", "5"],
  ];
  let y = 2.35;
  sheets.forEach(([fa, f1, f2, ba, b1, b2]) => {
    [[fa, f1, f2, M], [ba, b1, b2, 6.95]].forEach(([lab, p1, p2, x0]) => {
      s.addText(lab, { x: x0, y, w: 2.6, h: 0.35, fontSize: 12.5, bold: true, color: MUTED, fontFace: FONT, margin: 0 });
      s.addShape(p.shapes.RECTANGLE, { x: x0, y: y + 0.4, w: 5.5, h: 1.22, fill: { color: "FFFFFF" }, line: { color: PRIMARY, width: 1.5 }, shadow: shadow() });
      s.addShape(p.shapes.LINE, { x: x0 + 2.75, y: y + 0.4, w: 0, h: 1.22, line: { color: "C9BEB2", width: 1, dashType: "dash" } });
      s.addText(p1, { x: x0, y: y + 0.4, w: 2.75, h: 1.22, align: "center", valign: "middle", fontSize: 32, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
      s.addText(p2, { x: x0 + 2.75, y: y + 0.4, w: 2.75, h: 1.22, align: "center", valign: "middle", fontSize: 32, bold: true, color: INK, fontFace: FONT, margin: 0 });
    });
    y += 1.9;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 6.22, w: 12.3, h: 0.68, rectRadius: 0.07, fill: { color: DARK }, line: { type: "none" } });
  s.addText([
    { text: "第 k 张纸（k 从 0 计）公式：", options: { bold: true, color: "F2D8A0", fontSize: 12.5 } },
    { text: "正面 [N−2k, 2k+1] · 背面 [2k+2, N−2k−1]；页数自动补齐 4 的倍数（末尾补空白页）。沿中线装订折叠后恰为 1–8 页顺序（test_booklet_order_math 验收）。", options: { color: DTEXT, fontSize: 12 } },
  ], { x: 0.8, y: 6.22, w: 11.8, h: 0.68, valign: "middle", fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
}

/* ================= S30 章节 06 ================= */
divider(30, "06", "质检、对比与安全", "交出去之前，再把它从头到尾查一遍", [
  "GB/T 9704 格式体检：编号链条到字体行距",
  "文档对比 + SimHash 查重：改了哪、重复哪",
  "口令锁 + AES 备份：数据安全有多道保险",
]);

/* ================= S31 格式体检 ================= */
{
  const s = p.addSlide();
  header(s, "06 质检、对比与安全", "格式体检：把《条例》和《国标》装进检查器", 31);
  const rules = [
    ["标题编号链条", "一、（一）1.（1）逐行跟踪计数器：跳号、重复、回退、层级断档"],
    ["发文字号", "必须六角括号〔〕；圆括号 / 方括号直接报 error"],
    ["成文日期", "汉字年份、前导零、2026.8.30 点分格式提示"],
    ["结束语×文种", "请示→“妥否，请批示”；报告→“特此报告”；函→“为盼/为荷”"],
    ["文种混用", "报告夹带“请批示/请批复”报 error；请示多事项提示“一文一事”"],
    ["体例细节", "主送机关顶格；密级“秘密★保密期限”；附件说明全角冒号"],
    ["docx 版式", "页边距 · 仿宋正文 · 三号字 · 28 磅行距 · 页脚 PAGE 域"],
    ["防误报", "版式类 >30% 段落异常才整体报警，个别特例不打扰"],
  ];
  rules.forEach(([a, b], i) => {
    const x = M + (i % 2) * 6.25, y = 1.95 + Math.floor(i / 2) * 1.12;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y, w: 6.05, h: 0.98, rectRadius: 0.07, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText([
      { text: a, options: { bold: true, fontSize: 13, color: PRIMARY, breakLine: true } },
      { text: b, options: { fontSize: 11.5, color: INK } },
    ], { x: x + 0.22, y: y + 0.08, w: 5.65, h: 0.85, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
  });
  const sev = [["error 红", "D32F2F", "必须改"], ["warn 橙", "E58A2E", "建议改"], ["info 灰", "8C8178", "供参考"]];
  let x = M;
  s.addText("结果三级着色：", { x: M, y: 6.5, w: 1.6, h: 0.45, valign: "middle", fontSize: 12.5, bold: true, color: INK, fontFace: FONT, margin: 0 });
  x = 2.05;
  sev.forEach(([t, c, d]) => {
    chip(s, x, 6.5, 1.7, 0.45, t, { fill: c, color: "FFFFFF", bold: true, fontSize: 11.5 });
    s.addText(d, { x: x + 1.78, y: 6.5, w: 0.95, h: 0.45, valign: "middle", fontSize: 11.5, color: MUTED, fontFace: FONT, margin: 0 });
    x += 2.95;
  });
}

/* ================= S32 对比与查重 ================= */
{
  const s = p.addSlide();
  header(s, "06 质检、对比与安全", "文档对比与相似查重：改了哪、重复哪，一眼看清", 32);
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 1.9, w: 5.93, h: 4.85, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
  s.addText("文档对比 · differ.py", { x: 0.78, y: 2.05, w: 5, h: 0.4, fontSize: 15, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
  const dl = [
    "段落级 SequenceMatcher 行对齐",
    "replace 行内再做词级 diff（jieba 分词，短串退字符级）",
    "双栏红绿 HTML：删红 · 增绿 · 改黄",
    "统计增/删/改行数 + 相似度百分比",
    "对比对象：库内两文档，或任选本地文件（6 格式直读）",
  ];
  let y = 2.55;
  dl.forEach(t => { rowItem(s, 0.78, y, 5.5, t, "", { h: 0.55, leadSize: 12.5, leadColor: INK }); y += 0.68; });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 6.9, y: 1.9, w: 5.93, h: 4.85, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
  s.addText("相似查重 · simhash.py", { x: 7.15, y: 2.05, w: 5, h: 0.4, fontSize: 15, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
  const sl = [
    ["建指纹", "特征 = jieba 词语 + 相邻二元组；FNV-1a 64 位哈希（纯 Python 零依赖）"],
    ["粗筛", "SimHash 海明距离（阈值放宽 0.25），速度优先"],
    ["精判", "粗筛命中对再算字符三元组 Jaccard，作为最终相似度"],
    ["入库即算", "documents 表持久化指纹，查重免全库重算"],
    ["可调阈值", "40–99% 滑块（默认 70%），找出“换说法的重复件”"],
  ];
  y = 2.55;
  sl.forEach(([a, b]) => { rowItem(s, 7.15, y, 5.45, a, b, { h: 0.68 }); y += 0.82; });
}

/* ================= S33 快照与微调 ================= */
{
  const s = p.addSlide();
  header(s, "06 质检、对比与安全", "历史快照与排版微调：让“手滑”可反悔", 33);
  s.addText("历史快照", { x: M, y: 1.9, w: 4, h: 0.4, fontSize: 15, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
  const snaps = [
    ["每 3 分钟", "有改动时自动快照（reason=auto）"],
    ["保存前", "写入资料库前先存一版"],
    ["保留 30 版", "每文档独立计数，DAO 自动裁剪"],
    ["差异预览", "与当前内容词级 diff，确认后回滚"],
    ["回滚安全", "整块替换保留撤销栈，Ctrl+Z 可再反悔"],
  ];
  let y = 2.4;
  snaps.forEach(([a, b]) => { rowItem(s, M, y, 5.7, a, b, { h: 0.6 }); y += 0.72; });
  s.addText("一键排版微调（五步流水线，输出变更清单）", { x: 6.7, y: 1.9, w: 6.1, h: 0.4, fontSize: 15, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
  const fsteps = ["清理多余空格 / 空行", "全角数字转半角", "去除段首空白", "规范标题编号链（一、（一）1.（1））", "正文段补两字符缩进"];
  let fy = 2.45;
  fsteps.forEach((t, i) => {
    s.addShape(p.shapes.OVAL, { x: 6.7, y: fy, w: 0.5, h: 0.5, fill: { color: PRIMARY }, line: { type: "none" } });
    s.addText(String(i + 1), { x: 6.7, y: fy, w: 0.5, h: 0.5, align: "center", valign: "middle", fontSize: 15, bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
    s.addText(t, { x: 7.38, y: fy + 0.02, w: 5.4, h: 0.48, valign: "middle", fontSize: 13, color: INK, fontFace: FONT, margin: 0 });
    if (i < 4) s.addShape(p.shapes.LINE, { x: 6.95, y: fy + 0.5, w: 0, h: 0.28, line: { color: "D9C9BC", width: 1.5 } });
    fy += 0.78;
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 6.4, w: 12.3, h: 0.6, rectRadius: 0.07, fill: { color: TINT }, line: { type: "none" } });
  s.addText([
    { text: "相似主题：", options: { bold: true, color: PRIMARY } },
    { text: "跨文档批量查找替换（支持正则，先预览每篇命中再执行，执行前提醒备份）", options: { color: INK } },
  ], { x: 0.8, y: 6.4, w: 11.7, h: 0.6, valign: "middle", fontSize: 12.5, fontFace: FONT, margin: 0 });
}

/* ================= S34 安全体系 ================= */
{
  const s = p.addSlide();
  header(s, "06 质检、对比与安全", "安全与备份：四道保险，数据留在本机", 34);
  const cards = [
    ["口令锁", ["PBKDF2-HMAC-SHA256", "12 万次迭代 + 随机盐", "compare_digest 防时序攻击", "错 5 次强制等待 10 秒"]],
    ["加密备份", ["pyzipper AES（WZ_AES）", "口令可选，明文包也支持", "包内含库 + 模板 + 清单", "跨机迁移一键完成"]],
    ["自动轮转", ["退出时自动备份（可关）", "定时备份间隔可配置", "保留最近 20 份轮转", "文件名含毫秒防同秒覆盖"]],
    ["恢复护栏", ["备份前 WAL checkpoint 落盘", "恢复前自动再做一次备份", "SQLite 完整性试连校验", "临时目录校验后原子替换"]],
  ];
  cards.forEach(([t, items], i) => {
    const x = M + i * 3.14;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 1.95, w: 2.98, h: 3.6, rectRadius: 0.08, fill: { color: i === 0 ? TINT : PAPER }, line: { color: i === 0 ? "E5C9C2" : LINE, width: 1 } });
    s.addText(t, { x: x + 0.22, y: 2.12, w: 2.5, h: 0.45, fontSize: 16, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText(items.map((tt, j) => ({ text: tt, options: { fontSize: 11.5, color: INK, breakLine: true } })), { x: x + 0.22, y: 2.68, w: 2.6, h: 2.7, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.55 });
  });
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 5.85, w: 12.3, h: 0.95, rectRadius: 0.07, fill: { color: DARK }, line: { type: "none" } });
  s.addText([
    { text: "透明说明：", options: { bold: true, color: "F2D8A0", fontSize: 12.5 } },
    { text: "口令锁定位是“防误用级”防护，非涉密级加密；真正的安全底线是 —— 全程零网络请求，数据不出这台机器。", options: { color: DTEXT, fontSize: 12.5 } },
  ], { x: 0.8, y: 5.85, w: 11.7, h: 0.95, valign: "middle", fontFace: FONT, margin: 0 });
}

/* ================= S35 章节 07 ================= */
divider(35, "07", "交付、质量与生态", "装得上、跑得动、测得全、发得出", [
  "Windows x64 + 麒麟 V10 ARM64 双平台交付",
  "4 类安装包自动构建，GitHub Actions 全自动发布",
  "87 项测试 + 端到端自检脚本",
]);

/* ================= S36 双平台 ================= */
{
  const s = p.addSlide();
  header(s, "07 交付、质量与生态", "双平台交付：从 Windows 办公机到麒麟信创终端", 36);
  const plats = [
    ["Windows 10 / 11 · x64", ["目录版 dist\\gwtool\\gwtool.exe（启动 ≤5 秒）", "便携版 zip：免安装压缩包", "Inno Setup 安装包（无需管理员权限）", "右键菜单“用公文汇编助手导入”"]],
    ["麒麟 V10 · ARM64", ["原生 ARM64 runner 打包（非模拟）", "deb 包：desktop 入口 + Qt 运行库依赖声明", "makeself 自解压 .run 安装包", "离线 wheels：有网机预下载，内网纯离线装"]],
  ];
  plats.forEach(([t, items], i) => {
    const x = M + i * 6.25;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 1.9, w: 6.05, h: 2.6, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText(t, { x: x + 0.25, y: 2.05, w: 5.5, h: 0.45, fontSize: 16, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText(items.map(tt => ({ text: tt, options: { fontSize: 12, color: INK, breakLine: true } })), { x: x + 0.25, y: 2.6, w: 5.6, h: 1.8, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.45 });
  });
  s.addText("数据放哪，一目了然", { x: M, y: 4.75, w: 5, h: 0.4, fontSize: 14, bold: true, color: INK, fontFace: FONT, margin: 0 });
  const paths = [
    ["Windows 常规", "%APPDATA%\\gwtool\\"],
    ["麒麟 / Linux", "~/.local/share/gwtool/"],
    ["便携模式", "程序同级 Data\\（U 盘随带随走）"],
    ["汇编输出", "我的文档\\公文汇编输出\\"],
  ];
  paths.forEach(([a, b], i) => {
    const x = M + (i % 2) * 6.25, y = 5.25 + Math.floor(i / 2) * 0.62;
    s.addText([
      { text: a + "　", options: { bold: true, color: PRIMARY, fontSize: 12.5 } },
      { text: b, options: { color: INK, fontSize: 12.5 } },
    ], { x, y, w: 6.05, h: 0.5, valign: "middle", fontFace: FONT, margin: 0 });
  });
  s.addText("程序与数据分离：升级重装不动数据；“备份/恢复”可整体搬迁到另一台电脑", { x: M, y: 6.62, w: 12.3, h: 0.35, fontSize: 12, color: MUTED, fontFace: FONT, margin: 0 });
}

/* ================= S37 打包矩阵 ================= */
{
  const s = p.addSlide();
  header(s, "07 交付、质量与生态", "打包矩阵：一份 spec，四类产物", 37);
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 3.87, y: 1.9, w: 5.6, h: 1.55, rectRadius: 0.08, fill: { color: PRIMARY }, line: { type: "none" }, shadow: { type: "outer", color: "3A2E28", blur: 7, offset: 2, angle: 90, opacity: 0.18 } });
  s.addText([
    { text: "gwtool.spec · PyInstaller onedir", options: { bold: true, fontSize: 15.5, color: "FFFFFF", breakLine: true } },
    { text: "双平台共用唯一配置：携带 seed.db + OpenCC 数据；排除 17 个网络/重型模块；UPX 压缩；窗口程序无控制台", options: { fontSize: 11.5, color: "F2D8D0" } },
  ], { x: 4.15, y: 2.05, w: 5.05, h: 1.3, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
  const outs = [
    ["Windows 目录版", "gwtool.exe 启动最快，配便携 zip 分发"],
    ["Windows 安装包", "Inno Setup · lzma2 固实压缩 · 装完可启动"],
    ["ARM64 deb 包", "内置 desktop 文件，声明 Qt 运行库依赖"],
    ["ARM64 tar.gz / .run", "免 root 解压即用；makeself 自解压安装"],
  ];
  s.addShape(p.shapes.LINE, { x: 6.67, y: 3.45, w: 0, h: 0.35, line: { color: "B9A79A", width: 1.25 } });
  s.addShape(p.shapes.LINE, { x: M + 1.49, y: 3.8, w: 11.34 - M - 1.49, h: 0, line: { color: "B9A79A", width: 1.25 } });
  outs.forEach(([t, d], i) => {
    const x = M + i * 3.14;
    arrow(s, x + 1.49, 3.8, x + 1.49, 4.0, { color: "B9A79A", width: 1.25 });
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 4.0, w: 2.98, h: 1.5, rectRadius: 0.08, fill: { color: PAPER }, line: { color: LINE, width: 1 } });
    s.addText([
      { text: t, options: { bold: true, fontSize: 13.5, color: PRIMARY, breakLine: true } },
      { text: d, options: { fontSize: 11.5, color: MUTED } },
    ], { x: x + 0.2, y: 4.14, w: 2.6, h: 1.25, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.25 });
  });
  s.addText([
    { text: "系统集成：", options: { bold: true, color: PRIMARY, fontSize: 12.5 } },
    { text: "install_context_menu.bat 写当前用户注册表（无需管理员）挂右键菜单；gwtool.desktop 注册 6 类文档“打开方式”；OCR 安装引导在安装包初始化时弹窗提示。", options: { color: MUTED, fontSize: 12.5 } },
  ], { x: M, y: 5.85, w: 12.3, h: 0.8, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
}

/* ================= S38 CI/CD ================= */
{
  const s = p.addSlide();
  header(s, "07 交付、质量与生态", "CI/CD：打一个 tag，四类安装包自动发布", 38);
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: M, y: 1.95, w: 2.5, h: 3.9, rectRadius: 0.08, fill: { color: DARK }, line: { type: "none" } });
  s.addText([
    { text: "触发", options: { bold: true, fontSize: 15, color: "F2D8A0", breakLine: true } },
    { text: "push v* 标签\n或手动触发\n\nbuild.yml\nGitHub Actions", options: { fontSize: 12.5, color: DTEXT } },
  ], { x: 0.72, y: 2.15, w: 2.1, h: 3.5, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
  const jobs = [
    ["Job 1 · windows-latest", ["ruff 静态检查（gwtool/scripts/tests）", "pytest 全量 87 用例（offscreen + 300s 超时）", "PyInstaller 按 spec 打包", "便携 zip + ISCC 安装包（版本号取自代码）"]],
    ["Job 2 · ubuntu-24.04-arm", ["原生 ARM64 runner（非交叉编译）", "apt 装 Qt offscreen 运行库", "同一 spec 打包 + dpkg-deb 组装", "deb 声明依赖，推荐 tesseract 中文包"]],
    ["Job 3 · release", ["依赖前两个 Job 全绿", "gh release create 自动建发布", "附 4 类安装包 + 自动 Release Notes", "标题“公文汇编助手 v TAG”"]],
  ];
  jobs.forEach(([t, items], i) => {
    const y = 1.95 + i * 1.38;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 3.45, y, w: 9.38, h: 1.22, rectRadius: 0.07, fill: { color: i === 2 ? TINT : PAPER }, line: { color: i === 2 ? "E5C9C2" : LINE, width: 1 } });
    s.addText(t, { x: 3.7, y: y + 0.1, w: 2.6, h: 1.0, valign: "middle", fontSize: 13.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    s.addText(items.map(tt => ({ text: tt, options: { fontSize: 11.5, color: INK, breakLine: true } })), { x: 6.35, y: y + 0.08, w: 6.3, h: 1.08, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.18 });
    if (i < 2) arrow(s, 4.1, y + 1.22, 4.1, y + 1.38, { color: "B9A79A", width: 1.25 });
  });
  arrow(s, 3.0, 3.9, 3.42, 3.9, { color: "B9A79A", width: 1.25 });
}

/* ================= S39 质量保障 ================= */
{
  const s = p.addSlide();
  header(s, "07 交付、质量与生态", "质量保障：87 项测试，性能指标写进断言", 39);
  // 手绘条形图
  s.addText("测试分布（pytest，按文件）", { x: M, y: 1.9, w: 5, h: 0.38, fontSize: 13.5, bold: true, color: INK, fontFace: FONT, margin: 0 });
  const bars = [
    ["test_features", 24], ["test_corrector", 11], ["test_p2p3_features", 11],
    ["test_parsers", 9], ["test_p1_features", 9], ["test_data_search", 8],
    ["test_compile_pdf", 6], ["test_ui_guard", 5], ["test_app_smoke", 4],
  ];
  const maxV = 24, bx0 = 3.15, bw2 = 3.55;
  let by = 2.42;
  bars.forEach(([t, v], i) => {
    s.addText(t, { x: M, y: by, w: 2.6, h: 0.34, valign: "middle", align: "right", fontSize: 11, color: MUTED, fontFace: FONT, margin: 0 });
    s.addShape(p.shapes.RECTANGLE, { x: bx0, y: by + 0.055, w: bw2 * v / maxV, h: 0.23, fill: { color: i === 0 ? PRIMARY : "C4807A" }, line: { type: "none" } });
    s.addText(String(v), { x: bx0 + bw2 * v / maxV + 0.08, y: by, w: 0.6, h: 0.34, valign: "middle", fontSize: 11.5, bold: true, color: PRIMARY, fontFace: FONT, margin: 0 });
    by += 0.44;
  });
  s.addText("合计 87 用例 · 每次提交 ruff E9+F821 静态门槛（拦截“忘导入即崩溃”）", { x: M, y: 6.5, w: 7, h: 0.4, fontSize: 11.5, color: MUTED, fontFace: FONT, margin: 0 });
  // 右侧性能验收
  s.addShape(p.shapes.ROUNDED_RECTANGLE, { x: 7.6, y: 1.9, w: 5.23, h: 4.95, rectRadius: 0.08, fill: { color: DARK }, line: { type: "none" } });
  s.addText("性能验收（写进测试的硬指标）", { x: 7.88, y: 2.08, w: 4.7, h: 0.4, fontSize: 13.5, bold: true, color: "F2D8A0", fontFace: FONT, margin: 0 });
  const perf = [
    ["50 个文件导入", "< 10 秒"],
    ["20 万字语料检索", "< 1 秒"],
    ["精标 100 对识别率", "100%"],
    ["首启种子导入", "< 15 秒"],
    ["端到端自检", "9 步全 PASS"],
    ["网络请求", "0 次"],
  ];
  let py = 2.62;
  perf.forEach(([a, b], i) => {
    s.addText(a, { x: 7.88, y: py, w: 3.0, h: 0.42, valign: "middle", fontSize: 12.5, color: DTEXT, fontFace: FONT, margin: 0 });
    s.addText(b, { x: 10.9, y: py, w: 1.65, h: 0.42, valign: "middle", align: "right", fontSize: 15, bold: true, color: i === 5 ? ACCENT : "FFFFFF", fontFace: FONT, margin: 0 });
    if (i < 5) s.addShape(p.shapes.LINE, { x: 7.88, y: py + 0.5, w: 4.65, h: 0, line: { color: DLINE, width: 1 } });
    py += 0.62;
  });
  s.addText("另有 e2e_check.py：模拟真实数据目录走通 9 大流程，逐项 PASS/FAIL", { x: 7.88, y: 6.35, w: 4.7, h: 0.45, fontSize: 11, color: DMUTED, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.2 });
}

/* ================= S40 结束页 ================= */
{
  const s = p.addSlide();
  s.background = { color: DARK };
  seal(s, M, 0.55, 0.44, "文");
  s.addText("公文汇编助手 · 单机离线版 v1.2.0", { x: 1.08, y: 0.55, w: 7, h: 0.44, valign: "middle", fontSize: 12, color: DMUTED, fontFace: FONT, margin: 0 });
  s.addText("完全离线 · 数据自主 · 开箱即用", { x: M, y: 2.3, w: 11.5, h: 1.0, fontSize: 42, bold: true, color: "FFFFFF", fontFace: FONT, margin: 0 });
  s.addText("不联网、不上传、不留痕 —— 一台电脑，就是一座公文资料馆。", { x: 0.53, y: 3.45, w: 10, h: 0.5, fontSize: 16, color: DMUTED, fontFace: FONT, margin: 0 });
  const docs = [
    ["README.md", "功能总览 · 快速开始 · 打包发布 · 验收对照"],
    ["项目文件结构说明.md", "写给新手的逐文件导读与“改 X 去哪”速查表"],
    ["scripts/e2e_check.py", "9 步端到端自检，部署后一键验货"],
  ];
  docs.forEach(([t, d], i) => {
    const x = M + i * 4.2;
    s.addShape(p.shapes.ROUNDED_RECTANGLE, { x, y: 4.45, w: 3.95, h: 1.35, rectRadius: 0.08, fill: { color: DCARD }, line: { color: DLINE, width: 1 } });
    s.addText([
      { text: t, options: { bold: true, fontSize: 14, color: "F2D8A0", breakLine: true } },
      { text: d, options: { fontSize: 11.5, color: DMUTED } },
    ], { x: x + 0.22, y: 4.6, w: 3.5, h: 1.1, fontFace: FONT, margin: 0, lineSpacingMultiple: 1.3 });
  });
  s.addText("谢谢审阅", { x: M, y: 6.45, w: 4, h: 0.5, fontSize: 15, bold: true, color: ACCENT, charSpacing: 4, fontFace: FONT, margin: 0 });
  seal(s, 11.2, 5.55, 1.15, "文");
}

p.writeFile({ fileName: "C:/gwtool/公文汇编助手-系统介绍.pptx" }).then(() => console.log("DONE 40 slides"));
