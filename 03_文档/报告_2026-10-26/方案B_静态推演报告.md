# 方案 B 静态推演报告（StateBlock::Apply 拦截）
生成：2026-10-26 01:58 ｜ 状态：**只做推演，未编译、未装机**
游戏目录当前 = 稳定保底版 V1.0（`0c17dfc0`），可玩。

---

## 0. 三条结论（先说最重要的）

1. **必须订正我上一轮的判断**：v2 失败**不是**"draw-scope 原理不成立"，而是我把
   `g_fontTex` 与 `g_bigTex` 两个全局的地址**取反了**。PDB 符号表给出权威值：
   `g_fontTex = 0x10041A74`、`g_bigTex = 0x10041A70`（我按 0x41A70=fontTex 写的）。
   所以 v2 干的事是：**看到大图集绑着（正常状态）→ 换成游戏的小图集**，参数又是 4096
   → 采样越界 → 文字消失/压扁。症状与你的描述完全吻合。
   **draw-scope 没有被证伪，是我的实现把方向搞反了。**

2. **方案 B 的"重绑"动作，当前保底版里已经在跑**：`watchdog_swap`（RVA 0x6270）
   在 Present 里执行「TestCooperativeLevel → 若 stage0 == 引擎字体纹理 → 重绑大图集 +
   同帧补参数」。只是**粒度是帧**：恢复期每 2 帧一次，稳定态每 600 帧一次。
   所以你要的"同帧修复"，本质是把这段逻辑从 Present 提前到 Apply 返回点。

3. 但**有比 Apply 拦截更小、同帧、且不动钩子链的做法**：把这套判断搬进
   **已经存在的 DrawPrimitive 钩子**（文字只走 DP，实测 82/83/84 计数为 0）。
   我称 v4。它与 v2 的差别只有两点：**地址取正确** + **加"参数已补丁"门禁**。
   建议先做 v4；B1（CreateStateBlock→Apply）保留为后备。

---

## 1. 取证清单

### 1.1 PDB 符号（权威，来自 `06_独有资产/hook快照/version_c93e.pdb`，dbghelp 解析）

| 符号 | RVA | 大小 | 用途 |
|---|---|---|---|
| `g_bigTex` | **0x41A70** | 4 | 我们的大图集指针 |
| `g_fontTex` | **0x41A74** | 4 | 引擎字体纹理指针 |
| `g_fontTexN` | 0x41A7C | 4 | 字体纹理候选数 |
| `g_frames` | 0x41898 | 4 | 帧计数 |
| `g_orig_DP` | 0x419E8 | 4 | 原 DrawPrimitive |
| `g_orig_SetTexture` | 0x419C4 | 4 | 原 SetTexture（直调，绕过我们的钩子） |
| `g_orig_CreateTex` | 0x419D4 | 4 | 原 CreateTexture |
| `g_devPtr` | 0x419C8 | 4 | 当前 device |
| `g_doneH` / `g_doneHN` | 0x48E04 / 0x48AA4 | 128 / 4 | **"已改高"对象记录**（= 参数已补丁） |
| `g_drawCntDP` | 0x48C64 | 4 | DP 计数 |
| `g_obj512` / `g_obj512N` | 0x48CF0 / 0x48CE0 | 128 / 4 | 512x128 字体组登记 |
| `watchdog_swap` | **0x6270** | 563 | 帧末重绑看门狗（函数） |
| `apply_params_fast` | **0x64B0** | 643 | 参数补丁（函数） |
| `Hook_DP` | 0x5750 | 756 | 现有 DP 钩子（attach 登记值 0x10005750） |
| `Hook_SetTexture` | 0x4F20 | 2083 | 现有 SetTexture 钩子 |

> 说明：`version_c93e.dll` 与保底版 `0c17dfc0` 尺寸相同（282112），
> clearflash9 只改了 CAVE_ST（file 0x37984 起）与 1 个字节，
> 因此上表地址对保底版**全部有效**（改动区不在这些符号范围内）。

### 1.2 源码证据

