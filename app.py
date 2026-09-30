"""PDF 密码解除器 - a private, portable Windows desktop utility."""
from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from core import UnlockResult, unlock_pdf

BG = '#F4F5F7'
WHITE = '#FFFFFF'
INK = '#202A39'
MUTED = '#778191'
LINE = '#E5E9F0'
BLUE = '#265FE8'
PALE = '#F0F5FF'
GREEN = '#247653'
RED = '#BD3D48'
FONT = 'Microsoft YaHei UI'


@dataclass
class FileItem:
    path: Path
    password: str | None = None
    status: str = 'pending'
    message: str = '等待处理'
    output: Path | None = None


def button(parent, text, command, primary=False, **kwargs):
    options = dict(
        text=text, command=command, font=(FONT, 10),
        bg=BLUE if primary else WHITE, fg=WHITE if primary else INK,
        activebackground='#1C4FC9' if primary else PALE,
        activeforeground=WHITE if primary else BLUE,
        disabledforeground='#ADB5C3', relief='flat', bd=0,
        cursor='hand2', padx=13, pady=8, highlightthickness=1,
        highlightbackground=BLUE if primary else LINE,
        highlightcolor=BLUE,
    )
    options.update(kwargs)
    return tk.Button(parent, **options)


class SettingsPane(tk.Frame):
    """Keep all settings reachable on short screens and at larger DPI scales."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, bg=BG, width=300, height=1,
                                highlightthickness=0, yscrollincrement=1)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky='ns')
        self.scrollbar.grid_remove()
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = tk.Frame(self.canvas, bg=BG)
        self.content.grid_columnconfigure(0, weight=1)
        self.window_id = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', self._update_region)
        self.canvas.bind('<Configure>', self._resize)
        self.reveal_id = None

    def _resize(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self._update_region()

    def _update_region(self, _event=None):
        region = self.canvas.bbox('all')
        if not region:
            return
        self.canvas.configure(scrollregion=region)
        if region[3] > self.canvas.winfo_height():
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()
            self.canvas.yview_moveto(0)

    def contains(self, widget):
        while widget is not None:
            if widget is self:
                return True
            widget = widget.master
        return False

    def mousewheel(self, event):
        if not self.contains(event.widget) or not event.delta:
            return None
        # Keep wheel movement comfortable while focus scrolling uses exact pixels.
        pixels_per_notch = max(1, round(self.winfo_fpixels('1i') / 4))
        steps = max(1, abs(event.delta) // 120) * pixels_per_notch
        self.canvas.yview_scroll(-steps if event.delta > 0 else steps, 'units')
        return 'break'

    def reveal(self, widget):
        if self.reveal_id is not None:
            self.after_cancel(self.reveal_id)
        self.reveal_id = self.after_idle(lambda: self._reveal_now(widget))

    def _reveal_now(self, widget):
        self.reveal_id = None
        self.update_idletasks()
        if not widget.winfo_exists():
            return
        top = widget.winfo_rooty() - self.canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        viewport = self.canvas.winfo_height()
        delta = top if top < 0 else bottom - viewport if bottom > viewport else 0
        region = self.canvas.bbox('all')
        if delta and region and region[3]:
            target = self.canvas.canvasy(0) + delta
            self.canvas.yview_moveto(max(0, target) / region[3])

    def destroy(self):
        if self.reveal_id is not None:
            self.after_cancel(self.reveal_id)
            self.reveal_id = None
        super().destroy()


class UnlockApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.items: dict[str, FileItem] = {}
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.closing = False
        self.suppress_password_trace = False
        self.next_id = 0
        self.shared_password = tk.StringVar()
        self.show_password = tk.BooleanVar()
        self.output_mode = tk.StringVar(value='original')
        self.output_dir = tk.StringVar()
        self.status = tk.StringVar(value='选择 PDF，输入已知密码，即可开始。')
        self.controls: list[tk.Widget] = []

        root.title('PDF 密码解除器 · v1.1.1')
        root.configure(bg=BG)
        self.dpi_scale = root.winfo_fpixels('1i') / 96
        width = min(self.px(1080), root.winfo_screenwidth() - self.px(60))
        height = min(self.px(800), root.winfo_screenheight() - self.px(80))
        root.geometry(f'{width}x{height}')
        root.minsize(min(self.px(940), width), min(self.px(740), height))
        root.protocol('WM_DELETE_WINDOW', self.close)
        asset_dir = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
        icon = asset_dir / 'assets' / 'app.ico'
        if icon.exists():
            root.iconbitmap(str(icon))
        self._styles()
        self._build()
        self._scale_pixels(root)
        self._register_drop()
        self.shared_password.trace_add('write', self._password_changed)
        self.output_mode.trace_add('write', self._output_mode_changed)
        root.bind('<Control-o>', lambda _: self.choose_files())
        root.bind('<Control-Return>', lambda _: self.start())
        root.bind('<Delete>', self._delete_key)
        root.bind('<MouseWheel>', self.settings.mousewheel, add='+')
        root.bind('<FocusIn>', self._settings_focus, add='+')
        self.poll_id = root.after(70, self._poll)
        self._sync_controls()

    def px(self, value):
        return round(value * self.dpi_scale)

    def _scale_pixels(self, parent):
        """Scale pixel dimensions with the system DPI; point fonts already scale in Tk."""
        def scaled(value):
            values = self.root.tk.splitlist(str(value)) if not isinstance(value, (tuple, list)) else value
            result = tuple(self.px(float(str(part))) for part in values)
            return result[0] if len(result) == 1 else result
        for widget in parent.winfo_children():
            keys = widget.keys()
            for name in ('padx', 'pady', 'highlightthickness', 'borderwidth', 'wraplength'):
                if name in keys:
                    widget.configure(**{name: scaled(widget.cget(name))})
            if isinstance(widget, (tk.Canvas, tk.Frame)):
                widget.configure(width=scaled(widget.cget('width')), height=scaled(widget.cget('height')))
            if isinstance(widget, tk.Canvas) and widget is not self.drop:
                widget.scale('all', 0, 0, self.dpi_scale, self.dpi_scale)
            manager = widget.winfo_manager()
            if manager in ('pack', 'grid'):
                info = widget.pack_info() if manager == 'pack' else widget.grid_info()
                options = {name: scaled(info[name]) for name in ('padx', 'pady', 'ipadx', 'ipady') if name in info}
                if manager == 'pack':
                    widget.pack_configure(**options)
                else:
                    widget.grid_configure(**options)
            self._scale_pixels(widget)
        if parent == self.root:
            for column in ('#0', 'size', 'password', 'status'):
                self.tree.column(column, width=self.px(self.tree.column(column, 'width')),
                                 minwidth=self.px(self.tree.column(column, 'minwidth')))

    def _styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Queue.Treeview', font=(FONT, 10), rowheight=self.px(42),
                        background=WHITE, fieldbackground=WHITE, foreground=INK,
                        borderwidth=0, relief='flat')
        style.configure('Queue.Treeview.Heading', font=(FONT, 9), padding=(self.px(8), self.px(10)),
                        background='#F7F8FA', foreground=MUTED, relief='flat')
        style.map('Queue.Treeview', background=[('selected', '#E9F0FF')],
                  foreground=[('selected', '#1646AC')])
        style.map('Queue.Treeview.Heading', background=[('active', '#F7F8FA')])
        style.configure('TEntry', padding=self.px(9), fieldbackground=WHITE,
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
        style.map('TEntry', bordercolor=[('focus', BLUE)])
        style.configure('Blue.Horizontal.TProgressbar', troughcolor=LINE,
                        background=BLUE, borderwidth=0, thickness=self.px(5))
        style.configure('Vertical.TScrollbar', background='#E0E5EC',
                        troughcolor=WHITE, borderwidth=0, arrowsize=self.px(11))

    def _label(self, parent, text='', size=10, color=INK, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                        font=(FONT, size, 'bold' if bold else 'normal'), **kw)

    def _build(self):
        header = tk.Frame(self.root, bg=WHITE, padx=28, pady=20)
        header.pack(fill='x')
        logo = tk.Canvas(header, width=48, height=48, bg=WHITE, highlightthickness=0)
        logo.pack(side='left', padx=(0, 14))
        logo.create_rectangle(0, 0, 48, 48, fill=BLUE, outline=BLUE)
        logo.create_arc(15, 7, 34, 29, start=0, extent=180, style='arc', outline=WHITE, width=3)
        logo.create_rectangle(14, 22, 34, 37, fill=WHITE, outline=WHITE)
        logo.create_oval(22, 27, 26, 31, fill=BLUE, outline=BLUE)
        logo.create_line(24, 30, 24, 33, fill=BLUE, width=2)
        names = tk.Frame(header, bg=WHITE)
        names.pack(side='left')
        self._label(names, 'PDF 密码解除器', size=19, bold=True).pack(anchor='w')
        self._label(names, '输入一次密码，以后轻松打开', size=10, color=MUTED).pack(anchor='w', pady=(4, 0))
        self._label(header, '●  本机处理  ·  保留原件', color=GREEN, size=10).pack(side='right')
        tk.Frame(self.root, bg=LINE, height=1).pack(fill='x')

        body = tk.Frame(self.root, bg=BG, padx=24, pady=22)
        body.pack(fill='both', expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0, minsize=self.px(300))
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=WHITE, highlightbackground=LINE, highlightthickness=1)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 18))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(2, weight=1)
        tools = tk.Frame(left, bg=WHITE, padx=16, pady=13)
        tools.grid(row=0, column=0, sticky='ew')
        self._label(tools, '文件队列', size=12, bold=True).pack(side='left')
        self.count_label = self._label(tools, '0 个', color=MUTED, size=9)
        self.count_label.pack(side='left', padx=9)
        self.clear_btn = button(tools, '清空', self.clear)
        self.clear_btn.pack(side='right', padx=(7, 0))
        self.folder_btn = button(tools, '文件夹', self.choose_folder)
        self.folder_btn.pack(side='right', padx=(7, 0))
        self.add_btn = button(tools, '选 PDF', self.choose_files, primary=True)
        self.add_btn.pack(side='right')
        self.controls.extend([self.clear_btn, self.folder_btn, self.add_btn])

        self.drop = tk.Canvas(left, bg=PALE, height=128, highlightthickness=0, cursor='hand2')
        self.drop.grid(row=1, column=0, sticky='ew', padx=16, pady=(0, 16))
        self.drop.bind('<Configure>', self._draw_drop)
        self.drop.bind('<Button-1>', lambda _: self.choose_files())

        table = tk.Frame(left, bg=WHITE)
        table.grid(row=2, column=0, sticky='nsew', padx=8)
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('size', 'password', 'status'),
                                 style='Queue.Treeview', selectmode='extended')
        self.tree.heading('#0', text='文件名', anchor='w')
        self.tree.heading('size', text='大小', anchor='w')
        self.tree.heading('password', text='密码', anchor='w')
        self.tree.heading('status', text='状态', anchor='w')
        self.tree.column('#0', width=230, minwidth=150)
        self.tree.column('size', width=70, minwidth=60, stretch=False)
        self.tree.column('password', width=85, minwidth=80, stretch=False)
        self.tree.column('status', width=96, minwidth=88, stretch=False)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        for tag, color in [('pending', MUTED), ('success', GREEN), ('skipped', MUTED),
                           ('error', RED), ('processing', BLUE)]:
            self.tree.tag_configure(tag, foreground=color)
        self.tree.bind('<<TreeviewSelect>>', lambda _: self._selection())
        self.tree.bind('<Double-1>', lambda _: self.set_individual_password())

        details = tk.Frame(left, bg=WHITE, padx=16, pady=12)
        details.grid(row=3, column=0, sticky='ew')
        tk.Frame(details, bg=LINE, height=1).pack(fill='x', pady=(0, 10))
        self.detail_label = self._label(details, '支持拖入 PDF 或文件夹；双击文件可单独设置密码。',
                                       size=9, color=MUTED, anchor='w', justify='left', wraplength=520)
        self.detail_label.pack(fill='x', anchor='w')
        details.bind('<Configure>', lambda e: self.detail_label.configure(wraplength=max(self.px(200), e.width - self.px(32))))
        detail_actions = tk.Frame(details, bg=WHITE)
        detail_actions.pack(fill='x', pady=(9, 0))
        self.individual_btn = button(detail_actions, '设置单独密码', self.set_individual_password)
        self.individual_btn.pack(side='left')
        self.remove_btn = button(detail_actions, '移除所选', self.remove)
        self.remove_btn.pack(side='right')
        self.controls.extend([self.individual_btn, self.remove_btn])

        right = SettingsPane(body, bg=BG, width=300)
        self.settings = right
        right.grid(row=0, column=1, sticky='nsew')
        config = tk.Frame(right.content, bg=WHITE, padx=20, pady=20,
                          highlightbackground=LINE, highlightthickness=1)
        config.grid(row=0, column=0, sticky='ew')
        self._label(config, '解除设置', size=12, bold=True).pack(anchor='w')
        self._label(config, '共用密码', size=10, bold=True).pack(anchor='w', pady=(22, 8))
        self.password_entry = ttk.Entry(config, textvariable=self.shared_password,
                                        show='•', font=(FONT, 11), width=22)
        self.password_entry.pack(fill='x')
        self.password_entry.bind('<Return>', lambda _: self.start())
        show = tk.Checkbutton(config, text='显示密码', variable=self.show_password,
                              command=lambda: self.password_entry.configure(show='' if self.show_password.get() else '•'),
                              bg=WHITE, activebackground=WHITE, fg=MUTED, selectcolor=WHITE,
                              font=(FONT, 9), bd=0, padx=0)
        show.pack(anchor='w', pady=(8, 0))
        self._label(config, '多个文件密码相同，填一次即可。\n密码不同，可为所选文件单独设置。',
                    size=9, color=MUTED, justify='left').pack(anchor='w', pady=(10, 0))
        self.controls.extend([self.password_entry, show])

        output = tk.Frame(right.content, bg=WHITE, padx=20, pady=18,
                          highlightbackground=LINE, highlightthickness=1)
        output.grid(row=1, column=0, sticky='ew', pady=(14, 0))
        self._label(output, '输出位置', size=11, bold=True).pack(anchor='w', pady=(0, 9))
        for value, text in [('original', '原文件所在文件夹'), ('custom', '统一保存到指定文件夹')]:
            radio = tk.Radiobutton(output, text=text, value=value, variable=self.output_mode,
                                   font=(FONT, 10), bg=WHITE, fg=INK, activebackground=WHITE,
                                   selectcolor=WHITE, anchor='w', padx=0)
            radio.pack(fill='x', pady=3)
            self.controls.append(radio)
        self.output_path_label = self._label(output, '尚未选择文件夹', size=9, color=MUTED,
                                             justify='left', anchor='w', wraplength=252)
        self.output_path_label.pack(fill='x', pady=(9, 8))
        output.bind('<Configure>', lambda e: self.output_path_label.configure(
            wraplength=max(self.px(100), e.width - self.px(42))))
        self.output_btn = button(output, '选择输出文件夹', self.choose_output)
        self.output_btn.pack(fill='x')
        self.controls.append(self.output_btn)
        self._label(output, '另存为：文件名_无密码.pdf\n重名时自动编号，原文件始终保留。',
                    size=9, color=MUTED, justify='left').pack(anchor='w', pady=(14, 0))

        footer = tk.Frame(self.root, bg=WHITE, padx=26, pady=13)
        footer.pack(fill='x', side='bottom', before=body)
        action = tk.Frame(footer, bg=WHITE)
        action.pack(side='right', padx=(18, 0))
        self.action_area = action
        self.start_btn = button(action, '解除密码', self.start, primary=True, pady=13)
        self.start_btn.configure(font=(FONT, 11, 'bold'))
        self.start_btn.pack(fill='x')
        self.stop_btn = button(footer, '停止后续文件', self.stop)
        self.status_label = self._label(footer, size=9, color=MUTED,
                                       textvariable=self.status, anchor='w', justify='left', wraplength=460)
        self.status_label.pack(side='left', fill='x', expand=True)
        self.open_btn = button(footer, '打开输出文件夹', self.open_output)
        self.open_btn.pack(side='right', padx=(14, 0))
        footer.bind('<Configure>', lambda e: self.status_label.configure(
            wraplength=max(self.px(120), e.width - self.action_area.winfo_reqwidth()
                           - self.open_btn.winfo_reqwidth() - (self.stop_btn.winfo_reqwidth() if self.busy else 0)
                           - self.px(95))))
        self.progress = ttk.Progressbar(self.root, style='Blue.Horizontal.TProgressbar', maximum=100)
        self.progress.pack(fill='x', side='bottom', before=footer)

    def _draw_drop(self, _event=None):
        self.drop.delete('all')
        w, h = self.drop.winfo_width() / self.dpi_scale, self.drop.winfo_height() / self.dpi_scale
        self.drop.create_rectangle(1, 1, w - 2, h - 2, outline='#C6D6F9', dash=(5, 4))
        if self.items:
            self.drop.create_text(w / 2, h / 2, text='＋  拖入更多 PDF，或点击选择',
                                  fill=BLUE, font=(FONT, 10))
        else:
            x = w / 2
            self.drop.create_line(x, 21, x, 47, fill=BLUE, width=2)
            self.drop.create_line(x - 7, 28, x, 21, x + 7, 28, fill=BLUE, width=2)
            self.drop.create_line(x - 15, 40, x - 15, 53, x + 15, 53, x + 15, 40,
                                  fill=BLUE, width=2)
            self.drop.create_text(x, 77, text='把 PDF 拖到这里', fill=INK, font=(FONT, 12, 'bold'))
            self.drop.create_text(x, 104, text='或点击选择文件 · 支持批量处理', fill=MUTED, font=(FONT, 9))
        self.drop.scale('all', 0, 0, self.dpi_scale, self.dpi_scale)

    def _register_drop(self):
        for widget in [self.root, self.drop, self.tree]:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind('<<Drop>>', self._on_drop)

    def _on_drop(self, event):
        if not self.busy:
            self.add_paths(self.root.tk.splitlist(event.data))
        return 'copy'

    def choose_files(self):
        if self.busy:
            return
        paths = filedialog.askopenfilenames(parent=self.root, title='选择要解除密码的 PDF',
                                            filetypes=[('PDF 文件', '*.pdf *.PDF')])
        if paths:
            self.add_paths(paths)

    def choose_folder(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(parent=self.root, title='选择包含 PDF 的文件夹')
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths):
        if self.busy:
            return
        known = {os.path.normcase(str(item.path)) for item in self.items.values()}
        added, ignored = 0, 0
        expanded = []
        for raw in paths:
            path = Path(raw).resolve()
            if path.is_dir():
                try:
                    expanded.extend(sorted((p for p in path.iterdir() if p.suffix.lower() == '.pdf'),
                                           key=lambda p: p.name.lower()))
                except OSError:
                    ignored += 1
            else:
                expanded.append(path)
        for path in expanded:
            key = os.path.normcase(str(path.resolve()))
            if key in known:
                continue
            if not path.is_file() or path.suffix.lower() != '.pdf':
                ignored += 1
                continue
            known.add(key)
            self.next_id += 1
            item_id = str(self.next_id)
            self.items[item_id] = FileItem(path)
            self.tree.insert('', 'end', iid=item_id, text=path.name)
            self._row(item_id)
            added += 1
        self.drop.configure(height=self.px(58 if self.items else 128))
        self._draw_drop()
        self.count_label.configure(text=f'{len(self.items)} 个')
        if added:
            self.status.set(f'已添加 {added} 个 PDF。输入已知密码后，点击“解除密码”。')
        else:
            self.status.set('没有添加新 PDF；重复文件会自动忽略。')
        if ignored:
            self.status.set(self.status.get() + f' 已忽略 {ignored} 个无法读取或非 PDF 的项目。')
        self.progress['value'] = 0
        self._sync_controls()

    def _row(self, item_id):
        item = self.items[item_id]
        try:
            size = item.path.stat().st_size
            size_text = f'{size / 1048576:.1f} MB' if size >= 1048576 else f'{max(1, size / 1024):.0f} KB'
        except OSError:
            size_text = '—'
        labels = {'pending': '等待处理', 'processing': '正在解除', 'success': '已解除',
                  'error': '处理失败', 'skipped': '无需解除'}
        self.tree.item(item_id, values=(size_text, '单独密码' if item.password is not None else '共用密码',
                                       labels[item.status]), tags=(item.status,))

    def _selection(self):
        selected = self.tree.selection()
        if selected:
            item = self.items[selected[0]]
            detail = f'{item.message}\n{item.output or item.path}'
            if len(selected) > 1:
                detail = f'已选择 {len(selected)} 个文件；设置密码将应用到全部所选文件。'
            self.detail_label.configure(text=detail, fg=RED if item.status == 'error' else MUTED)
        else:
            self.detail_label.configure(text='支持拖入 PDF 或文件夹；双击文件可单独设置密码。', fg=MUTED)
        self._sync_controls()

    def _sync_controls(self):
        for widget in self.controls:
            widget.configure(state='disabled' if self.busy else 'normal')
        selected = bool(self.tree.selection())
        for widget in [self.individual_btn, self.remove_btn]:
            widget.configure(state='normal' if selected and not self.busy else 'disabled')
        self.clear_btn.configure(state='normal' if self.items and not self.busy else 'disabled')
        self.output_btn.configure(state='normal' if self.output_mode.get() == 'custom' and not self.busy else 'disabled')
        pending = sum(i.status in ('pending', 'error') for i in self.items.values())
        if self.busy:
            self.start_btn.configure(text='正在解除密码…', state='disabled')
        else:
            text = f'解除密码 · {pending} 个文件' if pending else ('已处理完成' if self.items else '解除密码')
            self.start_btn.configure(text=text, state='normal' if pending else 'disabled')
        self.open_btn.configure(state='normal' if not self.busy and any(i.output for i in self.items.values()) else 'disabled')

    def _output_mode_changed(self, *_):
        self._sync_controls()
        if self.output_mode.get() == 'custom':
            self.settings.reveal(self.output_btn)

    def _settings_focus(self, event):
        if self.settings.contains(event.widget):
            self.settings.reveal(event.widget)

    def _password_changed(self, *_):
        if self.busy or self.suppress_password_trace:
            return
        for item_id, item in self.items.items():
            if item.status == 'error' and item.password is None:
                item.status, item.message = 'pending', '已更新密码，等待重试'
                self._row(item_id)
        self._sync_controls()

    def _delete_key(self, _event):
        if self.root.focus_get() == self.tree:
            self.remove()

    def remove(self):
        if self.busy:
            return
        for item_id in self.tree.selection():
            self.items[item_id].password = None
            self.items.pop(item_id)
            self.tree.delete(item_id)
        self._after_remove()

    def clear(self):
        if self.busy:
            return
        for item in self.items.values():
            item.password = None
        self.items.clear()
        self.tree.delete(*self.tree.get_children())
        self.shared_password.set('')
        self.progress['value'] = 0
        self._after_remove()

    def _after_remove(self):
        self.drop.configure(height=self.px(58 if self.items else 128))
        self._draw_drop()
        self.count_label.configure(text=f'{len(self.items)} 个')
        self._selection()
        self.status.set('选择 PDF，输入已知密码，即可开始。' if not self.items else f'队列中有 {len(self.items)} 个 PDF。')

    def set_individual_password(self):
        selected = self.tree.selection()
        if self.busy or not selected:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('设置单独密码')
        dialog.configure(bg=WHITE)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        frame = tk.Frame(dialog, bg=WHITE, padx=24, pady=22)
        frame.pack(fill='both', expand=True)
        self._label(frame, '设置单独密码', size=14, bold=True).pack(anchor='w')
        title = self.items[selected[0]].path.name if len(selected) == 1 else f'为 {len(selected)} 个所选文件设置相同密码'
        self._label(frame, title, color=MUTED, justify='left', wraplength=370).pack(anchor='w', pady=(8, 16))
        value = tk.StringVar(value=self.items[selected[0]].password or '')
        entry = ttk.Entry(frame, textvariable=value, show='•', font=(FONT, 11), width=32)
        entry.pack(fill='x')
        reveal = tk.BooleanVar()
        tk.Checkbutton(frame, text='显示密码', variable=reveal, bg=WHITE, activebackground=WHITE,
                       font=(FONT, 9), fg=MUTED,
                       command=lambda: entry.configure(show='' if reveal.get() else '•')).pack(anchor='w', pady=8)
        self._label(frame, '这里的密码优先于共用密码；不会保存到磁盘。', size=9, color=MUTED).pack(anchor='w')
        actions = tk.Frame(frame, bg=WHITE)
        actions.pack(fill='x', pady=(20, 0))

        def apply(use_shared=False):
            for item_id in selected:
                item = self.items[item_id]
                item.password = None if use_shared else value.get()
                if item.status == 'error':
                    item.status, item.message = 'pending', '已更新密码，等待重试'
                self._row(item_id)
            value.set('')
            dialog.destroy()
            self._selection()

        button(actions, '使用共用密码', lambda: apply(True)).pack(side='left')
        button(actions, '应用密码', apply, primary=True).pack(side='right')
        dialog.bind('<Return>', lambda _: apply())
        dialog.bind('<Escape>', lambda _: dialog.destroy())
        self._scale_pixels(dialog)
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f'+{max(0, x)}+{max(0, y)}')
        entry.focus_set()

    def choose_output(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self.root, title='选择无密码 PDF 的保存位置')
        if path:
            self.output_dir.set(path)
            self.output_path_label.configure(text=path)
            self.settings.reveal(self.output_btn)

    def start(self):
        if self.busy:
            return
        jobs = [(item_id, item.path, item.password if item.password is not None else self.shared_password.get())
                for item_id, item in self.items.items() if item.status in ('pending', 'error')]
        if not jobs:
            return
        output = self.output_dir.get() if self.output_mode.get() == 'custom' else None
        if self.output_mode.get() == 'custom' and not output:
            self.status.set('请先选择输出文件夹。')
            self.choose_output()
            return
        self.busy = True
        self.stop_event.clear()
        self.progress['value'] = 0
        self.stop_btn.configure(state='normal', text='停止后续文件')
        self.stop_btn.pack(side='right', padx=(self.px(12), 0), before=self.action_area)
        self._sync_controls()
        threading.Thread(target=self._worker, args=(jobs, output), daemon=False).start()

    def _worker(self, jobs, output):
        counts = {'success': 0, 'error': 0, 'skipped': 0}
        try:
            for index, (item_id, path, password) in enumerate(jobs):
                if self.stop_event.is_set():
                    break
                self.events.put(('begin', item_id, index + 1, len(jobs)))
                result = unlock_pdf(path, password, output,
                                    progress=lambda p, i=index: self.events.put(('progress', (i + .85 * p / 100) / len(jobs) * 100)))
                counts[result.status] += 1
                self.events.put(('result', item_id, result))
                self.events.put(('progress', (index + 1) / len(jobs) * 100))
                password = ''
        finally:
            # Drop passwords from the snapshot before notifying the UI.
            jobs.clear()
            self.events.put(('done', counts, self.stop_event.is_set()))

    def _poll(self):
        self.poll_id = None
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == 'begin':
                    item_id, index, total = event[1:]
                    item = self.items[item_id]
                    item.status, item.message = 'processing', '正在解除密码…'
                    self._row(item_id)
                    self.tree.see(item_id)
                    self.status.set(f'正在处理 {index} / {total}：{item.path.name[:45]}')
                elif kind == 'progress':
                    self.progress['value'] = event[1]
                elif kind == 'result':
                    item_id, result = event[1:]
                    item = self.items[item_id]
                    item.status, item.message, item.output = result.status, result.message, result.output
                    item.password = None
                    self._row(item_id)
                    self._selection()
                elif kind == 'done':
                    counts, cancelled = event[1:]
                    self.busy = False
                    self.suppress_password_trace = True
                    self.shared_password.set('')
                    self.suppress_password_trace = False
                    for item in self.items.values():
                        item.password = None
                    for item_id in self.items:
                        self._row(item_id)
                    self.show_password.set(False)
                    self.password_entry.configure(show='•')
                    self.stop_btn.pack_forget()
                    prefix = '已停止' if cancelled else '处理完成'
                    self.status.set(f'{prefix}：成功 {counts["success"]}，失败 {counts["error"]}，无需解除 {counts["skipped"]}。')
                    self._sync_controls()
                    if self.closing:
                        self._destroy()
                        return
        except queue.Empty:
            pass
        self.poll_id = self.root.after(70, self._poll)

    def stop(self):
        if self.busy:
            self.stop_event.set()
            self.stop_btn.configure(state='disabled', text='将在当前文件完成后停止')
            self.status.set('正在完成当前文件，其余文件将保留在队列中。')

    def open_output(self):
        if self.busy:
            return
        selected = self.tree.selection()
        item = self.items[selected[0]] if selected else None
        target = item.output if item and item.output else next((i.output for i in reversed(list(self.items.values())) if i.output), None)
        if target:
            try:
                os.startfile(str(target.parent))
            except OSError:
                self.status.set('无法打开文件夹，请从文件详情中查看保存位置。')

    def close(self):
        if self.busy:
            self.closing = True
            self.stop()
            self.status.set('正在完成当前文件，完成后自动关闭。')
        else:
            self.shared_password.set('')
            for item in self.items.values():
                item.password = None
            self._destroy()

    def _destroy(self):
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        self.root.destroy()


def enable_dpi():
    if os.name == 'nt':
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def main():
    enable_dpi()
    if len(sys.argv) == 4 and sys.argv[1] == '--self-check':
        # Build verification only: uses synthetic PDFs, never a real user's password.
        request = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
        root = TkinterDnD.Tk()
        root.withdraw()
        report = {'tk': str(root.tk.call('info', 'patchlevel')),
                  'dnd': str(root.tk.call('package', 'require', 'tkdnd')), 'results': []}
        for job in request:
            result = unlock_pdf(job['source'], job.get('password', ''), job.get('output_dir'))
            report['results'].append({'status': result.status, 'message': result.message,
                                      'output': str(result.output) if result.output else None, 'pages': result.pages})
        root.destroy()
        Path(sys.argv[3]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    root = TkinterDnD.Tk()
    app = UnlockApp(root)
    if len(sys.argv) > 1:
        root.after(100, lambda: app.add_paths(sys.argv[1:]))
    root.mainloop()


if __name__ == '__main__':
    main()
