#!/usr/bin/env python3
"""生成用于评测 ebook-rename 的最小电子书样本。

运行: python3 build_fixture.py
产物: fixtures/library/ 下若干电子书，**文件名故意很脏**
（带副标题、简介、z-library 广告、分卷、系列名），
但内嵌 metadata 是可解析的「干净书名 + 作者」。

样本同时覆盖 epub / mobi / azw3 三种格式与多种边界情况。
"""
import os
import struct
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "fixtures", "library")

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>%\u4e00</title></head>
<body><nav epub:type="toc"><ol><li><a href="nav.xhtml">\u76ee\u5f55</a></li></ol></nav></body>
</html>
"""


def build_epub(path: str, title: str, creator: str) -> None:
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:fixture-{abs(hash(title)) % 10**12}</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:creator>{creator}</dc:creator>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine><itemref idref="nav"/></spine>
</package>
"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", NAV)


def _exth(rtype: int, data: bytes) -> bytes:
    return struct.pack(">II", rtype, 8 + len(data)) + data


def build_mobi(path: str, title: str, creator: str) -> None:
    """最小可解析 MOBI/AZW3：PDB 头 + MOBI 头 + EXTH(100=作者, 503=标题)。"""
    name = title.encode("utf-8")

    mobi = bytearray(232)
    mobi[0:4] = b"MOBI"
    struct.pack_into(">I", mobi, 0x04, len(mobi))   # header length
    struct.pack_into(">I", mobi, 0x08, 2)           # mobi type = book
    struct.pack_into(">I", mobi, 0x0C, 65001)       # utf-8
    struct.pack_into(">I", mobi, 0x10, 1)           # unique id
    struct.pack_into(">I", mobi, 0x14, 6)           # file version
    struct.pack_into(">I", mobi, 0x80, 0x40)        # EXTH flags

    body = _exth(100, creator.encode("utf-8")) + _exth(503, name)
    exth = b"EXTH" + struct.pack(">I", 12 + len(body)) + struct.pack(">I", 2) + body

    palm = bytearray(16)
    struct.pack_into(">H", palm, 0, 1)              # no compression
    struct.pack_into(">I", palm, 4, len(name))      # text length
    struct.pack_into(">H", palm, 8, 1)              # record count
    struct.pack_into(">H", palm, 10, 4096)          # record size

    fn_off = 16 + len(mobi) + len(exth)
    struct.pack_into(">I", mobi, 0x44, fn_off)      # full name offset（相对 MOBI 头）
    struct.pack_into(">I", mobi, 0x48, len(name))   # full name length

    rec0 = bytes(palm) + bytes(mobi) + exth + name
    rec1 = b"\x00" * 16

    header = bytearray(78)
    header[0:9] = b"TestBook\x00"
    header[60:64] = b"BOOK"
    header[64:68] = b"MOBI"
    struct.pack_into(">H", header, 76, 2)           # 2 records

    rec0_off = 78 + 8 * 2
    rec1_off = rec0_off + len(rec0)
    table = struct.pack(">I", rec0_off) + b"\x00" * 4
    table += struct.pack(">I", rec1_off) + b"\x00" * 4

    with open(path, "wb") as f:
        f.write(bytes(header) + table + rec0 + rec1)


# 文件名（脏） → (格式, metadata 书名, metadata 作者)
FIXTURES = [
    ("叫魂：1768年中国妖术大恐慌.epub", "epub",
     "叫魂：1768年中国妖术大恐慌", "[美] 孔飞力"),
    ("我的前半生 ([清] 爱新觉罗 · 溥仪) (z-library.sk, 1lib.sk, z-lib.sk).epub", "epub",
     "我的前半生：全本【贝托鲁奇电影原著】", "爱新觉罗·溥仪"),
    ("1929年大崩盘 (华安基金世界资本经典译丛).epub", "epub",
     "1929年大崩盘 (华安基金世界资本经典译丛)", "约翰•肯尼斯•加尔布雷思"),
    ("安史之乱  历史、宣传与神话 (张诗坪, 胡可奇).epub", "epub",
     "安史之乱 : 历史、宣传与神话", "张诗坪 / 胡可奇"),
    ("不一样的中国史·全13册（附概念民国1册） (杨照).epub", "epub",
     "不一样的中国史·全13册（附概念民国1册）", "杨照"),
    ("FIREPUNCH炎拳 - 卷01.epub", "epub",
     "FIREPUNCH炎拳 - 卷01", "藤本樹,龍幸伸,瓜生愛美"),
    ("FIREPUNCH炎拳 - 卷02.epub", "epub",
     "FIREPUNCH炎拳 - 卷02", "藤本樹,龍幸伸,瓜生愛美"),
    ("红太阳是怎样升起的 (Hua Gao 高华).mobi", "mobi",
     "红太阳是怎样升起的：延安整风运动的来龙去脉", "高华"),
    ("图解SkillAI提效实战指南 (宝玉).azw3", "azw3",
     "图解Skill：AI提效实战指南", "宝玉"),
    ("恐怖分子的洋伞.epub", "epub",
     "恐怖分子的洋伞（曾经让你毫无保留的好友…）读客悬疑文库", "[日] 藤原伊织"),
    ("摄影的艺术 (Bruce Barnbaum).epub", "epub",
     "摄影的艺术(彩印)", "[美] Bruce Barnbaum"),
]


def main() -> None:
    os.makedirs(LIB, exist_ok=True)
    for fname, fmt, title, creator in FIXTURES:
        path = os.path.join(LIB, fname)
        if fmt == "epub":
            build_epub(path, title, creator)
        else:
            build_mobi(path, title, creator)
        print(f"  {fname}")
    print(f"已生成 {len(FIXTURES)} 个样本 → {LIB}")


if __name__ == "__main__":
    main()
