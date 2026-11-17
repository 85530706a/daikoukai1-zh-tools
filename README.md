# 大航海时代 1 简体中文补丁与工具（DOS 繁体中文版）

光荣（KOEI）1990 年《大航海时代》繁体中文 DOS 版的一套工具，面向 macOS + DOSBox-X：

- **简体中文补丁生成器**：用你自己的原版游戏，在你电脑上生成简体中文版。字形换成简体，"甚麼""滑鼠""磁碟"等台湾用词改成大陆用法，开头密码画面也是简体。
- **实时修改器**：游戏运行时改金币、名望、爵位、船员；港口瞬移、瞬移到电脑舰队旁；作弊开关包括补给不消耗、海战一击必杀且无敌、国王任务回去就完成、召见检查每天一次、取消夜间不能战斗。
- **存档修改器**：一个网页，直接改 `SAVE.DAT`。
- **文档**：[主线攻略](docs/主线攻略.md)（含从代码里查出的召见门槛、升爵规则）和 [研究笔记](docs/研究笔记.md)（脱壳、字库、文字编码、内存地址、补丁原理）。

> **本仓库不包含任何游戏文件。** 游戏版权归光荣特库摩所有，请使用你自己合法持有的游戏。补丁只在你本地对你自己的文件进行修改。

## 适用版本

只支持下面这一版。补丁脚本会先核对校验值，对不上就停下，不改任何东西。

| 文件 | SHA-256 |
|---|---|
| `MAIN.EXE` | `22407c9eb7e6e1ff6f8e1d27d4f1ba0d349a07645ad65bef963ddc708a626ea5` |
| `ALLHAN.16P` | `831542db7083d2ce0e05b430ff7eca473cdf56d0c3e23ed0e9880d8b942394aa` |

在终端里核对：

```bash
shasum -a 256 MAIN.EXE ALLHAN.16P
```

日文版、英文版（Uncharted Waters）和其他中文版的程序、编码、地址都不同，不能直接用。[研究笔记](docs/研究笔记.md)里的方法可以参考。

## 准备（macOS）

```bash
brew install dosbox-x
```

```bash
xcode-select --install
```

第二条安装 Xcode 命令行工具，渲染简体字形要用到 `swiftc`。Python 3 用系统自带的或 Homebrew 的都可以。

## 1. 生成简体中文版

```bash
python3 -I patcher/apply_patch.py --game "/path/to/游戏文件"
```

- 大约 1 分钟。原文件会先备份到 `游戏文件/_backup_original/`。
- 只想检查、不写回：加 `--dry-run`。
- 换字体：`--font "NotoSansSC-Light"`（PostScript 名）；笔画粗细：`--threshold 0.45`，越大越细。
- 用默认字体（苹方 Light）生成的结果，会和作者的版本逐字节比对。不同系统版本渲染出来的像素可能略有不同，这是正常的。
- **还原**：把 `_backup_original` 里的两个文件复制回去即可。

补丁做了什么：
1. 用 `patcher/emu86.py`（一个极简 8086 模拟器）跑一遍 TINYPROG 解压程序，得到脱壳的 `MAIN.EXE`；
2. 按 `patcher/koei_text.py` 的编码规则改 90 条文本里的用词；
3. 用 CoreText 把 `ALLHAN.16P` 里 460 个繁简不同的字重画成简体，大小对齐原版点阵；
4. 把程序内置的密码画面字形换成简体。

## 2. 实时修改器

```bash
sh trainer/install.sh
```

- 程序装到 `~/Library/Application Support/DHHTrainer`。这个位置不受"下载""文稿"等文件夹的隐私保护，以管理员身份运行时也读得到。
- 启动器生成在 `~/Applications/启动实时修改器.app`。

用法：
1. 先启动游戏，进到游戏主画面（读档或开新游戏）。
2. 双击 `启动实时修改器.app`，输入电脑密码。macOS 只允许管理员读写其他程序的内存。
3. 浏览器会打开 `http://127.0.0.1:8765`，只在本机监听。

几点说明：
- 作弊开关是直接改 DOSBox 内存里的游戏代码，关闭时恢复原样；重开游戏后自动复位。
- 港口瞬移、瞬移到舰队后船处于停船状态，要先"操作 → 起锚"。
- 停止修改器：

```bash
sudo pkill -f 'DHHTrainer/trainer.py'
```

## 3. 存档修改器

用浏览器打开 `save-editor/save-editor.html`，载入 `SAVE.DAT`，选档位改数值，点保存下载新的 `SAVE.DAT`。文件只在浏览器本地处理。存档没有校验和，改完直接能读。

## 目录

```
patcher/       简体中文补丁生成器（apply_patch.py、emu86.py、koei_text.py、render_glyphs.swift）
trainer/       实时修改器（trainer.py、index.html、启动脚本、安装脚本）
save-editor/   存档修改器网页
docs/          主线攻略、研究笔记
```

## 许可

代码和文档以 [MIT 许可证](LICENSE) 发布。游戏本身及其数据的版权归光荣特库摩（KOEI TECMO）所有，与本项目无关。
