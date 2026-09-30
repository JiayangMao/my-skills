#!/usr/bin/env python3
"""按内嵌元数据把电子书重命名为「书名(作者).扩展名」。

元数据来源（不依赖 Calibre）：
  .epub          解析 content.opf 里的 dc:title / dc:creator
  .mobi/.azw3    解析 MOBI 头的 EXTH 记录（100=作者，503=标题）

默认只打印改名对照表（dry-run），加 --apply 才真正改名。
命名冲突、目标已存在、元数据里没有书名的文件都会被跳过并汇总报告。
PDF 等其它格式不处理：它们的元数据普遍不可靠（常是水印/乱码）。

用法：
  python3 rename_ebooks.py <目录>                      # 只看方案
  python3 rename_ebooks.py <目录> --apply              # 执行
  python3 rename_ebooks.py <目录> --strip-author-prefix # 去掉作者的 [美]/【韩】等前缀
  python3 rename_ebooks.py <目录> --overrides fixes.json
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import struct
import sys
import zipfile

BOOK_EXTS = (".epub", ".mobi", ".azw3", ".azw")

# ---------------------------------------------------------------- 元数据提取


def epub_meta(path: str) -> tuple[str | None, str | None]:
    """从 EPUB 的 OPF 里读 dc:title / dc:creator。"""
    try:
        with zipfile.ZipFile(path) as z:
            container = z.read("META-INF/container.xml").decode("utf-8", "ignore")
            m = re.search(r'full-path="([^"]+)"', container)
            opf = m.group(1) if m else next(
                (n for n in z.namelist() if n.endswith(".opf")), None
            )
            if not opf:
                return None, None
            data = z.read(opf).decode("utf-8", "ignore")
    except Exception:
        return None, None

    def grab(tag: str) -> str | None:
        for mm in re.finditer(
            r"<dc:%s[^>]*>(.*?)</dc:%s>" % (tag, tag), data, re.S | re.I
        ):
            return re.sub(r"<[^>]+>", "", mm.group(1)).strip()
        return None

    return grab("title"), grab("creator")


def mobi_meta(path: str) -> tuple[str | None, str | None]:
    """从 MOBI/AZW3 的 EXTH 记录里读标题与作者。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        n = struct.unpack(">H", data[76:78])[0]
        if n < 1:
            return None, None

        def rec_off(i: int) -> int:
            return struct.unpack(">I", data[78 + 8 * i: 82 + 8 * i])[0]

        r0 = data[rec_off(0): rec_off(1)]
    except Exception:
        return None, None

    title = author = None
    ex = r0.find(b"EXTH")
    if ex >= 0:
        count = struct.unpack(">I", r0[ex + 8: ex + 12])[0]
        pos = ex + 12
        for _ in range(count):
            rtype, rlen = struct.unpack(">II", r0[pos: pos + 8])
            val = r0[pos + 8: pos + rlen].decode("utf-8", "ignore")
            if rtype == 100:
                author = val
            elif rtype == 503:
                title = val
            pos += rlen
    if not title:
        mo = r0.find(b"MOBI")
        if mo >= 0:
            fn_len = struct.unpack(">I", r0[mo + 0x48: mo + 0x4c])[0]
            fn_off = struct.unpack(">I", r0[mo + 0x44: mo + 0x48])[0]
            title = r0[fn_off: fn_off + fn_len].decode("utf-8", "ignore")
    return title, author


def extract_meta(path: str) -> tuple[str | None, str | None]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        return epub_meta(path)
    if ext in (".mobi", ".azw3", ".azw"):
        return mobi_meta(path)
    return None, None


# ------------------------------------------------------------------ 文本清洗

