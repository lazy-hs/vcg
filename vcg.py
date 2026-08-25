import os
import threading
import time
import queue

import requests
from lxml import etree
from xpinyin import Pinyin

# 全局变量定义区，可能会被更改
# -------------------------------------------------------------------
g_img_progress_queue = queue.Queue(30)
g_down_status_queue = queue.Queue(10)
g_log_queue = queue.Queue(30)


# -------------------------------------------------------------------


# 下载并保存图片
class Download:
    def __init__(self, key_word, pages, format) -> None:
        self.key_word = key_word
        self.pages = pages
        self.format = format
        self.save_file_path = None
        self.img_progress_queue = None
        self.child_process_status_queue = None
        self.child_process_log_queue = None
        self.pinyin = Pinyin()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,'
                      'image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Referer': 'https://www.vcg.com/',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    # 保存图片到本地
    def save_img_to_local(self):
        try:
            tmp_src = self.parse_web()
        except Exception as e:
            self.trans_log_to_ui('下载失败：{0}'.format(e))
            g_down_status_queue.put('down_fail')
            return
        start = time.time()
        i = 1

        if not os.path.exists(self.save_file_path):
            os.makedirs(self.save_file_path)  # 确保文件夹存在

        for src in tmp_src:
            # 拼接路径时使用 os.path.join
            file_name = '{0}_{1}.{2}'.format(self.key_word, i, self.format)
            file_path = os.path.join(self.save_file_path, file_name)

            # 下载并保存图片
            print(f"准备保存图片到: {file_path}")  # 调试打印路径
            try:
                r = self.session.get('https:' + src, timeout=30)
                r.raise_for_status()
                with open(file_path, 'wb') as f:
                    f.write(r.content)
            except Exception as e:
                self.trans_log_to_ui('保存《{0}》失败：{1}'.format(file_name, e))
                i += 1
                continue

            # 更新进度和日志
            self.trans_log_to_ui('<-------- {0}_{1}.{2}'.format(self.key_word, i, self.format))
            self.update_img_progress(round(i / len(tmp_src), 3) * 100)
            i += 1
            time.sleep(0.2)

        end = time.time()
        self.trans_log_to_ui('\n --------> 全部下载完成 <-------- \n耗时: {0}s'.format(round(end - start, 3)))
        g_down_status_queue.put('down_sucess')

    # 解析网页的数据，拿到所有的图片url
    def parse_web(self):
        tmp_src = []
        keyword_trans_pinyin = self.pinyin.get_pinyin(self.key_word, '')
        for page in range(1, int(self.pages) + 1):
            url = "https://www.vcg.com/creative-image/{0}/?page={1}".format(keyword_trans_pinyin, str(page))
            time.sleep(0.2)
            self.trans_log_to_ui("开始第{0}页请求 -------->".format(str(page)))
            response = self.session.get(url, timeout=30)
            if response.status_code != 200:
                self.trans_log_to_ui('请求第{0}页失败，状态码：{1} -------->'.format(str(page), response.status_code))
                continue
            content = response.text
            time.sleep(0.2)
            self.trans_log_to_ui("获取第{0}页网页源码 -------->".format(str(page)))
            tree = etree.HTML(content)
            src_list = tree.xpath('//div[@id="root"]//div[@class="gallery_inner"]//figure/a/img/@data-src')
            tmp_src += src_list
            self.trans_log_to_ui(' --------> 抓取图片总数量：{}张 <--------'.format(len(tmp_src)))
        if not tmp_src:
            raise RuntimeError('未抓取到任何图片，可能被网站拦截(403)或关键词无结果。')
        return tmp_src

    # 子线程通知子进程要显示信息在UI上
    def trans_log_to_ui(self, msg):
        g_log_queue.put(msg)

    # 子线程通知子进程进度条清空
    def update_img_progress(self, val: int):
        g_img_progress_queue.put(val)


# 用于管理下载图片
class Child_Process():
    def __init__(self, key_word, pages, format) -> None:
        self.print_log_signal = None
        self.img_progress_signal = None
        self.down = Download(key_word, pages, format)

    # 子进程处理解析网页的数据和下载图片
    def process_work(self, queue1, queue2, queue3, save_file_path):
        self.down.save_file_path = save_file_path
        self.down.img_progress_queue = queue1
        self.down.child_process_status_queue = queue2
        self.down.child_process_log_queue = queue3

        self.img_progress_queue = queue1
        self.child_process_status_queue = queue2
        self.child_process_log_queue = queue3

        _thread_save_img = threading.Thread(target=self.down.save_img_to_local, daemon=True)
        _thread_save_img.start()
        while True:
            if not g_log_queue.empty():
                self.child_process_log_queue.put(g_log_queue.get())
            if not g_img_progress_queue.empty():
                self.img_progress_queue.put(g_img_progress_queue.get())
            if not g_down_status_queue.empty():
                self.child_process_status_queue.put(g_down_status_queue.get())
            time.sleep(0.2)