| 位置 | 内容 | 意义 |
|---|---|---|
| `version_proxy.c:1027-1028` | 「所有『图集 512x128』的字体组都要改宽高，否则 UV 归一化仍按 512/128，采样位置错乱」 | 换纹理与改尺寸是**两半**，必须同时 |
| `:1491-1498` | 「原子性门禁：两者必须同进同退。若只改参数、纹理没换…白竖条」+ 门禁条件 `!g_fontTex \|\| !g_bigTex \|\| !g_fontTexBound` | 官方铁律，v4 必须遵守 |
| `:1530-1535` | 只处理 `*(DWORD*)(obj+16)==24` 的组，`*(DWORD*)(obj+12)=TEX_H` | 只动 holstein24，其它组保持原样 |
| `:2683-2696` | 「c40 探针：Reset 后 stage0 从 NULL 变成游戏自己新建的 512×128 图集，而 SetTexture 调用数 = 0 ⇒ 游戏用了绕过 SetTexture 的绑定路径（状态块 Apply 等）」 | **Apply 假设的来源是 c40 实测，不是推测** |
| `:2697-2702` | 「对策：在所有 Draw* 入口前检查 stage0 —— 只要绑着的是游戏字体纹理，立即换成我们的大图集」 | **作者当初就想做 v4，只是最终落在 Present 看门狗上** |
| `:536-545` | c44「帧内局部干预」：帧中换、帧末还原 | 曾试过作用域干预，最终改成持久绑定 |
| `:807-818` | 看门狗触发：恢复期每 2 帧；稳定态每 600 帧 | 这就是"还会闪 1~2 帧"的量级来源 |
| `:2700-2704` | c41 教训：Draw 里逐次 GetTexture（38 万次）在设备丢失期污染 → Reset 返回 DEVICELOST 白屏 | **v4 必须先用 TestCooperativeLevel**，且只在必要时查询 |

---

## 2. v2 失败根因（订正，我的错）

v2 cave 干的事（实际语义）：

```
if (stage0 == [0x10041A70])      // 我以为是 g_fontTex，实际是 g_bigTex
    SetTexture(0, [0x10041A74]); // 我以为是 g_bigTex，实际是 g_fontTex
```

即：**在"大图集正绑着"的正常状态下，把引擎的小图集换了回去**。
而此刻字体组的 `+12` 已被 `apply_params_fast` 改成 4096，于是：

```
引擎算 v = glyphY / 4096              （参数已补丁）
实际纹理高 = 128                       （被我们换回去了）
采样行  = v * 128 = glyphY / 32        （越界 -> 取到图集外/空行）
```

结果：绝大多数字形采样到透明区 → **文字不显示**；少数落在顶部行 → **纵向压扁**。
与你反馈的"不显示 + 退出/设置界面扁扁的"逐条吻合。

**结论**：v2 是符号取反导致的实现错误。UV 机制（1027 行）本身没错，但它需要
「纹理 + 尺寸」同时正确；我搞反了纹理这一半。

---

## 3. 现状机制，以及为什么还会闪

保底版每帧（Present）做的事：

```
if (postResetNeedRebuild || fontTexBound || rearmPending) && frames%2==0
    if (postReset || rearm)  → 每 2 帧  watchdog_swap(dev)
    else                     → 每 600 帧 watchdog_swap(dev)
```

`watchdog_swap` 内部（源码 2711+）：TestCooperativeLevel → GetTexture(0) →
若 `stage0 == 游戏字体纹理` → 重绑大图集（+ 未开闸时同帧补参数）。

- **Reset / 切屏后**：恢复期每 2 帧响应 → 最快 1~2 帧修好 → **这就是你看到的闪**
- **稳定态**：每 600 帧（约 10 秒）才巡检一次 → 若期间被旁路改写，会长时间不正确

所以方案 B 的真实增益只有一句话：**把 1~2 帧的错误窗口压成 0 帧**。

---

## 4. 三条路径对比

