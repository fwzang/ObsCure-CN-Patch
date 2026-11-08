# Obscure 简体中文本地化 — 技术归档

> 最后更新：2026-10-06 18:40
> 用途：让任何人（或未来的自己）在 10 分钟内理解现状、复现成果、继续推进。

---

## 一、项目目标与当前状态

**目标**：把 Steam 版《Obscure》（英文版，已有社区西语/葡语翻译）完整汉化为简体中文。

**当前状态**：
- 文本层：✅ 100% 完成（3078 条记录，2893 条有中文译文，存于 `汉化素材/英文文本/en_lng_对照.tsv`）
- 引擎字体系统：✅ **已完全破解**
- 中文渲染：✅ **已打通并验证**（游戏画面上能显示汉字）
- 界面汉化：🟡 **62 条 / 115 字**（主菜单、设置、难度、退出确认、读档、暂停、物品栏等）
- 剧情正文汉化：🔴 未做（缺字形容量）

**可玩产物**：`hzp_tools/out/fallback_ui115/`（3 个游戏文件 + 构建脚本），备份后覆盖到游戏根目录即可。

---

## 二、★ 核心发现：引擎的字体系统

### 2.1 关键结论

**字体不是 16×16 网格，而是「比例字体 + 逐字符字形表」。**
（早期所有基于网格的假设都是错的，这条推翻了前 20 轮的方向。）

### 2.2 字体对象结构（exe 内，共 5 组，每组 56 字节）

```
+0x00  u32  字形表指针        ← 指向 exe .data 段
+0x04  u32  记录数
+0x08  u32  图集宽（像素）
+0x0C  u32  图集高（像素）
+0x10  u32  字形高（像素）    ← 引擎按 [y, y+此值] 采样
+0x14  u32  行高
+0x28  std::string 字体名（实测不用于纹理查找）
```

### 2.3 字形表格式（**最重要的收获**）

连续 **10 字节/条**：
```
[u16 字符码][u16 x][u16 y][u16 宽][u16 步进]
```
**字符码是 u16 → 原生支持任意 Unicode 码点。只要往表里加记录，中文就能渲染。**

### 2.4 五张表的位置（原版 `Obscure.exe`）

| 组 | 名字 | 表 VA | 表文件偏移 | 记录数 | 图集 | 字形高 |
|---|---|---|---|---|---|---|
| grp1 | font_holstein24 | 0x696380 | `0x293F80` | 150 | 512×128 | 24 |
| grp2 | font_holstein30 | 0x696960 | `0x294560` | 71 | 512×128 | 35 |
| grp3 | lucida10 | 0x696C28 | `0x294828` | 150 | 512×128 | 39 |
| grp4 | buttons_360 | 0x696E58 | `0x294A58` | 12 | 128×16 | 13 |
| grp5 | holstein16 | 0x696ED0 | `0x294AD0` | 28 | 256×256 | 32 |

第 0 条记录就是空格（无独立表头）。例：
`'A' = (65, 2, 0, 10, 11)`、`'I' = (73, 92, 0, 4, 5)`、`'M' = (109, 412, 0, 13, 14)`

### 2.5 初始化代码（改这些立即数）

exe 文件偏移：
```
grp1: 宽 0x5AB7B  高 0x5AB82  表指针 0x5AB89  记录数 0x5AB90  名字串 0x5AB71
grp2: 宽 0x5ABBB  高 0x5ABC5  表指针 0x5ABCC
grp3: 宽 0x5AC0C  高 0x5AC16  表指针 0x5AC1D
grp5: 宽 0x5B8C8  高 0x5B8CC  表指针 0x5ACDE
```
函数入口在文件偏移 `0x5AB70` 附近（VA = 文件偏移 + 0xC00）。

### 2.6 图集来源

- **界面/正文全部用 grp1**，图集是 **`cachpack.hvp` 的 `textures/font_holstein16.tga`**（512×128）
- 改 `datapack` 里的 `font_holstein24.tga` **对界面无效**（已实测）
- 改 exe 里的字体名字符串（如改成 `art_101.tga`）**无效**（引擎不按名字找纹理）

### 2.7 已证伪的两条路线

| 路线 | 实验 | 结论 |
|---|---|---|
| 字符串内切换字体 | 探针 `A FE01 A FE03 A...` | 显示 6 个 A，FE0x 码只被丢弃，**不能切字体** |
| 多字体叠加容量 | 4 张表的 `'F'` 分别改指向 Q/R/S/Z | 设置界面 `FÁCIL`→`QÁCIL`，**只用 grp1** |

