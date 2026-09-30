"""Collect unchanged license notices for the distributed runtime and libraries."""
import shutil
import sys
from importlib import metadata
from pathlib import Path

root = Path(__file__).parent / 'licenses'
root.mkdir(exist_ok=True)
for name in ('pikepdf', 'tkinterdnd2', 'Pillow', 'lxml', 'packaging', 'pyinstaller'):
    distribution = metadata.distribution(name)
    for file in distribution.files or []:
        parts = file.parts
        if 'licenses' in parts and any(part.endswith('.dist-info') for part in parts):
            relative = Path(*parts[parts.index('licenses') + 1:])
            target = root / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(distribution.locate_file(file), target)
base = Path(sys.base_prefix)
for source, target in [
    (base / 'LICENSE_PYTHON.txt', root / 'Python.txt'),
    (base / 'LICENSE.txt', root / 'Python-license.txt'),
    (base / 'Library/lib/tk8.6/license.terms', root / 'Tk.txt'),
    (base / 'Library/lib/tcl8.6/license.terms', root / 'Tcl.txt'),
    (base / 'tcl/tk8.6/license.terms', root / 'Tk.txt'),
    (base / 'tcl/tcl8.6/license.terms', root / 'Tcl.txt'),
]:
    if source.is_file():
        shutil.copyfile(source, target)
tkdnd = metadata.distribution('tkinterdnd2')
for file in tkdnd.files or []:
    if 'tkdnd' in str(file).lower() and file.name.lower() in ('license', 'license.txt', 'license.terms', 'copying'):
        target = root / 'tkdnd' / file.name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(tkdnd.locate_file(file), target)
(root / 'NOTICE.txt').write_text(
    'All third-party components are distributed without modification.\n'
    'pikepdf source: https://github.com/pikepdf/pikepdf/tree/v10.13.0.post1\n'
    'QPDF source: https://github.com/qpdf/qpdf\n'
    'Python source: https://www.python.org/downloads/release/python-3109/\n'
    'Tk/Tcl source: https://www.tcl.tk/software/tcltk/\n'
    'tkinterdnd2 and tkdnd: https://github.com/Eliav2/tkinterdnd2\n'
    'Pillow: https://github.com/python-pillow/Pillow\n'
    'lxml: https://github.com/lxml/lxml\n'
    'PyInstaller: https://github.com/pyinstaller/pyinstaller\n', encoding='utf-8')
print(f'Collected {len(list(root.rglob("*.*")))} license / notice files.')
