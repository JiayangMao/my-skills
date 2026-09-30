---
name: ebook-retypeset
description: 检查和修复中文电子书（EPUB/MOBI/AZW3/TXT）的格式与排版问题，并重新排版生成干净的新 EPUB。当用户提到电子书排版/格式不对、汉字之间出现多余空格导致断词、页眉页码混入正文、段落没有首行缩进、章节目录损坏或缺失、注释/脚注被压平混进正文、想把脚注做成弹出式（popup footnote）、要求“重新排版”“修复排版”“整理格式”“转换电子书”等任何相关需求时使用本技能，即使没有明确说出“排版”两个字。也适用于用户让检查某本书格式是否正确、或提到注释/脚注排版的情况。
---

# 电子书重新排版

把一本排版混乱的中文电子书，诊断问题、修复文本、重建成符合中文排版规范的新 EPUB。核心流程三步走：**诊断 → 修复 → 重建**。先用 `scripts/diagnose_epub.py` 自动诊断，再对照 `references/epub-issues.md` 的问题清单人工确认，然后决定是只修复局部（用 `scripts/fix_cjk_spaces.py`）还是整体重建。

## 第 1 步：定位并解压电子书

EPUB 本质是 zip 包。先找到文件（常在 `~/download`、`~/storage/downloads`、`/storage/emulated/0/Download`）：

```bash
unzip -o "书.epub" -d book_extracted && find book_extracted -type f | sort
```

关注 `content.opf`（元数据/spine 顺序/manifest）、`toc.ncx` / `nav.xhtml`（目录导航）、`stylesheet.css` / `page_styles.css`（排版样式）、`*.xhtml` / `*.html`（正文）。MOBI/AZW3 先转成 EPUB（`ebook-convert` 或 Calibre）再处理。

## 第 2 步：诊断问题

运行自动诊断（对已解压目录，或直接给 .epub 路径），再人工抽查确认：

```bash
python3 <skill-dir>/scripts/diagnose_epub.py book_extracted
```

把症状对到 `references/epub-issues.md` 的对应小节（那里有特征正则、示例与修复要点）：

| 症状 | 参考 |
| --- | --- |
| 汉字之间被塞进多余空格、断词（最伤阅读，必修） | §1 幽灵空格 |
| 正文混入孤立的页眉/页码短段，常把句子拦腰截断 | §2 页眉页码 |
| 正文没有 2 字首行缩进（CSS 未设 `text-indent: 2em`） | §3 首行缩进 |
| 章/节标题是普通 `<p>`，没有 `<h1>/<h2>` | §4 标题层级 |
| `toc.ncx` 只有一个 navPoint、HTML 目录缺失 | §5 目录损坏 |
| `dc:language` 不是 `zh-CN`、`title_sort` 是哈希值 | §6 元数据错误 |
| 正文大量空段落（`<p> </p>` / `&nbsp;`）撑间距 | §7 空段落 |
| 注释被压平进正文、句子被标记截断 | §11 注释 |

## 第 3 步：修复文本

### 3a. 修复幽灵空格

规则：删除两侧都是“中日韩字符/全角标点”的空格，字符集覆盖汉字、中文标点（，。、：；？！「」等）、全角字符、弯引号、破折号、省略号、间隔号；两侧不都是这类字符时保留空格（如汉字+数字、拉丁词之间，`"ICU-21 床"`、`"OK 镜"`）。详见 §1。批量处理：

```bash
python3 <skill-dir>/scripts/fix_cjk_spaces.py book_extracted -o book_fixed
```

脚本只处理非标题的文本节点：标题里的空格往往是有意的（如「第一章 测试」，与目录标签一致），删掉会让标题和目录对不上，所以 `<title>` / `<h1>`–`<h6>` 默认跳过（要强行处理加 `--include-headings`）；也不会误改标签属性（如 `alt="三 年"`）。

### 3b. 移除页眉并拼接被截断的段落

页眉通常呈 `[正文A][空段][页眉][空段][正文B]` 结构：删掉页眉段及相邻空段（页眉模式见 §2）。若 A 不以句末标点结尾（`。！？…"」』）)】〉》` 等），说明句子被页眉截断，把 A 与 B **直接拼接**（中间不加空格——原文此处是换行不是空格）。注意先确认没有标题被误判成 A 而参与拼接（标题段孤立存在、后跟空段，不会紧贴页眉）。

### 3c. 识别结构（部 / 章 / 节）

先看纸质书目录页或原 `toc.ncx`，提取“第X章 + 标题 + 各节标题”，再到正文里定位（坑位见 §8）：

- 标题通常是**被空段包裹的孤立短段落**；部/卷标记如“第一部”常配一个地点/引文题记。
- 警惕目录漏节（正文有而目录没有）、以及既作部名又作节名的重复地名，按出现顺序消歧。

### 3d. 处理注释（脚注）

中文纸书的脚注在转换时经常被“压平”进正文：注释文字变成普通段落夹在正文中间，甚至把句子从中截断。识别特征（标记开头的注释段落、正文对应标记、被换页拆断的续段、编号按页重复）见 §11。

