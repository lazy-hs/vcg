import json
import multiprocessing
import os
import queue
import sys
import threading
import time
from urllib.parse import quote

from PySide6.QtCore import QFile
from PySide6.QtCore import (
    Signal,
    QObject,
    QTimer,
    QDateTime,
    QUrl,
    QRegularExpression,
    QSettings,
)
from PySide6.QtGui import QIcon, QRegularExpressionValidator
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QLabel,
    QLayout,
    QVBoxLayout,
    QWidget,
)

from app_version import __version__
import vcg

# 全局常数定义区，默认值,不更改
# -------------------------------------------------------------------
g_SOURCE_DIR = os.path.dirname(os.path.abspath(__file__))
g_RESOURCE_DIR = getattr(sys, '_MEIPASS', g_SOURCE_DIR)
g_APP_DIR = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, 'frozen', False)
    else g_SOURCE_DIR
)
g_APP_ICON_PATH = os.path.join(g_RESOURCE_DIR, 'ico', 'app.ico')
if not os.path.isfile(g_APP_ICON_PATH):
    g_APP_ICON_PATH = os.path.join(g_RESOURCE_DIR, 'ico', 'head.ico')
g_UI_PATH = os.path.join(g_RESOURCE_DIR, 'UI', 'main.ui')
g_SETTINGS_ORGANIZATION = 'VCGDownloader'
g_SETTINGS_APPLICATION = 'settings'
g_SETTINGS_SAVE_PATH_KEY = 'download/save_path'
g_SETTINGS_WORKER_KEY = 'download/workers'
# -------------------------------------------------------------------
g_IDLE_BUTTON_STYLE = ('开始下载', '')
g_DOWN_BUTTON_STYLE = ('下载中…', '')
# -------------------------------------------------------------------
g_IN_Down_IMG = 'in_down'
g_IN_NoDown_IMG = 'in_nodown'
# -------------------------------------------------------------------
g_download_count = 20
# -------------------------------------------------------------------


# 全局变量定义区，可能会被更改
# -------------------------------------------------------------------
g_save_file_path = (
    os.path.join(os.path.expanduser('~'), 'Pictures', 'VCGDownloader')
    if getattr(sys, 'frozen', False)
    else os.path.join(g_APP_DIR, 'img')
)
os.makedirs(g_save_file_path, exist_ok=True)


# -------------------------------------------------------------------


# 自定义signal信号，用于子线程通知UI主线程显示消息
class Self_Signal(object):
    def __init__(self):
        self._signal = {}

    # name: 用于标识所定义的信号
    # slot：所定义的信号指向的槽函数
    # type1:信号和槽之间传递的参数类型
    # type2:默认不使用，预留着用于拓展参数个数
    def create(self, name, slot, type1, type2=None):
        class MySignals(QObject):
            if type2 is not None:
                obj = Signal(type1, type2)
            else:
                obj = Signal(type1)

        signal_object = MySignals()
        self._signal.update({name: signal_object})
        signal_object.obj.connect(slot)  # 注册信号和槽
        return self._signal[name].obj