---

## 三、cachpack 字体资源的硬约束

`cachpack.hvp` 的 `font_holstein16.tga` 目录项（由**前一条** `fgun.bmp` 的 25 字节 meta 描述）：
```
meta[11:13] = csize = 32133 (u16)
meta[13:17] = usize = 262188 (=512×128×4+44)
meta[21:25] = 绝对偏移 = 0x15B0B43
```

实测：

| 操作 | 结果 |
|---|---|
| 原地等长替换压缩数据 | ✅ 正常（压缩流 + 0x00 补齐，引擎不校验那个神秘字段） |
| 改 usize（放大图集） | ❌ 崩溃 0xC0000005 |
| 改 offset（搬到包内 128KB 空闲区） | ❌ 黑屏 |
| 追加数据使文件变大 | ❌ 崩溃 |
| `paktool` 打包 cachpack | ❌ 静默不生效（flat 目录结构不匹配） |

→ **图集被锁死在 512×128 = 65536 像素**。

---

## 四、容量与字号权衡（实测数据）

保留 88 条 ASCII + 12 个西语重音（牺牲其余 50 条记录）后，24px 行带下的真实容量：

| 汉字点阵 | 容量 | 观感 |
|---|---|---|
| 16px | 79 | 清晰 |
| 12px | 104 | 清晰 |
| **11px** | **115** | **清晰 ✅ 采用** |
| 10px | 124 | 明显发糊 ❌ |

完整汉化需要 **1308 个不同汉字** → 差 11 倍。这就是必须走 D3D9 Hook 的原因。

**排版要点**：字形高=24，引擎按 `[y, y+24]` 采样，所以
1. 汉字必须按 **24px 行带**间隔（不能 12px 交错，否则互相串扰）
2. 字形放在行带**下半部分**，并**清空整个 24px 行带** → 否则下半格残留旧字形形成「倒影」

---

## 五、已完成的汉化界面（62 条）

| 类别 | 记录号 |
|---|---|
| 主菜单 | 1901 开始新游戏 / 1902 读取进度 / 1903 游戏设置 / 1956 奖励内容 / 2000 成就一览 / 1915 退出游戏 |
| 设置页 | 1904 亮度 / 1905 音效 / 1906 单声道 / 1910 振动 / 1911 字幕 / 1912 手柄 / 1960 应用并退出 / 1989 控制器设置 / 1996 保存新设置？ / 1999 载入设置 |
| 难度页 | 1951 难度 / 1952 简单 / 1953 普通 / 1954 困难 |
| 退出确认 | 1916 确定要退出吗？ / 1917 是 / 1918 否 / 1908 是 / 1909 否 |
| 读档 | 1919 读取 / 1920 记忆卡槽位一 / 1921 记忆卡槽位二 / 1961 暂无存档 / 69 请选择记忆卡槽位 / 42 读取中 / 43 读取成功 / 44 读取失败 / 46 保存成功 / 47 保存失败 / 48 保存进度？ / 80 读取进度？ |
| 记录页 | 1992 行动记录／队友 / 1993 战斗记录 / 1994 其他 / 1995 按任意键… / 1997 默认控制 / 1998 保存设置？ |
| 手柄设置 | 2002 选择手柄 / 2004 游戏手柄 |
| 暂停/物品栏 | 1913 暂停 / 1914 继续游戏 / 1939 暂停 / 1930 回到集合点 / 134 暂停／取消 / 1941 取消／切换目标 / 1947 物品栏 / 1948 武器栏 / 144 武器菜单 / 151 换弹 / 405 换弹 / 407 保存 |
| 游戏内 | 393 跟我来 / 141 跟我来！ / 395 设置 / 555 地下室 / 1982 按 X 返回主菜单 |

**保留的西语重音**（12 个，正文需要）：`¡®¿ÃÐ×áéíñóú`

---

## 六、核心操作手册

### 6.1 重新构建汉化补丁

```bash
cd /e/SteamLibrary/steamapps/common/Obscure

# 1) 生成字库+字形表+文本补丁（CSZ 控制字号）
CSZ=11 "C:/Users/fwzang/.workbuddy/binaries/python/envs/default/Scripts/python.exe" hzp_tools/cn_ui.py

# 2) 打包 datapack
cd hzp_tools/out/hvptool_test
cp ../../datapack.hvp.bak ./ui11.hvp
MSYS_NO_PATHCONV=1 ./hvptool.exe /P "E:\\...\\ui11.hvp" "E:\\...\\patch_ui2"
cd ../.. && cp hzp_tools/out/hvptool_test/ui11.hvp datapack.hvp
```
`cn_ui.py` 会直接改写 `Obscure.exe` 和 `cachpack.hvp`（字库），无需额外步骤。
输出会报告「需要 X 字 / 放置 Y 个 / 未放下 [...]」。

