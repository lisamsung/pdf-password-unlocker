"""Regression tests with synthetic documents; no real documents or secrets."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pikepdf

from core import unlock_pdf


def make_document(path: Path):
    with pikepdf.Pdf.new() as pdf:
        font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                  BaseFont=pikepdf.Name.Helvetica))
        resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        for index in range(2):
            page = pdf.add_blank_page(page_size=(595, 842))
            page.Resources = resources
            page.Contents = pdf.make_stream((
                    f'q 0.94 0.96 1 rg 36 36 523 770 re f Q\n'
                    f'BT /F1 24 Tf 48 756 Td (PDF Unlocker - page {index + 1}) Tj ET\n'
                    'BT /F1 12 Tf 48 708 Td (Text, vectors, bookmarks, attachments and a form.) Tj ET\n'
                    'q 0.15 0.37 0.91 rg 48 600 180 10 re f Q\n'
            ).encode('ascii'))
        appearance = pdf.make_stream(b'q 1 1 1 rg 0 0 232 33 re f 0.3 0.3 0.3 RG 0 0 232 33 re S Q '
                                     b'BT /F1 12 Tf 8 11 Td (Ada Lovelace) Tj ET')
        appearance.Type = pikepdf.Name.XObject
        appearance.Subtype = pikepdf.Name.Form
        appearance.BBox = pikepdf.Array([0, 0, 232, 33])
        appearance.Resources = resources
        widget = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, FT=pikepdf.Name.Tx,
            T=pikepdf.String('client'), V=pikepdf.String('Ada Lovelace'),
            Rect=pikepdf.Array([48, 632, 280, 665]), F=4,
            DA=pikepdf.String('/F1 12 Tf 0 g'), AP=pikepdf.Dictionary(N=appearance)))
        pdf.pages[0].Annots = pikepdf.Array([widget])
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([widget]), DR=resources,
                                              NeedAppearances=False)
        pdf.docinfo.Title = pikepdf.String('Preservation fixture')
        pdf.docinfo.Author = pikepdf.String('Synthetic test')
        with pdf.open_outline() as outline:
            outline.root.append(pikepdf.OutlineItem('Chapter one', 0))
            outline.root.append(pikepdf.OutlineItem('Chapter two', 1))
        pdf.attachments['notes.txt'] = b'Preserved attachment.\n'
        pdf.save(path)


def encrypt(base: Path, path: Path, password='test-password', revision=6, aes=True, owner='owner-password'):
    with pikepdf.open(base) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(user=password, owner=owner,
                                                   R=revision, aes=aes, metadata=revision >= 4))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-unlock-tests-')
        self.folder = Path(self.temp.name)
        self.base = self.folder / '原始文档.pdf'
        make_document(self.base)
        self.protected = self.folder / '加密合同.pdf'
        encrypt(self.base, self.protected)
        self.before = hashlib.sha256(self.protected.read_bytes()).digest()

    def tearDown(self):
        self.temp.cleanup()

    def assert_preserved(self, output):
        with pikepdf.open(self.base) as original, pikepdf.open(output) as result:
            self.assertFalse(result.is_encrypted)
            self.assertEqual(len(result.pages), 2)
            for before, after in zip(original.pages, result.pages):
                self.assertEqual(before.Contents.read_bytes(), after.Contents.read_bytes())
                self.assertEqual(list(before.MediaBox), list(after.MediaBox))
            self.assertEqual(str(result.docinfo.Title), 'Preservation fixture')
            self.assertEqual(result.attachments['notes.txt'].get_file().read_bytes(), b'Preserved attachment.\n')
            with result.open_outline() as outline:
                self.assertEqual([item.title for item in outline.root], ['Chapter one', 'Chapter two'])
            fields = result.Root.AcroForm.Fields
            self.assertEqual(len(fields), 1)
            self.assertEqual(str(fields[0].V), 'Ada Lovelace')
            self.assertEqual(str(result.pages[0].Annots[0].T), 'client')
            self.assertTrue(fields[0].AP.N.read_bytes())
        self.assertEqual(hashlib.sha256(self.protected.read_bytes()).digest(), self.before)

    def test_aes256_and_document_preservation(self):
        result = unlock_pdf(self.protected, 'test-password')
        self.assertEqual(result.status, 'success', result.message)
        self.assert_preserved(result.output)

    def test_wrong_password_and_no_artifacts(self):
        result = unlock_pdf(self.protected, 'incorrect')
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.output)
        self.assertEqual(len(list(self.folder.glob('*无密码*'))), 0)
        self.assertEqual(len(list(self.folder.glob('.pdf-unlock-*'))), 0)
        self.assertEqual(hashlib.sha256(self.protected.read_bytes()).digest(), self.before)

    def test_owner_password(self):
        self.assert_preserved(unlock_pdf(self.protected, 'owner-password').output)

    def test_aes128_and_legacy_rc4(self):
        for revision, aes in [(4, True), (2, False)]:
            with self.subTest(revision=revision):
                file = self.folder / f'R{revision}.pdf'
                encrypt(self.base, file, revision=revision, aes=aes)
                result = unlock_pdf(file, 'test-password')
                self.assertEqual(result.status, 'success', result.message)
                self.assert_preserved(result.output)

    def test_unicode_and_surrounding_spaces(self):
        path = self.folder / '空格中文密码.pdf'
        encrypt(self.base, path, password=' 密码 abc ')
        self.assertEqual(unlock_pdf(path, '密码 abc').status, 'error')
        self.assert_preserved(unlock_pdf(path, ' 密码 abc ').output)

    def test_no_encryption_is_skipped(self):
        result = unlock_pdf(self.base, 'irrelevant')
        self.assertEqual(result.status, 'skipped')
        self.assertFalse(any(self.folder.glob('*无密码*')))

    def test_empty_user_password_permissions(self):
        path = self.folder / '权限限制.pdf'
        encrypt(self.base, path, password='')
        self.assert_preserved(unlock_pdf(path).output)

    def test_output_collision_and_custom_directory(self):
        folder = self.folder / '输出文件夹'
        folder.mkdir()
        existing = folder / '加密合同_无密码.pdf'
        existing.write_bytes(b'never overwrite')
        result = unlock_pdf(self.protected, 'test-password', folder)
        self.assertEqual(result.output.name, '加密合同_无密码 (2).pdf')
        self.assertEqual(existing.read_bytes(), b'never overwrite')
        self.assert_preserved(result.output)

    def test_long_filename_and_numbered_outputs(self):
        source = self.folder / ('a' * 248 + '.pdf')
        source.write_bytes(self.protected.read_bytes())
        before = source.read_bytes()
        output_dir = self.folder / 'output'
        results = [unlock_pdf(source, 'test-password', output_dir) for _ in range(3)]
        self.assertTrue(all(result.status == 'success' for result in results), results)
        self.assertEqual(len({result.output for result in results}), 3)
        for result in results:
            name = result.output.name
            units = len(name.encode('utf-16-le')) // 2 if os.name == 'nt' else len(name.encode('utf-8'))
            self.assertLessEqual(units, 255)
            self.assertIn('_无密码', name)
            self.assert_preserved(result.output)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse(any(output_dir.glob('.pdf-unlock-*')))

    @unittest.skipUnless(os.name == 'nt', 'Windows filename limits use UTF-16 units')
    def test_long_emoji_filename_keeps_whole_characters(self):
        source = self.folder / ('📄' * 124 + '.pdf')
        source.write_bytes(self.protected.read_bytes())
        results = [unlock_pdf(source, 'test-password', self.folder / 'emoji-output') for _ in range(2)]
        self.assertTrue(all(result.status == 'success' for result in results), results)
        self.assertEqual(len({result.output for result in results}), 2)
        for result in results:
            self.assertLessEqual(len(result.output.name.encode('utf-16-le')) // 2, 255)
            self.assertTrue(result.output.name.startswith('📄'))
            self.assert_preserved(result.output)

    def test_concurrent_output_collisions(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: unlock_pdf(self.protected, 'test-password'), range(3)))
        self.assertTrue(all(result.status == 'success' for result in results))
        self.assertEqual(len({result.output for result in results}), 3)
        for result in results:
            self.assert_preserved(result.output)

    def test_bad_missing_and_unwritable_targets(self):
        bad = self.folder / '损坏.pdf'
        bad.write_bytes(b'%PDF-1.7\ncorrupted')
        self.assertEqual(unlock_pdf(bad).status, 'error')
        self.assertEqual(unlock_pdf(self.folder / 'missing.pdf').status, 'error')
        self.assertEqual(unlock_pdf(self.protected, 'test-password', self.base).status, 'error')
        self.assertFalse(any(self.folder.glob('.pdf-unlock-*')))

    def test_signed_file_result_explains_invalidated_signature(self):
        signed = self.folder / '签名.pdf'
        with pikepdf.open(self.base) as pdf:
            field = pdf.make_indirect(pikepdf.Dictionary(
                FT=pikepdf.Name.Sig, T=pikepdf.String('signature'),
                V=pikepdf.Dictionary(Type=pikepdf.Name.Sig, ByteRange=pikepdf.Array([0, 10, 20, 10]),
                                     Contents=pikepdf.String('synthetic'))))
            pdf.Root.AcroForm.Fields.append(field)
            pdf.save(signed, encryption=pikepdf.Encryption(user='test-password', owner='owner-password', R=6))
        result = unlock_pdf(signed, 'test-password')
        self.assertEqual(result.status, 'success')
        self.assertIn('数字签名已失效', result.message)


class DesktopTests(unittest.TestCase):
    def assert_visible(self, widget):
        self.assertTrue(widget.winfo_ismapped())
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        width, height = widget.winfo_width(), widget.winfo_height()
        parent = widget.master
        while parent is not None:
            self.assertGreaterEqual(x, parent.winfo_rootx(), (widget, parent))
            self.assertGreaterEqual(y, parent.winfo_rooty(), (widget, parent))
            self.assertLessEqual(x + width, parent.winfo_rootx() + parent.winfo_width(), (widget, parent))
            self.assertLessEqual(y + height, parent.winfo_rooty() + parent.winfo_height(), (widget, parent))
            parent = parent.master

    def test_output_picker_accessible_on_short_screens(self):
        from app import TkinterDnD, UnlockApp, enable_dpi
        enable_dpi()
        for width, height, dpi in [(1366, 768, 96), (1920, 1080, 144), (1920, 1080, 192),
                                  (1024, 768, 192)]:
            with self.subTest(screen=(width, height), dpi=dpi):
                root = TkinterDnD.Tk()
                root.tk.call('tk', 'scaling', dpi / 72)
                root.winfo_screenwidth = lambda: width
                root.winfo_screenheight = lambda: height
                app = UnlockApp(root)
                try:
                    app.output_mode.set('custom')
                    root.update()
                    self.assert_visible(app.output_btn)
                    self.assert_visible(app.start_btn)
                    self.assertTrue(app.settings.scrollbar.winfo_ismapped())
                finally:
                    app.close()

    def test_settings_scroll_with_keyboard_and_mouse(self):
        from app import TkinterDnD, UnlockApp, enable_dpi
        enable_dpi()
        root = TkinterDnD.Tk()
        root.tk.call('tk', 'scaling', 96 / 72)
        root.winfo_screenwidth = lambda: 1366
        root.winfo_screenheight = lambda: 768
        app = UnlockApp(root)
        try:
            app.output_mode.set('custom')
            root.update()
            self.assert_visible(app.output_btn)
            app.password_entry.focus_force()
            root.update()
            self.assert_visible(app.password_entry)
            before = app.settings.canvas.yview()
            self.assertEqual(app.settings.mousewheel(SimpleNamespace(widget=app.password_entry, delta=-120)), 'break')
            self.assertNotEqual(app.settings.canvas.yview(), before)
            before = app.settings.canvas.yview()
            self.assertIsNone(app.settings.mousewheel(SimpleNamespace(widget=app.tree, delta=-120)))
            self.assertEqual(app.settings.canvas.yview(), before)
            app.output_btn.focus_force()
            root.update()
            self.assert_visible(app.output_btn)
        finally:
            app.close()

    def test_critical_controls_visible_at_different_dpi(self):
        from app import TkinterDnD, UnlockApp, enable_dpi
        enable_dpi()
        for dpi in (96, 144, 192):
            with self.subTest(dpi=dpi):
                root = TkinterDnD.Tk()
                root.tk.call('tk', 'scaling', dpi / 72)
                root.winfo_screenwidth = lambda: 1024
                root.winfo_screenheight = lambda: 768
                app = UnlockApp(root)
                root.update()
                try:
                    for control in (app.start_btn, app.status_label):
                        self.assert_visible(control)
                    # Settings scroll on a short desktop. Verify access to each
                    # control, rather than requiring them all to fit at once.
                    app.password_entry.focus_force()
                    root.update()
                    self.assert_visible(app.password_entry)
                    app.output_mode.set('custom')
                    root.update()
                    self.assert_visible(app.output_btn)
                    root.geometry(f'{app.px(940)}x{app.px(740)}')
                    root.update()
                    self.assertLessEqual(app.start_btn.winfo_rooty() + app.start_btn.winfo_height(),
                                         root.winfo_rooty() + root.winfo_height())
                    app.output_mode.set('custom')
                    root.update()
                    self.assert_visible(app.output_btn)
                finally:
                    app.close()

    def test_batch_drop_retry_and_password_clearing(self):
        from app import TkinterDnD, UnlockApp, enable_dpi
        enable_dpi()
        with tempfile.TemporaryDirectory(prefix='pdf-unlock-ui-') as directory:
            folder = Path(directory)
            base = folder / '空白.pdf'
            make_document(base)
            first = folder / 'first encrypted.pdf'
            second = folder / '第二个文档.pdf'
            encrypt(base, first, 'shared')
            encrypt(base, second, 'individual')
            root = TkinterDnD.Tk()
            root.withdraw()
            app = UnlockApp(root)
            try:
                quoted = root.tk.call('list', str(first), str(second), str(base))
                app._on_drop(SimpleNamespace(data=quoted))
                app.add_paths([first])
                self.assertEqual(len(app.items), 3)
                ids = list(app.items)
                app.shared_password.set('wrong')
                app.items[ids[1]].password = 'individual'
                app.start()
                self._wait(root, app)
                self.assertEqual([i.status for i in app.items.values()], ['error', 'success', 'skipped'])
                self.assertEqual(app.shared_password.get(), '')
                self.assertTrue(all(i.password is None for i in app.items.values()))
                app.shared_password.set('shared')
                self.assertEqual(app.items[ids[0]].status, 'pending')
                app.start()
                self._wait(root, app)
                self.assertEqual([i.status for i in app.items.values()], ['success', 'success', 'skipped'])
                self.assertEqual(len(list(folder.glob('*无密码*'))), 2)
                app.clear()
                self.assertEqual(len(app.items), 0)
            finally:
                app.close()

    def _wait(self, root, app):
        deadline = time.monotonic() + 15
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        self.assertFalse(app.busy, 'batch failed to complete')


class BuildTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell integration')
    def test_build_script_parses_in_windows_powershell_51(self):
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        if not powershell.is_file():
            self.skipTest('Windows PowerShell 5.1 is not installed')
        with tempfile.TemporaryDirectory(prefix='unlock-build-check-') as directory:
            check = Path(directory) / 'check.ps1'
            check.write_text('''param([string]$BuildPath)
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($BuildPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -gt 0) { $parseErrors | ForEach-Object { $_.Message }; exit 1 }
$expected = 'PDF' + [char]0x5bc6 + [char]0x7801 + [char]0x89e3 + [char]0x9664 + [char]0x5668
if (-not ($tokens | Where-Object { $_.Text -eq $expected })) { 'Incorrect application name encoding'; exit 2 }
'PASS'
''', encoding='ascii')
            result = subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(check), str(Path(__file__).parent / 'build.ps1')],
                                    capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(b'PASS', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
