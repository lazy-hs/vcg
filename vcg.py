import os
import queue
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from itertools import count, islice
from urllib.parse import quote, urljoin, urlparse

import requests
from lxml import etree
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from PIL import Image
except ImportError:  # pragma: no cover - 仅在缺少可选依赖时触发
    Image = None

try:
    from xpinyin import Pinyin
except ImportError:  # 未安装 xpinyin 时，VCG 会把中文路径重定向到拼音路径
    Pinyin = None


class AccessBlockedError(RuntimeError):
    """VCG 返回安全验证页时抛出，避免误报为关键词无结果。"""


SOURCE_VCG = "vcg"
SOURCE_BIZHIHUI = "bizhihui"
SOURCE_OPTIONS = (
    ("壁纸汇（最高直链画质）", SOURCE_BIZHIHUI),
    ("视觉中国（需验证）", SOURCE_VCG),
)


def _memory_capacity_bytes():
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys), int(status.ullAvailPhys)
        except (AttributeError, OSError, ValueError):
            pass

    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
        return page_size * total_pages, page_size * available_pages
    except (AttributeError, OSError, ValueError):
        return None, None


def estimate_download_workers():
    logical_cpus = max(1, os.cpu_count() or 4)
    total_memory, available_memory = _memory_capacity_bytes()
    gib = 1024 ** 3

    cpu_limit = max(4, min(32, logical_cpus * 2))
    memory_limit = cpu_limit
    if total_memory:
        memory_limit = max(2, min(32, int(total_memory / gib)))
    maximum = max(1, min(cpu_limit, memory_limit))

    available_limit = logical_cpus
    if available_memory:
        # 预留约 768MB/线程，覆盖大图解码、格式转换和系统波动。
        available_limit = max(2, int(available_memory / (768 * 1024 ** 2)))
    recommended = max(1, min(maximum, logical_cpus, available_limit))
    return {
        "logical_cpus": logical_cpus,
        "total_memory": total_memory,
        "available_memory": available_memory,
        "recommended": recommended,
        "maximum": maximum,
    }


