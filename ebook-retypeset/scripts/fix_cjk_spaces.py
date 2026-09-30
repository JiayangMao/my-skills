#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复中文电子书中的幽灵空格(硬换行转成的汉字间空格)。

用法:
    python3 fix_cjk_spaces.py <目录或文件> -o <输出目录>
    python3 fix_cjk_spaces.py <目录或文件> --in-place   # 原地修改

只处理 .xhtml/.html/.txt 文件。规则: 删除两侧都是
"中日韩字符/全角标点"的空格(含普通空格、全角空格、不间断空格),
其余位置的空格(如汉字与数字/拉丁字母之间)保留。

为什么要跳过标题: 章节标题里的空格往往是有意的(如「第一章 测试」,
通常与目录 toc.ncx 的标签一致), 删掉会让标题和目录对不上。
所以默认不动 <title> / <h1>–<h6> 里的空格; 若确实要处理标题,
加 --include-headings。

另外, 处理只作用于文本节点, 不会误改标签属性(如 <img alt="三 年">)。
"""
import argparse
import glob
import os
import re

CJKISH = ('\u4e00-\u9fff\u3400-\u4dbf'
          '\u3000-\u303f'
          '\uff00-\uffef'
          '\u2010-\u2027'
          '\u00b7')

# 用零宽断言, 一次替换即可处理连续多个空格与重叠情况
GHOST = re.compile(r'(?<=[%s])[ \u3000\u00a0]+(?=[%s])' % (CJKISH, CJKISH))
MULTI_SPACE = re.compile(r'[ \u3000\u00a0]{2,}')

# 这些标签里的文本不做空格处理
SKIP_TAGS = frozenset(['title', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                       'script', 'style', 'pre'])
VOID_TAGS = frozenset(['br', 'hr', 'img', 'meta', 'link', 'input',
                       'area', 'base', 'col', 'embed', 'source', 'track', 'wbr'])

# 注释 / 标签 / 文本 三种 token
TOKEN = re.compile(r'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<[^>]*>|[^<]+', re.DOTALL)
TAG_NAME = re.compile(r'<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9:-]*)')


def fix_chunk(chunk):
    return MULTI_SPACE.sub(' ', GHOST.sub('', chunk))


def fix_markup(text, include_headings=False):
    """只处理非标题元素的文本节点, 返回 (新文本, 修复的幽灵空格数)。"""
    out = []
    stack = []
    removed = 0
    for m in TOKEN.finditer(text):
        tok = m.group(0)
        if tok.startswith('<'):
            out.append(tok)
            nm = TAG_NAME.match(tok)
            if not nm or tok.startswith('<!'):
                continue
            closing, name = nm.group(1), nm.group(2).lower()
            if closing:
                while stack:
                    if stack.pop() == name:
                        break
            elif not tok.rstrip().endswith('/>') and name not in VOID_TAGS:
                stack.append(name)
            continue
        # 文本节点
        skip = (not include_headings) and any(t in SKIP_TAGS for t in stack)
        if skip:
            out.append(tok)
        else:
            removed += len(GHOST.findall(tok))
            out.append(fix_chunk(tok))
    return ''.join(out), removed


def iter_targets(path):
    if os.path.isfile(path):
        yield path
        return
    for ext in ('*.xhtml', '*.html', '*.txt'):
        for f in sorted(glob.glob(os.path.join(path, '**', ext), recursive=True)):
            yield f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('path')
    ap.add_argument('-o', '--output-dir')
    ap.add_argument('--in-place', action='store_true')
    ap.add_argument('--include-headings', action='store_true',
                    help='也处理 <title>/<h1>-<h6> 里的空格(默认保留标题空格)')
    args = ap.parse_args()

    targets = list(iter_targets(args.path))
    if not targets:
        print('未找到任何 .xhtml/.html/.txt 文件')
        return 1

    total = 0
    for f in targets:
        with open(f, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
        fixed, n = fix_markup(text, args.include_headings)
        total += n

        if args.in_place:
            out = f
        elif args.output_dir:
            if os.path.isfile(args.path):
                out = os.path.join(args.output_dir, os.path.basename(f))
            else:
                rel = os.path.relpath(f, args.path)
                out = os.path.join(args.output_dir, rel)
        else:
            print('请指定 -o 输出目录或 --in-place')
            return 1

        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(fixed)
        if n:
            print('%s: 修复 %d 处' % (f, n))

    print('共修复幽灵空格 %d 处' % total)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
