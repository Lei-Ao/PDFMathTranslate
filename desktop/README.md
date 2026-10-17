# Windows 桌面版

这里保存桌面启动器、图标、PyInstaller 配置和 Inno Setup 安装脚本。
翻译引擎源码位于仓库根目录的 `pdf2zh/`，版本信息在 `desktop_version.py`。

## 从源码运行

在仓库根目录执行（Windows，Python 3.11 或 3.12）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install pywebview
.\.venv\Scripts\pythonw.exe desktop\desktop_app.py
```

也可以双击 `启动桌面版.vbs`，它使用仓库根目录的 `.venv`。
网页入口为 `start_webui.py`，使用相同的 Python 环境。

## 构建 Windows 安装包

在仓库根目录安装构建依赖后，切换到本目录构建：

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller pyinstaller-hooks-contrib
cd desktop
..\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean desktop_build.spec
ISCC.exe installer\PDF翻译工具.iss
```

Inno Setup 6 的 `ISCC.exe` 需已加入 PATH。
免安装版生成在本目录的 `dist/PDF翻译工具/`；安装包生成在本目录，
文件名为 `PDF翻译工具安装包-<版本>.exe`。

`publish_release.py` 可将本目录已有的安装包上传至 GitHub Releases，
凭据从 Git 凭据管理器读取。

源码启动器默认将翻译结果保存到本目录的 `data/translations/`。
设置 `PDF2ZH_DATA_DIR` 可以指定其他目录。日志、浏览器状态、译文、
证书和构建产物均不纳入 Git。

`使用指南.md` 保留了原开发工作区的使用记录，其中绝对路径和旧目录结构
仅供参考；克隆仓库后的安装、启动及构建以本文件为准。