class Download:
    PAGE_URL = "https://www.vcg.com/creative-image/{slug}/?page={page}"
    RANDOM_PAGE_URL = "https://www.vcg.com/creative-image/?page={page}"
    REQUEST_TIMEOUT = (10, 30)
    MAX_WORKERS = 6
    BIZHIHUI_DETAIL_WORKERS = 12
    CHUNK_SIZE = 64 * 1024
    INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    BIZHIHUI_BASE_URL = "https://www.bizhihui.com/"
    BIZHIHUI_CATEGORY_MAP = {
        "动漫": "dongman",
        "卡通": "dongman",
        "二次元": "dongman",
        "人物": "renwu",
        "人像": "renwu",
        "美女": "renwu",
        "风景": "fengjing",
        "景色": "fengjing",
        "影视": "yingshi",
        "电影": "yingshi",
        "体育": "yingshi",
        "游戏": "youxi",
        "美食": "meishi",
        "食物": "meishi",
        "唯美": "weimei",
        "治愈": "weimei",
        "萌宠": "mengchong",
        "宠物": "mengchong",
        "动物": "mengchong",
        "艺术": "yishu",
        "绘画": "yishu",
        "宇宙": "yuzhou",
        "星空": "yuzhou",
        "太空": "yuzhou",
        "科技": "keji",
        "军事": "keji",
        "简约": "jianyue",
        "极简": "jianyue",
        "其他": "qita",
        "其它": "qita",
        "手机": "tags/shouji",
        "手机壁纸": "tags/shouji",
        "4k": "tags/4Kbizhi",
        "8k": "tags/8Kbizhi",
    }

    def __init__(
        self,
        key_word,
        target_count,
        img_format,
        source=SOURCE_VCG,
        download_workers=None,
    ) -> None:
        self.key_word = key_word.strip()
        self.target_count = int(target_count)
        self.format = img_format.lower().strip()
        self.download_workers = max(
            1,
            int(download_workers or self.MAX_WORKERS),
        )
        if self.target_count < 1:
            raise ValueError("下载数量必须大于 0")
        if self.format not in ("jpg", "jpeg", "png"):
            raise ValueError("图片格式仅支持 jpg、jpeg、png")
        if source not in (SOURCE_VCG, SOURCE_BIZHIHUI):
            raise ValueError("不支持的图片来源：{0}".format(source))
        self.source = source
        self.source_referer = (
            self.BIZHIHUI_BASE_URL if source == SOURCE_BIZHIHUI else "https://www.vcg.com/"
        )
        self.save_file_path = None
        self.img_progress_queue = None
        self.child_process_status_queue = None
        self.child_process_log_queue = None
        self._thread_local = None
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": self.source_referer,
        }

    def _new_session(self):
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.6,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(("GET",)),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        session = requests.Session()
        session.headers.update(self.headers)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _get_thread_session(self):
        if self._thread_local is None:
            self._thread_local = threading.local()
        if not hasattr(self._thread_local, "session"):
            self._thread_local.session = self._new_session()
        return self._thread_local.session

    def _keyword_slug(self):
        if Pinyin is not None:
            return Pinyin().get_pinyin(self.key_word, "")
        return quote(self.key_word, safe="")

    @classmethod
    def _safe_filename_part(cls, value):
        value = cls.INVALID_FILE_CHARS.sub("_", value).strip(" .")
        return value[:80] or "image"

    @staticmethod
    def _is_security_page(response):
        content = response.content[:8192].lower()
        return (
            response.status_code == 403
            and (b"waap" in content or b"captcha" in content)
        ) or "安全验证".encode("utf-8") in content

    @staticmethod
    def _extract_image_urls(content, base_url):
        tree = etree.HTML(content)
        if tree is None:
            return []

        image_nodes = tree.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), " gallery_inner ")]'
            '//figure//img'
        )
        if not image_nodes:
            image_nodes = tree.xpath("//figure//img")

        result = []
        seen = set()
        for image_node in image_nodes:
            src = (
                image_node.get("data-src")
                or image_node.get("src")
                or image_node.get("data-min")
                or ""
            )
            src = src.strip()
            if not src or src.startswith("data:"):
                continue
            absolute_url = urljoin(base_url, src)
            if absolute_url not in seen:
                seen.add(absolute_url)
                result.append(absolute_url)
        return result

    def _write_image(self, response, file_path):
        part_path = "{0}.{1}.part".format(file_path, threading.get_ident())
        converted_path = part_path + ".converted"
        try:
            with open(part_path, "wb") as file_obj:
                for chunk in response.iter_content(chunk_size=self.CHUNK_SIZE):
                    if chunk:
                        file_obj.write(chunk)

            if os.path.getsize(part_path) == 0:
                raise RuntimeError("服务器返回了空文件")

            target_format = "JPEG" if self.format in ("jpg", "jpeg") else "PNG"
            if Image is None:
                if target_format == "PNG":
                    raise RuntimeError("保存 PNG 需要安装 Pillow：pip install Pillow")
                os.replace(part_path, file_path)
                return

            with Image.open(part_path) as image:
                source_format = (image.format or "").upper()
                image.verify()

            if source_format == target_format:
                os.replace(part_path, file_path)
                return

            with Image.open(part_path) as image:
                if target_format == "JPEG":
                    image = image.convert("RGB")
                    image.save(converted_path, format="JPEG", quality=95)
                else:
                    if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                        image = image.convert("RGBA")
                    image.save(converted_path, format="PNG", optimize=True)
            os.replace(converted_path, file_path)
        finally:
            for temp_path in (part_path, converted_path):
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass

    def _download_one(self, index, src):
        name_prefix = self._safe_filename_part(self.key_word or "随机")
        if self.source == SOURCE_BIZHIHUI:
            name_prefix = "壁纸汇_{0}".format(name_prefix)
        file_name = "{0}_{1}.{2}".format(
            name_prefix, index, self.format
        )
        file_path = os.path.join(self.save_file_path, file_name)

        if self._existing_file_is_valid(file_path):
            return "skipped", file_name

        session = self._get_thread_session()
        with session.get(
            src,
            headers={"Referer": self.source_referer},
            timeout=self.REQUEST_TIMEOUT,
            stream=True,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not content_type.startswith("image/"):
                raise RuntimeError("响应不是图片：{0}".format(content_type))
            self._write_image(response, file_path)
        return "saved", file_name

    def _existing_file_is_valid(self, file_path):
        if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
            return False
        if Image is None:
            return True
        expected = "JPEG" if self.format in ("jpg", "jpeg") else "PNG"
        try:
            with Image.open(file_path) as image:
                actual = (image.format or "").upper()
                image.verify()
            return actual == expected
        except (OSError, ValueError):
            return False

    # 保存图片到本地
    def save_img_to_local(self, image_urls=None):
        start = time.time()
        try:
            if image_urls is None:
                image_urls = self.iter_web()
            os.makedirs(self.save_file_path, exist_ok=True)
        except Exception as exc:
            self.trans_log_to_ui("下载失败：{0}".format(exc))
            self._send_status("down_fail")
            return

        def unique_image_urls():
            seen_urls = set()
            for image_url in image_urls:
                if not image_url or image_url in seen_urls:
                    continue
                seen_urls.add(image_url)
                yield image_url

        saved = 0
        skipped = 0
        failed = 0
        attempted = 0
        fulfilled = 0
        workers = min(self.download_workers, self.target_count)
        self.trans_log_to_ui(
            "开始边解析边下载，目标 {0} 张，并发数 {1}".format(
                self.target_count, workers
            )
        )

        source_buffer = queue.Queue(maxsize=workers)
        source_finished = object()
        source_stop = threading.Event()
        source_errors = []

        def produce_image_urls():
            try:
                for src in unique_image_urls():
                    while not source_stop.is_set():
                        try:
                            source_buffer.put(src, timeout=0.2)
                            break
                        except queue.Full:
                            continue
                    if source_stop.is_set():
                        break
            except Exception as exc:
                source_errors.append(exc)
            finally:
                while not source_stop.is_set():
                    try:
                        source_buffer.put(source_finished, timeout=0.2)
                        break
                    except queue.Full:
                        continue

        source_thread = threading.Thread(target=produce_image_urls, daemon=True)
        source_thread.start()

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vcg-img") as pool:
            future_map = {}
            last_progress = 0
            source_exhausted = False
            while fulfilled < self.target_count:
                pending_limit = min(
                    workers,
                    self.target_count - fulfilled,
                )
                while len(future_map) < pending_limit and not source_exhausted:
                    try:
                        src = source_buffer.get_nowait()
                    except queue.Empty:
                        break
                    if src is source_finished:
                        source_exhausted = True
                        break
                    attempted += 1
                    future_map[
                        pool.submit(self._download_one, attempted, src)
                    ] = (attempted, src)

                if not future_map:
                    if source_exhausted:
                        break
                    try:
                        src = source_buffer.get(timeout=0.2)
                    except queue.Empty:
                        if not source_thread.is_alive():
                            source_exhausted = True
                        continue
                    if src is source_finished:
                        source_exhausted = True
                        continue
                    attempted += 1
                    future_map[
                        pool.submit(self._download_one, attempted, src)
                    ] = (attempted, src)

                done, _ = wait(
                    future_map,
                    timeout=0.2,
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    index, src = future_map.pop(future)
                    try:
                        result, file_name = future.result()
                        if result == "saved":
                            saved += 1
                            fulfilled += 1
                            if self._should_log_item(fulfilled):
                                self.trans_log_to_ui("已保存：{0}".format(file_name))
                        else:
                            skipped += 1
                            fulfilled += 1
                            if self._should_log_item(fulfilled):
                                self.trans_log_to_ui(
                                    "已存在，跳过：{0}".format(file_name)
                                )
                    except Exception as exc:
                        failed += 1
                        host = urlparse(src).netloc
                        self.trans_log_to_ui(
                            "第 {0} 张下载失败（{1}）：{2}".format(index, host, exc)
                        )
                    progress = round(fulfilled / self.target_count * 100)
                    if progress != last_progress:
                        last_progress = progress
                        self.update_img_progress(progress)

        source_stop.set()
        source_thread.join(timeout=0.5)
        if source_errors:
            self.trans_log_to_ui("继续解析图片时失败：{0}".format(source_errors[-1]))

        elapsed = round(time.time() - start, 2)
        if fulfilled < self.target_count:
            self.trans_log_to_ui(
                "图片来源已无更多可用结果：完成 {0}/{1} 张".format(
                    fulfilled, self.target_count
                )
            )
        self.trans_log_to_ui(
            "下载结束：完成 {0}/{1} 张，新增 {2} 张，跳过 {3} 张，失败 {4} 张，耗时 {5}s".format(
                fulfilled, self.target_count, saved, skipped, failed, elapsed
            )
        )
        if fulfilled >= self.target_count:
            self._send_status("down_success")
        elif fulfilled > 0:
            self._send_status("down_partial")
        else:
            self._send_status("down_fail")

    def _should_log_item(self, fulfilled):
        return (
            self.target_count <= 200
            or fulfilled <= 20
            or fulfilled % 100 == 0
            or fulfilled == self.target_count
        )

    def parse_web(self):
        return list(islice(self.iter_web(), self.target_count))

    def iter_web(self):
        if self.source == SOURCE_BIZHIHUI:
            return self._iter_bizhihui_web()
        return self._iter_vcg_web()

    # 解析视觉中国网页，拿到预览图片 URL
    def _parse_vcg_web(self):
        return list(islice(self._iter_vcg_web(), self.target_count))

    def _iter_vcg_web(self):
        seen = set()
        slug = self._keyword_slug()
        session = self._new_session()
        consecutive_failures = 0
        yielded = 0
        if not slug:
            self.trans_log_to_ui("视觉中国随机下载：从综合图片列表开始")
        try:
            for page in count(1):
                if slug:
                    url = self.PAGE_URL.format(slug=slug, page=page)
                else:
                    url = self.RANDOM_PAGE_URL.format(page=page)
                self.trans_log_to_ui("开始请求第 {0} 页".format(page))
                try:
                    response = session.get(url, timeout=self.REQUEST_TIMEOUT)
                    if response.status_code == 404:
                        self.trans_log_to_ui("没有第 {0} 页，停止翻页".format(page))
                        break
                    if self._is_security_page(response):
                        raise AccessBlockedError(
                            "VCG 要求安全验证。请先用浏览器正常访问 VCG，或稍后降低频率重试"
                        )
                    response.raise_for_status()
                except AccessBlockedError:
                    raise
                except requests.RequestException as exc:
                    self.trans_log_to_ui("第 {0} 页请求失败：{1}".format(page, exc))
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        self.trans_log_to_ui("连续 3 页请求失败，停止翻页")
                        break
                    continue

                consecutive_failures = 0
                page_urls = self._extract_image_urls(response.content, response.url)
                if not page_urls:
                    self.trans_log_to_ui(
                        "第 {0} 页没有更多图片，停止翻页".format(page)
                    )
                    break
                added = 0
                for src in page_urls:
                    if src not in seen:
                        seen.add(src)
                        added += 1
                        yielded += 1
                        yield src
                self.trans_log_to_ui(
                    "第 {0} 页解析完成，累计发现 {1} 张".format(page, yielded)
                )
                if added == 0:
                    self.trans_log_to_ui("页面内容重复，没有更多新图片")
                    break
                time.sleep(0.15)
        finally:
            session.close()

        if yielded == 0:
            raise RuntimeError("未抓取到图片：关键词无结果，或页面结构已变化")

    def _resolve_bizhihui_listing(self, session):
        normalized_keyword = self.key_word.strip().lower()
        if not normalized_keyword:
            self.trans_log_to_ui("壁纸汇随机下载：从最新图片列表开始")
            return self.BIZHIHUI_BASE_URL
        category = self.BIZHIHUI_CATEGORY_MAP.get(normalized_keyword)
        if category:
            return urljoin(self.BIZHIHUI_BASE_URL, category.rstrip("/") + "/")
        self.trans_log_to_ui("壁纸汇按标签查找：{0}".format(self.key_word))
        return urljoin(
            self.BIZHIHUI_BASE_URL,
            "tags/{0}/".format(quote(self.key_word, safe="")),
        )

    @staticmethod
    def _bizhihui_page_url(listing_url, page):
        if page == 1:
            return listing_url + "?order=time"
        return urljoin(listing_url, "{0}/?order=time".format(page))

    @staticmethod
    def _bizhihui_original_from_thumbnail(thumbnail_url, base_url):
        if not thumbnail_url:
            return None
        original_url = urljoin(base_url, thumbnail_url.strip())
        if "/upload/" not in original_url:
            return None
        for suffix in ("-pcthumbs", "-thumbs"):
            if original_url.endswith(suffix):
                return original_url[:-len(suffix)]
        return None

    @classmethod
    def _extract_bizhihui_listing_entries(cls, tree, base_url):
        if tree is None:
            return []
        items = tree.xpath(
            '//*[contains(concat(" ", normalize-space(@class), " "), " item-list ")]'
        )
        entries = []
        for item in items:
            detail_urls = item.xpath('.//a[contains(@href, "/p/")]/@href')
            detail_url = urljoin(base_url, detail_urls[0]) if detail_urls else None
            image_nodes = item.xpath(
                './/a[contains(concat(" ", normalize-space(@class), " "), " item-img ")]//img'
            )
            thumbnail_url = None
            if image_nodes:
                image_node = image_nodes[0]
                thumbnail_url = (
                    image_node.get("data-original")
                    or image_node.get("data-src")
                    or image_node.get("src")
                )
            original_url = cls._bizhihui_original_from_thumbnail(
                thumbnail_url, base_url
            )
            if detail_url or original_url:
                entries.append((detail_url, original_url))
        return entries

    def _parse_bizhihui_detail(self, index, detail_url):
        session = self._get_thread_session()
        response = session.get(
            detail_url,
            headers={"Referer": self.BIZHIHUI_BASE_URL},
            timeout=self.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        tree = etree.HTML(response.text)
        if tree is None:
            raise RuntimeError("详情页无法解析")
        image_url = self._extract_bizhihui_max_image(tree, detail_url)
        if not image_url:
            raise RuntimeError("未找到原图下载地址")
        has_quark_archive = bool(
            tree.xpath('//a[contains(@href, "pan.quark.cn/s/")]/@href')
        )
        return index, image_url, has_quark_archive

    @staticmethod
    def _extract_bizhihui_max_image(tree, detail_url):
        # 夸克网盘中的 4K/8K ZIP 需要登录；这里选择网页可直接下载的
        # 图片链接中标注分辨率最大的一个，不固定依赖某个按钮 ID。
        candidates = []
        for anchor in tree.xpath('//a[@href]'):
            href = anchor.get('href') or ''
            if '/upload/' not in href or href.lower().startswith('javascript:'):
                continue
            text = ''.join(anchor.itertext()).strip()
            dimensions = re.findall(r'(\d{3,5})\s*[xX×]\s*(\d{3,5})', text)
            if dimensions:
                width, height = map(int, dimensions[-1])
                candidates.append((width * height, max(width, height), href))

        if candidates:
            _, _, href = max(candidates)
            return urljoin(detail_url, href)

        fallback_urls = tree.xpath('//a[@id="changeTxt2"]/@href')
        if not fallback_urls:
            fallback_urls = tree.xpath(
                '//a[contains(normalize-space(string(.)), "下载原图尺寸壁纸")]/@href'
            )
        if not fallback_urls:
            fallback_urls = tree.xpath(
                '//a[contains(@href, "/upload/") and not(contains(@href, "x-oss-process"))]/@href'
            )
        return urljoin(detail_url, fallback_urls[0]) if fallback_urls else None

    # 壁纸汇：从允许抓取的分类/标签页进入详情页，提取高清原图
    def _parse_bizhihui_web(self):
        return list(islice(self._iter_bizhihui_web(), self.target_count))

    def _iter_bizhihui_web(self):
        session = self._new_session()
        seen_details = set()
        seen_images = set()
        detail_index = 0
        yielded = 0
        try:
            listing_url = self._resolve_bizhihui_listing(session)
            for page in count(1):
                page_url = self._bizhihui_page_url(listing_url, page)
                self.trans_log_to_ui("开始解析壁纸汇第 {0} 页".format(page))
                try:
                    response = session.get(
                        page_url,
                        headers={"Referer": self.BIZHIHUI_BASE_URL},
                        timeout=self.REQUEST_TIMEOUT,
                    )
                    if response.status_code == 404:
                        self.trans_log_to_ui("壁纸汇没有第 {0} 页，提前结束".format(page))
                        break
                    response.raise_for_status()
                except requests.RequestException as exc:
                    self.trans_log_to_ui(
                        "壁纸汇第 {0} 页请求失败，停止翻页：{1}".format(page, exc)
                    )
                    break
                response.encoding = "utf-8"
                tree = etree.HTML(response.text)
                page_entries = self._extract_bizhihui_listing_entries(
                    tree, response.url
                )

                direct_urls = []
                fallback_details = []
                for detail_url, original_url in page_entries:
                    if detail_url and detail_url in seen_details:
                        continue
                    if detail_url:
                        seen_details.add(detail_url)
                    if original_url:
                        if original_url not in seen_images:
                            seen_images.add(original_url)
                            direct_urls.append(original_url)
                    elif detail_url:
                        fallback_details.append(detail_url)
                self.trans_log_to_ui(
                    "壁纸汇第 {0} 页找到 {1} 个原图直链，{2} 个需要详情解析".format(
                        page, len(direct_urls), len(fallback_details)
                    )
                )
                if not direct_urls and not fallback_details:
                    break

                for original_url in direct_urls:
                    yielded += 1
                    yield original_url

                if fallback_details:
                    self.trans_log_to_ui(
                        "正在补充解析 {0} 个作品的原图地址……".format(
                            len(fallback_details)
                        )
                    )
                    workers = min(
                        self.BIZHIHUI_DETAIL_WORKERS,
                        len(fallback_details),
                    )
                    with ThreadPoolExecutor(
                        max_workers=workers, thread_name_prefix="bizhihui-detail"
                    ) as pool:
                        futures = {}
                        for detail_url in fallback_details:
                            current_index = detail_index
                            detail_index += 1
                            futures[
                                pool.submit(
                                    self._parse_bizhihui_detail,
                                    current_index,
                                    detail_url,
                                )
                            ] = detail_url
                        for future in as_completed(futures):
                            detail_url = futures[future]
                            try:
                                _, original_url, _ = future.result()
                                if original_url in seen_images:
                                    continue
                                seen_images.add(original_url)
                                yielded += 1
                                yield original_url
                            except Exception as exc:
                                self.trans_log_to_ui(
                                    "作品详情解析失败（{0}）：{1}".format(
                                        detail_url, exc
                                    )
                                )

                self.trans_log_to_ui(
                    "已累计发现 {0} 张最高直链画质图片".format(yielded)
                )
                time.sleep(0.15)
        finally:
            session.close()

        if yielded == 0:
            raise RuntimeError("壁纸汇未找到可下载的高清图片")

    def trans_log_to_ui(self, msg):
        if self.child_process_log_queue is not None:
            self.child_process_log_queue.put(msg)

    def update_img_progress(self, val: int):
        if self.img_progress_queue is not None:
            self.img_progress_queue.put(val)

    def _send_status(self, status):
        if self.child_process_status_queue is not None:
            self.child_process_status_queue.put(status)


class Child_Process:
    def __init__(
        self,
        key_word,
        target_count,
        img_format,
        image_urls=None,
        source=SOURCE_VCG,
        download_workers=None,
    ) -> None:
        self.down = Download(
            key_word,
            target_count,
            img_format,
            source=source,
            download_workers=download_workers,
        )
        self.image_urls = image_urls

    @staticmethod
    def _iter_image_queue(image_url_queue, stop_event):
        while stop_event is None or not stop_event.is_set():
            try:
                image_url = image_url_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if image_url is None:
                return
            yield image_url

    def process_work(
        self,
        queue1,
        queue2,
        queue3,
        save_file_path,
        image_url_queue=None,
        stop_event=None,
    ):
        self.down.save_file_path = os.path.abspath(save_file_path)
        self.down.img_progress_queue = queue1
        self.down.child_process_status_queue = queue2
        self.down.child_process_log_queue = queue3
        try:
            if image_url_queue is not None:
                image_urls = self._iter_image_queue(image_url_queue, stop_event)
            else:
                image_urls = self.image_urls
            self.image_urls = None
            self.down.save_img_to_local(image_urls)
        except Exception as exc:
            self.down.trans_log_to_ui("下载进程异常：{0}".format(exc))
            self.down._send_status("down_fail")
        finally:
            if stop_event is not None:
                stop_event.set()
