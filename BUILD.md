# Windows 和 Linux 打包说明

本项目使用 PyInstaller 的 `onedir`（目录）模式打包。程序使用了 PySide6 Qt WebEngine，目录模式比单文件模式启动更快、运行更稳定。

> Windows 程序必须在 Windows 上构建，Linux 程序必须在 Linux 上构建。PyInstaller 不支持直接跨平台编译。

## 1. 打包环境

推荐使用 Python 3.12，GitHub Actions 自动构建同样使用 Python 3.12。

打包前请确认：

- 已安装 Python 和 pip。
- Python 已加入 `PATH`。
- 能够访问 Python 软件包源。
- 在项目根目录执行本文命令。

查看当前环境：

```text
python --version
python -m pip --version
```

运行依赖位于 `requirements.txt`，打包依赖位于 `requirements-build.txt`。

## 2. Windows 打包

### 2.1 一键打包

打开 PowerShell，进入项目根目录执行：

```powershell
cd D:\huangsheng\project\vcg
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

脚本会自动：

1. 安装运行和打包依赖。
2. 清理以前的 PyInstaller 构建缓存。
3. 生成目录模式的 Windows 程序。
4. 将完整程序目录压缩为 ZIP 发布包。

构建产物：

```text
dist\VCGDownloader\VCGDownloader.exe
release\VCGDownloader-Windows-x64.zip
```

ARM64 Windows 环境的压缩包名称为 `VCGDownloader-Windows-arm64.zip`。

### 2.2 跳过依赖安装

如果当前 Python 环境已经安装了所有依赖，可以缩短构建时间：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -SkipInstall
```

如果出现模块缺失，请取消 `-SkipInstall` 后重新构建。

### 2.3 使用独立虚拟环境（推荐）

虚拟环境可以避免 Anaconda 或其他项目中的无关模块进入安装包：

```powershell
cd D:\huangsheng\project\vcg
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

完成后退出虚拟环境：

```powershell
deactivate
```

### 2.4 运行 Windows 包

将 ZIP 完整解压后运行：

```text
VCGDownloader\VCGDownloader.exe
```

不要只复制 EXE。`_internal` 目录包含 Qt WebEngine、Python 和图片解析所需的运行库，必须与 EXE 一起保留。

## 3. Linux 打包

### 3.1 安装系统依赖

Ubuntu/Debian 示例：

```bash
sudo apt update
sudo apt install -y \
  python3 python3-venv python3-pip \
  libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0
