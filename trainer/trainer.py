#!/usr/bin/env python3
"""大航海时代 1 实时修改器

在 DOSBox-X 运行游戏时，直接读写模拟器内存里的游戏数值。
需要管理员权限（macOS 只允许 root 读写其他进程内存）。
启动后在浏览器打开 http://127.0.0.1:8765
"""
import ctypes, ctypes.util, json, os, struct, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- mach API
libc = ctypes.CDLL(ctypes.util.find_library("System"), use_errno=True)
mach_port_t = ctypes.c_uint32
kern_return_t = ctypes.c_int
mach_vm_address_t = ctypes.c_uint64
mach_vm_size_t = ctypes.c_uint64

libc.task_for_pid.argtypes = [mach_port_t, ctypes.c_int, ctypes.POINTER(mach_port_t)]
libc.task_for_pid.restype = kern_return_t
libc.mach_vm_region.argtypes = [mach_port_t, ctypes.POINTER(mach_vm_address_t), ctypes.POINTER(mach_vm_size_t),
                                ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(mach_port_t)]
libc.mach_vm_region.restype = kern_return_t
libc.mach_vm_read_overwrite.argtypes = [mach_port_t, mach_vm_address_t, mach_vm_size_t, ctypes.c_void_p, ctypes.POINTER(mach_vm_size_t)]
libc.mach_vm_read_overwrite.restype = kern_return_t
libc.mach_vm_write.argtypes = [mach_port_t, mach_vm_address_t, ctypes.c_void_p, ctypes.c_uint32]
libc.mach_vm_write.restype = kern_return_t
SELF_TASK = mach_port_t.in_dll(libc, "mach_task_self_").value

VM_REGION_BASIC_INFO_64 = 9
VM_PROT_READ, VM_PROT_WRITE = 1, 2


class BasicInfo(ctypes.Structure):
    _fields_ = [("protection", ctypes.c_int), ("max_protection", ctypes.c_int), ("inheritance", ctypes.c_uint),
                ("shared", ctypes.c_uint), ("reserved", ctypes.c_uint), ("offset", ctypes.c_ulonglong),
                ("behavior", ctypes.c_int), ("user_wired_count", ctypes.c_ushort)]


