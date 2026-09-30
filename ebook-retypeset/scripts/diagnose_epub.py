#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断中文电子书的常见排版问题。

用法:
    python3 diagnose_epub.py <目录或 .epub 文件>

对已解压的 EPUB 目录或 .epub 文件本身做检查，输出:
  - 幽灵空格(汉字/全角标点之间的多余空格)数量与样例
  - 疑似页眉/页码段落
  - 注释段落与正文内注释标记的数量
  - CSS 首行缩进情况
  - 空段做间距的情况
  - 标题层级(h1/h2)使用情况
  - 目录(ncx/nav)与元数据质量
"""
import argparse
import glob
import os
import re
import shutil
import sys
import tempfile
import zipfile

# 页眉常见模式: "第一章 011" / "第二章041" / "Chapter 3 12" / 纯页码
RH_PATTERNS = [
    re.compile(r'^第[一二三四五六七八九十百零〇]+[章节部卷]\s*\d{1,4}$'),
    re.compile(r'^第[一二三四五六七八九十百零〇]+[章节部卷]\s*$'),
    re.compile(r'^(Chapter|CHAPTER)\s*\d+\s*$'),
    re.compile(r'^\d{1,4}$'),
]


def strip_tags(s):
    return re.sub(r'<[^>]+>', '', s)


def para_texts(content):
    """提取正文段落文本(保留空段)。"""
    out = []
    for m in re.finditer(r'<p[^>]*>(.*?)</p>', content, re.DOTALL):
        out.append(strip_tags(m.group(1)).strip())
    return out


def analyze(texts):
    result = {}
    # 1. 幽灵空格
    joined = '\n'.join(texts)
    ghost = re.findall(r'[\u4e00-\u9fff] [\u4e00-\u9fff]', joined)
    result['ghost_spaces'] = len(ghost)
    result['ghost_samples'] = []
    for m in re.finditer(r'[\u4e00-\u9fff] [\u4e00-\u9fff]', joined):
        s = m.start()
        result['ghost_samples'].append(joined[max(0, s - 8):s + 10])
        if len(result['ghost_samples']) >= 5:
            break
    # 2. 页眉候选
    rh = []
    for i, t in enumerate(texts):
        if not t:
            continue
        for pat in RH_PATTERNS:
            if pat.match(t):
                prev = texts[i - 1] if i > 0 else ''
                nxt = texts[i + 1] if i + 1 < len(texts) else ''
                rh.append((i, t, prev == '', nxt == ''))
                break
    result['running_heads'] = rh
    # 3. 空段
    result['empty_paras'] = sum(1 for t in texts if t == '')
    # 4. 注释
    CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
    note_paras = [t for t in texts if t and t[0] in CIRCLED]
    inline = sum(len(re.findall(r'[%s]' % CIRCLED, t)) for t in texts
                 if t and t[0] not in CIRCLED)
    result['note_paras'] = len(note_paras)
    result['inline_marks'] = inline
    result['note_samples'] = note_paras[:3]
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    args = ap.parse_args()

    tmp = None
    if args.path.lower().endswith('.epub'):
        tmp = tempfile.mkdtemp()
        with zipfile.ZipFile(args.path) as zf:
            zf.extractall(tmp)
        base = tmp
    else:
        base = args.path

    html_files = []
    for ext in ('*.xhtml', '*.html'):
        html_files += glob.glob(os.path.join(base, '**', ext), recursive=True)
    html_files = sorted(set(html_files))

    print('HTML/XHTML 文件数:', len(html_files))
    all_texts = []
    for f in html_files:
        with open(f, encoding='utf-8', errors='replace') as fh:
            all_texts += para_texts(fh.read())

    r = analyze(all_texts)

    print('\n===== 诊断结果 =====')
    print('段落总数(含空段):', len(all_texts))
    print('空段数量(疑似用空段撑间距):', r['empty_paras'])

    print('\n[1] 幽灵空格(汉字之间多余空格):', r['ghost_spaces'], '处')
    for s in r['ghost_samples']:
        print('    例:', s)

    print('\n[2] 疑似页眉/页码段落:', len(r['running_heads']), '处')
    for i, t, prev_empty, next_empty in r['running_heads'][:20]:
        print('    段落#%d %r  (前空=%s 后空=%s)' % (i, t, prev_empty, next_empty))
    if len(r['running_heads']) > 20:
        print('    ... 共 %d 处' % len(r['running_heads']))

    # 2b. 注释
    print('\n[2b] 注释: 注释段落 %d 段, 正文内标记 %d 个'
          % (r['note_paras'], r['inline_marks']))
    for s in r['note_samples']:
        print('    例:', s[:40])
    popup = 0
    for f in html_files:
        with open(f, encoding='utf-8', errors='replace') as fh:
            popup += len(re.findall(r'epub:type="footnote"', fh.read()))
    if popup:
        print('    已是弹出式脚注:', popup, '条')
    elif r['note_paras'] or r['inline_marks']:
        if r['note_paras'] != r['inline_marks']:
            print('    提示: 注释段落数与正文标记数不一致, 可能有注释被拆断或丢失')
        print('    提示: 可用 scripts/fix_notes.py 转成 EPUB3 弹出式脚注')

    # 4. CSS 缩进
    css_files = glob.glob(os.path.join(base, '**', '*.css'), recursive=True)
    print('\n[3] CSS 文件:', len(css_files), '个')
    indent_ok = False
    for f in css_files:
        with open(f, encoding='utf-8', errors='replace') as fh:
            css = fh.read()
        for m in re.finditer(r'text-indent\s*:\s*([^;]+);', css):
            print('    %s: text-indent: %s' % (os.path.basename(f), m.group(1).strip()))
            if '2em' in m.group(1):
                indent_ok = True
    if not indent_ok:
        print('    提示: 未发现 2em 首行缩进, 正文可能没有正确缩进')

    # 5. 标题层级
    with open(html_files[0], encoding='utf-8', errors='replace') as fh:
        sample = fh.read()
    h1 = len(re.findall(r'<h1[ >]', sample))
    h2 = len(re.findall(r'<h2[ >]', sample))
    print('\n[4] 标题层级(首个文件): h1=%d h2=%d' % (h1, h2))
    if h1 == 0 and h2 == 0:
        print('    提示: 未使用标题标签, 章/节标题很可能是普通 <p>')

    # 6. 目录与元数据
    opf = glob.glob(os.path.join(base, '**', '*.opf'), recursive=True)
    ncx = glob.glob(os.path.join(base, '**', '*.ncx'), recursive=True)
    print('\n[5] OPF 文件:', len(opf), '个, NCX 文件:', len(ncx), '个')
    if ncx:
        with open(ncx[0], encoding='utf-8', errors='replace') as fh:
            ncx_c = fh.read()
        np = len(re.findall(r'<navPoint ', ncx_c))
        print('    navPoint 数量:', np, '(若为 1 且叫 "Start", 目录基本是坏的)')
    if opf:
        with open(opf[0], encoding='utf-8', errors='replace') as fh:
            opf_c = fh.read()
        lang = re.search(r'<dc:language>([^<]+)</dc:language>', opf_c)
        title = re.search(r'<dc:title>([^<]+)</dc:title>', opf_c)
        print('    dc:language:', lang.group(1) if lang else '(未找到)')
        print('    dc:title:', title.group(1) if title else '(未找到)')
        if lang and lang.group(1) != 'zh' and lang.group(1) != 'zh-CN':
            print('    提示: 中文书语言应设为 zh-CN')

    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n诊断完成。对照 references/epub-issues.md 确认后决定修复方案。')


if __name__ == '__main__':
    main()
