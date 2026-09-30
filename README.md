# PDF 密码解除器

本地离线的 Windows 小工具。输入你已知的 PDF 密码，批量生成以后打开无需密码的副本，保留原文件。

A portable Windows utility that removes PDF encryption using a known password. Documents and passwords stay on your computer; originals are preserved.

[下载最新版](https://github.com/lisamsung/pdf-password-unlocker/releases/latest) · [更新记录](CHANGELOG.md) · [MIT 许可证](LICENSE.txt)

## 下载与使用

适用于 **Windows 10 / 11，64 位**。发布版无需安装 Python，运行时无需联网。

1. 从 [Releases](https://github.com/lisamsung/pdf-password-unlocker/releases/latest) 下载 `.exe`，或下载便携版 `.zip` 并解压。
2. 双击程序，拖入 PDF 或文件夹，也可以点击“选 PDF”“文件夹”。添加文件夹只读取直接包含的 PDF。
3. 输入“共用密码”。文件密码不同，可以选中后“设置单独密码”，或双击文件；支持多选设置。
4. 选择原文件所在文件夹或指定输出文件夹，点击“解除密码”。
5. 完成后点击“打开输出文件夹”，使用生成的无密码副本。

例如 `合同.pdf` 会生成 `合同_无密码.pdf`；重名时自动使用 `合同_无密码 (2).pdf`。过长的文件名会缩短，为后缀和编号预留空间。原件和已有输出都不会覆盖。

密码错误或文件损坏会显示失败原因。修正密码后重新开始，只重试未完成的文件。无加密文件自动跳过。

## 功能

- 拖放文件和文件夹，批量处理，共用或单独设置密码。
- 支持常见的 RC4、AES-128、AES-256 PDF 密码加密，以及已知的打开密码或权限密码。
- 只有权限限制、没有打开密码的文件，可以留空密码处理。
- 保存完整文档，保留页面内容、书签、附件、批注和交互表单。
- 文件和密码仅在本机处理，程序没有网络请求；密码不写入配置或日志，批次结束后清空输入。
- 小窗口可滚动设置面板，支持鼠标滚轮与键盘 Tab。
- “停止后续文件”完成当前文件后停止；处理中关闭窗口，也会等待当前文件完成。

快捷键：`Ctrl+O` 选择文件，`Ctrl+Enter` 开始，文件列表中的 `Delete` 移除所选项。

## 处理范围

本工具使用已知密码解除 PDF 加密，不提供未知密码破解、证书解密或 DRM 移除。特殊加密格式可能无法处理，失败时保留原件。

重新保存会使副本中的数字签名失效；检测到签名时，结果详情会提示。需要验证签名时请使用保留的原件。

程序被强制终止时，输出目录可能留下 `.pdf-unlock-*.tmp` 临时文件。它不是最终结果；重新处理原件即可，确认程序退出后可删除该临时文件。

## 从源码运行

需要 Windows 64 位 Python 3.10 或更高版本，并包含 Tkinter。建议使用独立虚拟环境。以下命令在仓库根目录的 PowerShell 中执行，无需激活环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## 测试与打包

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_unlocker
```

测试即时生成合成 PDF，覆盖加密方式、文档保留、错误密码、文件名冲突、长文件名、批处理、DPI、设置滚动和 Windows PowerShell 脚本解析，不需要任何真实 PDF。GUI 测试需要可用的 Windows 桌面环境。

打包脚本会安装依赖、生成图标、收集第三方许可证，并生成单文件程序：

```powershell
$env:PATH = "$(Resolve-Path .\.venv\Scripts);$env:PATH"
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

产物为 `dist\PDF密码解除器.exe`。`build.ps1` 保留 UTF-8 BOM，以兼容 Windows PowerShell 5.1。依赖安装需要联网。

GitHub Actions 在 Windows 上运行回归测试与打包检查。发布的 v1.1 程序还完成了独立运行检查，以及解密前后两页内容的像素对比。

## 代码结构

| 文件 | 用途 |
| --- | --- |
| `app.py` | Tkinter 界面、拖放、队列与批处理 |
| `core.py` | pikepdf 解密、验证和防覆盖保存 |
| `test_unlocker.py` | 合成文档和桌面回归测试 |
| `build.ps1` | Windows 打包入口 |
| `build_assets.py` | 生成原创图标 |
| `collect_licenses.py` / `licenses/` | 收集及保留第三方许可证 |

## 贡献与许可证

欢迎提交 Issue 或 Pull Request。反馈问题时请提供系统版本、操作步骤和界面错误信息；复现文件优先使用无敏感内容的合成 PDF。

本项目源码采用 [MIT](LICENSE.txt) 许可证。第三方组件遵循各自许可证，完整文本位于 [licenses](licenses/)。主要依赖为 [pikepdf / QPDF](https://github.com/pikepdf/pikepdf)、[tkinterdnd2](https://github.com/Eliav2/tkinterdnd2) 和 [PyInstaller](https://github.com/pyinstaller/pyinstaller)。
