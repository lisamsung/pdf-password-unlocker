"""Local PDF decryption. Never modifies the source or overwrites an output."""
from __future__ import annotations

import os
import tempfile
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pikepdf

_ENGINE_LOCK = threading.RLock()


@dataclass(frozen=True)
class UnlockResult:
    status: str
    message: str
    output: Path | None = None
    pages: int = 0


def _has_signature(pdf: pikepdf.Pdf) -> bool:
    """Look for an actual populated signature, including inherited field types."""
    form = pdf.Root.get('/AcroForm', {})
    fields = [(field, None) for field in form.get('/Fields', [])]
    seen: set[tuple[int, int]] = set()
    while fields:
        field, inherited = fields.pop()
        object_id = field.objgen
        if object_id != (0, 0):
            if object_id in seen:
                continue
            seen.add(object_id)
        kind = field.get('/FT', inherited)
        value = field.get('/V', {})
        if kind == pikepdf.Name.Sig and value and value.get('/ByteRange') is not None:
            return True
        fields.extend((child, kind) for child in field.get('/Kids', []))
    return False


def _publish(temp: Path, source: Path, folder: Path) -> Path:
    """Commit a verified file atomically, retrying collisions without replacement."""
    number = 1
    while True:
        suffix = '' if number == 1 else f' ({number})'
        target = folder / _output_name(source.stem, suffix)
        try:
            if os.name == 'nt':
                # Windows rename fails if a destination exists, unlike POSIX rename.
                os.rename(temp, target)
            else:
                os.link(temp, target)
                temp.unlink()
            return target
        except FileExistsError:
            number += 1


def _output_name(stem: str, suffix: str) -> str:
    """Reserve space for the suffix and counter within a filesystem component.

    Windows counts UTF-16 code units, so slicing by Python character count would
    still overflow for emoji. Truncate whole characters, including on UTF-8 hosts.
    """
    def units(value):
        if os.name == 'nt':
            return len(value.encode('utf-16-le', errors='surrogatepass')) // 2
        return len(value.encode('utf-8'))

    tail = f'_无密码{suffix}.pdf'
    budget = 255 - units(tail)
    prefix, used = [], 0
    for char in stem:
        length = units(char)
        if used + length > budget:
            break
        prefix.append(char)
        used += length
    return ''.join(prefix) + tail


def unlock_pdf(
    source: Path | str,
    password: str = '',
    output_dir: Path | str | None = None,
    progress: Callable[[int], None] | None = None,
) -> UnlockResult:
    """Use a supplied user/owner password; empty passwords work for permissions-only PDFs.

    Password strings are passed verbatim, are never logged, and are not persisted.
    The entire document is saved, preserving forms, outlines, annotations and attachments.
    """
    # Serialize access to the native engine: concurrent password opens can race.
    # Separate application processes still publish safely through _publish().
    with _ENGINE_LOCK, warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning,
                                message='A password was provided, but no password was needed')
        return _unlock_pdf(source, password, output_dir, progress)


def _unlock_pdf(source, password, output_dir, progress):
    source = Path(source).resolve()
    temporary: Path | None = None
    try:
        if not source.is_file():
            return UnlockResult('error', '找不到文件，可能已被移动或删除')
        if source.suffix.lower() != '.pdf':
            return UnlockResult('error', '请选择 PDF 文件')
        with pikepdf.open(source, password=password) as pdf:
            pages = len(pdf.pages)
            if not pdf.is_encrypted:
                return UnlockResult('skipped', '文件本身没有密码，已跳过', pages=pages)
            signed = _has_signature(pdf)
            folder = Path(output_dir).resolve() if output_dir else source.parent
            folder.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.pdf-unlock-', suffix='.tmp', dir=folder)
            os.close(fd)
            temporary = Path(name)
            pdf.save(
                temporary,
                encryption=False,
                fix_metadata_version=False,
                progress=progress,
            )
            with pikepdf.open(temporary) as check:
                if check.is_encrypted or len(check.pages) != pages:
                    raise RuntimeError('verification_failed')
            with temporary.open('r+b') as stream:
                os.fsync(stream.fileno())
            output = _publish(temporary, source, folder)
            temporary = None
            message = f'已解除密码 · {pages} 页'
            if signed:
                message += '；副本中的数字签名已失效，原件保留'
            return UnlockResult('success', message, output, pages)
    except pikepdf.PasswordError:
        return UnlockResult('error', '密码不正确，请重新输入')
    except PermissionError:
        return UnlockResult('error', '没有读写权限，或文件正被其他程序占用')
    except pikepdf.PdfError:
        return UnlockResult('error', 'PDF 损坏，或使用了暂不支持的加密方式')
    except OSError:
        return UnlockResult('error', '读写失败，请检查输出位置和剩余磁盘空间')
    except Exception:
        # Exception messages may contain document content: do not expose or log them.
        return UnlockResult('error', '处理失败；原文件未修改，请检查文件后重试')
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
