# 安装包目录结构（v1.02 · 单文件自足）

```
01_安装包/
├─ 精简版/
│   ├─ ObsCure汉化安装器_v1.02.exe      ← 就是它，双击即可（34.55 MB）
│   ├─ DISCLAIMER.txt                    免责声明原文（GBK，供查看）
│   └─ 安装说明.txt
├─ 加强版/
│   ├─ ObsCure加强汉化版安装器_v1.02.exe ← 双击即可（1.56 GB）
│   ├─ DISCLAIMER.txt
│   └─ 安装说明.txt
├─ _历史版本/
│   ├─ ObsCure汉化安装器_v1.01.exe              （即时回退用；v1.00 已删除）
│   └─ ObsCure加强汉化版安装器_v1.01.exe
├─ 免责声明.txt      （装完释放到游戏目录的那份，UTF-8 BOM）
└─ README_安装包结构.md（本文件）
```

> **v1.02 起不再需要任何 bat / ps1**：所有行为都编译进 exe 了。
> 之前那两个包装脚本（`一键安装并自动退出.bat`、`图形界面安装-装完自动关闭.bat`
> 及对应 `install_auto.ps1` / `gui_auto.ps1`）已全部删除。

## 一、exe 里已经内置的行为（v1.02 重编译）

| 行为 | 实现位置 |
|---|---|
| 点完免责声明确认 → 主界面**直接弹到最前** | `ShowWindow` 后 `BringWindowToTop` + `SetForegroundWindow` + `SetWindowPos(HWND_TOPMOST,…)` |
| 装完提示框 **3 秒自动关闭 → 程序直接退出** | `MessageBoxTimeoutW(…,3000)` + `PostMessageW(WM_CLOSE)` |
| **装完自校验 4 个组件 MD5** | `check_components()`：结果显示在完成提示、明细进 `%TEMP%\O1Setup.log` |
| 免责声明全文 v1.02 | `info_o1.txt` → `gen_info.py` → `info_o1.h` → 编译进 exe |

日志：`%TEMP%\O1Setup.log`，实测末尾：

```
[bin] files=6
[chk] version.dll     = 0c17dfc07b666255b04652dbf0cbe541 OK
[chk] dinput8.dll     = b8c51891352e3e7bfc2c30f2c903b46c OK
[chk] big_atlas_c.raw = 99a1244494b42d9cb9572e92335720ff OK
[chk] table_c.bin     = 6a498e9d8478b48ff9b3dc0e29f5c183 OK
[chk] bad=0
```

## 二、命令行开关（可选，不用也行）

```bat
<exe> /test:游戏目录      静默安装（无窗口，装完自己退出）
<exe> /restore:游戏目录   静默还原
```

注意：安装器**不剥离路径引号**，游戏目录不能含空格。

## 三、O1SETUP2 载荷格式（维护者参考）

```
footer（贴文件尾，共 8080 字节）
  "O1SETUP2"(8) + ver u32 + n u32 + n×84 + 零填充
  record = name[64] + off u64 + size u64 + crc u32     stride = 84
  crc = zlib.crc32(blob)      off = blob 在 exe 内的文件偏移
  ver = 2 是【格式版本】，不是显示版本
```

- 替换内嵌组件：同尺寸 blob 就地替换 + 改该条 crc（`02_源码/产线工具/patch_installer_101.py`）
- 换骨架重挂载荷：`02_源码/产线工具/repack_installer.py`（自动算 delta、平移所有 off、crc 不变）
- 当前条目：精简 **41**（bin 6 / rb 34 / tool 1）、加强 **61**（bin 6 / ode 19 / rb 34 / tool 2）

## 四、v1.02 相对 v1.01 改了什么

| 项目 | v1.01 | v1.02 |
|---|---|---|
| 内嵌组件 | 保底版四件 | 保底版四件（未变） |
| 主界面层级 | 可能排在后面 | **置顶弹出** |
| 装完退出 | 需手动点「退出」 | **提示 3 秒后自动退出** |
| 装完校验 | 靠外部 bat | **exe 内自校验** |
| 游戏说明 | 三处不一致（v1.00/1.00/1.01） | **三处统一 v1.02** |
| 包装脚本 | 有 bat / ps1 | **无（已删除）** |
| 版本 | 1.0.0.1 | **1.0.0.2** |

## 五、指纹

| 文件 | 大小 | MD5 |
|---|---|---|
| `精简版/ObsCure汉化安装器_v1.02.exe` | 36,227,148 | `9280e6508e1f2098241b56bc4e6967d8` |
| `加强版/ObsCure加强汉化版安装器_v1.02.exe` | 1,680,524,307 | `70844a6a2604702d297392b995b22260` |