| 维度 | v4：DP 入口同帧修复 | B1：CreateStateBlock→Apply 拦截 | 保持现状（clearflash） |
|---|---|---|---|
| 新增钩子 | **0**（复用现有 DP 钩子） | +1（设备 vt[59]）+ block vtable[5] | 0 |
| 同帧性 | 每次文字绘制前修复 → **0 帧错窗** | Apply 返回即修复 → 0 帧错窗 | 1~2 帧（Reset 期）/ 最长 600 帧（稳定期） |
| 改动面 | 只换 attach 的钩子指针（4 字节）+ 新节 | 要改 attach 序列（需重定位一次 attach 调用）+ 两处 vtable | 无 |
| 失效面 | 文字若不走 DP 则无效（实测 82/83/84 计数=0，走 81） | 覆盖所有绕过 SetTexture 的路径 | 0 |
| 已知风险 | c41 类（设备丢失期查询）→ 用 TestCoop 挡住 | 状态块对象的**生命周期与 vtable 共享性未验证**；对象被 Release 后钩子悬挂 | 无 |
| 回滚 | 换回 DLL 即可 | 同 | — |
| 实现量 | 约 60 行汇编 | 约 150 行汇编 + attach 序列改造 | 0 |

**结论**：B1 相对 v4 **没有额外收益**，却引入状态块对象生命周期这个新风险面；
而 v4 的"文字只走 DrawPrimitive"有实测支撑（源码 2680：「实测本引擎不用 82/83/84，
主绘制路径很可能是 81」）。所以推荐 v4，B1 作为 v4 无效时的后备。

---

## 5. v4 设计（推荐）

### 5.1 判别逻辑：**不需要区分 UI / 3D**

你担心的"别把 3D 搞出竖条纹"，用**状态判据**解决，而不是场景判据：

> 只有当 `stage0` 当前绑定的纹理**恰好等于引擎自己的字体纹理**时才动手。

- UI/文字帧：引擎刚把字体纹理绑到 stage0 → 命中 → 换大图集 → 正确
- 3D 帧：stage0 是材质纹理（≠ 字体纹理）→ 不命中 → 完全不动
- 3D 里"没重绑、沿用陈旧值"的帧：若陈旧值恰是字体纹理，说明引擎本就打算用它画字，
  换成大图集正是我们想要的；若陈旧值是材质，则不动

这条判据不是猜的：它就是 `watchdog_swap` 现在用的同一条判据，已在真机跑通。

### 5.2 门禁（必须有，否则重演 v2）

三条同时成立才动手：

1. `g_fontTex != NULL`
2. `g_bigTex != NULL`
3. **`g_doneHN > 0`** —— 表示"参数已补丁"（`g_doneH` 记录数非 0）。
   这条就是源码 1491 行那条"原子性门禁"的可执行等价物：
   参数没补丁时，stage0==字体纹理是**正常的原版渲染**，绝不能换纹理。

> 为什么不用 `g_fontTexBound`：该符号在 PDB 里查不到（可能被内联/改名），
> 而 `g_doneHN`（0x48AA4）语义更直接 —— 它就是"已改高对象数"，
> Reset 时 `clear_all_caches` 会清零，正好对应"参数已回退"。

### 5.3 处理流程（汇编草案，PIC）

```
prologue（保存 ebx/esi/edi，sub esp,0x10）
esi = 运行时基址            ; call $+5 / pop esi / sub esi, rva
inc [esi+g_drawCntDP]

if (![esi+g_fontTex]) goto pass
if (![esi+g_bigTex])  goto pass
if ([esi+g_doneHN] <= 0) goto pass        ; ★ 原子性门禁（v2 缺的就是这条）

this = [ebp+8]; vt = [this]
if (!vt[3]) goto pass
TestCooperativeLevel(this)                ; ★ c41 教训
if (hr != S_OK) goto pass

if (!vt[64]) goto pass
GetTexture(this, 0, &t)                   ; vt[64]，注意不是 65
if (FAILED(hr)) goto pass
if (t) Release(t)                         ; ★ c81 教训：配对释放

if (t != [esi+g_fontTex]) goto pass       ; ★ 地址已订正
SetTexture(this, 0, [esi+g_bigTex])       ; 用 g_orig_SetTexture 直调，避免重入

pass:
orig_DP(this, type, start, count)
done:
epilogue; ret 0x10
```

要点：