# 副标题分隔符：在最早出现处截断（其后是副标题/简介）
_SUBTITLE_SEPS = ("：", ":", "——", "—", "–", " - ")
# 简介 / 丛书 / 版本说明的起始括号
_BRACKETS = ("(", "（", "【", "[")
# 站点广告 / 水印（常混进 metadata 标题里）
_AD_RE = re.compile(
    r"[\s\-–—]*(?:t\.me/|tg@|z-?lib(?:rary)?\.\w+|1lib\.\w+|annas-archive\.\w+|"
    r"libgen\.\w+|@[\w.]+\.(?:com|net|sk|org))\S*",
    re.I,
)
# 丛书卷册信息：不一样的中国史·全13册（……）→ 整段丢弃
_SERIES_RE = re.compile(
    r"[·・]\s*(?:全\s*\d+\s*[册卷]|第\s*\d+\s*[册卷部]|卷\s*\d+|\d+\s*[册卷部]).*$"
)
# 分卷标记：第2卷 / 卷02 / 卷二 / Vol.2
_VOL_RE = re.compile(
    r"第\s*\d+\s*[卷册部]|卷\s*\d+|卷[零一二三四五六七八九十百]+|[Vv]ol\.?\s*\d+"
)
# 作者国别/朝代前缀：[美] 【韩】 （英） [清]
_AUTHOR_PREFIX_RE = re.compile(r"^\s*[\[【（(〔]\s*[^\]】）)〕]{1,8}\s*[\]】）)〕]\s*")
# 非法文件名字符（全角标点如「：」保留，不替换）
_ILLEGAL_RE = re.compile(r'[\x00-\x1f/\\:*?"<>|]')


def _normalize_volume(vol: str) -> str:
    """卷 01 → 第01卷；第2卷 / Vol.2 原样保留。"""
    m = re.fullmatch(r"卷\s*(\d+)", vol)
    return f"第{m.group(1)}卷" if m else re.sub(r"\s+", "", vol)


def _split_volume(t: str) -> tuple[str, str | None]:
    m = _VOL_RE.search(t)
    if not m:
        return t, None
    return t[: m.start()], _normalize_volume(m.group(0))


def clean_title(raw: str | None) -> str | None:
    """书名：去副标题 / 简介 / 丛书 / 广告，保留分卷号。"""
    if not raw:
        return None
    t = re.sub(r"<[^>]+>", "", raw)
    t = t.replace("\u3000", " ").replace("\xa0", " ").strip()
    t = _AD_RE.sub("", t)
    name, vol = _split_volume(t)
    name = _SERIES_RE.sub("", name)
    cuts = [i for i in (name.find(s) for s in _SUBTITLE_SEPS) if i > 0]
    cuts += [i for i in (name.find(b) for b in _BRACKETS) if i > 0]
    if cuts:
        name = name[: min(cuts)]
    name = re.sub(r"\s+", " ", name).strip(" ·-—–")
    if not name:
        return None
    return f"{name} {vol}" if vol else name


def clean_author(raw: str | None, strip_prefix: bool = False) -> str | None:
    """作者：统一 •→·、多作者用逗号、去重；可选去国别前缀。"""
    if not raw:
        return None
    a = re.sub(r"<[^>]+>", "", raw).replace("\u3000", " ").strip()
    if strip_prefix:
        a = _AUTHOR_PREFIX_RE.sub("", a)
    a = a.replace("•", "·")
    a = re.sub(r"\s*·\s*", "·", a)
    a = re.sub(r"\s*[,/、;；]\s*", ", ", a)
    # 「陶涵 [陶涵]」→「陶涵」：结尾括号内容与前面的名字重复时去掉
    m = re.match(r"^(.*?)\s*[\[（(]\s*(.+?)\s*[\]）)]\s*$", a)
    if m and m.group(2) in m.group(1):
        a = m.group(1)
    a = re.sub(r"\s+", " ", a).strip(" ,·")
    return a or None


def sanitize(name: str) -> str:
    return _ILLEGAL_RE.sub("_", name).strip()


# --------------------------------------------------------------- 人工修正表

def load_overrides(path: str | None) -> list[dict]:
    """读 overrides JSON：按顺序匹配原始 metadata 书名，首个命中生效。

    每条形如 {"match": "<正则>", "title": "<模板>", "author": "<模板>"}。
    title/author 里可用 {raw_title}、{raw_author}、{volume} 占位。
    因为匹配的是 metadata 而不是文件名，重复运行仍然幂等。
    """
    if not path:
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("overrides 文件应为 JSON 数组")
    return data