# UI主线程/父进程
class UI_Work():
    def __init__(self) -> None:
        self.child_thread_obj = None
        self.child_process_obj = None
        self.browser_profile = None
        self.browser_view = None
        self.browser_dialog = None
        self.browser_poll_timer = None
        self.browser_page_ready = False
        self.browser_check_pending = False
        self.browser_generation = 0
        self.pending_browser_task = None
        self.browser_image_url_queue = None
        self.browser_feed_queue = None
        self.browser_stop_event = None
        self.browser_feeder_thread = None
        self.settings = QSettings(
            QSettings.IniFormat,
            QSettings.UserScope,
            g_SETTINGS_ORGANIZATION,
            g_SETTINGS_APPLICATION,
        )
        self.worker_profile = vcg.estimate_download_workers()
        self.signal = Self_Signal()  # 初始化自定义信号对象
        self.ui_init()
        self.init_source_selector()
        self.init_worker_selector()
        self.start_count_time()
        QTimer.singleShot(0, self._refresh_ui_layout)
        QTimer.singleShot(200, self._refresh_ui_layout)
        self.img_progress_queue = multiprocessing.Queue(30)
        self.child_process_status_queue = multiprocessing.Queue(10)
        self.child_process_log_queue = multiprocessing.Queue(30)
        # 子进程有事务，需要先放到队列里，由 UI主进程里的 子线程去取出事务，子线程再通过 signal 通知 UI 主线程处理
        self.print_log_signal = self.signal.create('log', self.slot_print_log, str)
        self.img_progress_signal = self.signal.create("progress", self.slot_update_file_progress, int)
        self.download_status_signal = self.signal.create(
            "download_status", self.slot_download_finished, str
        )
        # 这个子线程用于和子进程之间的通信，子进程->这个子线程->UI主进程
        _thread = threading.Thread(target=self.watch_child_thread, daemon=True)
        _thread.start()

    # 初始化UI
    def ui_init(self):
        global g_save_file_path
        qfile = QFile(g_UI_PATH)
        qfile.open(QFile.ReadOnly)
        self.ui = QUiLoader().load(qfile)
        qfile.close()
        self.ui.setWindowIcon(QIcon(g_APP_ICON_PATH))
        self.ui.version_label.setText('v{0}'.format(__version__))
        self.ui.version_label.setToolTip('当前版本 {0}'.format(__version__))
        self.ui.download_btn.clicked.connect(self.slot_start_download_img)
        self.ui.clear_log_btn.clicked.connect(self.slot_clear_log)
        self.ui.save_img_path_btn.clicked.connect(self.slot_change_save_file_path)
        self.ui.save_path_lineEdit.editingFinished.connect(
            self._remember_save_path_from_input
        )
        restored_save_path = self.settings.value(
            g_SETTINGS_SAVE_PATH_KEY,
            g_save_file_path,
            type=str,
        ).strip()
        if not restored_save_path:
            restored_save_path = g_save_file_path
        g_save_file_path = os.path.abspath(restored_save_path)
        self.ui.save_path_lineEdit.setText(g_save_file_path)
        self.ui.log_plainTextEdit.document().setMaximumBlockCount(2000)
        self.ui.pages_lineEdit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r'[1-9][0-9]*'), self.ui)
        )
        self.ui.pages_lineEdit.setText(str(g_download_count))
        self.progress_state = 'idle'
        self.set_progress_state('idle', '等待任务')

    def init_source_selector(self):
        self.source_label = self.ui.source_label
        self.source_combox = self.ui.source_combox
        self.source_combox.clear()
        for label, source_id in vcg.SOURCE_OPTIONS:
            self.source_combox.addItem(label, source_id)

    def init_worker_selector(self):
        recommended = self.worker_profile['recommended']
        maximum = self.worker_profile['maximum']
        logical_cpus = self.worker_profile['logical_cpus']
        total_memory = self.worker_profile['total_memory']
        total_memory_gb = (
            round(total_memory / (1024 ** 3), 1) if total_memory else None
        )

        self.worker_combox = self.ui.worker_combox
        self.ui.worker_label.setText('下载线程（≤{0}）'.format(maximum))
        profile_text = '{0} 个逻辑处理器'.format(logical_cpus)
        if total_memory_gb is not None:
            profile_text += '，{0}GB 内存'.format(total_memory_gb)
        self.worker_combox.setToolTip(
            '检测到 {0}；推荐 {1} 线程。线程过高可能增加内存占用或触发网站限流。'.format(
                profile_text, recommended
            )
        )
        self.worker_combox.addItem(
            '自动（推荐 {0}）'.format(recommended),
            'auto',
        )
        for worker_count in range(1, maximum + 1):
            self.worker_combox.addItem(
                '{0} 线程'.format(worker_count),
                worker_count,
            )

        stored_value = str(
            self.settings.value(g_SETTINGS_WORKER_KEY, 'auto')
        ).strip()
        if stored_value != 'auto':
            try:
                stored_workers = int(stored_value)
            except ValueError:
                stored_workers = None
            if stored_workers is not None and 1 <= stored_workers <= maximum:
                index = self.worker_combox.findData(stored_workers)
                if index >= 0:
                    self.worker_combox.setCurrentIndex(index)
        self.worker_combox.currentIndexChanged.connect(
            self._remember_worker_selection
        )

    def _refresh_ui_layout(self):
        if self.ui is None:
            return
        self.ui.ensurePolished()
        for widget in self.ui.findChildren(QWidget):
            widget.ensurePolished()
            widget.updateGeometry()
        layouts = self.ui.findChildren(QLayout)
        for layout in reversed(layouts):
            layout.invalidate()
        for layout in reversed(layouts):
            layout.activate()
        root_layout = self.ui.layout()
        if root_layout is not None:
            root_layout.invalidate()
            root_layout.setGeometry(self.ui.rect())
            root_layout.activate()
        self.ui.updateGeometry()
        self.ui.update()

    def _selected_download_workers(self):
        selected = self.worker_combox.currentData()
        if selected == 'auto':
            return self.worker_profile['recommended']
        return int(selected)

    def _remember_worker_selection(self):
        self.settings.setValue(
            g_SETTINGS_WORKER_KEY,
            self.worker_combox.currentData(),
        )
        self.settings.sync()

    def init_browser_parser(self):
        if self.browser_profile is not None:
            return
        profile_path = os.environ.get('VCG_BROWSER_PROFILE_DIR')
        if not profile_path:
            app_data_path = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
            profile_path = os.path.join(
                app_data_path, 'VCGDownloader', 'web_profile'
            )
        cache_path = os.path.join(profile_path, 'cache')
        os.makedirs(cache_path, exist_ok=True)

        self.browser_profile = QWebEngineProfile('vcg_downloader', self.ui)
        self.browser_profile.setPersistentStoragePath(profile_path)
        self.browser_profile.setCachePath(cache_path)
        self.browser_profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )

        self.browser_dialog = QDialog(self.ui)
        self.browser_dialog.setWindowTitle('VCG 安全验证')
        self.browser_dialog.resize(1000, 760)
        layout = QVBoxLayout(self.browser_dialog)
        tip = QLabel('请在下方网页中手动完成 VCG 安全验证。验证通过后会自动继续下载。')
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.browser_view = QWebEngineView(self.browser_dialog)
        self.browser_view.setPage(QWebEnginePage(self.browser_profile, self.browser_view))
        self.browser_view.loadStarted.connect(self._on_browser_load_started)
        self.browser_view.loadFinished.connect(self._on_browser_load_finished)
        layout.addWidget(self.browser_view)
        self.browser_dialog.rejected.connect(self._cancel_browser_parse)

        self.browser_poll_timer = QTimer(self.ui)
        self.browser_poll_timer.setInterval(1000)
        self.browser_poll_timer.timeout.connect(self._poll_browser_page)

    # 显示实时时间到 UI
    def start_count_time(self):
        self.Timer = QTimer()  # QTimer类
        self.Timer.start(1000)  # 每1s运行一次
        self.Timer.timeout.connect(self.update_time)  # 与updateTime函数连接

    # Timer到达 1s 后更新时间到 UI
    def update_time(self):
        time = QDateTime.currentDateTime()  # 获取现在的时间
        timeplay = time.toString('yyyy-MM-dd  HH:mm')  # 设置显示时间的格式
        self.ui.time_label.setText(timeplay)  # 设置timeLabel控件显示的内容

    # 打印日志
    def slot_print_log(self, msg: str):
        self.ui.log_plainTextEdit.appendPlainText(msg)

    # 清屏
    def slot_clear_log(self):
        if self.ui.download_btn.text() != g_DOWN_BUTTON_STYLE[0]:
            self.slot_update_file_progress(0)
            self.set_progress_state('idle', '等待任务')
        self.ui.log_plainTextEdit.clear()

    # 更新进度条
    def slot_update_file_progress(self, val):
        val = max(0, min(100, int(val)))
        self.ui.img_progressBar.setValue(val)
        self.ui.progress_percent_label.setText('{0}%'.format(val))
        if self.progress_state == 'running' and val > 0:
            self.ui.progress_status_label.setText('下载中')

    def set_progress_state(self, state, text):
        self.progress_state = state
        self.ui.progress_status_label.setText(text)
        for widget in (
            self.ui.progress_status_label,
            self.ui.progress_percent_label,
            self.ui.img_progressBar,
        ):
            widget.setProperty('state', state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    # 选择保存目录
    def slot_change_save_file_path(self):
        global g_save_file_path
        current_path = self.ui.save_path_lineEdit.text().strip() or g_save_file_path
        file_path = QFileDialog.getExistingDirectory(
            self.ui,
            "打开文件夹",
            current_path,
        )
        if file_path:
            self._remember_save_path(file_path)
        else:
            self.ui.log_plainTextEdit.appendPlainText('未选择文件夹!')

    def _remember_save_path_from_input(self):
        save_path = self.ui.save_path_lineEdit.text().strip()
        if save_path:
            self._remember_save_path(save_path)

    def _remember_save_path(self, save_path):
        global g_save_file_path
        save_path = os.path.abspath(save_path)
        g_save_file_path = save_path
        self.ui.save_path_lineEdit.setText(save_path)
        self.settings.setValue(g_SETTINGS_SAVE_PATH_KEY, save_path)
        self.settings.sync()
        return save_path

    def _prepare_save_path(self):
        global g_save_file_path
        save_path = self.ui.save_path_lineEdit.text().strip() or g_save_file_path
        save_path = os.path.abspath(save_path)
        try:
            os.makedirs(save_path, exist_ok=True)
        except OSError as exc:
            self.ui.log_plainTextEdit.appendPlainText('无法创建保存目录：{0}'.format(exc))
            return None
        return self._remember_save_path(save_path)

    # 准备开启下载图片
    def slot_start_download_img(self):
        global g_download_count
        self.slot_update_file_progress(0)
        key_word = self.ui.keyword_lineEdit.text()
        count_text = self.ui.pages_lineEdit.text()
        img_format = self.ui.img_format_combox.currentText()
        source = self.source_combox.currentData()
        download_workers = self._selected_download_workers()
        if len(count_text) == 0:
            self.ui.log_plainTextEdit.appendPlainText('下载数量不能为空！')
            return
        try:
            g_download_count = int(count_text)
        except ValueError:
            self.ui.log_plainTextEdit.appendPlainText('下载数量必须是整数！')
            return
        if g_download_count < 1:
            self.ui.log_plainTextEdit.appendPlainText('下载数量必须大于 0！')
            return
        if self.ui.download_btn.text() == g_IDLE_BUTTON_STYLE[0]:
            save_path = self._prepare_save_path()
            if save_path is None:
                return
            self.set_connect_btn_style()
            self.change_other_btn_enable(g_IN_Down_IMG)
            self.set_progress_state('running', '解析并下载中')
            self.ui.log_plainTextEdit.appendPlainText(
                '目标下载数量：{0} 张'.format(g_download_count)
            )
            self.ui.log_plainTextEdit.appendPlainText(
                '下载线程：{0}'.format(download_workers)
            )
            if not key_word.strip():
                self.ui.log_plainTextEdit.appendPlainText(
                    '搜索关键词为空，已启用随机下载模式。'
                )
            if source == vcg.SOURCE_VCG:
                self.start_browser_parse(
                    key_word.strip(),
                    g_download_count,
                    img_format,
                    save_path,
                    download_workers,
                )
            else:
                self.ui.log_plainTextEdit.appendPlainText(
                    '图片来源：{0}'.format(self.source_combox.currentText())
                )
                self.create_child_process(
                    key_word.strip(),
                    g_download_count,
                    img_format,
                    save_path,
                    None,
                    source,
                    download_workers,
                )

    def start_browser_parse(
        self,
        key_word,
        target_count,
        img_format,
        save_path,
        download_workers,
    ):
        self.init_browser_parser()
        self.browser_image_url_queue = multiprocessing.Queue(24)
        self.browser_feed_queue = queue.Queue()
        self.browser_stop_event = multiprocessing.Event()
        self.browser_feeder_thread = threading.Thread(
            target=self._feed_browser_urls,
            args=(
                self.browser_feed_queue,
                self.browser_image_url_queue,
                self.browser_stop_event,
            ),
            daemon=True,
        )
        self.browser_feeder_thread.start()
        self.pending_browser_task = {
            'key_word': key_word,
            'target_count': target_count,
            'img_format': img_format,
            'save_path': save_path,
            'page': 1,
            'discovered': 0,
            'seen': set(),
            'poll_attempts': 0,
            'verification_shown': False,
        }
        if not self.create_child_process(
            key_word,
            target_count,
            img_format,
            save_path,
            None,
            vcg.SOURCE_VCG,
            download_workers,
            self.browser_image_url_queue,
            self.browser_stop_event,
        ):
            self._cleanup_browser_stream()
            self.pending_browser_task = None
            return
        self.ui.log_plainTextEdit.appendPlainText('启动浏览器模式，正在解析第 1 页……')
        self.browser_poll_timer.start()
        self._load_browser_page()

    @staticmethod
    def _feed_browser_urls(feed_queue, image_url_queue, stop_event):
        while not stop_event.is_set():
            try:
                image_url = feed_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            while not stop_event.is_set():
                try:
                    image_url_queue.put(image_url, timeout=0.2)
                    break
                except queue.Full:
                    continue
                except (OSError, ValueError):
                    return
            if image_url is None:
                return

    def _load_browser_page(self):
        task = self.pending_browser_task
        if task is None:
            return
        task['poll_attempts'] = 0
        task['verification_shown'] = False
        self.browser_generation += 1
        self.browser_page_ready = False
        self.browser_check_pending = False
        slug = quote(task['key_word'], safe='')
        if slug:
            url = 'https://www.vcg.com/creative-image/{0}/?page={1}'.format(
                slug, task['page']
            )
        else:
            url = 'https://www.vcg.com/creative-image/?page={0}'.format(
                task['page']
            )
        self.browser_view.load(QUrl(url))

    def _on_browser_load_started(self):
        if self.pending_browser_task is not None:
            self.browser_page_ready = False

    def _on_browser_load_finished(self, _success):
        if self.pending_browser_task is None:
            return
        self.browser_page_ready = True
        QTimer.singleShot(600, self._poll_browser_page)

    def _poll_browser_page(self):
        if (
            self.pending_browser_task is None
            or not self.browser_page_ready
            or self.browser_check_pending
        ):
            return
        self.browser_check_pending = True
        generation = self.browser_generation
        script = r'''
            (() => JSON.stringify({
                title: document.title || '',
                body: document.body ? document.body.innerText.slice(0, 300) : '',
                urls: Array.from(document.querySelectorAll('.gallery_inner figure img'))
                    .map(img => img.getAttribute('data-src') || img.getAttribute('src') || img.getAttribute('data-min'))
                    .filter(Boolean)
                    .map(src => new URL(src, location.href).href)
            }))()
        '''
        self.browser_view.page().runJavaScript(
            script,
            lambda result, current_generation=generation: self._handle_browser_result(
                result, current_generation
            ),
        )

    def _handle_browser_result(self, result, generation):
        if generation != self.browser_generation or self.pending_browser_task is None:
            return
        self.browser_check_pending = False
        task = self.pending_browser_task
        task['poll_attempts'] += 1
        try:
            data = json.loads(result or '{}')
        except (TypeError, json.JSONDecodeError):
            data = {}

        urls = data.get('urls') or []
        if urls:
            if self.browser_dialog.isVisible():
                self.browser_dialog.hide()
            added = 0
            for src in urls:
                if src not in task['seen']:
                    task['seen'].add(src)
                    self.browser_feed_queue.put(src)
                    task['discovered'] += 1
                    added += 1
            self.ui.log_plainTextEdit.appendPlainText(
                '第 {0} 页解析完成，新增 {1} 张，累计发现 {2} 张'.format(
                    task['page'], added, task['discovered']
                )
            )
            if added == 0:
                self.ui.log_plainTextEdit.appendPlainText('没有更多新图片，停止翻页。')
                self._finish_browser_parse()
            else:
                self._load_next_browser_page_when_ready()
            return

        title = data.get('title') or ''
        body = data.get('body') or ''
        if '安全验证' in title or '按顺序点击' in body or 'session-id' in body:
            if not task['verification_shown']:
                task['verification_shown'] = True
                self.ui.log_plainTextEdit.appendPlainText(
                    'VCG 要求安全验证，请在弹出的网页中手动完成。'
                )
                self.set_progress_state('running', '等待安全验证')
            if not self.browser_dialog.isVisible():
                self.browser_dialog.show()
                self.browser_dialog.raise_()
                self.browser_dialog.activateWindow()
            return

        if task['poll_attempts'] >= 5 and task['discovered']:
            self.ui.log_plainTextEdit.appendPlainText('没有更多图片，等待已发现图片下载。')
            self._finish_browser_parse()
        elif task['poll_attempts'] >= 30:
            self._fail_browser_parse('页面加载超时，或 VCG 页面结构已经变化。')

    def _load_next_browser_page_when_ready(self):
        task = self.pending_browser_task
        if task is None:
            return
        if self.browser_stop_event is not None and self.browser_stop_event.is_set():
            self.pending_browser_task = None
            self.browser_poll_timer.stop()
            self.browser_page_ready = False
            if self.browser_dialog.isVisible():
                self.browser_dialog.hide()
            return
        if self.browser_feed_queue is not None and self.browser_feed_queue.qsize() > 8:
            QTimer.singleShot(100, self._load_next_browser_page_when_ready)
            return
        task['page'] += 1
        self._load_browser_page()

    def _finish_browser_parse(self):
        task = self.pending_browser_task
        if task is None:
            return
        self.pending_browser_task = None
        self.browser_poll_timer.stop()
        self.browser_page_ready = False
        self.browser_dialog.hide()
        if self.browser_feed_queue is not None:
            self.browser_feed_queue.put(None)
        self.ui.log_plainTextEdit.appendPlainText(
            '网页解析结束，共发现 {0} 张，等待剩余下载完成。'.format(
                task['discovered']
            )
        )
        self.set_progress_state('running', '完成剩余下载')

    def _fail_browser_parse(self, message):
        self.ui.log_plainTextEdit.appendPlainText(message)
        if self.pending_browser_task is not None and self.child_process_obj is not None:
            self._finish_browser_parse()
            return
        self.pending_browser_task = None
        self.browser_poll_timer.stop()
        self.browser_page_ready = False
        self.browser_dialog.hide()
        self.set_connect_btn_style(connect=0)
        self.change_other_btn_enable(g_IN_NoDown_IMG)
        self.set_progress_state('error', '解析失败')

    def _cancel_browser_parse(self):
        if self.pending_browser_task is not None:
            self.ui.log_plainTextEdit.appendPlainText('已取消安全验证和下载任务。')
            self.pending_browser_task = None
            self.browser_poll_timer.stop()
            self.browser_page_ready = False
            if self.browser_stop_event is not None:
                self.browser_stop_event.set()
            self.close_child_process()
            self._cleanup_browser_stream()
            self.set_connect_btn_style(connect=0)
            self.change_other_btn_enable(g_IN_NoDown_IMG)
            self.set_progress_state('partial', '已取消')

    def _cleanup_browser_stream(self):
        if self.browser_stop_event is not None:
            self.browser_stop_event.set()
        self.browser_image_url_queue = None
        self.browser_feed_queue = None
        self.browser_stop_event = None
        self.browser_feeder_thread = None

    # 创建并启动子进程
    def create_child_process(
        self,
        key_word,
        target_count,
        img_format,
        save_path,
        image_urls,
        source,
        download_workers,
        image_url_queue=None,
        stop_event=None,
    ):
        self.vcg = vcg.Child_Process(
            key_word,
            target_count,
            img_format,
            image_urls,
            source=source,
            download_workers=download_workers,
        )
        self.child_process_obj = multiprocessing.Process(
            target=self.vcg.process_work,
            args=(
                self.img_progress_queue,
                self.child_process_status_queue,
                self.child_process_log_queue,
                save_path,
                image_url_queue,
                stop_event,
            )
        )
        try:
            self.child_process_obj.start()
            self.vcg.image_urls = None
            return True
        except Exception as exc:
            self.ui.log_plainTextEdit.appendPlainText('启动下载进程失败：{0}'.format(exc))
            self.child_process_obj = None
            self.set_connect_btn_style(connect=0)
            self.change_other_btn_enable(g_IN_NoDown_IMG)
            self.set_progress_state('error', '启动失败')
            return False

    # 结束程序
    def slot_exit_process(self):
        self.pending_browser_task = None
        if self.browser_poll_timer is not None:
            self.browser_poll_timer.stop()
        if self.browser_dialog is not None:
            self.browser_dialog.hide()
        if self.browser_stop_event is not None:
            self.browser_stop_event.set()
        self.close_child_process()
        self._cleanup_browser_stream()

    # 切换连接按钮的样式
    def set_connect_btn_style(self, connect=1):
        if connect == 1:
            self.ui.download_btn.setText(g_DOWN_BUTTON_STYLE[0])
            self.ui.download_btn.setStyleSheet(g_DOWN_BUTTON_STYLE[1])
        else:
            self.ui.download_btn.setText(g_IDLE_BUTTON_STYLE[0])
            self.ui.download_btn.setStyleSheet(g_IDLE_BUTTON_STYLE[1])

    # 父进程主动结束子进程
    def close_child_process(self):
        if self.child_process_obj is not None:
            self.child_process_obj.join(timeout=0.5)
        if self.child_process_obj is not None and self.child_process_obj.is_alive():
            self.child_process_obj.terminate()
            self.child_process_obj.join(timeout=1)
        self.child_process_obj = None

    def slot_download_finished(self, status):
        self.pending_browser_task = None
        if self.browser_poll_timer is not None:
            self.browser_poll_timer.stop()
        if self.browser_dialog is not None and self.browser_dialog.isVisible():
            self.browser_dialog.hide()
        if self.browser_stop_event is not None:
            self.browser_stop_event.set()
        self.set_connect_btn_style(connect=0)
        self.change_other_btn_enable(g_IN_NoDown_IMG)
        self.close_child_process()
        self._cleanup_browser_stream()
        status_text = {
            'down_success': '任务完成。',
            'down_partial': '任务结束，但图片来源不足或有部分下载失败。',
            'down_fail': '任务失败。',
        }.get(status, status)
        self.ui.log_plainTextEdit.appendPlainText(status_text)
        if status == 'down_success':
            self.slot_update_file_progress(100)
            self.set_progress_state('success', '下载完成')
        elif status == 'down_partial':
            self.set_progress_state('partial', '部分完成')
        else:
            self.set_progress_state('error', '下载失败')

    # 更改其他按钮的 Enable 状态
    def change_other_btn_enable(self, status):
        res = False
        # 处于等待接收文件时
        if status == g_IN_Down_IMG:
            res = False
        # 处于无连接状态时
        elif status == g_IN_NoDown_IMG:
            res = True
        self.ui.keyword_lineEdit.setEnabled(res)
        self.ui.img_format_combox.setEnabled(res)
        self.source_combox.setEnabled(res)
        self.worker_combox.setEnabled(res)
        self.ui.pages_lineEdit.setEnabled(res)
        self.ui.save_img_path_btn.setEnabled(res)
        self.ui.save_path_lineEdit.setEnabled(res)
        self.ui.download_btn.setEnabled(res)

    # 在UI主进程创建子线程，用于轮询判断子进程是否有数据需要传递到主进程
    def watch_child_thread(self):
        while True:
            try:
                while True:
                    self.print_log_signal.emit(self.child_process_log_queue.get_nowait())
            except queue.Empty:
                pass
            try:
                while True:
                    self.img_progress_signal.emit(self.img_progress_queue.get_nowait())
            except queue.Empty:
                pass
            try:
                status = self.child_process_status_queue.get_nowait()
            except queue.Empty:
                status = None
            if status is not None:
                self.download_status_signal.emit(status)
            time.sleep(0.05)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    if os.name == 'nt':
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'VCG.HighResolutionImageDownloader'
            )
        except (AttributeError, OSError):
            pass
    app = QApplication([])  # 初始化应用
    app.setApplicationName('高清图片下载')
    app.setApplicationDisplayName('高清图片下载')
    app.setWindowIcon(QIcon(g_APP_ICON_PATH))  # 主界面和任务栏图标
    main = UI_Work()  # 实例化对象
    main.ui.show()  # 加载UI显示所有的控件在界面上
    app.aboutToQuit.connect(main.slot_exit_process)  # 关闭主程序/主线程
    app.exec()  # 主线程阻塞等待信号