- **不做"画完还原"**。正确终态本来就是"大图集绑着"（这正是既有机制的目标态），
  所以换完就走，下一帧/下一 draw 直接命中 `pass`。这一点与 v2 的"作用域还原"完全不同，
  也是 v2 思路别扭的根源 —— 不需要还原。
- 一次命中之后，后续 DP 只花 `TestCoop + GetTexture + Release` 三次轻调用。
  若嫌多，可加一个"本帧已修复"标志，命中一次后本帧不再查询（二期优化）。
- 全部用 `[esi+RVA]` 寻址，天然免疫 ASLR（v1 的教训）。

### 5.4 交付物形态（待你点头后再做）

- 新节 `.cf11`（承接方式同 v2，但**地址表用本报告 1.1 的正确值**）
- 唯一的挂钩点改动：attach imm32 `0x10005750 → 新 cave VA`（同 v2）
- 差分仍然只有 20 字节 + 追加节
- 静态校验沿用 v2 那套（反汇编逐条 / 路径级栈平衡 / PIC 操作数自检 / 白名单差分）
- 新增一条自检：**断言 cave 里引用的 fontTex 偏移是 0x41A74、bigTex 是 0x41A70**
  （防止这次的取反错误重演）

---

## 6. B1 设计（若你仍要走 Apply 拦截）

供参考，不建议先做：

1. 需要新增 `CreateStateBlock` 钩子。设备 vt[59]，签名
   `HRESULT CreateStateBlock(D3DSTATEBLOCKTYPE, IDirect3DStateBlock9** ppSB)`（ret 0x0C）。
   现有 attach 序列（file 0x1B60–0x1C1B）**没有剩余空间**，要新增必须把序列末端的
   一次 attach 调用改成 `jmp` 到新节、在新节里重放两次 attach 再跳回 —— 需要先精确
   复刻 `attach_dev` 的调用约定（ecx=dev / edx=idx / 栈=hook），这是**未验证的假设**。
2. 拿到 `ppSB` 后补 block 的 vtable：`IDirect3DStateBlock9` 索引
   `GetDevice=3 / Capture=4 / Apply=5`（偏移 0x14）。
3. **风险**：① 状态块对象可被 `Release`，vtable 若按对象分配则钩子悬挂；
   ② d3d9 运行时的 block vtable 是否全局共享**未验证**；③ 与 vtable_heal 的
   槽位登记机制不兼容（heal 只管 device vtable）。
4. UV 侧不需要额外动作（参数与纹理由既有机制保持），Apply 后只需重绑纹理，
   但要遵守同一套门禁与 TestCoop。

**结论**：先做 v4。若 v4 上线后仍有残余错窗（比如发现文字并非走 81），再上 B1。

---

## 7. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 设备丢失期查询污染（c41 白屏） | 入口先 TestCooperativeLevel，非 S_OK 直接放行 |
| 纹理引用泄漏（c81） | GetTexture 结果立即 Release |
| ASLR 重定位（v1 崩） | 全 PIC，构建器带操作数自检 |
| 符号取反（v2 文字全丢） | 构建器断言 fontTex=0x41A74 / bigTex=0x41A70，并在 cave 里注释 |
| 参数未补丁时误换纹理 | `g_doneHN > 0` 门禁 |
| 性能 | 命中后每 DP 3 次轻调用；如需可加"本帧已修"标志 |

回滚：`rollback_cave_ds.bat`（保底版 0c17dfc0 同目录 ASCII 命名，md5 校验）。
最坏情况一分钟复原，不存在不可逆损失。

---

## 8. 需要你确认的三个点

1. **路径**：直接做 v4（推荐），还是坚持先写 B1 的 Apply 拦截？
2. **性能容忍度**：v4 在每个 DrawPrimitive 入口最多多 3 次轻量 COM 调用。
   若你要求"零额外调用"，我就加"本帧已修复"标志（多约 20 字节汇编）。
3. **验收顺序**：v4 装好后先看 ①能否进主菜单 ②菜单中文是否正常
   ③切屏是否还需要黑屏遮罩（若 v4 有效，可以尝试把 clearflash 的遮罩窗口缩短甚至关掉，
   这是最终目标）。

你点头我就出 v4 的构建脚本 + 静态校验报告（仍然不装机，先给你看校验结果）。
