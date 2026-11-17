#!/usr/bin/env python3
"""把你自己的《大航海时代》繁体中文 DOS 版改成简体中文。

用法（macOS）：
    python3 -I patcher/apply_patch.py --game "/path/to/游戏文件"

需要：Python 3、Xcode 命令行工具（swiftc，用来渲染简体字形）。
只支持校验值与下方 ORIGINAL_* 一致的那一版文件；对不上会停下，不会改任何东西。
原文件会先备份到 <游戏目录>/_backup_original/。
"""
import argparse, hashlib, json, os, shutil, struct, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import koei_text as kt  # noqa: E402

ORIGINAL_MAIN = "22407c9eb7e6e1ff6f8e1d27d4f1ba0d349a07645ad65bef963ddc708a626ea5"
ORIGINAL_ALLHAN = "831542db7083d2ce0e05b430ff7eca473cdf56d0c3e23ed0e9880d8b942394aa"
UNPACKED_MAIN = "41892a8133888452b71f273f6f6bc6974d6d5a177d53e54e85b289b1896f8629"
TEXT_PATCHED_MAIN = "3680cc74fbe0ae72c5e83de5595b790ac52a896b2ab66467804d08c1882d5fa5"
# Results produced with the default font on macOS 2026; another font or OS
# version renders slightly different pixels, which is fine.
REFERENCE_ALLHAN = "2d8c29177355bd01677cc08a36a24375efa33d985b93bc77e519c54c26ab296e"
REFERENCE_MAIN = "82f1e2efe03adb7d9fc1a2c0340088335fb37ed332bfce51b19bafbc2350d47f"

# Taiwan wording -> mainland wording. Same length, so strings never move.
WORD_RULES = [("甚麼", "什麼"), ("滑鼠", "鼠標"), ("左鈕", "左鍵"), ("右鈕", "右鍵"),
              ("磁碟機", "磁盤機"), ("磁碟", "磁盤"), ("磁片", "磁盤"), ("文檔", "文件"),
              ("資料", "數據"), ("不支援", "不支持")]
SPECIAL = {(0xA597, 0xA335): "磁盤"}          # 磁+(rare glyph) in the save-disk prompt
PASSWORD_FONT = (0x3384, 17)                   # built-in 30-byte glyph table used before ALLHAN loads


def sha(b):
    return hashlib.sha256(b).hexdigest()


def step(msg):
    print("•", msg, flush=True)


# ------------------------------------------------------------------ unpack
def unpack(main_exe, work):
    """Run the TINYPROG stub twice at different load segments; words that
    differ by 0x1000 are relocations. Rebuild a plain MZ executable."""
    dumps = {}
    for psp in ("0ff0", "1ff0"):
        out = os.path.join(work, "dump_" + psp)
        env = dict(os.environ, PSPSEG=psp)
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "emu86.py"), main_exe, out],
                       check=True, env=env, stdout=subprocess.DEVNULL)
        state = eval(open(os.path.join(out, "state.txt")).read(), {"__builtins__": {}})  # written by emu86.py
        mem = open(os.path.join(out, "mem.bin"), "rb").read()
        load = state["LOAD"]
        dumps[psp] = (mem[load * 16:], state)
    a, st = dumps["0ff0"]
    b, _ = dumps["1ff0"]
    load = st["LOAD"]
    ss, sp, ip = st["S"][2] - load, st["R"][4], st["ip"]
    if st["S"][1] != load:
        raise SystemExit("脱壳失败：没有停在程序入口。")
    size = ss * 16 + sp
    a, b = a[:size], b[:size]
    relocs, i = [], 0
    while i < size - 1:
        if a[i] != b[i] or a[i + 1] != b[i + 1]:
            wa, wb = a[i] | a[i + 1] << 8, b[i] | b[i + 1] << 8
            if (wb - wa) & 0xFFFF != 0x1000:
                raise SystemExit("脱壳失败：重定位对不上（偏移 %#x）。" % i)
            relocs.append(i)
            i += 2
            continue
        i += 1
    img = bytearray(a)
    for o in relocs:
        w = (img[o] | img[o + 1] << 8) - 0x1000
        img[o], img[o + 1] = w & 0xFF, (w >> 8) & 0xFF
    end = len(img)
    while end and img[end - 1] == 0:
        end -= 1
    img = img[:end]
    table = b"".join(struct.pack("<HH", o & 0xF, o >> 4) for o in relocs)
    hpara = (0x1C + len(table) + 15) // 16
    total = hpara * 16 + len(img)
    minalloc = (size - end + 15) // 16 + 0x100
    hdr = struct.pack("<2s13H", b"MZ", total % 512, (total + 511) // 512, len(relocs), hpara,
                      minalloc, 0xFFFF, ss, sp, 0, ip, 0, 0x1C, 0)
    out = bytearray(hdr + table)
    out += b"\0" * (hpara * 16 - len(out))
    return bytes(out + img)


