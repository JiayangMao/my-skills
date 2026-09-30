#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把中文电子书里被压平/散落的注释，转成 EPUB3 弹出式脚注（默认行为）。

用法:
    # 直接处理 .epub：解压 -> 处理 -> 升级 EPUB3 -> 重新打包
    python3 fix_notes.py book.epub -o book_fixed.epub

    # 对已解压目录原地处理
    python3 fix_notes.py book_extracted --in-place

它做四件事:
  1. 在每个 XHTML 里找出“注释段落”(以 ①/[1]/注1/（1）/* 之类标记开头的段落)；
  2. 把正文中对应的标记换成 <sup><a epub:type="noteref" href="#fnN" id="fnrefN">[N]</a></sup>;
  3. 在该文件末尾生成 <section epub:type="footnotes">，每条注释一个
     <aside epub:type="footnote" id="fnN">，并带回链;
  4. 给 <html> 补上 xmlns:epub 命名空间，并把包升级为 EPUB 3。

弹出式脚注依赖阅读器对 epub:type 的支持，EPUB3 包里最可靠，所以默认开
--epub3（可用 --no-epub3 关掉）。不支持弹出脚注的阅读器会把这些 <aside>
当作章末注正常显示，所以不会更糟。

标记样式: 默认 --marker-style auto，会在下列样式里自动挑一个“注释段落数与
正文标记数一致”的:
    circled   圈号 ① ② ③ …
    bracket   [1] / ［1］
    label     注1 / 注释1
    paren     （1） / (1)
    asterisk  * / ** / ***
也可以用 --marker-style 指定，或 --marker-regex 给一个自定义正则。

关于“注释被拆断”: 若某条注释不以句末标点结尾，且下一段不是另一条注释，则认为
被换页/页眉拆断，自动拼接（例如“①脱敏治疗…降低个体焦虑”+“反应的行为疗法。”）。
"""
import argparse
import glob
import html as htmlmod
import os
import re
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET

CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
# 段落结束符：出现这些才认为段落/注释完整（不继续拼接）
TERMINAL = set('。！？…?!' + '\u201d' + '\u2019' + '」』）)】〕〉》' + '：；—～')
EPUB_NS = 'http://www.idpf.org/2007/ops'

BLOCK = re.compile(r'<(h1|h2|p)\b([^>]*)>(.*?)</\1>', re.DOTALL)

# 标记样式表。每个样式给出三个正则：
#   note    —— 锚定段首，判断某段是不是注释
#   inline  —— 在正文里匹配引用标记
#   strip   —— 去掉注释段开头的标记
MARKER_STYLES = {
    'circled': {
        'desc': '圈号 ① ② ③ …',
        'note': re.compile('^[%s]' % CIRCLED),
        'inline': re.compile('[%s]' % CIRCLED),
        'strip': re.compile('^[%s]\\s*' % CIRCLED),
    },
    'bracket': {
        'desc': '[1] / ［1］',
        'note': re.compile(r'^[\[［]\s*\d+\s*[\]］]'),
        'inline': re.compile(r'[\[［]\s*\d+\s*[\]］]'),
        'strip': re.compile(r'^[\[［]\s*\d+\s*[\]］]\s*'),
    },
    'label': {
        'desc': '注1 / 注释1',
        'note': re.compile(r'^注\s*(?:释)?\s*\d+'),
        'inline': re.compile(r'注\s*(?:释)?\s*\d+'),
        'strip': re.compile(r'^注\s*(?:释)?\s*\d+\s*[：:、.．]?\s*'),
    },
    'paren': {
        'desc': '（1） / (1)',
        'note': re.compile(r'^[（(]\s*\d+\s*[）)]'),
        'inline': re.compile(r'[（(]\s*\d+\s*[）)]'),
        'strip': re.compile(r'^[（(]\s*\d+\s*[）)]\s*'),
    },
    'asterisk': {
        'desc': '* / ** / ***',
        'note': re.compile(r'^\*{1,3}'),
        'inline': re.compile(r'\*{1,3}'),
        'strip': re.compile(r'^\*{1,3}\s*'),
    },
}
AUTO_ORDER = ['circled', 'bracket', 'label', 'paren', 'asterisk']


def strip_tags(s):
    return re.sub(r'<[^>]+>', '', s)


def get_class(attrs):
    m = re.search(r'class="([^"]*)"', attrs)
    return m.group(1) if m else ''


def note_complete(t):
    """注释是否完整（以句末标点或‘编者注’收尾）。"""
    return (not t) or t[-1] in TERMINAL or t.endswith('编者注')


def xml_text(t):
    """转义 XML 特殊字符，同时避免把已有实体二次转义。"""
    t = re.sub(r'&(?!#\d+;|[A-Za-z][A-Za-z0-9]*;)', '&amp;', t)
    return t.replace('<', '&lt;').replace('>', '&gt;')


def iter_blocks(body):
    """把正文切成 (tag, attrs, inner)；非 h1/h2/p 的部分作为 raw 原样保留。"""
    pos = 0
    for m in BLOCK.finditer(body):
        if m.start() > pos:
            yield ('raw', '', body[pos:m.start()])
        yield (m.group(1), m.group(2), m.group(3))
        pos = m.end()
    if pos < len(body):
        yield ('raw', '', body[pos:])


def body_paragraphs(html):
    """产出正文里所有 <p> 的纯文本，用于统计标记。"""
    mb = re.search(r'<body\b[^>]*>(.*)</body>', html, re.DOTALL)
    if not mb:
        return
    for m in re.finditer(r'<p[^>]*>(.*?)</p>', mb.group(1), re.DOTALL):
        yield strip_tags(m.group(1)).strip()


def count_style(files, style):
    """统计某标记样式下：注释段落数、正文引用标记数。"""
    notes = marks = 0
    for f in files:
        html = open(f, encoding='utf-8', errors='replace').read()
        for t in body_paragraphs(html):
            if not t:
                continue
            if style['note'].match(t):
                notes += 1
            else:
                marks += len(style['inline'].findall(t))
    return notes, marks


def pick_style(files):
    """自动挑最合适的标记样式：优先“注释数>0 且 注释数==标记数”，否则取注释数最多者。"""
    fallback = None
    for name in AUTO_ORDER:
        st = MARKER_STYLES[name]
        n, m = count_style(files, st)
        if n and n == m:
            return name, n, m
        if n and (fallback is None or n > fallback[2]):
            fallback = (name, n, m)
    return fallback if fallback else ('circled', 0, 0)


def process_document(html, style, mode='popup', note_title='注释',
                   merge_split_paragraphs=False):
    """把一份 XHTML 里的注释改成弹出式脚注，返回 (新html, 注释数, 标记数)。"""
    if 'xmlns:epub' not in html:
        html = re.sub(r'(<html\b)', r'\1 xmlns:epub="%s"' % EPUB_NS, html, count=1)

    mb = re.search(r'(<body\b[^>]*>)(.*)(</body>)', html, re.DOTALL)
    if not mb:
        return html, 0, 0
    pre, body, post = html[:mb.start(2)], mb.group(2), html[mb.end(2):]

    note_re, inline_re, strip_re = style['note'], style['inline'], style['strip']
    tokens = list(iter_blocks(body))

    # --- 摘出注释（含被拆断的注释拼接） ---
    out = []
    notes = []
    i = 0
    while i < len(tokens):
        tag, attrs, inner = tokens[i]
        if tag == 'p' and get_class(attrs) == '':
            text = strip_tags(inner).strip()
            if text and note_re.match(text):
                while i < len(tokens):
                    t2, a2, in2 = tokens[i]
                    if not (t2 == 'p' and get_class(a2) == ''):
                        break
                    tx = strip_tags(in2).strip()
                    if not (tx and note_re.match(tx)):
                        break
                    ntext = strip_re.sub('', tx, count=1).strip()
                    i += 1
                    # 注释若被换页拆断，与后续非注释段落拼接
                    while not note_complete(ntext) and i < len(tokens):
                        t3, a3, in3 = tokens[i]
                        tx3 = strip_tags(in3).strip()
                        if t3 == 'p' and get_class(a3) == '' and tx3 and note_re.match(tx3):
                            break
                        ntext += tx3
                        i += 1
                    notes.append(ntext)
                out.append(('seam', '', ''))
                continue
        out.append(tokens[i])
        i += 1

    # --- 合并被换页/注释拆断的正文段落 ---
    # 注释被摘走后，两段之间往往还隔着空白 raw token(以及一个 seam 标记)，
    # 所以要向前跳过它们去找真正的上一段，合并时再把这些都丢掉。
    # 默认只合并「注释所在处的接缝」(seam)；要把整本书里所有被分页拆断的
    # 段落都合并，需显式打开 merge_split_paragraphs —— 否则很容易把一个
    # 本就没有句末标点的独立行(如日记称呼「雅雅，」)误并进下一段。
    merged = []
    for tok in out:
        tag, attrs, inner = tok
        if tag == 'seam':
            merged.append(tok)          # 仅作回看标记，渲染时丢弃
            continue
        if tag == 'p' and get_class(attrs) == '':
            text = strip_tags(inner).strip()
            j = len(merged) - 1
            crossed_seam = False
            while j >= 0 and merged[j][0] in ('raw', 'seam') \
                    and (merged[j][0] == 'seam' or merged[j][2].strip() == ''):
                if merged[j][0] == 'seam':
                    crossed_seam = True
                j -= 1
            if j >= 0 and (crossed_seam or merge_split_paragraphs):
                pt, pa, pi = merged[j]
                if pt == 'p' and get_class(pa) == '':
                    ptext = strip_tags(pi).strip()
                    if ptext and ptext[-1] not in TERMINAL:
                        merged[j] = (pt, pa, ptext + text)
                        del merged[j + 1:]
                        continue
            merged.append(tok)
        else:
            merged.append(tok)

    # --- 正文标记 -> 引用链接 ---
    counter = [0]

    def make_ref(_mo):
        counter[0] += 1
        n = counter[0]
        if mode == 'popup':
            return ('<sup class="note-ref"><a epub:type="noteref" '
                    'href="#fn%d" id="fnref%d" role="doc-noteref">[%d]</a></sup>'
                    % (n, n, n))
        return ('<sup class="note-ref"><a href="#fn%d" id="fnref%d">[%d]</a></sup>'
                % (n, n, n))

    parts = []
    for tag, attrs, inner in merged:
        if tag == 'seam':
            continue
        if tag == 'raw':
            parts.append(inner)
        elif tag == 'p' and get_class(attrs) == '':
            text = inline_re.sub(make_ref, strip_tags(inner).strip())
            parts.append('<p>%s</p>' % text)
        else:
            parts.append('<%s%s>%s</%s>' % (tag, attrs, inner, tag))

    # --- 生成注释区 ---
    if notes:
        if mode == 'popup':
            parts.append('<section class="footnotes" epub:type="footnotes" '
                         'role="doc-endnotes">')
            parts.append('<h2 class="notes-title">%s</h2>' % xml_text(note_title))
            for n, nt in enumerate(notes, 1):
                parts.append(
                    '<aside class="footnote" epub:type="footnote" id="fn%d" '
                    'role="doc-footnote"><p><a class="note-num" href="#fnref%d" '
                    'role="doc-backlink">[%d]</a>%s</p></aside>'
                    % (n, n, n, xml_text(nt)))
            parts.append('</section>')
        else:
            parts.append('<div class="notes">')
            parts.append('<h2 class="notes-title">%s</h2>' % xml_text(note_title))
            for n, nt in enumerate(notes, 1):
                parts.append('<p class="note" id="fn%d">'
                             '<a class="note-num" href="#fnref%d">[%d]</a>%s</p>'
                             % (n, n, n, xml_text(nt)))
            parts.append('</div>')

    return pre + ''.join(parts) + post, len(notes), counter[0]


# ---------------- EPUB 3 升级 ----------------
def _nav_items(parent, ns):
    items = []
    for np in parent.findall('n:navPoint', ns):
        lbl = np.find('n:navLabel/n:text', ns)
        src = np.find('n:content', ns)
        items.append((lbl.text if lbl is not None else '',
                      src.get('src') if src is not None else '',
                      _nav_items(np, ns)))
    return items


def _render_ol(items):
    if not items:
        return ''
    out = ['<ol>']
    for label, href, children in items:
        out.append('<li><a href="%s">%s</a>%s</li>'
                   % (htmlmod.escape(href, quote=True), xml_text(label),
                      _render_ol(children)))
    out.append('</ol>')
    return ''.join(out)


def build_nav(ncx_path, title='目录'):
    ns = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
    root = ET.parse(ncx_path).getroot()
    navmap = root.find('n:navMap', ns)
    ol = _render_ol(_nav_items(navmap, ns)) if navmap is not None else '<ol></ol>'
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="%s">\n'
            '<head><title>%s</title>'
            '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/></head>\n'
            '<body>\n<nav epub:type="toc" id="toc"><h1>%s</h1>%s</nav>\n'
            '</body>\n</html>\n' % (EPUB_NS, xml_text(title), xml_text(title), ol))


def upgrade_to_epub3(base_dir):
    """把 OPF 升到 EPUB 3：version、dcterms:modified、cover-image、nav 文档。"""
    opfs = glob.glob(os.path.join(base_dir, '**', '*.opf'), recursive=True)
    if not opfs:
        print('  (未找到 OPF，跳过 EPUB3 升级)')
        return
    opf_path = opfs[0]
    opf_dir = os.path.dirname(opf_path)
    opf = open(opf_path, encoding='utf-8').read()

    opf = re.sub(r'(<package\b[^>]*\bversion=")[^"]*"', r'\g<1>3.0"', opf)
    if 'dcterms:modified' not in opf:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        opf = opf.replace('</metadata>',
                          '    <meta property="dcterms:modified">%s</meta>\n  </metadata>'
                          % now, 1)

    # 封面图标记为 cover-image
    opf = re.sub(
        r'(<item\b[^>]*\bid="cover"[^>]*media-type="image/[^"]*"[^>]*?)(\s*/?>)',
        lambda m: m.group(1)
        + ('' if 'properties=' in m.group(1) else ' properties="cover-image"')
        + m.group(2),
        opf)

    # nav 文档：从 NCX 生成
    ncx = glob.glob(os.path.join(base_dir, '**', '*.ncx'), recursive=True)
    if ncx and 'properties="nav"' not in opf:
        nav_name = 'nav.xhtml'
        with open(os.path.join(opf_dir, nav_name), 'w', encoding='utf-8') as f:
            f.write(build_nav(ncx[0]))
        opf = opf.replace(
            '</manifest>',
            '    <item id="nav" href="%s" media-type="application/xhtml+xml" '
            'properties="nav"/>\n  </manifest>' % nav_name, 1)

    with open(opf_path, 'w', encoding='utf-8') as f:
        f.write(opf)
    print('  已升级为 EPUB 3')


