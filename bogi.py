import sys
import subprocess
import time
import logging
import json
import os
import re
import platform

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QPushButton, QLineEdit, QLabel, QComboBox, QTextEdit, 
                             QHBoxLayout, QFileDialog, QSplitter, QTabWidget, QListWidget, 
                             QListWidgetItem, QDialog, QMessageBox, QInputDialog)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (QWebEngineProfile, QWebEngineSettings, 
                                   QWebEnginePage, QWebEngineScript, QWebEngineNewWindowRequest)
from PyQt6.QtNetwork import QNetworkCookie
from PyQt6.QtCore import QThread, pyqtSignal, QUrl, Qt, QSize
from PyQt6.QtGui import QIcon

try:
    from streamlink import Streamlink, PluginError, NoPluginError
    STREAMLINK_AVAILABLE = True
except ImportError:
    STREAMLINK_AVAILABLE = False
    logging.warning("streamlink 모듈을 찾을 수 없습니다. subprocess 모드로 실행됩니다.")

logging.basicConfig(
    filename='bogi_debug.log', 
    level=logging.DEBUG, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

CONFIG_FILE = "bogi_config.json"

def find_vlc_path():
    if platform.system() != 'Windows':
        return None
    try:
        import winreg
        reg_paths = [r"SOFTWARE\VideoLAN\VLC", r"SOFTWARE\WOW6432Node\VideoLAN\VLC"]
        for path in reg_paths:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
                val, _ = winreg.QueryValueEx(key, "InstallDir")
                vlc_exe = os.path.join(val, "vlc.exe")
                if os.path.exists(vlc_exe):
                    return vlc_exe
            except WindowsError:
                continue
    except Exception as e:
        logging.error(f"VLC 자동 탐색 실패: {e}")
    return None

class StreamWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, url, platform, login_id, login_pw, stream_pw, cookies, vlc_path):
        super().__init__()
        self.url = url
        self.platform = platform
        self.login_id = login_id
        self.login_pw = login_pw
        self.stream_pw = stream_pw
        self.cookies = cookies
        self.vlc_path = vlc_path
        self.is_running = True
        self.vlc_process = None

    def run(self):
        self._run_with_subprocess()
        self.finished_signal.emit()

    def _run_with_subprocess(self):
        self.log_signal.emit(f"🚀 Streamlink 실행 시작: {self.url}")
        player_cmd = self.vlc_path if self.vlc_path else "vlc"
        command = ["streamlink", "--player", player_cmd]
        
        if self.cookies:
            cookie_str = "; ".join([f"{k}={v}" for k, v in self.cookies.items()])
            command.extend(["--http-header", f"Cookie={cookie_str}"])
            
        if "숲" in self.platform or "sooplive" in self.url:
            if self.login_id and self.login_pw:
                command.extend(["--soop-username", self.login_id])
                command.extend(["--soop-password", self.login_pw])
                command.append("--soop-purge-credentials")
            if self.stream_pw:
                command.extend(["--soop-stream-password", self.stream_pw])
                
        elif "판다" in self.platform:
            if self.stream_pw:
                command.extend(["--pandatv-password", self.stream_pw])
                
        elif "치지직" in self.platform:
            if self.stream_pw:
                command.extend(["--chzzk-password", self.stream_pw])

        command.extend([self.url, "best"])
        is_panda = "판다" in self.platform
        
        while self.is_running:
            self.log_signal.emit(f"▶️ 영상 연결 시도... URL: {self.url}")
            try:
                self.vlc_process = subprocess.Popen(command, stdout=subprocess.PIPE, 
                                          stderr=subprocess.STDOUT, text=True, 
                                          encoding='utf-8', errors='ignore', 
                                          creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0)
                
                for line in self.vlc_process.stdout:
                    if not self.is_running:
                        self.vlc_process.terminate()
                        break
                    
                    clean_line = line.strip()
                    if clean_line:
                        self.log_signal.emit(f"[{self.url[-15:]}] {clean_line}")
                        
                    if is_panda and ("보유한 풀방입장권이 없습니다." in clean_line or "full" in clean_line.lower()):
                        self.log_signal.emit(f"⚠️ [{self.url[-15:]}] 풀방 감지! 5초 후 재접속을 시도합니다...")
                        self.vlc_process.terminate()
                        time.sleep(5)
                        break 
                else:
                    break
            except Exception as e:
                self.log_signal.emit(f"❌ 실행 오류: {e}")
                break

    def stop(self):
        self.is_running = False
        if self.vlc_process and self.vlc_process.poll() is None:
            self.vlc_process.terminate()