# ------------------------------------------------------------------ text
def patch_words(exe, allhan):
    header = struct.unpack("<H", exe[8:10])[0] * 16
    img = bytearray(exe[header:])
    known = set(allhan[k * 30:k * 30 + 2].decode("big5") for k in range(len(allhan) // 30))
    strings = list(kt.find_strings(bytes(img), known))
    char2code = {}
    counts = {}
    for _, s in strings:
        for kind, code, ch in kt.tokens(s):
            if kind == "c" and ch:
                counts.setdefault(ch, {}).setdefault(code, 0)
                counts[ch][code] += 1
    for ch, c in counts.items():
        char2code[ch] = max(c, key=lambda k: (c[k], -k))
    changed = 0
    for off, s in strings:
        toks = kt.tokens(s)
        hit, j = False, 0
        while j < len(toks):
            if (j + 1 < len(toks) and toks[j][0] == toks[j + 1][0] == "c"
                    and (toks[j][1], toks[j + 1][1]) in SPECIAL):
                for q, ch in enumerate(SPECIAL[(toks[j][1], toks[j + 1][1])]):
                    toks[j + q] = ("c", char2code[ch], ch)
                hit, j = True, j + 2
                continue
            for old, new in WORD_RULES:
                n = len(old)
                if all(j + q < len(toks) and toks[j + q][0] == "c" and toks[j + q][2] == old[q] for q in range(n)):
                    for q, ch in enumerate(new):
                        if ch != old[q]:
                            toks[j + q] = ("c", char2code[ch], ch)
                    hit, j = True, j + n
                    break
            else:
                j += 1
        if hit:
            new = kt.encode(toks)
            assert len(new) == len(s)
            img[off:off + len(s)] = new
            changed += 1
    return exe[:header] + bytes(img), changed


# ------------------------------------------------------------------ glyphs
def render_allhan(allhan, font, threshold, work):
    swift = os.path.join(work, "render_glyphs")
    subprocess.run(["swiftc", "-O", os.path.join(HERE, "render_glyphs.swift"), "-o", swift],
                   check=True, stderr=subprocess.DEVNULL)
    chars = [allhan[k * 30:k * 30 + 2].decode("big5") for k in range(len(allhan) // 30)]
    src = os.path.join(work, "chars.txt")
    open(src, "w", encoding="utf-8").write("\n".join(chars))
    tsv = os.path.join(work, "glyphs.tsv")
    subprocess.run([swift, src, tsv, font, str(threshold)], check=True)
    rendered = {l.split("\t")[0]: l.split("\t") for l in open(tsv, encoding="utf-8").read().splitlines()}
    out = bytearray(allhan)
    n = 0
    for k, ch in enumerate(chars):
        trad, simp, hexbits = rendered[ch]
        if simp != ch:                      # only characters whose shape changes
            out[k * 30 + 2:k * 30 + 30] = bytes.fromhex(hexbits)
            n += 1
    return bytes(out), n


def patch_password_font(exe, allhan_simp):
    header = struct.unpack("<H", exe[8:10])[0] * 16
    simp = {allhan_simp[k * 30:k * 30 + 2]: allhan_simp[k * 30 + 2:k * 30 + 30] for k in range(len(allhan_simp) // 30)}
    out = bytearray(exe)
    start, count = PASSWORD_FONT
    n = 0
    for i in range(count):
        p = header + start + 30 * i
        key = bytes(out[p:p + 2])
        if key in simp and simp[key] != out[p + 2:p + 30]:
            out[p + 2:p + 30] = simp[key]
            n += 1
    return bytes(out), n


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", required=True, help="游戏目录（里面有 MAIN.EXE 和 ALLHAN.16P）")
    ap.add_argument("--font", default="PingFangSC-Light", help="渲染简体字形用的字体（PostScript 名）")
    ap.add_argument("--threshold", type=float, default=0.45, help="二值化阈值，越大笔画越细")
    ap.add_argument("--dry-run", action="store_true", help="只检查和生成，不写回游戏目录")
    args = ap.parse_args()

    main_path = os.path.join(args.game, "MAIN.EXE")
    font_path = os.path.join(args.game, "ALLHAN.16P")
    main_exe, allhan = open(main_path, "rb").read(), open(font_path, "rb").read()
    if sha(main_exe) != ORIGINAL_MAIN or sha(allhan) != ORIGINAL_ALLHAN:
        raise SystemExit("MAIN.EXE 或 ALLHAN.16P 不是支持的原版（校验值对不上），没有做任何修改。\n"
                         "如果你已经打过补丁，请先用 _backup_original 里的文件还原。")

    with tempfile.TemporaryDirectory() as work:
        step("脱壳 MAIN.EXE（模拟运行解压程序，约 10 秒）")
        unpacked = unpack(main_path, work)
        if sha(unpacked) != UNPACKED_MAIN:
            raise SystemExit("脱壳结果和预期不一致，已停止。")

        step("替换台湾用词")
        worded, n = patch_words(unpacked, allhan)
        print("  改了 %d 条文本" % n)
        if sha(worded) != TEXT_PATCHED_MAIN:
            raise SystemExit("改词结果和预期不一致，已停止。")

        step("渲染简体字库（%s）" % args.font)
        allhan_simp, n = render_allhan(allhan, args.font, args.threshold, work)
        print("  重画了 %d 个字" % n)

        step("替换密码画面的内置字形")
        final, n = patch_password_font(worded, allhan_simp)
        print("  重画了 %d 个字" % n)

    same = sha(allhan_simp) == REFERENCE_ALLHAN and sha(final) == REFERENCE_MAIN
    print("  与作者生成的版本%s" % ("完全一致" if same else "像素略有不同（字体或系统版本不同，正常）"))

    if args.dry_run:
        print("--dry-run：没有写回游戏目录。")
        return
    backup = os.path.join(args.game, "_backup_original")
    os.makedirs(backup, exist_ok=True)
    for name in ("MAIN.EXE", "ALLHAN.16P"):
        dst = os.path.join(backup, name)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(args.game, name), dst)
    open(main_path, "wb").write(final)
    open(font_path, "wb").write(allhan_simp)
    step("完成。原文件在 %s" % backup)


if __name__ == "__main__":
    main()
