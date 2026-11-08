# -*- coding: utf-8 -*-
"""
ObsCure 汉化补丁安装器 v1.00  ——  差分（轻量）GUI 版
======================================================
单文件 exe，payload 以 ZIP 追加在 exe 尾部，启动直接读自身尾部，无需解压，秒开。

体积策略：差分安装 —— 不携带 663MB 的 datapack.hvp，而是携带
  obscure-hvp.exe (1.4MB, 第三方 HVPack 工具) + zh_en.lng (117KB 汉化译文)
在用户**本地原版** datapack.hvp 上重建出汉化版。安装包总体积 ~9MB。

功能：
  * 安装目录自由选择（自动探测 Steam 库，含 libraryfolders.vdf 多库）
  * 组件自由勾选：汉化核心 / 宽屏补丁
  * 可选「安装前备份原文件」（备份到游戏目录 _obscure_backup/，直接放文件无时间戳）
  * 内置「卸载 / 还原」
"""

import os
import sys
import json
import time
import zipfile
import shutil
import hashlib
import struct
import ctypes
import subprocess
import tempfile

APP_TITLE = "ObsCure 汉化补丁安装器"
APP_VER = "1.00"

DISCLAIMER_TEXT = """《ObsCure》汉化补丁 v1.00 · 免责声明

1. 本补丁所翻译的中文文本/字幕内容（即游戏原版文案）的著作权归
   育碧软件（Ubisoft）及原开发组（Hydravision Entertainment）所有。
2. 本汉化补丁为爱好者基于上述内容制作的学习研究用途作品，仅供已合法
   购买正版游戏的用户在个人学习、研究、欣赏目的下使用。
3. 本补丁不包含、不修改、不破解任何版权保护机制，不含任何游戏本体的
   分发内容；禁止用于任何商业用途或牟利行为。
4. 使用本补丁所产生的一切后果由使用者自行承担。若本补丁或其内容
   侵犯了您的合法权益，请联系制作者，经确认后将立即停止分发并删除相关内容。
5. 请支持正版：请在已取得正版游戏的前提下使用本补丁。

点击「确认」按钮即表示您已阅读、理解并同意以上全部声明。

                                    fwzang · 2026-10-18
"""
APP_BUILD = "20261017"
MANIFEST = "INSTALLER_MANIFEST.json"
BACKUP_DIR = "_obscure_backup"
ADDED_LIST = "_added_files.txt"     # 备份目录内的清单：本次新增、原本不存在的文件

HV_EXE = "obscure-hvp.exe"
HV_LNG = "zh_en.lng"
NEW_DP_MIN = 100 * 1024 * 1024      # 重建后体积下限，低于则判失败
DP_NAME = "datapack.hvp"

# 地图汉化贴图（v1.00 新增）：payload/maps_pc/ 下 33 件，替换解包树
# _common/maps_pc/ 中的同名文件（30 BMP + 3 TGA，零售官方中文）
MAP_TEX_DIR = "maps_pc"
MAP_TEX_TARGET = "_common/maps_pc"

# 基底识别（差分重建只认这两类 + 已汉化态）
VANILLA_LNG_SIZE = 149816           # 英文原版 en.lng 体积（Steam 原版与 ODE 英文版相同）
ZH_LNG_MD5 = None                   # 运行时从 manifest 取（避免两处硬编码）

# 直接覆盖类文件（相对游戏根）
DIRECT_FILES_CORE = ["version.dll", "big_atlas_c.raw", "table_c.bin"]
DIRECT_FILES_WIDE = ["dinput8.dll", "scripts/main.patch", "scripts/obscure.asi"]


# ================================================================ 工具函数

def app_exe_path():
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return os.path.abspath(sys.argv[0])


def find_payload():
    """返回 (ZipFile 或 None, devdir 或 None)。"""
    p = app_exe_path()
    if os.path.isfile(p):
        try:
            z = zipfile.ZipFile(p, "r")
            if MANIFEST in z.namelist():
                return z, None
            z.close()
        except Exception:
            pass
    d = os.path.join(os.path.dirname(app_exe_path()), "payload")
    if os.path.isdir(d):
        return None, d
    return None, None


def read_manifest(z, devdir):
    if z is not None:
        with z.open(MANIFEST) as f:
            return json.loads(f.read().decode("utf-8"))
    with open(os.path.join(devdir, MANIFEST), "r", encoding="utf-8") as f:
        return json.load(f)


