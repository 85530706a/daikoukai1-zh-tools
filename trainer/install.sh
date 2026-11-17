#!/bin/sh
# 安装实时修改器：把程序复制到 ~/Library/Application Support/DHHTrainer
# （这个位置不受“下载”等文件夹的隐私保护限制），并生成双击用的启动器 App。
# 用法：sh trainer/install.sh [启动器放在哪个目录，默认 ~/Applications]
set -eu
here=$(cd "$(dirname "$0")" && pwd)
dest="$HOME/Library/Application Support/DHHTrainer"
appdir="${1:-$HOME/Applications}"
mkdir -p "$dest" "$appdir"
cp "$here/trainer.py" "$here/index.html" "$here/start-trainer.sh" "$dest/"
chmod +x "$dest/start-trainer.sh"
osacompile -o "$appdir/启动实时修改器.app" "$here/launcher.applescript"
echo "已安装：$dest"
echo "启动器：$appdir/启动实时修改器.app"