### 6.2 自动操作游戏

```python
import sys; sys.path.insert(0, r'E:\SteamLibrary\steamapps\common\Obscure\hzp_tools')
import ginput
ginput.key('DOWN')      # 方向键
ginput.key('ENTER')     # 回车
ginput.click(960, 716)  # 鼠标点击(需 1536x864 窗口)
ginput.shot('tag')      # 截图
```

**⚠️ 关键**：DirectInput 读**扫描码状态**，必须用 `SendInput` + `KEYEVENTF_SCANCODE`。
普通 `keybd_event` 无效。必须 `SetForegroundWindow` + `SetFocus` + 校验 `GetForegroundWindow()`。

游戏分辨率会变（1536×864 / 1280×720），截图裁剪要用**相对坐标**。

### 6.3 Steam 版键位

界面：方向键 + Enter
游戏内：WASD 移动 / Space 奔跑 / Enter 动作确认 / E 武器栏 / Q 物品栏 / P 地图 / F 切换角色 / R 换弹匣 / Esc 暂停 / Backspace 切换目标 / 右Shift 缓步

---

## 七、工具链

### 7.1 编译环境（原本系统无 C 编译器）

```bash
pip install ziglang
python -m ziglang cc -target x86-windows-gnu -O2 -fno-sanitize=undefined -shared -o x.dll x.c
```
`-fno-sanitize=undefined` **必须加**，否则 zig 内部构建 libubsan 会 AccessDenied 失败。

### 7.2 已构建的注入工具（`hzp_tools/hook/`）

| 文件 | 说明 | 状态 |
|---|---|---|
| `inject.exe` | 32 位 DLL 注入器 | ✅ **必须用这个**（64 位 Python 取的 kernel32 地址在 WOW64 目标里是错的） |
| `launchinject.exe` | 挂起启动+注入+恢复 | ⚠️ 打到的是短命启动器进程 |
| `fastinject.py` | 进程轮询注入（找 Steam 拉起的真游戏） | ✅ |
| `modlist.py` | 枚举进程已加载模块 | ✅ |
| `d3dprobe.c/.dll` | D3D9 IAT hook + Reset vtable 探测 | ✅ 注入成功，IAT 补成功 |
| `binkw32_o.dll` | 原始 binkw32 备份 | ✅ |
| `binkhook.c` | binkw32 转发包装 | ❌ 14 个 `_Bink*` 导出未生成，会导致**游戏启动即退出** |

### 7.3 ⚠️ 关键发现：游戏不走 exe 的 IAT 创建 D3D 设备

- 进程模块表确认已加载：`d3d9.dll` (base `0x70700000`)、`nvd3dum.dll`(NVIDIA D3D UM)、`DINPUT8.dll`、`XINPUT1_3.dll`
- 在进程出现后 **1.59 秒**注入，IAT 补成功（`0x70764B20 -> 0x703D1000`）
- **但 `Direct3DCreate9` 从未被调用**
→ exe 的 `d3d9!Direct3DCreate9` 导入项**不是真正的创建路径**。
**在 IAT 上 hook / 下断是无效的**，必须改用：
- **方案 A**：`VirtualProtect` + 5 字节 `E9` inline hook `d3d9!Direct3DCreate9` 本体（需写 trampoline）
- **方案 B（风险最低）**：inline hook `LoadLibraryA/W` + `GetProcAddress`，打印调用者地址，
  定位到底谁动态取 `Direct3DCreate9`

### 7.4 注入链路的 5 个坑（已全部解决）

1. 游戏**自重启**：Steam 会拉起另一个 Obscure.exe，必须轮询找 parent=16316 的那个
2. 注入器必须 **32 位**（WOW64 下 kernel32 基址不同）
3. DLL 里的 `FILE*` 一直开着 → 游戏运行期间读日志报 Permission denied，**先 taskkill 再读**
4. DLL 必须保留 `DllMain` 入口，否则注入成功但 hook 从不初始化
5. 进程刚创建时加载器锁被占 → 注入要延迟 ~0.3s 并重试

---

## 八、待办（按优先级）

