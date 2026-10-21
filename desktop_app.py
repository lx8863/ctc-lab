"""Local Tkinter control panel. No browser or HTTP server is started."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from dashboard_server import (ARTIFACTS, ROOT, LABELS, JobManager,
                              read_json, report_index, selected_report, spectrum_data)
from send_receive import LIMITS, parse_payload

PROTOCOLS = {'蓝牙广播（BLE）': 'ble', '802.15.4 底层数据帧': 'ieee802154',
             '跨协议发送（BLE → 802.15.4）': 'ctc', 'Nordic ESB（单向数据包）': 'esb'}


class DesktopApp:
    def __init__(self, root):
        self.root = root
        self.manager = JobManager()
        self.events = queue.Queue()
        self.report = None
        self.spectrum = {}
        self.seen_job = None
        self.last_logs = None
        self.loading = 0
        self.closing = False
        root.title('CTC Lab · 本地 Python 实验台')
        root.geometry('1240x800')
        root.minsize(1030, 740)
        root.configure(bg='#edf2f7')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Microsoft YaHei UI', 10))
        style.configure('TFrame', background='#edf2f7')
        style.configure('TLabelframe', background='#edf2f7')
        style.configure('TLabelframe.Label', background='#edf2f7', foreground='#243852')
        style.configure('TLabel', background='#edf2f7', foreground='#243852')
        style.configure('Title.TLabel', font=('Microsoft YaHei UI', 22, 'bold'))
        style.configure('Field.TLabel', font=('Microsoft YaHei UI', 10, 'bold'))
        style.configure('Help.TLabel', foreground='#52667d')
        style.configure('Error.TLabel', foreground='#b3261e')
        style.configure('TButton', padding=(12, 7))
        style.configure('Treeview', rowheight=29, font=('Consolas', 10))
        outer = ttk.Frame(root, padding=20)
        outer.pack(fill='both', expand=True)
        header = ttk.Frame(outer)
        header.pack(fill='x')
        ttk.Label(header, text='CTC LAB', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='本地 Python 实验台  /  多协议收发与 CTC').pack(side='left', padx=22)
        self.state = tk.StringVar(value='就绪 · 当前程序未启动发射')
        ttk.Label(header, textvariable=self.state).pack(side='right')
        summary = read_json(ARTIFACTS/'ctc_summary.json', {})
        self.workflow = tk.StringVar(value='选择协议 → 输入数据 → 发送与采集 → 校验并对比接收数据')
        ttk.Label(outer, textvariable=self.workflow,
                  font=('Microsoft YaHei UI', 12)).pack(anchor='w', pady=(8, 12))
        payload_box = ttk.LabelFrame(outer, text='发送内容', padding=10)
        payload_box.pack(fill='x', pady=(0, 10))
        self.protocol = tk.StringVar(value='跨协议发送（BLE → 802.15.4）')
        self.encoding = tk.StringVar(value='UTF-8 文本')
        self.payload = tk.StringVar(value='Hello CTC')
        self.payload_hint = tk.StringVar()
        self.protocol_help = tk.StringVar()
        self.format_help = tk.StringVar()
        self.protocol_details = tk.StringVar()
        for column, text in enumerate(('① 发送协议', '② 数据格式', '③ 要发送的内容')):
            ttk.Label(payload_box, text=text, style='Field.TLabel').grid(row=0,column=column,sticky='w',pady=(0,6))
        self.protocol_choice = ttk.Combobox(payload_box, textvariable=self.protocol,
            values=list(PROTOCOLS), state='readonly', width=34)
        self.protocol_choice.grid(row=1, column=0, padx=(0, 12))
        self.encoding_choice = ttk.Combobox(payload_box, textvariable=self.encoding,
            values=['UTF-8 文本', 'HEX 十六进制'], state='readonly', width=15)
        self.encoding_choice.grid(row=1, column=1, padx=(0, 12))
        ttk.Entry(payload_box, textvariable=self.payload, width=55).grid(row=1, column=2, sticky='ew')
        payload_box.columnconfigure(2, weight=1)
        ttk.Label(payload_box, textvariable=self.protocol_help, wraplength=1070).grid(row=2,column=0,columnspan=3,sticky='w',pady=(10,3))
        self.payload_status = ttk.Label(payload_box, textvariable=self.payload_hint, style='Help.TLabel', wraplength=1070)
        self.payload_status.grid(row=3,column=0,columnspan=3,sticky='w',pady=(0,3))
        ttk.Label(payload_box, textvariable=self.format_help, style='Help.TLabel',wraplength=1070).grid(row=4,column=0,columnspan=3,sticky='w')
        self.show_details = tk.BooleanVar(value=False)
        self.details_label = ttk.Label(payload_box, textvariable=self.protocol_details, style='Help.TLabel',wraplength=1070)
        def toggle_details():
            if self.show_details.get():
                self.details_label.grid(row=6,column=0,columnspan=3,sticky='w',pady=(5,0))
            else:
                self.details_label.grid_remove()
        ttk.Checkbutton(payload_box,text='查看技术参数与术语解释（可选）',variable=self.show_details,
                        command=toggle_details).grid(row=5,column=0,columnspan=3,sticky='w',pady=(6,0))
        for variable in (self.protocol, self.encoding, self.payload):
            variable.trace_add('write', lambda *_: self.describe_payload())
        self.describe_payload()

        controls = ttk.LabelFrame(outer, text='实验控制 · 同一时间执行一个任务', padding=12)
        controls.pack(fill='x')
        self.fields = {}
        values = [('uri', '设备 URI', 'usb:1.5.5'), ('snr', 'SNR / dB', '24'),
                  ('cfo', '频偏 / Hz', '12000'), ('clock', '时钟 / ppm', '10'),
                  ('tx_gain', 'TX / dB', '-25'), ('rx_gain', 'RX / dB', '20'),
                  ('seconds', 'RF 时长 / s', '5'), ('blocks', '采集块数', '4')]
        for i, (key, label, default) in enumerate(values):
            cell = ttk.Frame(controls)
            cell.grid(row=0, column=i, sticky='ew', padx=4)
            controls.columnconfigure(i, weight=1)
            ttk.Label(cell, text=label).pack(anchor='w')
            self.fields[key] = tk.StringVar(value=default)
            ttk.Entry(cell, textvariable=self.fields[key], width=13).pack(fill='x', pady=5)
        buttons = ttk.Frame(controls)
        buttons.grid(row=1, column=0, columnspan=8, sticky='ew', pady=(7, 0))
        self.action_buttons = {}
        for action in ('devices', 'send_simulate', 'send_rf', 'symbols', 'validate'):
            button = ttk.Button(buttons, text=LABELS[action], command=lambda a=action: self.start(a))
            button.pack(side='left', padx=(0, 7))
            self.action_buttons[action] = button
        self.stop_button = ttk.Button(buttons, text='停止任务', command=self.stop, state='disabled')
        self.stop_button.pack(side='right')
        self.device_state = tk.StringVar(value='“仅仿真”不使用板卡；“硬件 RF”会实际发射并采集。两种模式均自动解析与对比数据。')
        ttk.Label(controls, textvariable=self.device_state, wraplength=1070).grid(row=2, column=0, columnspan=8, sticky='w', pady=(9, 0))

        row = ttk.Frame(outer, padding=(0, 12))
        row.pack(fill='x')
        ttk.Label(row, text='实验报告').pack(side='left')
        self.report_choice = ttk.Combobox(row, state='readonly', width=68)
        self.report_choice.pack(side='left', fill='x', expand=True, padx=10)
        self.report_choice.bind('<<ComboboxSelected>>', lambda _: self.load_report(self.report_choice.get()))
        ttk.Button(row, text='刷新', command=self.refresh).pack(side='left', padx=4)
        ttk.Button(row, text='导出 JSON', command=self.export).pack(side='left', padx=4)
        ttk.Button(row, text='打开报告目录', command=self.open_folder).pack(side='left', padx=4)
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill='both', expand=True)
        result_tab = ttk.Frame(self.tabs, padding=10)
        spectrum_tab = ttk.Frame(self.tabs, padding=10)
        log_tab = ttk.Frame(self.tabs, padding=10)
        raw_tab = ttk.Frame(self.tabs, padding=10)
        for frame, label in [(result_tab, '解码结果'), (spectrum_tab, '接收频谱'), (log_tab, '任务日志'), (raw_tab, '完整报告')]:
            self.tabs.add(frame, text=label)
        ors_tab = ttk.Frame(self.tabs,padding=10)
        self.tabs.add(ors_tab,text='论文复现 · ORS ACK')
        ttk.Label(ors_tab,text='第一阶段：用 OQPSK 波形发送 ACK，按 BLE 的 1 微秒相位差间隔检测其存在性。',
                  style='Field.TLabel',wraplength=1090).pack(anchor='w',pady=(0,5))
        ttk.Label(ors_tab,text='本页不使用上方的协议与输入数据。仿真使用上方噪声/频偏设置；硬件使用上方设备、增益、时长与块数。',
                  style='Help.TLabel',wraplength=1090).pack(anchor='w')
        ors_actions = ttk.Frame(ors_tab)
        ors_actions.pack(fill='x',pady=10)
        for action,label in [('ors_simulate','仅仿真检测 ACK'),('ors_rf','硬件发射与检测 ACK'),
                             ('ors_background','背景检查（不发射）'),('ors_validate','扰动与负样本验证')]:
            button = ttk.Button(ors_actions,text=label,command=lambda a=action:self.start(a))
            button.pack(side='left',padx=(0,8))
            self.action_buttons[action] = button
        self.ors_note = tk.StringVar(value='选择一种检测方式。这里不进行载荷、CRC 或 ACK 类型判决。')
        ttk.Label(ors_tab,textvariable=self.ors_note,wraplength=1090).pack(anchor='w',pady=(0,8))
        ors_table = ttk.Frame(ors_tab)
        ors_table.pack(fill='both',expand=True)
        self.ors_frames = ttk.Treeview(ors_table,columns=('block','time','count','power','coherence','shape','cfo'),show='headings',height=4)
        for key,title,width in [('block','采集块',70),('time','检测位置 / ms',150),('count','重复匹配数 / 15',160),
                                ('power','高于噪声 / dB',130),('coherence','重复相干度',130),
                                ('shape','波形一致性',130),('cfo','残余频偏 / Hz',160)]:
            self.ors_frames.heading(key,text=title)
            self.ors_frames.column(key,width=width,minwidth=60)
        ors_scroll = ttk.Scrollbar(ors_table,orient='vertical',command=self.ors_frames.yview)
        ors_scroll.pack(side='right',fill='y')
        self.ors_frames.configure(yscrollcommand=ors_scroll.set)
        self.ors_frames.pack(fill='both',expand=True)
        feedback_tab = ttk.Frame(self.tabs,padding=10)
        self.tabs.add(feedback_tab,text='ACK 类型与闭环')
        ttk.Label(feedback_tab,text='第二阶段：用参考段与差分位编码四类状态，加入序号和 CRC8；不是原论文的绝对象限判决。',
                  style='Field.TLabel',wraplength=1090).pack(anchor='w')
        ttk.Label(feedback_tab,text='硬件使用上方设备、增益和块数，采集后立即停发；不使用上方 RF 保持时长。电脑顺序调度，不是标准实时 ACK。',
                  style='Help.TLabel',wraplength=1090).pack(anchor='w',pady=(4,7))
        self.feedback_data = tk.StringVar(value='Hello CTC ACK')
        self.feedback_encoding = tk.StringVar(value='UTF-8 文本')
        self.feedback_attempts = tk.StringVar(value='3')
        self.feedback_timeout = tk.StringVar(value='10')
        feedback_input = ttk.Frame(feedback_tab)
        feedback_input.pack(fill='x')
        ttk.Label(feedback_input,text='闭环数据（最多 38 字节）').pack(side='left')
        ttk.Combobox(feedback_input,textvariable=self.feedback_encoding,values=['UTF-8 文本','HEX 十六进制'],
                     state='readonly',width=14).pack(side='left',padx=6)
        ttk.Entry(feedback_input,textvariable=self.feedback_data,width=28).pack(side='left',fill='x',expand=True)
        ttk.Label(feedback_input,text='ACK 超时/s').pack(side='left',padx=(10,3))
        ttk.Entry(feedback_input,textvariable=self.feedback_timeout,width=4).pack(side='left')
        ttk.Label(feedback_input,text='最多轮数').pack(side='left',padx=(10,3))
        ttk.Entry(feedback_input,textvariable=self.feedback_attempts,width=3).pack(side='left')
        for row in [[('feedback_types_simulate','四类 ACK 仿真'),('feedback_types_rf','四类 ACK 硬件测试'),
                     ('feedback_background','只接收背景检查')],
                    [('feedback_loop_simulate','闭环仿真：丢 ACK 后重传'),('feedback_loop_rf','硬件数据与 ACK 闭环'),
                     ('feedback_validate','扰动与负样本验证')]]:
            bar = ttk.Frame(feedback_tab)
            bar.pack(fill='x',pady=(7,0))
            for action,label in row:
                button = ttk.Button(bar,text=label,command=lambda a=action:self.start(a))
                button.pack(side='left',padx=(0,8))
                self.action_buttons[action] = button
        self.feedback_note = tk.StringVar(value='类型测试不使用输入数据；闭环按钮使用本页数据。仿真重传按钮会故意丢掉第一次 ACK。')
        ttk.Label(feedback_tab,textvariable=self.feedback_note,wraplength=1090).pack(anchor='w',pady=8)
        feedback_table = ttk.Frame(feedback_tab)
        feedback_table.pack(fill='both',expand=True)
        self.feedback_frames = ttk.Treeview(feedback_table,columns=('round','sequence','state','type','check','detail'),
                                            show='headings',height=4)
        for key,title,width in [('round','轮次 / 帧',95),('sequence','序号',70),('state','结果',120),
                                ('type','ACK 类型',160),('check','CRC8',70),('detail','接收数据 / 检测详情',520)]:
            self.feedback_frames.heading(key,text=title)
            self.feedback_frames.column(key,width=width,minwidth=60)
        feedback_scroll = ttk.Scrollbar(feedback_table,orient='vertical',command=self.feedback_frames.yview)
        feedback_scroll.pack(side='right',fill='y')
        self.feedback_frames.configure(yscrollcommand=feedback_scroll.set)
        self.feedback_frames.pack(fill='both',expand=True)
        def show_task_fields(_=None):
            tab_index = self.tabs.index(self.tabs.select())
            is_ors = tab_index in (4,5)
            if is_ors:
                payload_box.pack_forget()
                self.workflow.set('ORS ACK 实验：选择方式 → 仿真或采集 → 检测波形特征')
                if tab_index==5:
                    self.workflow.set('数据校验 → 带序号的 ACK → 确认送达或超时重传')
            else:
                if not payload_box.winfo_manager():
                    payload_box.pack(fill='x',pady=(0,10),before=controls)
                self.workflow.set('选择协议 → 输入数据 → 发送与采集 → 校验并对比接收数据')
            for action in ('send_simulate','send_rf','symbols','validate'):
                button = self.action_buttons[action]
                if is_ors:
                    button.pack_forget()
                elif not button.winfo_manager():
                    button.pack(side='left',padx=(0,7),before=self.stop_button)
        self.tabs.bind('<<NotebookTabChanged>>',show_task_fields)
        self.result_note = tk.StringVar(value='正在读取历史报告…')
        ttk.Label(result_tab, textvariable=self.result_note, wraplength=1100).pack(anchor='w', pady=(0, 10))
        columns = ('protocol', 'sequence', 'check', 'match', 'payload', 'text')
        table = ttk.Frame(result_tab)
        table.pack(fill='both', expand=True)
        self.frames = ttk.Treeview(table, columns=columns, show='headings')
        for key, name, width in [('protocol', '接收协议', 145), ('sequence', '序号 / 块', 90),
                                 ('check', '校验', 65), ('match', '与发送一致', 100),
                                 ('payload', '接收数据 HEX', 340), ('text', '接收文本 UTF-8', 270)]:
            self.frames.heading(key, text=name)
            self.frames.column(key, width=width, minwidth=70, stretch=key=='payload')
        frame_scroll = ttk.Scrollbar(table, orient='vertical', command=self.frames.yview)
        frame_scroll.pack(side='right', fill='y')
        self.frames.configure(yscrollcommand=frame_scroll.set)
        self.frames.pack(fill='both', expand=True)
        ttk.Label(result_tab, text='RF 报告来自保存的分块采集；帧数不能直接作为丢包率。').pack(anchor='w', pady=(10, 0))
        self.plot = tk.Canvas(spectrum_tab, bg='#101d30', highlightthickness=0)
        self.plot.pack(fill='both', expand=True)
        self.plot.bind('<Configure>', lambda _: self.draw_spectrum())
        self.plot_note = tk.StringVar(value='')
        ttk.Label(spectrum_tab, textvariable=self.plot_note).pack(anchor='w', pady=(8, 0))
        self.logs = self.text_box(log_tab)
        self.raw = self.text_box(raw_tab)
        ttk.Label(outer, text=f'工作目录：{ROOT}   ·   Tkinter 原生窗口 / 无浏览器服务', foreground='#677b91').pack(anchor='w', pady=(10, 0))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.refresh()
        recent = report_index()
        initial = recent[0]['id'] if recent and recent[0]['mode'].startswith(('user-data-','ORS-')) else None
        if initial:
            data = read_json(ARTIFACTS/initial/'report.json', {})
            if data.get('experiment')=='ors-feedback' and data.get('sent_hex'):
                payload = bytes.fromhex(data['sent_hex'])
                try:
                    text = payload.decode('utf-8')
                except UnicodeDecodeError:
                    text = ''
                self.feedback_encoding.set('UTF-8 文本' if text.isprintable() else 'HEX 十六进制')
                self.feedback_data.set(text if text.isprintable() else payload.hex(' '))
            selected = next((name for name, code in PROTOCOLS.items() if code==data.get('protocol')), None)
            if selected:
                self.protocol.set(selected)
                payload = bytes.fromhex(data.get('sent_hex', ''))
                try:
                    text = payload.decode('utf-8')
                except UnicodeDecodeError:
                    text = ''
                self.encoding.set('UTF-8 文本' if text.isprintable() else 'HEX 十六进制')
                self.payload.set(text if text.isprintable() else payload.hex(' '))
        self.load_report(initial)
        root.after(200, self.poll)

    @staticmethod
    def text_box(parent):
        widget = tk.Text(parent, wrap='none', bg='#101d30', fg='#dce8f6',
                         insertbackground='white', font=('Consolas', 10), relief='flat')
        scroll = ttk.Scrollbar(parent, orient='vertical', command=widget.yview)
        scroll.pack(side='right', fill='y')
        horizontal = ttk.Scrollbar(parent, orient='horizontal', command=widget.xview)
        horizontal.pack(side='bottom', fill='x')
        widget.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set, state='disabled')
        widget.pack(fill='both', expand=True)
        return widget

    @staticmethod
    def replace(widget, text):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', text)
        widget.configure(state='disabled')

    def refresh(self):
        self.report_choice['values'] = [row['id'] for row in report_index()]

    def load_report(self, run=None):
        self.loading += 1
        token = self.loading
        def worker():
            try:
                report = selected_report(run)
                spectrum = spectrum_data(report['id']) if report else {}
                self.events.put(('report', token, report, spectrum))
            except Exception as exc:
                self.events.put(('error', token, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def show_report(self, report, spectrum):
        self.report, self.spectrum = report, spectrum
        self.frames.delete(*self.frames.get_children())
        if not report:
            self.result_note.set('尚无实验报告，请先运行仿真。')
            return
        self.report_choice.set(report['id'])
        data = report['data']
        self.replace(self.raw, json.dumps(data, ensure_ascii=False, indent=2))
        self.result_note.set(f"{data.get('mode', '实验报告')}  ·  {report['id']}")
        if data.get('experiment') in ('ors-feedback','ors-feedback-validation'):
            self.feedback_frames.delete(*self.feedback_frames.get_children())
            if data['experiment']=='ors-feedback-validation':
                self.feedback_note.set(f"四类 ACK：{data['positive_pass']} / {data['positive_total']} 组正确；"
                    f"{data['negative_pass']} / {data['negative_total']} 组负样本无有效 ACK。详细条件见完整报告。")
            elif 'rounds' in data:
                source = '实际同板 RF' if data['physical'] else '仿真信道'
                self.feedback_note.set(f"{source} · {'已确认' if data['completed'] else '未确认'} · 共 {data['attempt_count']} 轮、"
                    f"重传 {data['retransmissions']} 次 · 接收端交付 {data['unique_receiver_deliveries']} 个唯一帧。\n"
                    f"接收载荷比对：{'一致' if data['received_payload_matches'] else '未通过'}；时延为电脑调度观测，不代表 192 微秒时序。")
                for row in data['rounds']:
                    ack,reply = row.get('accepted_ack'),row.get('reply')
                    detail = '未收到 FCS 正确的数据' if not reply else 'HEX: '+reply['payload_hex']
                    detail += f"；回复阶段延迟 {row['host_rx_return_to_ack_stage_ms']:.1f} ms"
                    if row.get('host_rx_return_to_ack_tx_call_return_ms') is not None:
                        detail += f"；TX 调用返回延迟 {row['host_rx_return_to_ack_tx_call_return_ms']:.1f} ms"
                    state = {'confirmed':'已确认','timeout':'超时','late_ack':'ACK 超时到达'}.get(row['outcome'],row['outcome'])
                    self.feedback_frames.insert('','end',values=(row['attempt'],row['pending_sequence'],state,
                        ack['status_text'] if ack else '未接受 ACK','通过' if ack else '—',detail))
            else:
                source = '实际同板 RF' if data['physical'] else '仿真信道'
                self.feedback_note.set(f"{source} · 有效 ACK {len(data['frames'])} 帧 · "
                    +('背景对照，应无有效 ACK。' if data.get('negative_control') else f"四种类型匹配 {len(data['matched_statuses'])} / 4。")
                    +'这是带参考段的自定义扩展，不是手机接收结果。')
                for index,row in enumerate(data['frames']):
                    self.feedback_frames.insert('','end',values=(index+1,row['sequence'],'已解析',row['status_text'],'通过',
                        f"参考匹配 {row['minimum_pair_score']:.3f}；频偏 {row['residual_cfo_hz']:.0f} Hz"))
            self.result_note.set('当前为 ACK 类型/闭环报告，请查看“ACK 类型与闭环”页。')
            self.draw_spectrum()
            self.tabs.select(5)
            return
        if data.get('experiment') in ('ors-ack-presence','ors-validation'):
            self.ors_frames.delete(*self.ors_frames.get_children())
            if data['experiment']=='ors-validation':
                self.ors_note.set(f"合成信道：{data['positive_pass']} / {data['positive_trials']} 组检测通过；"
                                  f"{data['negative_pass']} / {data['negative_trials']} 组负样本无误报。详细条件见完整报告。")
            else:
                source = {'ORS-rf':'实际板卡 RF','ORS-simulate':'仿真信道','ORS-background':'仅接收背景对照'}.get(data['mode'],data['mode'])
                outcome = '发现 ORS 特征候选' if data['ack_present'] else '未发现 ORS 特征候选'
                self.ors_note.set(f"{source} · {outcome} · 共 {data['detection_count']} 个。\n"
                    '仅判断波形存在性，不代表数据包已确认送达；未判决 ACK 类型，也不是手机接收结果。')
                for row in data['detections']:
                    self.ors_frames.insert('','end',values=(row['capture_block'],f"{row['time_ms']:.4f}",row['ors_count'],
                        f"{row['power_above_noise_db']:.1f}",f"{row['lag_coherence']:.3f}",
                        f"{row.get('oqpsk_shape_score',0):.3f}",f"{row['residual_cfo_hz']:.0f}"))
            self.result_note.set('当前为 ORS ACK 存在性报告，请查看“论文复现 · ORS ACK”页。')
            self.draw_spectrum()
            self.tabs.select(4)
            return
        if 'decoded_payloads' in data:
            source = '实际 RF 采集' if data.get('mode')=='user-data-rf' else '仿真信道'
            outcome = '收到一致载荷' if data.get('payload_received') else '未解析到一致载荷'
            self.result_note.set(f"{source} · {outcome} · 发送 {data['sent_bytes']} 字节 / HEX: {data['sent_hex']}\n"
                                 f"解析出 {len(data['decoded_payloads'])} 帧，其中 {data['matched_payload_frames']} 帧校验通过且与发送一致。")
            for row in data['decoded_payloads']:
                # Render control characters visibly rather than inserting them into the widget.
                text = json.dumps(row['received_text'], ensure_ascii=False)[1:-1]
                self.frames.insert('', 'end', values=(row['protocol'], row.get('capture_block', '—'),
                    '通过' if row['checksum_ok'] else '未通过', '一致' if row['matches_sent'] else '不一致',
                    row['received_hex'], text))
            if not data['decoded_payloads']:
                self.result_note.set(self.result_note.get()+'\n没有收到有效数据；发送内容不会作为接收结果填充。')
                if data.get('mode')=='user-data-rf':
                    self.result_note.set(self.result_note.get()+' 请检查 TX/RX 天线、接收增益和保存 IQ 的频谱。')
            self.draw_spectrum()
            return
        for key, protocol, check in [('ieee802154_frames', 'IEEE 802.15.4', 'fcs_ok'),
                                      ('ble_primary_frames', 'BLE Primary', 'crc_ok'),
                                      ('ble_auxiliary_frames', 'BLE Auxiliary', 'crc_ok')]:
            for frame in data.get(key, []):
                payload = frame.get('payload_hex', frame.get('pdu_hex', frame.get('psdu_hex', '')))
                self.frames.insert('', 'end', values=(protocol,
                    f"{frame.get('sequence', '—')} / {frame.get('capture_block', '—')}",
                    '通过' if frame.get(check) is True else '未通过' if frame.get(check) is False else '见报告',
                    '—', payload, json.dumps(frame.get('payload_text',''),ensure_ascii=False)[1:-1]))
        if not self.frames.get_children():
            self.result_note.set(self.result_note.get()+'；此报告为验证汇总，详细指标见“完整报告”。')
        self.draw_spectrum()

    def describe_payload(self):
        protocol = PROTOCOLS[self.protocol.get()]
        encoding = 'hex' if self.encoding.get()=='HEX 十六进制' else 'utf8'
        self.protocol_help.set({
            'ble':'用途：发送蓝牙广播。手机可用 nRF Connect 扫描，设备名称为 SDR-BLE-TEST。',
            'ieee802154':'用途：发送并解析 Zigbee 使用的底层无线数据帧；当前不包含 Zigbee 入网与应用通信。',
            'ctc':'用途：用蓝牙方式发送，让 802.15.4 接收器恢复数据。下方输入的是希望对方收到的内容。',
            'esb':'用途：测试 ESB 数据包收发。当前只发送数据，不等待对方回复；手机蓝牙无法扫描此协议。'}[protocol])
        self.protocol_details.set({
            'ble':'信道 37，频率 2402 MHz，速率 1 Mbps（每秒 100 万比特）。自定义数据位于广播的 Manufacturer Data 字段。',
            'ieee802154':'信道 15，频率 2425 MHz，速率 250 kbps。FCS 是帧校验，用于检查接收的数据是否损坏。',
            'ctc':'BLE 主广播 2426 MHz、辅助广播 2430 MHz；目标为 802.15.4 信道 16。当前由板卡和软件接收器验证。',
            'esb':'频率：2440 MHz；速率：1 Mbps（每秒 100 万比特）；地址：D3 91 B7 A6 C5（识别目标接收端）。\n'
                  'CRC16：检查数据是否损坏；无应答：不等待接收确认。尚未验证独立 nRF24 芯片接收。'}[protocol])
        self.format_help.set('输入示例：AB CD EF 01。每两个十六进制数字表示 1 字节，空格可省略。' if encoding=='hex' else
                             '输入示例：Hello ESB 或 你好。UTF-8 是文字编码；中文通常占多个字节，字节数不等于字数。')
        try:
            payload = parse_payload(protocol, encoding, self.payload.get())
            self.payload_hint.set(f'数据长度：已输入 {len(payload)} 字节，本协议最多允许 {LIMITS[protocol]} 字节。')
            self.payload_status.configure(style='Help.TLabel')
        except ValueError as exc:
            self.payload_hint.set('请检查输入：'+str(exc))
            self.payload_status.configure(style='Error.TLabel')

    def draw_spectrum(self):
        canvas = self.plot
        canvas.delete('all')
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if w < 100 or h < 100:
            return
        data = self.spectrum
        if not data.get('available'):
            canvas.create_text(w/2, h/2, text=data.get('reason', '此实验没有保存接收 IQ'), fill='#a6bad4', font=('Microsoft YaHei UI', 13))
            self.plot_note.set('选择射频采集报告可查看保存 IQ 的频谱。')
            return
        left, top, right, bottom = 65, 28, w-25, h-46
        for db in range(0, -81, -20):
            y = top+(bottom-top)*(-db/80)
            canvas.create_line(left, y, right, y, fill='#273951')
            canvas.create_text(left-10, y, text=str(db), anchor='e', fill='#a6bad4')
        freq, power = data['frequency_mhz'], data['power_db']
        for i in range(5):
            x = left+(right-left)*i/4
            canvas.create_text(x, bottom+18, text=f'{freq[0]+(freq[-1]-freq[0])*i/4:.1f}', fill='#a6bad4')
        points = []
        for f, p in zip(freq, power):
            points.extend([left+(right-left)*(f-freq[0])/(freq[-1]-freq[0]), top+(bottom-top)*min(80, max(0, -p))/80])
        if len(points) >= 4:
            canvas.create_line(*points, fill='#43d9bb', width=2)
        canvas.create_text(left, 12, text='相对峰值功率 / dB', anchor='w', fill='#a6bad4')
        canvas.create_text(right, bottom+34, text='频率 / MHz', anchor='e', fill='#a6bad4')
        self.plot_note.set(f"保存的 IQ · 非实时频谱 · {data['source_run']} · {data['samples']:,} 样本")

    def start(self, action):
        try:
            params = {key: value.get() for key, value in self.fields.items()}
            params.update(protocol=PROTOCOLS[self.protocol.get()],
                          encoding='hex' if self.encoding.get()=='HEX 十六进制' else 'utf8', data=self.payload.get())
            if action.startswith('feedback_'):
                params.update(data=self.feedback_data.get(),
                    encoding='hex' if self.feedback_encoding.get()=='HEX 十六进制' else 'utf8',
                    attempts=self.feedback_attempts.get(),ack_timeout=self.feedback_timeout.get())
            self.manager.start(action, params)
            if action in ('send_simulate', 'send_rf') or action.startswith(('ors_','feedback_')):
                self.loading += 1  # Ignore any pending historical report load.
                self.report = None
                self.spectrum = {}
                self.frames.delete(*self.frames.get_children())
                self.report_choice.set('')
                self.replace(self.raw, '')
                self.result_note.set('本次任务正在运行，尚无接收结果。完成后自动显示解析与对比。')
                if action.startswith('ors_'):
                    self.ors_frames.delete(*self.ors_frames.get_children())
                    self.ors_note.set('本次 ORS 任务运行中，尚无检测结果。')
                if action.startswith('feedback_'):
                    self.feedback_frames.delete(*self.feedback_frames.get_children())
                    self.feedback_note.set('本次 ACK 类型/闭环任务运行中，尚无结果；可在任务日志中查看进度。')
                self.draw_spectrum()
            self.tabs.select(5 if action.startswith('feedback_') else 4 if action.startswith('ors_') else 2)
            self.update_job()
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror('无法启动任务', str(exc), parent=self.root)

    def stop(self):
        self.manager.stop()
        self.update_job()

    def update_job(self):
        job = self.manager.snapshot()
        running = bool(job and job['status']=='running')
        for button in self.action_buttons.values():
            button.configure(state='disabled' if running or self.closing else 'normal')
        self.stop_button.configure(state='normal' if running else 'disabled')
        if job:
            status = {'running': '停止中' if job['stop_requested'] else '运行中', 'complete':'完成',
                      'incomplete':'未全部通过', 'failed':'失败', 'stopped':'已停止'}[job['status']]
            self.state.set(f"{job['label']} · {status} · {job['elapsed']:.1f} s")
            logs = '\n'.join(job['logs'])
            if logs != self.last_logs:
                self.replace(self.logs, logs)
                self.logs.see('end')
                self.last_logs = logs
            if not running and self.seen_job != job['id']:
                self.seen_job = job['id']
                self.refresh()
                if job.get('report_id'):
                    self.load_report(job['report_id'])
                    if job['action'] in ('send_simulate', 'send_rf'):
                        self.tabs.select(0)
                if job['action']=='devices':
                    self.device_state.set('设备检测结果：'+json.dumps(self.manager.devices, ensure_ascii=False))
        return running

    def poll(self):
        while not self.events.empty():
            event = self.events.get_nowait()
            if event[1] != self.loading:
                continue
            if event[0]=='report':
                self.show_report(event[2], event[3])
            else:
                self.result_note.set('报告读取失败：'+event[2])
        running = self.update_job()
        if self.closing and not running:
            self.root.destroy()
            return
        self.root.after(250, self.poll)

    def export(self):
        if not self.report:
            return
        destination = filedialog.asksaveasfilename(parent=self.root, title='导出实验报告',
            initialfile=self.report['id']+'.json', defaultextension='.json', filetypes=[('JSON 报告', '*.json')])
        if destination:
            try:
                source = ARTIFACTS/self.report['id']/'report.json'
                if source.resolve() != Path(destination).resolve():
                    shutil.copyfile(source, destination)
            except OSError as exc:
                messagebox.showerror('导出失败', str(exc), parent=self.root)

    def open_folder(self):
        os.startfile(str(ARTIFACTS/self.report['id'] if self.report else ARTIFACTS))

    def close(self):
        self.closing = True
        self.manager.stop()
        self.state.set('正在停止本窗口启动的任务…')


def main():
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