class Proc:
    def __init__(self, pid):
        self.pid = pid
        t = mach_port_t()
        kr = libc.task_for_pid(SELF_TASK, pid, ctypes.byref(t))
        if kr != 0:
            raise PermissionError("task_for_pid 失败（错误 %d）。请用管理员权限运行。" % kr)
        self.task = t.value

    def regions(self):
        addr = mach_vm_address_t(0)
        while True:
            size = mach_vm_size_t(0)
            info = BasicInfo()
            cnt = ctypes.c_uint32(ctypes.sizeof(BasicInfo) // 4)
            obj = mach_port_t()
            kr = libc.mach_vm_region(self.task, ctypes.byref(addr), ctypes.byref(size), VM_REGION_BASIC_INFO_64,
                                     ctypes.byref(info), ctypes.byref(cnt), ctypes.byref(obj))
            if kr != 0:
                return
            yield addr.value, size.value, info.protection
            addr.value += size.value

    def read(self, addr, n):
        buf = ctypes.create_string_buffer(n)
        got = mach_vm_size_t(0)
        kr = libc.mach_vm_read_overwrite(self.task, addr, n, buf, ctypes.byref(got))
        if kr != 0:
            raise OSError("读取内存失败（%d）" % kr)
        return buf.raw[:got.value]

    def write(self, addr, data):
        buf = ctypes.create_string_buffer(data, len(data))
        kr = libc.mach_vm_write(self.task, addr, buf, len(data))
        if kr != 0:
            raise OSError("写入内存失败（%d）" % kr)


# ---------------------------------------------------------------- game layout
SIG = b"MS Run-Time Library - Copyright (c) 1988, Microsoft Corp"   # at DGROUP:0008
RANK_TABLE = bytes.fromhex("d91fda1fdf1fe41fe91ff01ff51ffa1fff1f0420")  # at DGROUP:20F4 (only in MAIN.EXE)
RANKS = ["无", "侍从", "乡绅", "爵士", "从男爵", "男爵", "子爵", "伯爵", "侯爵", "公爵"]
FIELDS = {
    "gold": (0x0CAD, 2, "身上的金币", 65535),
    "bank": (0x17D0, 2, "皇宫存款（千）", 10000),
    "fame": (0x17D2, 2, "名望", 65535),
    "rank": (0x17D4, 1, "爵位", 9),
}
SHIP0, SHIP_STRIDE, SHIPS = 0x1336, 0x15, 5


PLAYER_POSITION = 0x0CA4
PLAYER_FLAGS = PLAYER_POSITION + 0x10
VIEW_MODE = 0x204E
MAP_TILE_CACHE = 0x20A5
PORT_TABLE, PORT_STRIDE, PORT_NAME_SIZE = 0x17D6, 20, 15

# Port records 0–69 in MAIN.EXE, followed by the local coordinates of a
# neighboring water tile from NEWGAME.DAT. The game's docking check looks
# around the fleet, so its position must be on the water tile.
PORTS = (
    ("里斯本", (32, 8, 3, 12), (2, 12)),
    ("塞维尔", (32, 10, 6, 5), (5, 5)),
    ("伊斯坦堡", (38, 9, 11, 2), (11, 3)),
    ("热内亚", (36, 8, 2, 10), (2, 11)),
    ("威尼斯", (36, 8, 11, 0), (11, 1)),
    ("比萨", (36, 9, 5, 1), (4, 1)),
    ("马赛", (35, 9, 1, 2), (1, 3)),
    ("巴伦西亚", (33, 10, 10, 6), (11, 6)),
    ("阿尔及尔", (34, 11, 1, 8), (1, 7)),
    ("马任加", (36, 10, 6, 7), (7, 7)),
    ("突尼斯", (35, 10, 10, 11), (11, 11)),
    ("拿坡里", (36, 9, 11, 9), (10, 9)),
    ("亚历山大", (38, 11, 6, 7), (6, 6)),
    ("特拉比松", (40, 9, 5, 10), (4, 10)),
    ("亚速海", (40, 7, 5, 7), (5, 8)),
    ("伦敦", (34, 5, 5, 5), (6, 5)),
    ("波尔多", (34, 7, 5, 10), (4, 10)),
    ("奥斯陆", (35, 5, 5, 2), (5, 3)),
    ("汉堡", (35, 5, 8, 11), (8, 10)),
    ("安特卫普", (34, 6, 9, 3), (9, 2)),
    ("圣多明各", (19, 16, 12, 0), (12, 1)),
    ("牙买加", (18, 16, 10, 7), (10, 8)),
    ("危地马拉", (16, 16, 8, 8), (9, 8)),
    ("巴拿马", (17, 17, 9, 9), (10, 9)),
    ("韦拉克鲁斯", (14, 15, 8, 10), (9, 10)),
    ("加拉加斯", (19, 17, 7, 9), (7, 8)),
    ("帕拉马里博", (26, 22, 10, 12), (11, 12)),
    ("阿尔金", (32, 18, 5, 11), (4, 11)),
    ("圣约瑟", (34, 18, 5, 10), (5, 11)),
    ("索法拉", (42, 23, 0, 4), (1, 4)),
    ("莫桑比克", (42, 21, 8, 12), (9, 12)),
    ("蒙巴萨", (42, 20, 5, 4), (6, 4)),
    ("马斯喀特", (45, 14, 8, 11), (9, 11)),
    ("巴格达", (44, 12, 3, 2), (3, 3)),
    ("亚丁", (42, 16, 5, 5), (4, 5)),
    ("麦加", (41, 13, 5, 9), (4, 9)),
    ("花剌子模", (45, 13, 4, 9), (5, 9)),
    ("果阿", (48, 16, 7, 4), (6, 4)),
    ("帝汶", (47, 13, 7, 10), (6, 10)),
    ("马六甲", (53, 18, 9, 9), (8, 9)),
    ("锡兰", (50, 18, 11, 11), (11, 10)),
    ("苏门答腊", (53, 21, 6, 6), (6, 7)),
    ("爪哇", (55, 22, 4, 7), (4, 6)),
    ("阿波尼纳", (59, 21, 4, 9), (4, 8)),
    ("特尔那特", (58, 22, 0, 3), (1, 3)),
    ("加尔各答", (49, 17, 5, 10), (5, 11)),
    ("交趾", (49, 18, 11, 1), (10, 1)),
    ("长崎", (59, 12, 6, 2), (6, 3)),
    ("澳门", (56, 14, 5, 6), (5, 7)),
    ("兹特恩", (57, 13, 12, 3), (13, 3)),
    ("新格兰德", (17, 18, 11, 1), (11, 0)),
    ("里约热内卢", (22, 31, 8, 11), (9, 11)),
    ("岘港", (55, 16, 6, 8), (7, 8)),
    ("关岛", (64, 15, 10, 11), (10, 10)),
    ("开岛", (1, 16, 8, 5), (8, 4)),
    ("欧里卡", (8, 8, 13, 7), (12, 7)),
    ("弗吉尼亚", (19, 9, 5, 7), (6, 7)),
    ("柯皮阿波", (20, 27, 10, 11), (10, 10)),
    ("里约格兰德", (25, 26, 10, 2), (11, 2)),
    ("开普敦", (39, 27, 6, 7), (6, 8)),
    ("罗安达", (37, 22, 6, 9), (6, 8)),
    ("维尔德", (30, 16, 1, 7), (0, 7)),
    ("印吉卡", (40, 4, 5, 5), (5, 4)),
    ("马旦", (62, 22, 9, 11), (9, 10)),
    ("腊布克", (57, 26, 9, 9), (9, 8)),
    ("塔尼斯克", (64, 5, 1, 6), (1, 7)),
    ("希毕尔", (48, 4, 10, 7), (10, 6)),
    ("强獽", (18, 24, 3, 4), (2, 4)),
    ("鲁文", (41, 19, 3, 1), (2, 1)),
    ("阿卡普鲁", (12, 15, 9, 5), (8, 5)),
)
PORT_IDS = {
    bytes(position): port_id
    for port_id, (_, dock, berth) in enumerate(PORTS)
    for position in (dock, dock[:2] + berth)
}


# Code patches. Offsets are from the start of MAIN.EXE's load image; the code
# lives at DGROUP - 0x45B00 in emulated memory. `orig` uses None for bytes the
# DOS loader relocates (far-call segments); the live bytes are saved on enable
# and written back on disable.
FLEET_COUNT, FLEET_STRIDE = 21, 17
NATIONS = {0: "葡萄牙", 1: "西班牙", 2: "奥斯曼"}
CODE_FROM_DS = 0x45B00
MISSION_FLAGS = 0x1DF9   # bit0 summoned, bit3 accepted, bit1 done
MISSION_QUOTA = 0x1DF0   # goods mission: quantity the king wants
TREASURE_FLAG = 0x0CB2   # player record +0x0E bit 7: treasure in hand
CHEATS = {
    "supplies": {
        "label": "航行不消耗粮食和水",
        "patches": [(0x36230, [0x55], [0xCB])],   # consume() -> retf
    },
    "cannon": {
        "label": "炮击：我方一炮击沉，敌方伤害为 0",
        "patches": [(0x324A0,
                     list(bytes.fromhex("807efa00750e8b46fc99b90a00f7f9d1e02946fc")),
                     list(bytes.fromhex("807efa007507c746fcff00eb05c746fc00009090")))],
    },
    "summon": {
        "label": "国王召见检查改为每天一次（原版只有 5% 的概率）",
        "patches": [(0x0B63F, [0x75, 0x05], [0x90, 0x90])],   # skip "random(20) != 0"
    },
    "missions": {
        "label": "国王任务：接了任务回王宫就算完成",
        "patches": [
            (0x1A95D, [0x74, 0x0D], [0x90, 0x90]),   # "mission done?" ignores the done bit (pirates, letters)
            (0x1B3F0, [0x75, 0x03], [0xEB, 0x03]),   # goods mission: proceed even with none aboard
        ],
    },
    "night": {
        "label": "取消“夜间不能战斗”",
        "patches": [(0x3D1E7, [0x72, 0x1C], [0xEB, 0x1C])],   # "if hour < 30 fight" -> always fight
    },
    "boarding": {
        "label": "白刃战：我方一击清空敌船员，敌方不伤我方",
        "patches": [(0x321E1,
                     [0x50, 0x8B, 0x5E, 0xFE, 0xFF, 0x77, 0x04, 0x9A, 0x93, 0x77, None, None,
                      0x83, 0xC4, 0x04, 0x8B, 0x5E, 0xFE, 0x29, 0x47, 0x04],
                     list(bytes.fromhex("807e0605730f8b5efec74704000090909090909090")))],
    },
}


def port_info(port_id):
    name, (x, y, _, _), _ = PORTS[port_id]
    return {"id": port_id, "name": name, "x": x, "y": y}


class Game:
    def __init__(self):
        self.proc = None
        self.ds = None          # host address of DGROUP:0000
        self.locks = {}         # (offset, size) -> value
        self.lock = threading.RLock()
        self.message = ""
        self.saved = {}         # (pid, code offset) -> live original bytes

    def find_pid(self):
        out = subprocess.run(["pgrep", "-x", "dosbox-x"], capture_output=True, text=True).stdout.split()
        if not out:
            out = subprocess.run(["pgrep", "-f", "dosbox-x"], capture_output=True, text=True).stdout.split()
        return int(out[0]) if out else None

    def attach(self):
        pid = self.find_pid()
        if not pid:
            raise RuntimeError("没找到正在运行的 DOSBox-X。先启动游戏并读档进入游戏。")
        if not self.proc or self.proc.pid != pid:
            self.proc = Proc(pid)
            self.ds = None
        if self.ds is not None and self._valid(self.ds):
            return
        self.ds = self._scan()
        if self.ds is None:
            raise RuntimeError("在 DOSBox-X 内存里没找到游戏数据。请先进入游戏主画面（读档或开始新游戏）再点“重新连接”。")

    def _valid(self, ds):
        try:
            return self.proc.read(ds + 8, len(SIG)) == SIG and self.proc.read(ds + 0x20F4, len(RANK_TABLE)) == RANK_TABLE
        except OSError:
            return False

    def _scan(self):
        CH = 8 << 20
        for addr, size, prot in self.proc.regions():
            if (prot & (VM_PROT_READ | VM_PROT_WRITE)) != (VM_PROT_READ | VM_PROT_WRITE):
                continue
            if size > (1 << 31):
                continue
            pos = 0
            while pos < size:
                n = min(CH + len(SIG), size - pos)
                try:
                    data = self.proc.read(addr + pos, n)
                except OSError:
                    break
                i = data.find(SIG)
                while i >= 0:
                    cand = addr + pos + i - 8
                    if self._valid(cand):
                        return cand
                    i = data.find(SIG, i + 1)
                pos += CH
        return None

    def rd(self, off, size):
        b = self.proc.read(self.ds + off, size)
        return b[0] if size == 1 else struct.unpack("<H", b)[0]

    def wr(self, off, size, v):
        self.proc.write(self.ds + off, bytes([v & 255]) if size == 1 else struct.pack("<H", v & 0xFFFF))

    @staticmethod
    def _matches(live, pattern):
        return len(live) == len(pattern) and all(p is None or p == b for p, b in zip(pattern, live))

    def cheat_state(self, name):
        """'on', 'off' or 'unknown' (code does not match this game version)."""
        base = self.ds - CODE_FROM_DS
        states = set()
        for off, orig, new in CHEATS[name]["patches"]:
            live = self.proc.read(base + off, len(new))
            if live == bytes(new):
                states.add("on")
            elif self._matches(live, orig):
                states.add("off")
            else:
                states.add("unknown")
        return states.pop() if len(states) == 1 else "unknown"

    def set_cheat(self, name, on):
        if name not in CHEATS:
            raise ValueError("没有这个开关")
        state = self.cheat_state(name)
        if state == "unknown":
            raise RuntimeError("游戏代码和修改器对不上，不能开启这个开关。")
        base = self.ds - CODE_FROM_DS
        for off, orig, new in CHEATS[name]["patches"]:
            key = (self.proc.pid, off)
            if on and state == "off":
                self.saved[key] = self.proc.read(base + off, len(new))
                self.proc.write(base + off, bytes(new))
            elif not on and state == "on":
                restore = self.saved.get(key)
                if restore is None:
                    if None in orig:
                        raise RuntimeError("修改器重启过，无法自动还原这项代码。请重开游戏来恢复原样。")
                    restore = bytes(orig)
                self.proc.write(base + off, restore)

    def teleport_blocker(self):
        if self.rd(VIEW_MODE, 1) != 0:
            return "请先出港，回到海上主画面。"
        # These fleet states skip the normal ocean update or return to port.
        if self.rd(PLAYER_FLAGS, 1) & 0x3A:
            return "请回到正常的海上航行画面。"
        if any(off < PLAYER_POSITION + 4 and off + size > PLAYER_POSITION for off, size in self.locks):
            return "请先解除舰队位置的地址锁定。"
        if any(off <= PLAYER_FLAGS < off + size for off, size in self.locks):
            return "请先解除舰队航行状态的地址锁定。"
        return ""

    def teleport(self, port_id):
        if type(port_id) is not int or not 0 <= port_id < len(PORTS):
            raise ValueError("请选择有效的港口。")
        blocker = self.teleport_blocker()
        if blocker:
            raise RuntimeError(blocker)
        _, dock, berth = PORTS[port_id]
        offset = PORT_TABLE + PORT_STRIDE * port_id + PORT_NAME_SIZE
        if self.proc.read(self.ds + offset, 4) != bytes(dock):
            raise RuntimeError("当前游戏版本的港口坐标与修改器不一致。")
        self._place_fleet(bytes(dock[:2] + berth))

    def fleets(self):
        """Computer fleets: admiral records 1..20 at 0x0CA4 (17 bytes each).
        Byte 15 is the nation (0 Portugal, 1 Spain, 2 Ottoman, 0x3x pirates);
        bytes 2-3 are 0xFF while the fleet is out at sea."""
        out = []
        for i in range(1, FLEET_COUNT):
            r = self.proc.read(self.ds + PLAYER_POSITION + FLEET_STRIDE * i, FLEET_STRIDE)
            x, y, at_sea = r[0], r[1], bytes(r[:4]) not in PORT_IDS
            nation = NATIONS.get(r[15], "海盗" if r[15] >= 0x30 else "未知")
            near = min(PORTS, key=lambda p: (p[1][0] - x) ** 2 + (p[1][1] - y) ** 2)[0]
            out.append({"i": i, "nation": nation, "x": x, "y": y, "atSea": at_sea, "near": near})
        return out

    def teleport_to_fleet(self, fleet_id):
        if type(fleet_id) is not int or not 1 <= fleet_id < FLEET_COUNT:
            raise ValueError("请选择有效的舰队。")
        blocker = self.teleport_blocker()
        if blocker:
            raise RuntimeError(blocker)
        r = self.proc.read(self.ds + PLAYER_POSITION + FLEET_STRIDE * fleet_id, 4)
        if bytes(r) in PORT_IDS:
            raise RuntimeError("这支舰队停在港口里，等它出港后再试。")
        if r[2] == 0xFF or r[3] == 0xFF:
            # Only the map block is known while the fleet is far away: use its centre.
            self._place_fleet(bytes([r[0], r[1], 7, 7]))
        else:
            # Exact tile known (fleet is near you): stop on the tile right next to
            # it so the battle command can aim at it.
            x, y, fx, fy = r[0], r[1], r[2], r[3]
            if fx > 0:
                self._place_fleet(bytes([x, y, fx - 1, fy]))
                self.last_fleet_direction = "东"
            else:
                self._place_fleet(bytes([x, y, fx + 1, fy]))
                self.last_fleet_direction = "西"
            return
        self.last_fleet_direction = ""

    def _place_fleet(self, position):
        flags = self.rd(PLAYER_FLAGS, 1)
        if flags & 1:
            self.wr(PLAYER_FLAGS, 1, flags & ~1)
        self.proc.write(self.ds + PLAYER_POSITION, position)
        # The game's sea redraw uses 0xFF as the invalid tile and view mode.
        # The next ocean update rebuilds the map and nearby-port list.
        self.proc.write(self.ds + MAP_TILE_CACHE, b"\xff\xff")
        self.wr(VIEW_MODE, 1, 0xFF)

    def state(self):
        self.attach()
        f = {k: self.rd(o, s) for k, (o, s, _, _) in FIELDS.items()}
        ships = []
        for k in range(SHIPS):
            base = SHIP0 + k * SHIP_STRIDE
            raw = self.proc.read(self.ds + base, 12)
            name = raw.split(b"\0")[0].decode("latin-1")
            ships.append({"i": k, "name": name, "crew": self.rd(base + 15, 1), "wep": self.rd(base + 17, 1),
                          "crewOff": base + 15, "wepOff": base + 17})
        with self.lock:
            locks = [{"off": o, "size": s, "value": v} for (o, s), v in self.locks.items()]
        position = self.proc.read(self.ds + PLAYER_POSITION, 4)
        fleet = {"x": position[0], "y": position[1], "port": PORT_IDS.get(position),
                 "teleportBlocker": self.teleport_blocker()}
        cheats = [{"name": k, "label": v["label"], "state": self.cheat_state(k)} for k, v in CHEATS.items()]
        return {"ok": True, "fleet": fleet, "fleets": self.fleets(), "cheats": cheats, "pid": self.proc.pid, "ds": hex(self.ds), "fields": f, "ships": ships, "locks": locks,
                "ranks": RANKS, "meta": {k: {"off": o, "size": s, "label": l, "max": m} for k, (o, s, l, m) in FIELDS.items()}}

    def assist_missions(self):
        """While a king's mission is accepted (flags 0x1DF9 bits 0 and 3), make
        the goods quota zero and mark the treasure as found."""
        try:
            if self.cheat_state("missions") != "on":
                return
            if self.rd(MISSION_FLAGS, 1) & 0x09 != 0x09:
                return
            if self.rd(MISSION_QUOTA, 2) != 0:
                self.wr(MISSION_QUOTA, 2, 0)
            b = self.rd(TREASURE_FLAG, 1)
            if not b & 0x80:
                self.wr(TREASURE_FLAG, 1, b | 0x80)
        except Exception:
            pass

    def freezer(self):
        while True:
            time.sleep(0.3)
            with self.lock:
                if self.proc is None or self.ds is None:
                    continue
                self.assist_missions()
                for (o, s), v in self.locks.items():
                    try:
                        if self.rd(o, s) != v:
                            self.wr(o, s, v)
                    except Exception:
                        pass


game = Game()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as fh:
                return self._send(200, fh.read(), "text/html; charset=utf-8")
        if self.path == "/api/ports":
            return self._send(200, {"ok": True, "ports": [port_info(i) for i in range(len(PORTS))]})
        if self.path == "/api/state":
            try:
                with game.lock:
                    state = game.state()
                return self._send(200, state)
            except Exception as e:
                return self._send(200, {"ok": False, "error": str(e)})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        # Only accept requests from this machine's own page
        if self.headers.get("Origin") not in (None, "http://127.0.0.1:%d" % PORT, "http://localhost:%d" % PORT):
            return self._send(403, {"ok": False, "error": "forbidden"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(n) or b"{}")
            with game.lock:
                code, result = self._handle_post(req)
            self._send(code, result)
        except Exception as e:
            self._send(200, {"ok": False, "error": str(e)})

    def _handle_post(self, req):
        game.attach()
        if self.path == "/api/teleport":
            port_id = req.get("port")
            game.teleport(port_id)
            return 200, {**game.state(), "destination": port_info(port_id)}
        if self.path == "/api/teleport_fleet":
            game.teleport_to_fleet(req.get("fleet"))
            return 200, {**game.state(), "direction": getattr(game, "last_fleet_direction", "")}
        if self.path == "/api/cheat":
            game.set_cheat(str(req.get("name")), bool(req.get("on")))
            return 200, game.state()
        if self.path == "/api/set":
            off, size, v = int(req["off"]), int(req["size"]), int(req["value"])
            if not (0 <= off <= 0xFFFF and size in (1, 2) and 0 <= v <= (255 if size == 1 else 65535)):
                raise ValueError("地址或数值超出范围")
            game.wr(off, size, v)
            if (off, size) in game.locks:
                game.locks[(off, size)] = v
        elif self.path == "/api/lock":
            off, size, on = int(req["off"]), int(req["size"]), bool(req["on"])
            if on:
                game.locks[(off, size)] = int(req.get("value", game.rd(off, size)))
            else:
                game.locks.pop((off, size), None)
        elif self.path == "/api/read":
            off, size = int(req["off"]), int(req["size"])
            return 200, {"ok": True, "value": game.rd(off, size)}
        elif self.path == "/api/reconnect":
            game.ds = None
            game.attach()
        else:
            return 404, {"ok": False, "error": "not found"}
        return 200, game.state()


def main():
    if os.geteuid() != 0:
        print("需要管理员权限：请用 sudo 运行，或双击“启动实时修改器.app”。")
        sys.exit(1)
    threading.Thread(target=game.freezer, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("实时修改器已启动： http://127.0.0.1:%d" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
