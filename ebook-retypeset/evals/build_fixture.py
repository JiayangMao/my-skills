#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成用于评测的最小 EPUB 测试书。

运行: python3 build_fixture.py
产物:
  fixtures/notes-flat.epub   注释被压平在正文里（含 1 条被拆断的注释）
  fixtures/cjk-spaces.epub   汉字之间被塞进空格（幽灵空格），含拉丁词应保留空格

每本书同时输出同名解压目录，便于直接查看。
"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures')

HEAD = ('<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n<head>\n'
        '<title>%s</title>\n'
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>\n'
        '<link rel="stylesheet" type="text/css" href="stylesheet.css"/>\n'
        '</head>\n<body>\n%s</body>\n</html>\n')

CSS = '''@charset "utf-8";
body { line-height: 1.9; }
p { text-indent: 2em; margin: 0; }
h1 { text-align: center; }
'''

CONTAINER = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
'''


def opf(title, uid, chapter_ids):
    items = '\n'.join('    <item id="%s" href="%s.xhtml" media-type="application/xhtml+xml"/>'
                      % (cid, cid) for cid in chapter_ids)
    spine = '\n'.join('    <itemref idref="%s"/>' % cid for cid in chapter_ids)
    return '''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>%s</dc:title>
    <dc:creator opf:role="aut">测试作者</dc:creator>
    <dc:language>zh-CN</dc:language>
    <dc:identifier id="BookId" opf:scheme="UUID">%s</dc:identifier>
  </metadata>
  <manifest>
%s
    <item id="css" href="stylesheet.css" media-type="text/css"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
%s
  </spine>
</package>
''' % (title, uid, items, spine)


def ncx(title, uid, chapters):
    pts = '\n'.join(
        '    <navPoint id="n%d" playOrder="%d"><navLabel><text>%s</text></navLabel>'
        '<content src="%s.xhtml"/></navPoint>' % (i, i, label, cid)
        for i, (cid, label) in enumerate(chapters, 1))
    return '''<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN">
  <head><meta name="dtb:uid" content="%s"/></head>
  <docTitle><text>%s</text></docTitle>
  <navMap>
%s
  </navMap>
</ncx>
''' % (uid, title, pts)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def make_epub(name, title, uid, chapters, files):
    out_dir = os.path.join(FIX, name)
    for rel, text in files.items():
        write(os.path.join(out_dir, rel), text)
    epub = os.path.join(FIX, name + '.epub')
    if os.path.exists(epub):
        os.remove(epub)
    zf = zipfile.ZipFile(epub, 'w', zipfile.ZIP_DEFLATED)
    zf.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
    for root, _dirs, fs in os.walk(out_dir):
        for fn in fs:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, out_dir)
            if rel == 'mimetype':
                continue
            zf.write(full, rel)
    zf.close()
    print('已生成:', epub)


# ---------------- notes-flat ----------------
NOTES_CH01 = HEAD % ('第一章 测试', '''<h1>第一章 测试</h1>
<p>医生做了量表，测出来我是中度抑郁①和中度焦虑②,给我开了药。</p>
<p>她经常请假，有时候领导找不到她还会</p>
<p>①抑郁症，也被称为抑郁障碍，主要特征是显著而持久的情绪低落。</p>
<p>②焦虑症是一种常见的心理障碍，表现为过度担忧和恐惧。</p>
<p>批评她。她会把这些告诉我，说因为你，我的工作快没了。</p>
<p>小正妈，你还是应该再去医院看看，孩子是否有多动障碍①。</p>
<p>①多动障碍、多动症，即注意缺陷多动障碍 (ADHD)。</p>
<p>阿叔说，下一步首先进行的是脱敏治疗①。</p>
<p>①脱敏治疗：通过渐进暴露于恐惧刺激中，降低个体焦虑</p>
<p>反应的行为疗法。</p>
''')

NOTES_CH02 = HEAD % ('第二章 测试', '''<h1>第二章 测试</h1>
<p>这一章没有任何注释，用来确认脚本不会在无注释的文档里乱动。</p>
<p>正文照样应该保持首行缩进。</p>
''')

# ---------------- cjk-spaces ----------------
CJK_CH01 = HEAD % ('第一章 测试', '''<h1>第一章 测试</h1>
<p>敏敏休学已经三 年了。她不想梳理这三年所发生的事情，同 时，又觉得这样做意 义非凡。</p>
<p>她戴着 OK 镜，曾经住过 ICU-21 床，这些拉丁词与汉字之间的空格要保留。</p>
<p>她承受不了再次回想父母暴力给她带来的巨大痛苦，同 时，又觉得只有当她开始正视这些事情，她才真正可以摆脱它们。</p>
''')

CJK_CH02 = HEAD % ('第二章 测试', '''<h1>第二章 测试</h1>
<p>这一章没有空格问题。</p>
''')


def main():
    # notes-flat
    make_epub(
        'notes-flat', '排版测试书', 'test-notes-fixture-001',
        [('ch01', '第一章 测试'), ('ch02', '第二章 测试')],
        {
            'mimetype': 'application/epub+zip',
            'META-INF/container.xml': CONTAINER,
            'OEBPS/ch01.xhtml': NOTES_CH01,
            'OEBPS/ch02.xhtml': NOTES_CH02,
            'OEBPS/stylesheet.css': CSS,
            'OEBPS/content.opf': opf('排版测试书', 'test-notes-fixture-001', ['ch01', 'ch02']),
            'OEBPS/toc.ncx': ncx('排版测试书', 'test-notes-fixture-001',
                                 [('ch01', '第一章 测试'), ('ch02', '第二章 测试')]),
        })

    # cjk-spaces
    make_epub(
        'cjk-spaces', '空格测试书', 'test-cjk-fixture-001',
        [('ch01', '第一章 测试'), ('ch02', '第二章 测试')],
        {
            'mimetype': 'application/epub+zip',
            'META-INF/container.xml': CONTAINER,
            'OEBPS/ch01.xhtml': CJK_CH01,
            'OEBPS/ch02.xhtml': CJK_CH02,
            'OEBPS/stylesheet.css': CSS,
            'OEBPS/content.opf': opf('空格测试书', 'test-cjk-fixture-001', ['ch01', 'ch02']),
            'OEBPS/toc.ncx': ncx('空格测试书', 'test-cjk-fixture-001',
                                 [('ch01', '第一章 测试'), ('ch02', '第二章 测试')]),
        })


if __name__ == '__main__':
    main()