修复目标是**弹出式脚注**（EPUB3 popup footnote）：点正文上标时阅读器在原地弹出注释，不打断阅读。契约（noteref 作上标，footnote 收在该章末尾的 `<section>`，每条一个 `<aside>`，带双向链接）：

```html
<p>……中度抑郁<sup><a epub:type="noteref" href="#fn1" id="fnref1">1</a></sup>和中度焦虑<sup><a epub:type="noteref" href="#fn2" id="fnref2">2</a></sup>……</p>
<section epub:type="footnotes" role="doc-endnotes">
  <h2>注释</h2>
  <aside epub:type="footnote" id="fn1" role="doc-footnote">
    <p><a href="#fnref1">1</a> 抑郁症，也被称为抑郁障碍……</p>
  </aside>
</section>
```

要点：
- `<html>` 必须声明 `xmlns:epub="http://www.idpf.org/2007/ops"`，否则 `epub:type` 会被忽略。
- noteref 与 footnote 放在**同一份 XHTML** 里弹出最可靠；注释统一收在该章末尾，按章内出现顺序编号并带回链。
- 圈号标记要换成上标数字/链接（保留圈号会让编号语义混乱）；拆断的注释与后段拼接完整，编号按章内顺序重排避免跨页重复。
- 这是“渐进增强”：不支持弹出脚注的阅读器会把 `<aside>` 当普通章末注显示，所以不会更糟。

用脚本自动完成（识别注释、拆断拼接、正文标记替换、章末 `<aside>` 生成、命名空间注入、EPUB3 升级）：

```bash
python3 <skill-dir>/scripts/fix_notes.py book.epub -o book_fixed.epub   # .epub：解压→处理→升级 EPUB3→重打包
python3 <skill-dir>/scripts/fix_notes.py book_extracted --in-place      # 已解压目录原地处理
```

其余选项（`--marker-style` / `--marker-regex` / `--mode endnote` / `--note-title` / `--no-epub3` / `--merge-split-paragraphs` 等）见 `python3 <skill-dir>/scripts/fix_notes.py --help`。（--merge-split-paragraphs 慎用：容易把日记称呼等独立短行误并；--no-epub3 不推荐）

弹出脚注依赖阅读器认 `epub:type`，**EPUB 3 包最可靠**，所以脚本默认 `--epub3`：把 `version` 升到 `3.0`、补 `dcterms:modified`、从 NCX 生成 `nav.xhtml`（`properties="nav"`）、并给封面图加 `properties="cover-image"`。

## 第 4 步：重建新 EPUB

### 中文排版 CSS 要点

```css
body { font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "SimSun", serif;
       line-height: 1.9; text-align: justify; }
p { margin: 0; text-indent: 2em; }          /* 正文首行缩进两字 */
h1 { text-align: center; page-break-before: always; }  /* 章/部标题另起页居中 */
h2 { text-align: center; margin: 1.6em 0 0.8em; }      /* 节标题居中 */
p.epigraph { text-indent: 0; margin: 0 1.5em 0.4em; }   /* 题记不缩进 */
p.attribution { text-align: right; text-indent: 0; }     /* 题记署名右对齐 */
sup.note-ref { font-size: 0.72em; line-height: 0; }       /* 正文上标注释号 */
section.footnotes { margin-top: 2.4em; border-top: 1px solid #bbb; padding-top: 0.6em; }
h2.notes-title { text-align: left; font-size: 1em; font-weight: bold; margin: 0 0 0.5em; }
aside.footnote { font-size: 0.86em; line-height: 1.65; margin: 0.35em 0; color: #333; }
aside.footnote p, p.note { text-indent: 0; margin: 0; }     /* 注释不缩进 */
```

标题字号层级：部 > 章 > 节。正文段落一律 2em 缩进，题记、署名、日期、居中句、版权页等用 `.no-indent` 取消缩进（其余排版细节见 §10）。

目录与元数据：HTML 目录页做 部→章→节 三级锚点；`toc.ncx` 的 navMap 按 部→章→节 嵌套、playOrder 连续；元数据填 `dc:title` / `dc:creator` / `dc:language`（`zh-CN`）/ `dc:publisher`。

打包成合法 EPUB（硬性要求与自检见 §9）：做成 **EPUB 3**——`<package version="3.0">` + `<meta property="dcterms:modified">2025-01-01T00:00:00Z</meta>`，提供 `nav.xhtml`（`<nav epub:type="toc">`，manifest 标 `properties="nav"`），封面图 item 标 `properties="cover-image"`（`fix_notes.py --epub3` 可自动做）；`mimetype` 内容为 `application/epub+zip`，必须是 zip **第一个条目**且 **不压缩**（`ZIP_STORED`），否则阅读器不认；包内需有 `META-INF/container.xml` 指向 `content.opf`。

```python
zf = zipfile.ZipFile('out.epub', 'w', zipfile.ZIP_DEFLATED)
zf.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
# ... 再写入其余文件 ...
```

## 常见问题速查

诊断时逐项对照 `references/epub-issues.md`：§1 幽灵空格、§2 页眉页码、§3 首行缩进、§4 标题层级、§5 目录损坏、§6 元数据、§7 空段落、§8 结构识别、§9 合法 EPUB 硬性要求、§10 中文排版细节、§11 注释脚注。每项都有特征正则、示例与修复要点。