def md5_of(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def is_game_dir(d):
    return bool(d) and os.path.isfile(os.path.join(d, "Obscure.exe"))



def detect_steam_dirs():
    """探测所有含 Obscure.exe 的 Steam 库目录。"""
    import winreg
    out = []
    roots = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam"),
                      (winreg.HKEY_CURRENT_USER, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for val in ("SteamPath", "InstallPath"):
                    try:
                        roots.append(winreg.QueryValueEx(k, val)[0])
                        break
                    except OSError:
                        continue
        except OSError:
            continue
    seen = set()
    libs = []
    for r in roots:
        r = r.replace("/", "\\").rstrip("\\")
        if r.lower() in seen:
            continue
        seen.add(r.lower())
        libs.append(r + "\\steamapps\\common")
        vdf = r + "\\steamapps\\libraryfolders.vdf"
        if os.path.isfile(vdf):
            try:
                import re
                with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        # VDF 行形如:  "path"\t\t"D:\\SteamLibrary"
                        # 逐个提取引号字符串，只取像绝对路径的
                        for m in re.findall(r'"([^"]+)"', line):
                            if len(m) < 4 or m.lower().endswith(".vdf"):
                                continue
                            seg = m.replace("\\\\", "\\").replace("/", "\\")
                            if len(seg) < 3 or seg[1] != ":":
                                continue
                            if os.path.isdir(seg):
                                libs.append(seg + "\\steamapps\\common")
            except Exception:
                pass
    for d in libs:
        if not os.path.isdir(d):
            continue
        if is_game_dir(d):
            if d not in out:
                out.append(d)
            continue
        # Steam 库结构: <lib>\steamapps\common\<AppName>\Obscure.exe
        try:
            subs = sorted(os.listdir(d))
        except OSError:
            continue
        for s in subs:
            sd = os.path.join(d, s)
            if is_game_dir(sd) and sd not in out:
                out.append(sd)
    # 名字越短越像真安装目录（备份目录名通常很长）
    out.sort(key=lambda p: (len(os.path.basename(p)), p))
    return out


# ================================================================ 核心安装器

class Installer(object):
    def __init__(self):
        self.z, self.devdir = find_payload()
        if self.z is None and self.devdir is None:
            raise SystemExit("安装包已损坏：未找到内置数据。")
        self.mf = read_manifest(self.z, self.devdir)
        self.ui = None

    # ---- payload 读取
    def open_arc(self, arc):
        if self.z is not None:
            return self.z.open(arc)
        return open(os.path.join(self.devdir, arc.replace("/", os.sep)), "rb")

    def _map_tex_arcs(self):
        """payload 中 maps_pc/ 下的全部贴图 arc 名（相对 payload 根）。"""
        return sorted(a for a in self.mf["files"] if a.startswith(MAP_TEX_DIR + "/"))

    def arc_size(self, arc):
        return self.mf["files"].get(arc, {}).get("size", 0)

    def arc_md5(self, arc):
        return self.mf["files"].get(arc, {}).get("md5")

    def total_payload(self, arcs):
        return sum(self.arc_size(a) for a in arcs)

    def material(self, arcs, dest_dir):
        """把 payload 中的文件释放到 dest_dir，返回 {arc: 落地路径}。"""
        made = {}
        for arc in arcs:
            dst = os.path.join(dest_dir, arc.replace("/", os.sep))
            d = os.path.dirname(dst)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            src = self.open_arc(arc)
            try:
                with open(dst, "wb") as o:
                    shutil.copyfileobj(src, o, 1 << 20)
            finally:
                src.close()
            made[arc] = dst
        return made

    # ---- 主流程
    def install(self, game, want_core, want_wide, do_backup, tmp_root):
        """
        返回 (ok:bool, msg:str, backup_dir:str|None)
        """
        if not is_game_dir(game):
            return False, "目标目录不是 ObsCure 目录（未找到 Obscure.exe）。", None
        if not os.access(game, os.W_OK):
            return False, "目标目录不可写（请以管理员身份运行）：\n" + game, None

        direct = []
        if want_core:
            direct += DIRECT_FILES_CORE
            direct.append("免责声明.txt")     # 安装时释放到游戏根目录
        if want_wide:
            direct += DIRECT_FILES_WIDE
        if not direct and not want_core:
            return False, "未选择任何组件。", None

        log = self.ui.log

        # ---------- 备份
        # 备份直接放 _obscure_backup/ 下（无时间戳子目录）。
        # 「只备份首次、不覆盖」由下面的 if not os.path.isfile(dst) 保证，
        # 避免原版备份被中间版本覆盖，导致卸载无法回到原版。
        # 同时记录「本次新增、原本不存在」的文件（*.new），卸载时删除它们，
        # 这样在纯净 Steam 安装上卸载后目录能回到完全干净的原状。
        bdir = None
        if do_backup:
            bdir = self._backup_dir(game, log)
            n = 0
            added = []
            for rel in direct + [DP_NAME]:
                src = os.path.join(game, rel.replace("/", os.sep))
                dst = os.path.join(bdir, rel.replace("/", os.sep))
                if os.path.isfile(src):
                    if not os.path.isfile(dst):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        n += 1
                else:
                    if rel not in added:
                        added.append(rel)
            if added:
                os.makedirs(bdir, exist_ok=True)
                # 合并而非覆盖：首次安装记录了"原本不存在"的文件，
                # 后续重装时这些文件已存在（added 为空），绝不能把清单冲掉。
                prev = []
                if os.path.isfile(os.path.join(bdir, ADDED_LIST)):
                    try:
                        with open(os.path.join(bdir, ADDED_LIST), "r", encoding="utf-8") as f:
                            prev = [x.strip() for x in f if x.strip()]
                    except OSError:
                        prev = []
                merged = prev + [a for a in added if a not in prev]
                with open(os.path.join(bdir, ADDED_LIST), "w", encoding="utf-8") as f:
                    f.write("\n".join(merged))
                log("记录新增文件（卸载时删除）：%s" % ", ".join(merged))
            if n:
                log("已备份 %d 个原文件 -> %s" % (n, bdir))
            else:
                log("沿用首次备份（不覆盖）：%s" % bdir)
            if added:
                log("记录 %d 个新增文件（卸载时将删除）：%s" % (len(added), ", ".join(added)))
        else:
            log("按用户选择：跳过备份")

        # ---------- 直接覆盖的文件
        total = self.total_payload(direct)
        done = [0]
        arcs_map = dict((a, a) for a in direct)
        # payload 里的 arc 名与目标相对路径一致
        arcs = []
        for rel in direct:
            arcs.append(rel)

        self.ui.status("正在释放组件文件…")
        try:
            self.material(arcs, game)
        except Exception as e:
            return False, "释放组件文件失败：%s" % e, bdir
        for a in arcs:
            done[0] += self.arc_size(a)
            self.ui.progress(done[0], total or 1)
            log("安装  " + a)
        log("组件文件安装完成（%d 个）" % len(arcs))

        # ---------- 校验组件 md5
        for a in arcs:
            exp = self.arc_md5(a)
            if not exp:
                continue
            got = md5_of(os.path.join(game, a.replace("/", os.sep)))
            if got != exp:
                return False, "校验失败：%s md5 不匹配\n请重新下载安装包。" % a, bdir
        log("组件 md5 校验通过")

        # ---------- 汉化：差分重建 datapack
        if want_core:
            ok, msg = self._rebuild_datapack(game, tmp_root, log)
            if not ok:
                return False, msg, bdir
        return True, "安装完成", bdir

    def _rebuild_datapack(self, game, tmp_root, log):
        dp = os.path.join(game, DP_NAME)
        if not os.path.isfile(dp):
            return False, ("未找到 %s。\n请确认游戏目录正确，或先用 Steam「验证游戏文件完整性」修复。"
                           % DP_NAME), None
        size = os.path.getsize(dp)
        if size < NEW_DP_MIN:
            return False, "%s 体积异常（%d B），疑似已损坏。\n请先用 Steam 验证文件完整性。" % (DP_NAME, size), None

        self.ui.status("正在准备汉化数据（此步解包约需 1.4GB 临时空间）…")
        tmp = tempfile.mkdtemp(prefix="obscure_zh_", dir=tmp_root)
        try:
            ex = os.path.join(tmp, "ex")
            base = os.path.join(tmp, "base.hvp")
            newp = os.path.join(tmp, "new.hvp")
            shutil.copy2(dp, base)
            log("临时目录：" + tmp)

            hv = self.open_arc(HV_EXE)
            hv_exe = os.path.join(tmp, HV_EXE)
            try:
                with open(hv_exe, "wb") as o:
                    shutil.copyfileobj(hv, o, 1 << 20)
            finally:
                hv.close()

            lng_p = self.open_arc(HV_LNG)
            zh_lng = os.path.join(tmp, HV_LNG)
            try:
                with open(zh_lng, "wb") as o:
                    shutil.copyfileobj(lng_p, o, 1 << 20)
            finally:
                lng_p.close()
            # v1.00：释放 33 件地图贴图 payload
            tex_arcs = self._map_tex_arcs()
            for a in tex_arcs:
                src = self.open_arc(a)
                dst_p = os.path.join(tmp, a.replace("/", os.sep))
                try:
                    os.makedirs(os.path.dirname(dst_p), exist_ok=True)
                    with open(dst_p, "wb") as o:
                        shutil.copyfileobj(src, o, 1 << 20)
                finally:
                    src.close()
            log("已释放打包工具、中文译文与 %d 件地图贴图" % len(tex_arcs))

            # ---- extract
            self.ui.status("正在解包原版数据包（约 1~3 分钟）…")
            log("解包原版 datapack…")
            r = subprocess.run([hv_exe, "-g", "obscure1", "extract", base, ex],
                               cwd=tmp, capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not os.path.isdir(ex):
                return False, ("解包失败。\n\n可能你的游戏版本与本补丁不匹配。\n"
                               "请用 Steam「验证游戏文件完整性」后重试。\n\n"
                               "输出：\n" + (r.stdout or "")[-400:] + (r.stderr or "")[-400:]), None

            ex_lng = os.path.join(ex, "_texts", "en.lng")
            if not os.path.isfile(ex_lng):
                return False, "解包结果中未找到 _texts\\en.lng，数据包结构不匹配。", None
            ex_tex = os.path.join(ex, MAP_TEX_TARGET.replace("/", os.sep))
            if not os.path.isdir(ex_tex):
                return False, "解包结果中未找到 %s，数据包结构不匹配。" % MAP_TEX_TARGET, None

            # ---- v1.00 基底识别：只认 英文原版 / 已汉化版，其余拒绝并提示
            zh_lng_md5 = self.arc_md5(HV_LNG)
            lng_md5 = md5_of(ex_lng)
            lng_sz = os.path.getsize(ex_lng)
            if lng_md5 == zh_lng_md5:
                base_kind = "已汉化（文本）"
            elif lng_sz == VANILLA_LNG_SIZE:
                base_kind = "英文原版"
            else:
                return False, ("无法识别的数据包版本（en.lng %d B）。\n\n"
                               "本补丁支持：Steam 英文原版 / ODE 英文版 / 本汉化版。\n"
                               "请用 Steam「验证游戏文件完整性」恢复原版后重试。"
                               % lng_sz), None
            log("基底识别：%s（en.lng %d B）" % (base_kind, lng_sz))

            # ---- v1.00 幂等：文本中文 + 33 件贴图全中文 → 跳过重建
            tex_all_zh = True
            for a in tex_arcs:
                rel = a[len(MAP_TEX_DIR) + 1:]
                fp = os.path.join(ex_tex, rel.replace("/", os.sep))
                if not os.path.isfile(fp) or md5_of(fp) != self.arc_md5(a):
                    tex_all_zh = False
                    break
            if base_kind != "英文原版" and tex_all_zh:
                log("数据包已是完整汉化版本（文本+地图贴图），跳过重建")
                self.ui.progress(1, 1)
                return True, ""
            if base_kind != "英文原版":
                log("文本已是中文但地图贴图未汉化 → 继续重建补齐贴图")

            # ---- 替换中文译文
            if lng_md5 != zh_lng_md5:
                log("替换中文译文 en.lng（%d → %d B）"
                    % (lng_sz, os.path.getsize(zh_lng)))
                shutil.copy2(zh_lng, ex_lng)

            # ---- v1.00：替换 33 件地图贴图
            n_tex = 0
            for a in tex_arcs:
                rel = a[len(MAP_TEX_DIR) + 1:]
                dst = os.path.join(ex_tex, rel.replace("/", os.sep))
                if os.path.isfile(dst) and md5_of(dst) == self.arc_md5(a):
                    continue
                shutil.copy2(os.path.join(tmp, a.replace("/", os.sep)), dst)
                n_tex += 1
            log("替换地图贴图 %d/%d 件 → %s" % (n_tex, len(tex_arcs), MAP_TEX_TARGET))

            # ---- create
            self.ui.status("正在重建数据包（约 1~3 分钟，请勿关闭）…")
            log("重建 datapack…")
            r = subprocess.run([hv_exe, "-g", "obscure1", "create", base, ex, newp],
                               cwd=tmp, capture_output=True, text=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0 or not os.path.isfile(newp):
                return False, ("重建失败，原文件未改动。\n\n请用 Steam 验证文件完整性后重试，"
                               "或改用全量安装包。\n\n输出：\n"
                               + (r.stdout or "")[-400:] + (r.stderr or "")[-400:]), None
            nsz = os.path.getsize(newp)
            log("新数据包 %d B" % nsz)
            if nsz < NEW_DP_MIN:
                return False, "重建结果体积异常（%d B），已拒绝写入。" % nsz, None

            self.ui.status("正在写入游戏目录…")
            shutil.copy2(newp, dp)
            log("已写入 " + dp)
            return True, ""
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _backup_dir(self, game, log):
        """备份目录 = 游戏目录\\_obscure_backup（不用时间戳子目录，直接放文件）。
        「只备份首次、不覆盖」由 install() 的 if not os.path.isfile(dst) 保证。"""
        d = os.path.join(game, BACKUP_DIR)
        os.makedirs(d, exist_ok=True)
        return d

    # ---- 卸载
    def uninstall(self, game, log):
        if not is_game_dir(game):
            return False, "目标目录不是 ObsCure 目录。"
        bdir = os.path.join(game, BACKUP_DIR)
        if not os.path.isdir(bdir):
            return False, "未找到备份目录，无法自动还原。\n备份目录名：%s" % BACKUP_DIR
        n = 0
        # 读取新增文件清单（安装前不存在、卸载时要删除的文件）
        added = []
        af = os.path.join(bdir, ADDED_LIST)
        if os.path.isfile(af):
            try:
                with open(af, "r", encoding="utf-8") as f:
                    for x in f:
                        x = x.strip().replace("\\", "/")
                        if x and x not in added:
                            added.append(x)
            except OSError:
                pass
        # 备份文件直接放在 _obscure_backup\ 下（无时间戳子目录）：逐项覆盖回游戏目录
        for root, _d, files in os.walk(bdir):
            for fn in files:
                if fn == ADDED_LIST:
                    continue          # 清单本身不是备份内容
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, bdir)
                dst = os.path.join(game, rel)
                if os.path.isdir(os.path.dirname(dst)):
                    shutil.copy2(src, dst)
                    log("还原  " + rel)
                    n += 1
        # 删除本次新增的文件，使目录回到安装前的干净状态
        for rel in added:
            dst = os.path.join(game, rel.replace("/", os.sep))
            if os.path.isfile(dst):
                try:
                    os.remove(dst)
                    log("删除  " + rel + "（安装前不存在）")
                    n += 1
                except OSError as e:
                    log("删除失败 " + rel + "：" + str(e))
        # 清空并删除备份目录本身（还原后不再保留）
        try:
            shutil.rmtree(bdir, ignore_errors=True)
        except OSError:
            pass
        try:
            os.rmdir(os.path.join(game, BACKUP_DIR))
        except OSError:
            pass
        return True, "已还原 %d 项（覆盖/删除）。" % n


# ================================================================ GUI

def run_gui(inst):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    BG = "#eef1f6"
    CARD = "#ffffff"
    FG = "#1b2030"
    SUB = "#5d667c"
    ACC = "#2f6fed"
    WARN_BG = "#fff8e6"
    WARN_FG = "#7a5b00"

    root = tk.Tk()
    root.title("%s  v%s" % (APP_TITLE, APP_VER))
    root.configure(bg=BG)
    root.resizable(False, False)

    inst.ui = UI(tk, ttk, messagebox, root, BG, CARD, FG, SUB, ACC)

    W = 740
    # ---------------- 顶部标题栏
    top = tk.Frame(root, bg="#1f2a44", height=62)
    top.pack(fill="x")
    top.pack_propagate(False)
    tk.Label(top, text="ObsCure  汉化补丁安装器", bg="#1f2a44", fg="#ffffff",
             font=("Microsoft YaHei UI", 15, "bold")).pack(side="left", padx=20, pady=10)
    tk.Label(top, text="v%s" % APP_VER, bg="#2b3a5c", fg="#9fb4e0",
             font=("Consolas", 10, "bold"), padx=8, pady=3).pack(side="right", padx=20)
    tk.Label(top, text="正式版 · 地图汉化已完成", bg="#1f2a44", fg="#7d8db3",
             font=("Microsoft YaHei UI", 8)).pack(side="right")

    # ---------------- 主体卡片
    card = tk.Frame(root, bg=CARD, padx=22, pady=18,
                    highlightbackground="#d7dce8", highlightthickness=1)
    card.pack(fill="both", expand=True, padx=14, pady=14)

    # --- 1 安装目录
    sec = tk.Frame(card, bg=CARD)
    sec.pack(fill="x")
    tk.Label(sec, text="① 选择安装目录", bg=CARD, fg=FG,
             font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")

    found = detect_steam_dirs()
    var_dir = tk.StringVar(value=found[0] if found else "")
    row = tk.Frame(sec, bg=CARD)
    row.pack(fill="x", pady=(6, 4))
    ent = tk.Entry(row, textvariable=var_dir, font=("Consolas", 9), width=66,
                   relief="solid", bd=1, highlightthickness=0, bg="#f7f8fb")
    ent.pack(side="left", fill="x", expand=True, ipady=4)

    def browse():
        d = filedialog.askdirectory(title="选择 ObsCure 安装目录",
                                    initialdir=var_dir.get() or "C:\\")
        if d:
            var_dir.set(d)
    tk.Button(row, text="浏览…", command=browse, width=9, font=("Microsoft YaHei UI", 9),
              bg="#e6eaf2", fg=FG, relief="flat", bd=0, cursor="hand2").pack(side="left", padx=(8, 0))

    hint = ("自动探测到： " + "   |   ".join(found)) if found else \
           ("未自动探测到，请手动选择：  <Steam库>\\steamapps\\common\\Obscure")
    inst.ui.detect_label(hint, bool(found))

    # --- 2 组件
    sec2 = tk.Frame(card, bg=CARD)
    sec2.pack(fill="x", pady=(14, 0))
    tk.Label(sec2, text="② 选择安装组件", bg=CARD, fg=FG,
             font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")

    box = tk.Frame(sec2, bg="#f7f8fb", highlightbackground="#e0e5f0",
                   highlightthickness=1)
    box.pack(fill="x", pady=(6, 0))
    var_core = tk.IntVar(value=1)
    var_wide = tk.IntVar(value=1)
    var_bak = tk.IntVar(value=1)

    def cb(parent, var, text, desc, first=False):
        f = tk.Frame(parent, bg="#f7f8fb")
        f.pack(fill="x", padx=12, pady=(10 if first else 8, 0), anchor="w")
        row = tk.Frame(f, bg="#f7f8fb")
        row.pack(fill="x", anchor="w")
        tk.Checkbutton(row, variable=var, bg="#f7f8fb", fg=FG, anchor="w",
                       activebackground="#f7f8fb", selectcolor="#f7f8fb",
                       font=("Microsoft YaHei UI", 9), bd=0).pack(side="left")
        tk.Label(row, text=text, bg="#f7f8fb", fg=FG,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        if desc:
            tk.Label(f, text=desc, bg="#f7f8fb", fg=SUB,
                     font=("Microsoft YaHei UI", 8), anchor="w",
                     justify="left").pack(fill="x", padx=(24, 0))

    cb(box, var_core, "汉化核心",
       "version.dll + 图集 + 字表 + 中文译文 + 地图贴图（由你本地原版重建数据包）", first=True)
    cb(box, var_wide, "宽屏修复补丁",
       "dinput8.dll + obscure.asi（第三方 ASI 补丁，可独立开关）")
    cb(box, var_bak, "安装前备份原文件",
       "备份到游戏目录 _obscure_backup\\，可直接删除或用于还原")

    # --- 提示
    note = ("提示：汉化核心的 4 个文件成套生效，缺一不可；宽屏补丁与汉化相互独立。\n"
            "已知限制：请用 Alt-Tab 切屏，不要「最小化→还原」（会触发游戏原生白屏）。")
    tk.Label(card, text=note, bg=WARN_BG, fg=WARN_FG, justify="left",
             font=("Microsoft YaHei UI", 8), wraplength=W - 70, padx=10, pady=7,
             relief="flat").pack(fill="x", pady=(12, 0))

    # --- 进度
    prog = ttk.Progressbar(card, mode="determinate", maximum=100)
    prog.pack(fill="x", pady=(12, 0))
    lbl_status = tk.Label(card, text="就绪", bg=CARD, fg=SUB, anchor="w",
                          font=("Microsoft YaHei UI", 8))
    lbl_status.pack(fill="x", pady=(4, 0))

    txt = tk.Text(card, height=8, font=("Consolas", 8), relief="flat", bd=0,
                  bg="#151a24", fg="#c8d0e0", padx=8, pady=6, wrap="none",
                  highlightthickness=0)
    txt.pack(fill="x", pady=(8, 0))

    # --- 按钮
    btns = tk.Frame(card, bg=CARD)
    btns.pack(fill="x", pady=(14, 0))
    state = {"busy": False}

    def set_busy(b):
        state["busy"] = b
        for w in (b_ok, b_un):
            w.configure(state="disabled" if b else "normal")

    def on_install():
        if state["busy"]:
            return
        d = var_dir.get().strip().strip('"')
        if not d:
            messagebox.showwarning("请选择目录", "请先选择 ObsCure 安装目录。", parent=root)
            return
        if not var_core.get() and not var_wide.get():
            messagebox.showwarning("未选择组件", "请至少勾选一个安装组件。", parent=root)
            return
        if not var_core.get():
            if not messagebox.askyesno(
                    "仅安装宽屏补丁",
                    "你未勾选「汉化核心」。\n\n"
                    "这只会安装第三方宽屏修复补丁，游戏仍为英文原版。\n\n是否继续？",
                    parent=root):
                return
        if not messagebox.askyesno(
                "确认安装",
                "目标目录：\n%s\n\n%s\n\n汉化数据包将在本机重建（约需 1~4 分钟，\n"
                "期间请勿关闭本程序，磁盘需约 1.4GB 临时空间）。\n\n开始安装？"
                % (d, "汉化核心 + 宽屏补丁" if (var_core.get() and var_wide.get())
                   else ("汉化核心" if var_core.get() else "宽屏补丁")),
                parent=root):
            return

        txt.delete("1.0", "end")
        t0 = time.time()
        try:
            tmp_root = os.path.dirname(d.rstrip("\\")) or tempfile.gettempdir()
            if not os.access(tmp_root, os.W_OK):
                tmp_root = tempfile.gettempdir()
            ok, msg, bdir = inst.install(d, bool(var_core.get()), bool(var_wide.get()),
                                         bool(var_bak.get()), tmp_root)
        except Exception as e:
            ok, msg, bdir = False, "安装过程中发生异常：\n%s" % e, None
        dt = time.time() - t0
        if ok:
            inst.ui.progress(1, 1)
            inst.ui.status("完成，用时 %.1f 秒" % dt)
            inst.ui.log("★ 安装完成（%.1f 秒）" % dt)
            messagebox.showinfo(
                "安装成功",
                "汉化补丁安装完成！\n\n"
                + ("原文件已备份到：\n%s\n\n" % bdir if bdir else "")
                + "接下来：\n"
                "1. 通过 Steam 启动 ObsCure\n"
                "2. 语言选择 English（汉化在渲染层生效）\n\n"
                "如需还原，重新运行本程序点「卸载 / 还原」。", parent=root)
        else:
            inst.ui.progress(0, 1)
            inst.ui.status("失败")
            inst.ui.log("✗ " + msg.replace("\n", "\n  "))
            messagebox.showerror("安装失败", msg, parent=root)

    def on_uninstall():
        if state["busy"]:
            return
        d = var_dir.get().strip().strip('"')
        if not d:
            messagebox.showwarning("请选择目录", "请先选择目录。", parent=root)
            return
        if not messagebox.askyesno("确认还原",
                                   "将用备份覆盖回原文件：\n%s\n\n是否继续？" % d, parent=root):
            return
        txt.delete("1.0", "end")
        set_busy(True)
        try:
            ok, msg = inst.uninstall(d, inst.ui.log)
        except Exception as e:
            ok, msg = False, str(e)
        set_busy(False)
        if ok:
            messagebox.showinfo("还原完成", msg, parent=root)
        else:
            messagebox.showerror("还原失败", msg, parent=root)

    b_ok = tk.Button(btns, text="开始安装", command=on_install, width=14,
                     font=("Microsoft YaHei UI", 10, "bold"), bg=ACC, fg="white",
                     activebackground="#2457c4", activeforeground="white",
                     relief="flat", bd=0, cursor="hand2", pady=7)
    b_ok.pack(side="left")
    b_un = tk.Button(btns, text="卸载 / 还原", command=on_uninstall, width=12,
                     font=("Microsoft YaHei UI", 9), bg="#e6eaf2", fg=FG,
                     activebackground="#d8dde8", relief="flat", bd=0,
                     cursor="hand2", pady=7)
    b_un.pack(side="left", padx=(10, 0))
    tk.Button(btns, text="退出", command=root.destroy, width=8,
              font=("Microsoft YaHei UI", 9), bg="#e6eaf2", fg=SUB,
              relief="flat", bd=0, cursor="hand2", pady=7).pack(side="right")

    inst.ui.bind(txt, prog, lbl_status)
    # 启动即弹出强制模态声明（类似安装软件的使用说明），确认后才进入安装流程
    if not show_disclaimer(root):
        root.destroy()
        sys.exit(0)
    root.mainloop()


class UI(object):
    def __init__(self, tk, ttk, messagebox, root, BG, CARD, FG, SUB, ACC):
        self.tk, self.ttk, self.root = tk, ttk, root
        self.txt = self.prog = self.lbl = None
        self.det = None

    def bind(self, txt, prog, lbl):
        self.txt, self.prog, self.lbl = txt, prog, lbl

    def detect_label(self, text, ok):
        if self.det is None:
            self.det = self.tk.Label(text=text, bg="#f7f8fb", fg="#5d667c",
                                     font=("Microsoft YaHei UI", 8),
                                     anchor="w", justify="left", wraplength=660)
            self.det.pack(fill="x", pady=(4, 0))
        else:
            self.det.configure(text=text, fg="#2e7d4f" if ok else "#8a6d1f")

    def log(self, s):
        if self.txt is None:
            return
        self.txt.insert("end", s + "\n")
        self.txt.see("end")
        self.root.update_idletasks()

    def progress(self, cur, total):
        if self.prog is None:
            return
        self.prog["value"] = (cur / float(total) * 100.0) if total else 0
        self.root.update_idletasks()

    def status(self, s):
        if self.lbl is not None:
            self.lbl.configure(text=s)
            self.root.update_idletasks()



def show_disclaimer(root):
    """主界面之上强制模态弹窗（类似安装软件的使用说明/EULA），确认后才能安装。
    返回 True=确认继续；False=退出。
    测试逃生口：设置环境变量 OBSCURE_SKIP_DISCLAIMER=1 可跳过（仅用于自动化）。"""
    if os.environ.get("OBSCURE_SKIP_DISCLAIMER") == "1":
        return True
    import tkinter as tk
    d = tk.Toplevel(root)
    d.title("免责声明 · 请仔细阅读")
    d.attributes("-topmost", True)
    d.minsize(600, 480)
    bar = tk.Frame(d, bg="#2b3a5c")
    bar.pack(fill="x")
    tk.Label(bar, text="使用本汉化补丁前，请务必阅读以下声明", bg="#2b3a5c",
             fg="#ffffff", font=("Microsoft YaHei UI", 11, "bold"),
             padx=14, pady=9).pack(side="left")
    frm = tk.Frame(d)
    frm.pack(fill="both", expand=True, padx=14, pady=10)
    txt = tk.Text(frm, wrap="word", font=("Microsoft YaHei UI", 10),
                  padx=10, pady=10, relief="flat", bg="#fffdf6", fg="#1b2030",
                  width=44, height=16)
    sb = tk.Scrollbar(frm, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    txt.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    txt.insert("1.0", DISCLAIMER_TEXT)
    txt.config(state="disabled")
    state = {"v": False}

    def yes():
        state["v"] = True
        d.grab_release()
        d.destroy()

    def no():
        state["v"] = False
        d.grab_release()
        d.destroy()

    btns = tk.Frame(d, bg="#eef1f6")
    btns.pack(fill="x", padx=14, pady=(0, 12))
    tk.Button(btns, text="确认", command=yes,
              bg="#2f6fed", fg="#ffffff", font=("Microsoft YaHei UI", 10, "bold"),
              padx=26, pady=6, relief="flat").pack(side="right", padx=6)
    tk.Button(btns, text="退出", command=no,
              bg="#e6e9f0", fg="#5d667c", font=("Microsoft YaHei UI", 10),
              padx=26, pady=6, relief="flat").pack(side="right")
    d.update_idletasks()
    d.geometry("640x520")   # 控件构建后强制尺寸
    d.grab_set()
    root.wait_window(d)
    return state["v"]


def main():
    try:
        inst = Installer()
    except SystemExit as e:
        ctypes.windll.user32.MessageBoxW(None, str(e), APP_TITLE, 0x10)
        return 1
    run_gui(inst)
    return 0


if __name__ == "__main__":
    sys.exit(main())