class CustomWebEnginePage(QWebEnginePage):
    url_snatched = pyqtSignal(str)

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        if "BOGI_URL_SNATCH:" in message:
            url = message.split("BOGI_URL_SNATCH:")[1].strip()
            self.url_snatched.emit(url)
        super().javaScriptConsoleMessage(level, message, lineNumber, sourceID)

class LoginDialog(QDialog):
    def __init__(self, url, cookie_manager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("로그인 / 성인인증")
        self.resize(900, 700)
        self.cookie_manager = cookie_manager
        
        layout = QVBoxLayout(self)
        
        self.browser = QWebEngineView()
        self.profile = QWebEngineProfile("bogi_login_profile", self.browser)
        
        default_ua = self.profile.httpUserAgent()
        clean_ua = re.sub(r'QtWebEngine/[\d\.]+\s?', '', default_ua).strip()
        self.profile.setHttpUserAgent(clean_ua)
        
        page = QWebEnginePage(self.profile, self.browser)
        self.browser.setPage(page)
        
        self.cookie_store = self.profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self.on_cookie_added)
        
        layout.addWidget(self.browser)
        
        btn_close = QPushButton("인증 완료 후 닫기")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
        
        self.browser.setUrl(QUrl(url))

    def on_cookie_added(self, cookie: QNetworkCookie):
        name = cookie.name().data().decode('utf-8')
        value = cookie.value().data().decode('utf-8')
        self.cookie_manager[name] = value

class BogiGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("bogi (통합 다중 Streamlink GUI - 자동 캡처)")
        self.setWindowIcon(QIcon('bogi.ico'))
        self.resize(1200, 900)
        self.setMinimumSize(800, 600)
        
        self.apply_dark_theme()
        
        self.current_cookies = {}
        self.active_threads = []
        self.recent_streams = []
        
        self.vlc_path = find_vlc_path()
        
        main_layout = QVBoxLayout()
        
        top_layout = QHBoxLayout()
        self.platform_combo = QComboBox()
        self.platform_combo.addItems(["치지직 (CHZZK)", "숲라이브 (SOOP)", "판다티비 (PandaTV)"])
        self.platform_combo.currentIndexChanged.connect(self.change_browser_url)
        top_layout.addWidget(QLabel("📺 플랫폼:"))
        top_layout.addWidget(self.platform_combo)
        
        self.vlc_path_input = QLineEdit()
        self.vlc_path_input.setPlaceholderText("VLC 경로 (자동 탐색됨)")
        if self.vlc_path:
            self.vlc_path_input.setText(self.vlc_path)
        self.vlc_browse_btn = QPushButton("VLC 찾기")
        self.vlc_browse_btn.clicked.connect(self.browse_vlc)
        top_layout.addWidget(QLabel("📂 VLC:"))
        top_layout.addWidget(self.vlc_path_input)
        top_layout.addWidget(self.vlc_browse_btn)
        
        main_layout.addLayout(top_layout)

        nav_layout = QHBoxLayout()
        self.back_btn = QPushButton("◀ 뒤로")
        self.forward_btn = QPushButton("▶ 앞으로")
        self.refresh_btn = QPushButton("🔄 새로고침")
        self.home_btn = QPushButton("🏠 플랫폼 홈")
        self.login_btn = QPushButton("🔑 로그인/인증")
        
        nav_layout.addWidget(self.back_btn)
        nav_layout.addWidget(self.forward_btn)
        nav_layout.addWidget(self.refresh_btn)
        nav_layout.addWidget(self.home_btn)
        nav_layout.addWidget(self.login_btn)
        nav_layout.addStretch()
        main_layout.addLayout(nav_layout)

        self.splitter = QSplitter(Qt.Orientation.Vertical)

        # --- 브라우저 세팅 ---
        self.browser = QWebEngineView()
        self.profile = QWebEngineProfile("bogi_profile", self.browser)
        
        browser_settings = self.profile.settings()
        browser_settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        browser_settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
        browser_settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        browser_settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        default_ua = self.profile.httpUserAgent()
        clean_ua = re.sub(r'QtWebEngine/[\d\.]+\s?', '', default_ua).strip()
        self.profile.setHttpUserAgent(clean_ua)

        self.page = CustomWebEnginePage(self.profile, self.browser)
        self.page.newWindowRequested.connect(self.on_new_window_requested)
        self.browser.urlChanged.connect(self.on_url_changed)
        self.page.url_snatched.connect(self.process_snatched_url)
        
        self.browser.setPage(self.page)

        self.cookie_store = self.profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self.on_cookie_added)
        
        # 🌟 숲라이브용 Hover & Click 스파이
        snatch_script = QWebEngineScript()
        snatch_script.setSourceCode("""
            function handleLink(e, isClick) {
                let link = e.target.closest('a');
                if(link && link.href) {
                    let url = link.href;
                    if(url.includes('play.sooplive')) {
                        console.log("BOGI_URL_SNATCH:" + url);
                        if(isClick) {
                            e.preventDefault();
                            e.stopPropagation();
                        }
                    }
                }
            }
            document.addEventListener('mouseover', function(e) { handleLink(e, false); }, true);
            document.addEventListener('click', function(e) { handleLink(e, true); }, true);
        """)
        snatch_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        snatch_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        self.profile.scripts().insert(snatch_script)

        self.back_btn.clicked.connect(self.browser.back)
        self.forward_btn.clicked.connect(self.browser.forward)
        self.refresh_btn.clicked.connect(self.browser.reload)
        self.home_btn.clicked.connect(self.change_browser_url)
        self.login_btn.clicked.connect(self.open_login_dialog)

        self.splitter.addWidget(self.browser)

        self.bottom_tabs = QTabWidget()
        
        control_tab = QWidget()
        bottom_layout = QVBoxLayout(control_tab)
        
        url_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("숲: 마우스 올리기 / 판다, 치지직: 썸네일 클릭 시 캡처")
        self.url_input.returnPressed.connect(self.start_stream)
        self.get_url_btn = QPushButton("🔽 수동 주소 캡처")
        self.get_url_btn.clicked.connect(self.get_current_url)
        
        url_layout.addWidget(self.get_url_btn)
        url_layout.addWidget(QLabel("URL:"))
        url_layout.addWidget(self.url_input)
        bottom_layout.addLayout(url_layout)
        
        auth_layout = QHBoxLayout()
        self.login_id_input = QLineEdit()
        self.login_id_input.setPlaceholderText("계정 ID (SOOP용)")
        self.login_pw_input = QLineEdit()
        self.login_pw_input.setPlaceholderText("계정 PW (SOOP용)")
        self.login_pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        
        self.stream_pw_input = QLineEdit()
        self.stream_pw_input.setPlaceholderText("방송 방 비밀번호")
        self.stream_pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        
        auth_layout.addWidget(QLabel("🔑 로그인:"))
        auth_layout.addWidget(self.login_id_input)
        auth_layout.addWidget(self.login_pw_input)
        auth_layout.addWidget(QLabel("🔒 방 비밀번호:"))
        auth_layout.addWidget(self.stream_pw_input)
        bottom_layout.addLayout(auth_layout)
        
        btn_layout = QHBoxLayout()
        self.play_btn = QPushButton("▶️ VLC 새 창으로 띄우기 (멀티 지원)")
        self.play_btn.clicked.connect(self.start_stream)
        self.play_btn.setMinimumHeight(40)
        self.play_btn.setStyleSheet("font-size: 14px; font-weight: bold; background-color: #2196F3;")
        
        btn_layout.addWidget(self.play_btn)
        bottom_layout.addLayout(btn_layout)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        bottom_layout.addWidget(self.log_output)
        
        self.bottom_tabs.addTab(control_tab, "🛠️ 제어 및 로그")
        
        recent_tab = QWidget()
        recent_layout = QVBoxLayout(recent_tab)
        self.recent_list = QListWidget()
        self.recent_list.itemDoubleClicked.connect(self.play_recent_stream)
        recent_layout.addWidget(QLabel("더블클릭하면 해당 방송을 새 VLC 창으로 실행합니다."))
        recent_layout.addWidget(self.recent_list)
        self.bottom_tabs.addTab(recent_tab, "🕒 최근 시청")

        self.splitter.addWidget(self.bottom_tabs)
        self.splitter.setSizes([550, 350])
        main_layout.addWidget(self.splitter)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.load_settings()
        self.change_browser_url()

    def on_new_window_requested(self, request: QWebEngineNewWindowRequest):
        url = request.requestedUrl().toString()
        request.reject()
        self.process_snatched_url(url)

    # =========================================================
    # 🌟 핵심 1: 치지직 / 판다티비 방 진입 감지 및 자동 뒤로가기!
    # =========================================================
    def on_url_changed(self, qurl):
        url = qurl.toString()
        
        # 쓸데없는 도메인이나 메인 화면 이동은 무시
        if url.endswith(".kr") or url.endswith(".kr/") or url.endswith(".com") or url.endswith(".com/"):
            return
            
        # 방송 방 진입 URL 패턴 확인
        if ("pandalive.co.kr/play/" in url) or ("pandalive.co.kr/live/play/" in url) or ("chzzk.naver.com/live/" in url):
            # 주소 캡처 처리
            self.process_snatched_url(url)
            
            # 🌟 캡처를 완료했으니 곧바로 브라우저를 메인 목록으로 되돌려버립니다!
            self.browser.back()
            self.log_output.append("💡 주소 캡처 완료! 목록으로 자동 복귀했습니다.")
    # =========================================================

    def process_snatched_url(self, url):
        if not url:
            return
            
        if "play.sooplive.com/" in url or "play.sooplive.co.kr/" in url or "chzzk.naver.com/live/" in url or "pandalive.co.kr/play/" in url or "pandalive.co.kr/live/play/" in url:
            
            if "sooplive.com" in url:
                url = url.replace("sooplive.com", "sooplive.co.kr")
            
            if "pandalive.co.kr" in url:
                url = re.sub(r'pandalive\.co\.kr/(live/)?play/', 'pandalive.co.kr/live/play/', url)

            # 입력창 갱신
            if self.url_input.text() != url:
                self.url_input.setText(url)
                self.log_output.append(f"📡 주소 캡처: {url}")

    def apply_dark_theme(self):
        dark_qss = """
        QMainWindow { background-color: #2b2b2b; }
        QLabel { color: #e0e0e0; font-weight: bold; }
        QPushButton { 
            background-color: #3c3f41; color: #ffffff; border: 1px solid #555; 
            padding: 6px; border-radius: 4px; 
        }
        QPushButton:hover { background-color: #4b4e50; }
        QPushButton:pressed { background-color: #5b5e60; }
        QPushButton:disabled { background-color: #2b2b2b; color: #666; border: 1px solid #444; }
        QLineEdit { background-color: #1e1e1e; color: #ffffff; border: 1px solid #555; padding: 4px; }
        QComboBox { background-color: #3c3f41; color: #ffffff; border: 1px solid #555; padding: 4px; }
        QTextEdit { background-color: #1e1e1e; color: #76ff03; border: 1px solid #555; font-family: Consolas; }
        QListWidget { background-color: #1e1e1e; color: #e0e0e0; border: 1px solid #555; }
        QListWidget::item { padding: 5px; }
        QListWidget::item:hover { background-color: #3c3f41; }
        QListWidget::item:selected { background-color: #555; color: #fff; }
        QTabWidget::pane { border: 1px solid #555; background-color: #2b2b2b; }
        QTabBar::tab { background: #3c3f41; color: #aaa; padding: 8px 15px; border: 1px solid #555; }
        QTabBar::tab:selected { background: #2b2b2b; color: #fff; font-weight: bold; }
        QSplitter::handle { background-color: #444; }
        """
        self.setStyleSheet(dark_qss)

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    if "platform_index" in config:
                        self.platform_combo.setCurrentIndex(config["platform_index"])
                    if "vlc_path" in config:
                        self.vlc_path_input.setText(config["vlc_path"])
                    if "login_id" in config:
                        self.login_id_input.setText(config["login_id"])
                    if "login_pw" in config:
                        self.login_pw_input.setText(config["login_pw"])
                    if "recent_streams" in config:
                        self.recent_streams = config["recent_streams"]
                        self.update_recent_list_ui()
            except Exception as e:
                self.log_output.append(f"⚠️ 설정 불러오기 실패: {e}")

    def save_settings(self):
        config = {
            "platform_index": self.platform_combo.currentIndex(),
            "vlc_path": self.vlc_path_input.text(),
            "login_id": self.login_id_input.text(),
            "login_pw": self.login_pw_input.text(),
            "recent_streams": self.recent_streams
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            logging.error(f"설정 저장 실패: {e}")

    def closeEvent(self, event):
        self.save_settings()
        for worker in self.active_threads:
            worker.stop()
            worker.wait()
        super().closeEvent(event)

    def on_cookie_added(self, cookie: QNetworkCookie):
        name = cookie.name().data().decode('utf-8')
        value = cookie.value().data().decode('utf-8')
        self.current_cookies[name] = value

    def change_browser_url(self):
        platform = self.platform_combo.currentText()
        if "치지직" in platform:
            url = "https://chzzk.naver.com"
        elif "숲라이브" in platform:
            url = "https://www.sooplive.co.kr"
        elif "판다티비" in platform:
            url = "https://www.pandalive.co.kr"
        self.browser.setUrl(QUrl(url))
        self.log_output.append(f"🌐 {platform} 홈으로 이동")

    def get_current_url(self):
        current_url = self.browser.url().toString()
        self.process_snatched_url(current_url)

    def browse_vlc(self):
        path, _ = QFileDialog.getOpenFileName(self, "VLC 실행 파일 선택", "", "Executable (*.exe)")
        if path:
            self.vlc_path_input.setText(path)

    def open_login_dialog(self):
        url = self.browser.url().toString()
        dialog = LoginDialog(url, self.current_cookies, self)
        if dialog.exec():
            self.log_output.append("🔑 브라우저 로그인 완료")

    def start_stream(self):
        url = self.url_input.text()
        login_id = self.login_id_input.text()
        login_pw = self.login_pw_input.text()
        stream_pw = self.stream_pw_input.text()
        platform = self.platform_combo.currentText()
        vlc_path = self.vlc_path_input.text() or self.vlc_path
        
        if not url:
            self.get_current_url()
            url = self.url_input.text()
            
        if not url:
            return

        if "sooplive.com" in url:
            url = url.replace("sooplive.com", "sooplive.co.kr")
            self.url_input.setText(url)

        if "pandalive.co.kr" in url:
            url = re.sub(r'pandalive\.co\.kr/(live/)?play/', 'pandalive.co.kr/live/play/', url)
            self.url_input.setText(url)

        if not vlc_path:
            QMessageBox.warning(self, "경고", "VLC 경로를 설정해주세요.")
            return

        page_title = self.browser.title() or "수동 입력"
        history_item = {"title": page_title, "url": url}
        
        self.recent_streams = [item for item in self.recent_streams if item["url"] != url]
        self.recent_streams.insert(0, history_item)
        self.recent_streams = self.recent_streams[:20]
        
        self.update_recent_list_ui()
        self.save_settings()

        worker = StreamWorker(url, platform, login_id, login_pw, stream_pw, self.current_cookies, vlc_path)
        worker.log_signal.connect(self.update_log)
        worker.finished_signal.connect(self.on_thread_finished)
        worker.start()
        
        self.active_threads.append(worker)

    def update_recent_list_ui(self):
        self.recent_list.clear()
        for item in self.recent_streams:
            display_text = f"{item['title']}\n({item['url']})"
            list_item = QListWidgetItem(display_text)
            list_item.setData(Qt.ItemDataRole.UserRole, item['url'])
            self.recent_list.addItem(list_item)

    def play_recent_stream(self, item):
        url = item.data(Qt.ItemDataRole.UserRole)
        self.url_input.setText(url)
        self.start_stream()

    def update_log(self, message):
        self.log_output.append(message)
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def on_thread_finished(self):
        worker = self.sender()
        if worker in self.active_threads:
            self.active_threads.remove(worker)
            worker.deleteLater()
        self.log_output.append("ℹ️ 창이 닫혀 스트림 1개가 정상적으로 종료되었습니다.")

if __name__ == "__main__":
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--enable-gpu-rasterization --ignore-gpu-blocklist"
    
    app = QApplication(sys.argv)
    window = BogiGUI()
    window.show()
    sys.exit(app.exec())