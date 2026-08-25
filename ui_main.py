import multiprocessing
import os
import threading
import time

from PySide6.QtCore import QFile
from PySide6.QtCore import Signal, QObject, QTimer, QDateTime
from PySide6.QtGui import QIcon
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QFileDialog

import vcg

# 全局常数定义区，默认值,不更改
# -------------------------------------------------------------------
g_APP_ICON_PATCH = 'ico/head.ico'
g_UI_PATH = 'UI/main.ui'
# -------------------------------------------------------------------
g_IDLE_BUTTON_STYLE = ('下载', "background-color: rgb(0, 145, 0);color: rgb(255, 255, 255);font: 13pt '黑体'")
g_DOWN_BUTTON_STYLE = ('下载中', "background-color: rgb(65, 194, 194);color: rgb(255, 255, 255);font: 13pt '黑体'")
# -------------------------------------------------------------------
g_IN_Down_IMG = 'in_down'
g_IN_NoDown_IMG = 'in_nodown'
# -------------------------------------------------------------------
g_pages = 1
# -------------------------------------------------------------------


# 全局变量定义区，可能会被更改
# -------------------------------------------------------------------
g_save_file_path = './img/'  # 用于存放要发送的文件的路径，或者要接收文件保存的路径
if not os.path.exists(g_save_file_path):
    os.mkdir(g_save_file_path)


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
        self.signal = Self_Signal()  # 初始化自定义信号对象
        self.ui_init()
        self.start_count_time()
        self.img_progress_queue = multiprocessing.Queue(30)
        self.child_process_status_queue = multiprocessing.Queue(10)
        self.child_process_log_queue = multiprocessing.Queue(30)
        # 子进程有事务，需要先放到队列里，由 UI主进程里的 子线程去取出事务，子线程再通过 signal 通知 UI 主线程处理
        self.print_log_signal = self.signal.create('log', self.slot_print_log, str)
        self.img_progress_signal = self.signal.create("progress", self.slot_update_file_progress, int)
        # 这个子线程用于和子进程之间的通信，子进程->这个子线程->UI主进程
        _thread = threading.Thread(target=self.watch_child_thread, daemon=True)
        _thread.start()

    # 初始化UI
    def ui_init(self):
        qfile = QFile('UI/main.ui')
        qfile.open(QFile.ReadOnly)
        qfile.close()
        self.ui = QUiLoader().load(qfile)
        self.ui.download_btn.clicked.connect(self.slot_start_download_img)
        self.ui.clear_log_btn.clicked.connect(self.slot_clear_log)
        self.ui.save_img_path_btn.clicked.connect(self.slot_change_save_file_path)
        self.ui.save_path_lineEdit.setText(g_save_file_path)
        self.ui.pages_lineEdit.setText(str(g_pages))

    # 显示实时时间到 UI
    def start_count_time(self):
        self.Timer = QTimer()  # QTimer类
        self.Timer.start(1000)  # 每1s运行一次
        self.Timer.timeout.connect(self.update_time)  # 与updateTime函数连接

    # Timer到达 1s 后更新时间到 UI
    def update_time(self):
        time = QDateTime.currentDateTime()  # 获取现在的时间
        timeplay = time.toString('yyyy-MM-dd hh:mm:ss dddd')  # 设置显示时间的格式
        self.ui.time_label.setText(timeplay)  # 设置timeLabel控件显示的内容

    # 打印日志
    def slot_print_log(self, msg: str):
        self.ui.log_plainTextEdit.appendPlainText(msg)

    # 清屏
    def slot_clear_log(self):
        self.ui.img_progressBar.setValue(0)
        self.ui.log_plainTextEdit.clear()

    # 更新进度条
    def slot_update_file_progress(self, val):
        self.ui.img_progressBar.setValue(val)

    # 选择保存目录
    def slot_change_save_file_path(self):
        global g_save_file_path
        print("进入保存路径选择函数")
        dialog = QFileDialog()
        filePath = dialog.getExistingDirectory(self.ui, "打开文件夹")  # 弹出文件管理器窗口
        if filePath:  # 确保路径不为空
            self.ui.save_path_lineEdit.setText(filePath)  # 设置文本框的内容，显示在UI
            g_save_file_path = filePath
            print(f"保存路径已更改为: {g_save_file_path}")
        else:
            self.ui.log_plainTextEdit.appendPlainText('未选择文件夹!')

    # 准备开启下载图片
    def slot_start_download_img(self):
        global g_pages
        self.ui.img_progressBar.setValue(0)
        key_word = self.ui.keyword_lineEdit.text()
        pages = self.ui.pages_lineEdit.text()
        format = self.ui.img_format_combox.currentText()
        if len(key_word) == 0:
            self.ui.log_plainTextEdit.appendPlainText('搜索关键字不能为空！')
            return
        if len(pages) == 0:
            self.ui.log_plainTextEdit.appendPlainText('下载页数不能为空！')
            return
        g_pages = int(pages)
        if self.ui.download_btn.text() == g_IDLE_BUTTON_STYLE[0]:
            self.set_connect_btn_style()
            self.change_other_btn_enable(g_IN_Down_IMG)
            self.create_child_process(key_word, format)

    # 创建并启动子进程
    def create_child_process(self, key_word, format):
        self.vcg = vcg.Child_Process(key_word, g_pages, format)
        self.child_process_obj = multiprocessing.Process(
            target=self.vcg.process_work,
            args=(
                self.img_progress_queue,
                self.child_process_status_queue,
                self.child_process_log_queue,
                g_save_file_path
            )
        )
        self.child_process_obj.start()

    # 结束程序
    def slot_exit_process(self):
        self.close_child_process()
        exit(0)

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
        if self.child_process_obj is not None and self.child_process_obj.is_alive():
            self.child_process_obj.terminate()
            time.sleep(0.5)
            assert self.child_process_obj.is_alive() == False
        self.child_process_obj = None

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
        self.ui.pages_lineEdit.setEnabled(res)
        self.ui.save_img_path_btn.setEnabled(res)
        self.ui.save_path_lineEdit.setEnabled(res)
        self.ui.download_btn.setEnabled(res)

    # 在UI主进程创建子线程，用于轮询判断子进程是否有数据需要传递到主进程
    def watch_child_thread(self):
        while True:
            if not self.child_process_status_queue.empty():
                self.ui.keyword_lineEdit.clear()
                self.ui.pages_lineEdit.clear()
                self.set_connect_btn_style(connect=0)
                self.change_other_btn_enable(g_IN_NoDown_IMG)
                self.close_child_process()
                self.print_log_signal.emit(self.child_process_status_queue.get())
            if not self.child_process_log_queue.empty():
                self.print_log_signal.emit(self.child_process_log_queue.get())
            if not self.img_progress_queue.empty():
                self.img_progress_signal.emit(self.img_progress_queue.get())
            time.sleep(0.2)


if __name__ == '__main__':
    app = QApplication([])  # 初始化应用
    app.setWindowIcon(QIcon(g_APP_ICON_PATCH))  # 主界面图标 
    main = UI_Work()  # 实例化对象
    main.ui.show()  # 加载UI显示所有的控件在界面上
    app.aboutToQuit.connect(main.slot_exit_process)  # 关闭主程序/主线程
    app.exec()  # 主线程阻塞等待信号