# ---------------- 输入 / 输出 ----------------
def html_files(base):
    files = []
    for ext in ('*.xhtml', '*.html'):
        files += glob.glob(os.path.join(base, '**', ext), recursive=True)
    return sorted(set(files))


def pack_epub(src_dir, out_path):
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.exists(out_path):
        os.remove(out_path)
    zf = zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED)
    zf.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, src_dir)
            if rel == 'mimetype':
                continue
            zf.write(full, rel)
    zf.close()


def main():
    ap = argparse.ArgumentParser(description='把电子书注释转成 EPUB3 弹出式脚注')
    ap.add_argument('path', help='已解压目录或 .epub 文件')
    ap.add_argument('-o', '--output', help='输出目录或 .epub 路径')
    ap.add_argument('--in-place', action='store_true', help='原地修改解压目录')
    ap.add_argument('--marker-style', default='auto',
                    choices=['auto'] + AUTO_ORDER,
                    help='注释标记样式（默认 auto 自动识别）')
    ap.add_argument('--marker-regex',
                    help='自定义标记正则（匹配单个标记，覆盖 --marker-style）')
    ap.add_argument('--mode', choices=['popup', 'endnote'], default='popup',
                    help='popup=弹出式脚注(默认)；endnote=普通章末注')
    ap.add_argument('--note-title', default='注释', help='注释区标题')
    ap.add_argument('--merge-split-paragraphs', action='store_true',
                    help='同时合并全书被分页拆断的正文段落(默认只合并注释所在的接缝)')
    ap.add_argument('--epub3', action=argparse.BooleanOptionalAction, default=True,
                    help='同时把包升级为 EPUB 3（默认开；用 --no-epub3 关闭）')
    args = ap.parse_args()

    src_epub = args.path.lower().endswith('.epub')
    tmp = None
    if src_epub:
        tmp = tempfile.mkdtemp()
        with zipfile.ZipFile(args.path) as zf:
            zf.extractall(tmp)
        base = tmp
    else:
        base = args.path

    # 决定工作目录
    if args.in_place:
        work = base
    elif args.output:
        out_is_epub = args.output.lower().endswith('.epub')
        if src_epub:
            work = base
        else:
            if out_is_epub:
                print('输入是目录时，--output 应为目录')
                return 1
            if os.path.exists(args.output):
                shutil.rmtree(args.output)
            shutil.copytree(base, args.output)
            work = args.output
    else:
        print('请指定 -o 输出路径，或 --in-place')
        return 1

    files = html_files(work)

    # 选标记样式
    if args.marker_regex:
        rx = re.compile(args.marker_regex)
        style = {'note': re.compile('^(?:%s)' % args.marker_regex),
                 'inline': rx,
                 'strip': re.compile('^(?:%s)\\s*' % args.marker_regex)}
        style_name = 'custom:%s' % args.marker_regex
    elif args.marker_style == 'auto':
        style_name, n0, m0 = pick_style(files)
        style = MARKER_STYLES[style_name]
        print('自动识别标记样式: %s（%s），注释 %d / 标记 %d'
              % (style_name, style['desc'], n0, m0))
    else:
        style_name = args.marker_style
        style = MARKER_STYLES[style_name]

    total_notes = total_marks = 0
    for f in files:
        html = open(f, encoding='utf-8', errors='replace').read()
        new, nn, nm = process_document(html, style, args.mode, args.note_title,
                                       args.merge_split_paragraphs)
        if nn or nm:
            with open(f, 'w', encoding='utf-8') as fh:
                fh.write(new)
            rel = os.path.relpath(f, work)
            flag = '' if nn == nm else '  <== 注释数(%d)与标记数(%d)不一致！' % (nn, nm)
            print('%s: 注释 %d, 标记 %d%s' % (rel, nn, nm, flag))
        total_notes += nn
        total_marks += nm

    if args.epub3:
        upgrade_to_epub3(work)

    if src_epub and args.output:
        pack_epub(work, args.output)
        print('已生成:', args.output)
    elif src_epub and not args.output:
        print('处理的是 .epub，但没有给 -o，未打包回 .epub')
        return 1

    print('合计: 注释 %d, 标记 %d' % (total_notes, total_marks))
    if total_notes != total_marks:
        print('警告: 注释数与标记数不一致，请人工核对。')
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