def apply_overrides(
    rules: list[dict], raw_title: str | None, raw_author: str | None
) -> tuple[str | None, str | None]:
    rt = (raw_title or "").strip()
    _, vol = _split_volume(rt)
    for rule in rules:
        if re.search(rule.get("match", ""), rt):
            ctx = {"raw_title": rt, "raw_author": raw_author or "", "volume": vol or ""}
            title = rule.get("title")
            author = rule.get("author")
            return (
                title.format(**ctx) if title else None,
                author.format(**ctx) if author else None,
            )
    return None, None


# ---------------------------------------------------------------------- 主流程


def plan(directory: str, strip_prefix: bool, rules: list[dict]) -> tuple[list[dict], list[str]]:
    files = sorted(
        p for p in glob.glob(os.path.join(directory, "*"))
        if os.path.splitext(p)[1].lower() in BOOK_EXTS
    )
    rows: list[dict] = []
    skipped: list[str] = []

    for path in files:
        base = os.path.basename(path)
        stem, ext = os.path.splitext(base)
        raw_title, raw_author = extract_meta(path)

        ov_title, ov_author = apply_overrides(rules, raw_title, raw_author)
        title = ov_title or clean_title(raw_title)
        author = ov_author or clean_author(raw_author, strip_prefix)

        if not title:
            skipped.append(f"{base}  （元数据里没有书名）")
            continue

        # 保留 .bak 之类的中间后缀：要有光（梁鸿）.bak.epub → ….bak.epub
        mid = ""
        if stem.endswith(".bak"):
            mid = ".bak"

        new_name = (
            f"{sanitize(title)}({sanitize(author)}){mid}{ext}" if author
            else f"{sanitize(title)}{mid}{ext}"
        )
        rows.append({
            "old": base, "new": new_name, "path": path,
            "title": title, "author": author, "no_author": not author,
        })

    # 冲突检测：目标重名或已存在则跳过，绝不覆盖
    targets: dict[str, str] = {}
    for row in rows:
        err = None
        if row["new"] in targets:
            err = f"与「{targets[row['new']]}」目标同名"
        elif row["new"] != row["old"] and os.path.exists(
            os.path.join(directory, row["new"])
        ):
            err = "目标文件名已存在"
        if err:
            row["error"] = err
            skipped.append(f"{row['old']}  ({err})")
        else:
            targets[row["new"]] = row["old"]
    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser(
        description="按元数据把电子书重命名为 书名(作者).扩展名（默认只打印方案）"
    )
    ap.add_argument("directory", help="放电子书的目录")
    ap.add_argument("--apply", action="store_true", help="真正执行改名（默认 dry-run）")
    ap.add_argument("--strip-author-prefix", action="store_true",
                    help="去掉作者的国别/朝代前缀，如 [美]、【韩】、（英）")
    ap.add_argument("--overrides", metavar="FILE",
                    help="人工修正表 JSON（按 metadata 书名匹配，保证幂等）")
    args = ap.parse_args()

    if not os.path.isdir(args.directory):
        print(f"目录不存在：{args.directory}", file=sys.stderr)
        return 2
    try:
        rules = load_overrides(args.overrides)
    except Exception as e:
        print(f"overrides 读取失败：{e}", file=sys.stderr)
        return 2

    rows, skipped = plan(args.directory, args.strip_author_prefix, rules)
    todo = [r for r in rows if "error" not in r and r["new"] != r["old"]]

    print(f"目录：{args.directory}")
    print(f"{'原文件名':<46} -> 新文件名")
    print("-" * 108)
    for r in rows:
        print(f"{r['old']:<46} -> {r['new'] if r['new'] != r['old'] else '(不变)'}")
    print("-" * 108)
    unchanged = len(rows) - len(todo) - len(skipped)
    print(f"待改名 {len(todo)} 个，不变 {unchanged} 个，跳过 {len(skipped)} 个")
    if skipped:
        print("\n跳过：")
        for s in skipped:
            print(f"  - {s}")

    if not args.apply:
        print("\n[dry-run] 未做任何改动。人工确认后加 --apply 执行。")
        return 0

    done = 0
    for r in todo:
        os.rename(r["path"], os.path.join(args.directory, r["new"]))
        done += 1
    print(f"\n完成，共改名 {done} 个。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
