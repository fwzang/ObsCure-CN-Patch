# 产线工具清单（v1.01 · 2026-10-26）

所有脚本用 **托管 Python** 跑（装了 PIL / capstone / zlib）：
```
C:\Users\fwzang\.workbuddy\binaries\python\envs\default\Scripts\python.exe <脚本> [参数]
```
（Windows venv 的可执行文件在 `Scripts\`，不是 `bin\`）

---

## 一、本轮新增（cave 改造 / 诊断 / 安装器）

| 脚本 | 用途 | 注意 |
|---|---|---|
| `verify_mask_state.py` | **发布前必跑**：校验「切屏黑屏遮罩」是否开启（裸 DLL / 安装器 exe / zip 都支持） | 判据 = `version.dll` file `0x37954` 为 `73 24`；`EB 24` = 遮罩被关，禁止发布 |
| `analyze_dump.py` | 解析 `.mdmp` 崩溃转储：异常码 / 读写方向 / 目标地址 / 出错模块 / 模块内偏移 / 对应文件偏移 / 是否 ASLR | **崩溃先跑它** |
| `patch_installer_101.py` | 把 O1SETUP2 安装器内嵌组件更新到指定版本并复核全量 crc | 只在同尺寸时替换；会先复制副本 |
| `build_cave_drawscope_v2.py` | v2：PIC 版 draw-scope（**文字全丢，已废**） | 仅留档 |
| `build_cave_v4.py` | v4：DP 入口自判 + 改纹理（**文字全丢，已废**） | 仅留档 |
| `build_cave_v5.py` | v5：DP 入口委托 watchdog_swap（**文字全丢，已废**） | 仅留档 |
| `build_cave_v6.py` | v6：前哨 thunk（未装机验证） | 仅留档 |
| `build_clearflash9_arm.py` | 保底版所用补丁的构建器（首屏武装 + 阈值 3→20） | 产物 = `0c17dfc0` |

## 二、图集生成（唯一真源）

| 脚本 | 用途 |
|---|---|
| `make_atlas_retail.py` | **正式图集生成器**（当前颜色纯白，24px 汉字 + 20px 端黑拉丁） |
| `make_atlas_border_test.py` | 边框三方案 A/B（V1 投影 / V2 硬描边 / V3 光晕） |
| `make_atlas_ascii_test.py` | 英文统一三方案（V4a 原位拟合 / V4b 20px 自然字距 / V4c 汉字式字距） |
| `make_atlas_combo_v1_v4b.py` | 组合版（V1 投影 + V4b 英文），即保底版图集 |

> 图集写入纪律：tile 必须按 512 行距 `paste`，**禁止**连续字节切片写入（会水平涂抹）。

## 三、文本 / 数据包

| 脚本 | 用途 |
|---|---|
| `hvp_toc.py` | 列出 `.hvp` 内部条目（TOC） |
| `extract_steam_en.py` / `extract_retail_zh.py` | 从英文 / 官方中文数据包提取文本 |
| `build_zh_text.py` / `build_zh_lng.py` | 生成译文与 `.lng` 记录 |
| `inject_lng.py` / `merge_retail_official.py` | 注入译文 / 合并官方文本 |
| `apply_glossary.py` | 术语表统一 |
| `fix_nontext_entries.py` / `FINAL_PARSE_FIXED.py` | 修正非文本条目与解析 |
| `export_variants.py` | 导出对照变体 |

## 四、校验 / 注入 / 其他

| 脚本 | 用途 |
|---|---|
| `full_verify.py` / `d2_verify.py` / `verify_alignment.py` | 图集与表对齐校验 |
| `exe_addsec.py` / `exe_extend.py` | PE 加节 / 扩展（cave 承载研究用） |
| `fastinject.py` / `inject.c` 相关 | 进程注入实验 |
| `log_watch.py` / `test3_watch.py` | 日志/时序观察 |
| `ginput.py` | 手柄输入探测 |
| `工具二进制_hvptool/` | `hvptool.exe` + `zlib.dll`（HVP 解包/打包，**破坏性操作前必须备份并校验**） |

---

## 五、下次开工的正确顺序

1. **先读** `03_文档/报告_2026-10-26/技术死结论报告_最终核验_2026-10-26.md`
   —— 里面写清了当前机制为什么没有还原路径，以及哪些结论是证据、哪些还是疑点。
2. 要动 `version.dll` 前，先确认 `01_安装包` 与 `04_基线与回滚` 都是可回滚状态。
3. 想推进"无缝"研究，**不要**再从猜返回地址 / 手写机器码入手；
   先装 **JDK + Ghidra**，对 `Obscure.exe` 建分析库，做报告里的 F3（定位文字绘制批次与字体对象持有者）。
4. `06_独有资产/ghidra反编译_2026-10-26/` 里已有 hook 侧函数反编译文本，
   `func_10006270.txt` 就是 `watchdog_swap`（死代码结论的来源）。

---

## 六、环境提醒

- 本机**没有** gcc / cl / winget / choco；`ghidra_proj2` 只有项目目录，**没有 Ghidra 程序与 Java**
- 检查 `D:/Basic` 等路径务必用**正斜杠**（Git Bash 会吞反斜杠）
- 写 bat 一律纯 ASCII + CRLF；**`echo ... -> 路径` 里的 `>` 是重定向符**，会覆盖文件