```

不同发行版的软件包名称可能不同。Linux 必须具有图形桌面环境才能运行本程序。

### 3.2 构建 Linux 包

推荐在虚拟环境中构建：

```bash
cd /path/to/vcg
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
chmod +x packaging/build_linux.sh packaging/linux/install.sh
./packaging/build_linux.sh
```

如果依赖已经安装，可以跳过依赖安装：

```bash
./packaging/build_linux.sh --skip-install
```

构建产物：

```text
dist/VCGDownloader/VCGDownloader
release/VCGDownloader-Linux-<架构>.tar.gz
```

常见架构名称包括 `x86_64` 和 `aarch64`。

### 3.3 运行 Linux 包

以 x86_64 包为例：

```bash
tar -xzf release/VCGDownloader-Linux-x86_64.tar.gz
cd VCGDownloader
chmod +x VCGDownloader
./VCGDownloader
```

### 3.4 安装到应用菜单

发布包包含用户级安装脚本，不需要 `sudo`：

```bash
cd VCGDownloader
chmod +x install.sh
./install.sh
```

默认安装位置：

```text
~/.local/opt/vcg-downloader
~/.local/share/applications/vcg-downloader.desktop
~/.local/share/icons/hicolor/256x256/apps/vcg-downloader.png
```

安装后可以从桌面环境的应用菜单启动“高清图片下载”。

### 3.5 Linux 兼容性

Linux 二进制会依赖构建系统的 glibc。为了兼容更多机器，应在较旧且仍受支持的 Linux 版本上构建。项目的 GitHub Actions 当前使用 Ubuntu 22.04。

## 4. GitHub Actions 自动打包

工作流文件为 `.github/workflows/build-packages.yml`，会在独立环境中同时生成 Windows 和 Linux 包。

### 4.1 手动触发

1. 将代码推送到 GitHub。
2. 打开仓库的 `Actions` 页面。
3. 选择 `Build packages`。
4. 点击 `Run workflow`。
5. 构建完成后，在任务页面的 `Artifacts` 区域下载产物。

Artifact 名称：

```text
windows-package
linux-package
```

### 4.2 使用版本标签触发

推送以 `v` 开头的标签会自动构建：

```bash
git tag v1.0.0
git push origin v1.0.0
```

当前工作流只上传 Actions Artifacts，不会自动创建 GitHub Release。

## 5. 修改程序版本

程序界面显示的版本号位于：

```text
app_version.py
```

Windows 文件属性中的版本信息位于：

```text
packaging/windows_version_info.txt
```

发布新版本时，请同步修改以上两个文件，然后重新运行打包脚本。`app_version.py` 使用三段式版本号，例如 `1.1.0`；Windows 版本文件中的产品版本和文件版本也应同步更新。

## 6. 清理构建产物

需要完全重新构建时，可以删除以下目录：

```text
build
dist
release
```

这些目录已经加入 `.gitignore`，不会提交到 Git 仓库。构建脚本本身也会使用 PyInstaller 的 `--clean` 参数清理缓存。

## 7. 常见问题

### PowerShell 不允许运行脚本

使用本文提供的命令临时绕过当前进程的执行策略：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

### 提示 `ModuleNotFoundError`

重新安装依赖：

```text
python -m pip install -r requirements.txt -r requirements-build.txt
```

或者直接运行不带 `-SkipInstall` 的构建脚本。

### Linux 提示缺少 Qt、xcb 或 OpenGL 库

Ubuntu/Debian 可先安装：

```bash
sudo apt install libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0
```

其他发行版请安装对应的 EGL、OpenGL、XKB 和 XCB 运行库。

### 安装包体积较大

这是正常现象。Qt WebEngine 自带浏览器内核和相关资源，占用了安装包的大部分空间。项目已明确排除不使用的 NumPy/MKL 运行库。

### Windows 显示“未知发布者”

当前 EXE 未进行代码签名，Windows 可能显示安全提示，但不影响程序功能。正式对外发布时可以为 EXE 添加代码签名。

## 8. 替换程序 Logo

程序窗口、Windows EXE、任务栏和 Linux 应用菜单使用同一个 ICO 图标来源。图标目录为：

```text
ico
```

### 8.1 最便捷的替换方式

准备好新的 ICO 文件，将其命名为：

```text
ico/logo.ico
```

然后重新执行 Windows 或 Linux 打包脚本即可，不需要修改任何代码。为了兼容已有项目，也可以直接覆盖 `ico/app.ico`。

建议 ICO 文件：

- 使用正方形图案和透明背景。
- 至少包含 `16×16`、`32×32`、`48×48`、`128×128` 和 `256×256`。
- 推荐先用 `1024×1024` 的 PNG 或 SVG 原图导出多尺寸 ICO。
- 不要把 PNG 直接改名为 `.ico`，必须转换成真正的 ICO 格式。

### 8.2 支持的 ICO 文件名

程序只识别以下两个文件名，选择顺序为：

1. `logo.ico`
2. `app.ico`

当两个文件同时存在时使用 `logo.ico`。`head.ico`、`Big.ico` 和其他自定义名称不会被程序或打包脚本选用。

`logo.png` 和 `logo.svg` 可以作为设计源文件保留，但不再决定打包程序使用的图标。Linux 打包脚本会自动从选中的 ICO 生成应用菜单所需的 PNG 图标。

### 8.3 重新打包

Windows：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -SkipInstall
```

Linux：

```bash
./packaging/build_linux.sh --skip-install
```

替换图标后必须重新打包，直接修改已经生成的 ZIP、EXE 或 Linux 压缩包不会自动生效。

### 8.4 Windows 仍显示旧图标

Windows 资源管理器可能缓存旧图标。确认已经使用新包后，可以尝试：

1. 删除旧快捷方式并重新创建。
2. 将新程序解压到不同目录。
3. 重启 Windows 资源管理器或注销后重新登录。

应用窗口中显示的新图标不受旧快捷方式图标缓存影响。