### P0 — 补齐剩余西语
1. **读档界面的道具名列**（`CASCO / CINTA / ESTRELLA / ANILLO / FOTOGRAFÍA / SIGUIENTE NEGRO / ROBOT / AGENDA / recording / SCHOOL PRESENTE…`）
   → 在 sp.lng 里不是独立短记录，需重新定位（可能拼接了长描述或来自其他记录区间）
2. **`CARGANDO` 加载画面** → 5 个语言文件全文搜索无此文本，判定是**预渲染贴图**，需定位并替换对应 TGA/BMP

### P1 — 突破字库容量（唯一能实现完整汉化的路）
**D3D9 纹理 Hook**：拦截 `IDirect3DDevice9::CreateTexture`，把 512×128 的字体纹理换成 2048×512，容量扩到 2600+ 汉字。

未解决的两个卡点：
1. `Direct3DCreate9` 的 IAT hook 成功（`0x70764B20 -> 0x705F12F0`），但**该函数从未被调用** → 游戏走的是别的 D3D 创建路径
   → 建议：改成遍历已加载模块 hook `d3d9.dll` 本体，或从 `Reset` 之后的 vtable 反查
2. 若用「替换 binkw32.dll」保证早于 D3D 加载，需修好 14 个 `_Bink*` 导出
   → zig 下 `__declspec(dllexport)` 放在宏里不生效，需用 `__asm__("_BinkOpen@8")` 重命名导出名
   → **注意：替换失败会导致游戏启动即退出**

### P2 — 玩法验证
- 启动新游戏，确认游戏内文字（对话/提示）是否也走 grp1（目前证据表明是）
- 确认 62 条汉化在 1280×720 下都清晰

---

## 九、★ 不可重犯的教训

1. **别再逆向包体格式**。cachpack 的 usize/offset 一改就崩/黑屏，投入产出比极低。
2. **别再假设字形是网格**。这是比例字体 + 字形表，网格假设会让所有容量估算错误。
3. **改 exe 不能放进 `.data` 的 BSS 区**。必须**新增独立 PE 节**（`.wfont`）；放 BSS 会被游戏全局变量覆盖，表现为菜单渲染错乱（如 `PARTIDA NUEVA`→`PARIDANEA`）。
4. **IAT hook 读名字必须用 `OriginalFirstThunk`**，写才用 `FirstThunk`。用错会「找不到函数」。
5. **DirectInput 必须注入扫描码**（`SendInput`+`KEYEVENTF_SCANCODE`），`keybd_event` 无效。
6. **PIL truetype 文字会溢出指定格子**，必须先渲染到独立小图再 `paste`。
7. **空闲区判断用 `any(occ)` 不是 `all(occ)`**，用错会覆盖已有拉丁字形。
8. **TGA `desc=0x08` 表示上下翻转**，文件行 0 = 图集底部。填字形要按 `file_y = H-1-atlas_y` 换算。
9. 改包前先备份，改完**逐项验证 + 跑一次游戏**。`taskkill` 强杀会产生 mdmp，**不是游戏崩溃**。

---

## 十、文件索引

```
hzp_tools/
├── cn_ui.py                    ← 【主构建脚本】字库+表+文本一条龙
├── ginput.py                   ← 游戏自动操作（键盘/鼠标/截图）
├── cn_layout.py                ← 排版算法
├── cap_scan.py                 ← 容量扫描
├── build_font.py               ← 早期官方字库分析（参考）
├── FINAL_PARSE_FIXED.py        ← 语言文件官方结构解析器
├── write_minimal.py            ← HVP 最小改动写入器
├── hvptool_test/hvptool.exe    ← HVP 打包工具（只支持 datapack）
├── hook/                       ← D3D9 Hook 工具链
└── out/
    ├── Obscure.exe.bak         ← 原版 exe
    ├── datapack.hvp.bak        ← 原版数据��
    ├── fallback_ui115/         ← ★ 当前可用汉化成品
    ├── fallback_ui101/         ← 上一版（12px/101字/54条）
    └── fallback_menu/          ← 最初版（16px/78字）
```

---

## 十一、一句话总结

> 引擎字体系统已彻底破解（比例字体 + u16 字符码的逐字符字形表，可扩展）；
> 中文已在游戏内成功显示；界面已汉化 62 条。
> **唯一瓶颈**：字体图集被 cachpack 锁死在 512×128（最多约 115 个清晰汉字），
> 完整汉化需 1308 字 → **必须走 D3D9 纹理 Hook 扩容**。
