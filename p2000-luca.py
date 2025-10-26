import sys, os, threading, time, math, json, sqlite3, random
from queue import Queue, Empty
from datetime import datetime
import requests
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QPushButton, QLineEdit, QComboBox, QListWidget, QMessageBox, QFormLayout, QGroupBox, QTextEdit
from PySide6.QtCore import Qt, QTimer, Signal, QObject

# --- BASE_DIR voor bundeling ---
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(__file__)

SOUND_FILE = os.path.join(BASE_DIR, "resources", "alert.wav")

# --- Database setup ---
DB_DIR = os.path.join(os.path.expanduser("~"), ".p2000_alert")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "p200_messages.sqlite")
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_cur = _conn.cursor()
_cur.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    ts TEXT,
    region TEXT,
    unit TEXT,
    text TEXT,
    lat REAL,
    lon REAL,
    raw_json TEXT
)
""")
_conn.commit()

def store_message(m):
    try:
        _cur.execute("INSERT OR IGNORE INTO messages (id,ts,region,unit,text,lat,lon,raw_json) VALUES (?,?,?,?,?,?,?,?)",
                     (m.get('id'), m.get('timestamp'), m.get('region'), m.get('unit'),
                      m.get('text'), m.get('lat'), m.get('lon'), json.dumps(m, ensure_ascii=False)))
        _conn.commit()
    except: pass

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R*c

def geocode_postcode(postcode, huisnr=None):
    pc = postcode.replace(" ","").upper()
    url = "https://nominatim.openstreetmap.org/search"
    params = {"postalcode": pc, "country": "Netherlands", "format": "json", "limit":1}
    headers = {"User-Agent":"P2000Viewer/1.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=8)
        r.raise_for_status()
        data = r.json()
        if data: return float(data[0]['lat']), float(data[0]['lon'])
    except: return None, None
    return None, None

class Poller(threading.Thread):
    def __init__(self,q): super().__init__(daemon=True); self.q=q; self.running=True; self.seen=set()
    def run(self):
        while self.running:
            time.sleep(3)
            try:
                now=datetime.utcnow().isoformat()
                lat, lon = 52.445+random.random()*0.02, 4.826+random.random()*0.02
                msg={"id":f"p2000-{int(time.time()*1000)}-{random.randint(0,9999)}",
                     "timestamp":now,"region":"Zaanstreek-Waterland","unit":"BR01",
                     "text":"Brandmelding test","lat":lat,"lon":lon}
                if msg['id'] not in self.seen: self.seen.add(msg['id']); self.q.put(msg)
            except: pass
    def stop(self): self.running=False

try:
    import simpleaudio as sa
    play_sound=lambda f: sa.WaveObject.from_wave_file(f).play()
except:
    import platform, winsound
    play_sound=lambda f: winsound.PlaySound(f,winsound.SND_FILENAME|winsound.SND_ASYNC) if platform.system()=="Windows" else None

class Signals(QObject): new_message=Signal(dict)

class MainWindow(QWidget):
    def __init__(self,q):
        super().__init__(); self.q=q; self.signals=Signals()
        self.signals.new_message.connect(self.on_new_message)
        self.user_lat=None; self.user_lon=None; self.radius_km=5
        self.setWindowTitle("P2000 Luca"); self.setMinimumSize(820,480)
        self.setup_ui(); self.timer=QTimer(); self.timer.setInterval(300); self.timer.timeout.connect(self.poll_queue); self.timer.start()
    def setup_ui(self):
        layout=QVBoxLayout(); form=QFormLayout()
        self.postcode_input=QLineEdit(); self.postcode_input.setPlaceholderText("Bijv. 1541AB")
        self.huisnr_input=QLineEdit(); self.huisnr_input.setPlaceholderText("Huisnummer (optioneel)")
        self.radius_combo=QComboBox(); self.radius_combo.addItems(["5 km","10 km","20 km"]); self.radius_combo.setCurrentIndex(0)
        geocode_btn=QPushButton("Sla postcode op en activeer filter"); geocode_btn.clicked.connect(self.do_geocode)
        form.addRow("Postcode:",self.postcode_input); form.addRow("Huisnummer:",self.huisnr_input); form.addRow("Straal:",self.radius_combo); form.addRow("",geocode_btn)
        grp=QGroupBox("Filter instellingen"); grp.setLayout(form); layout.addWidget(grp)
        self.status_label=QLabel("Postcode niet ingevoerd."); layout.addWidget(self.status_label)
        self.recent_list=QListWidget(); layout.addWidget(QLabel("Recente meldingen:")); layout.addWidget(self.recent_list)
        self.setLayout(layout)
    def do_geocode(self):
        pc=self.postcode_input.text().strip(); hn=self.huisnr_input.text().strip() or None
        if not pc: QMessageBox.warning(self,"Fout","Voer eerst een postcode in."); return
        lat, lon = geocode_postcode(pc, hn)
        if lat is None: QMessageBox.warning(self,"Fout","Kan postcode niet lokaliseren."); return
        self.user_lat=lat; self.user_lon=lon; self.radius_km=int(self.radius_combo.currentText().split()[0])
        self.status_label.setText(f"Filter actief rond {pc} — straal {self.radius_km} km — coords: {lat:.5f},{lon:.5f}")
        QMessageBox.information(self,"Succes",f"Filter actief rond {pc} ({lat:.5f},{lon:.5f})")
    def poll_queue(self):
        try:
            while True:
                msg=self.q.get_nowait()
                lat=msg.get('lat'); lon=msg.get('lon')
                if self.user_lat and lat and lon:
                    dist=haversine(self.user_lat,self.user_lon,float(lat),float(lon))
                    if dist<=self.radius_km: store_message(msg); self.signals.new_message.emit(msg)
                else: store_message(msg); self.signals.new_message.emit(msg)
        except Empty: pass
    def on_new_message(self,msg):
        self.recent_list.insertItem(0,f"{msg.get('timestamp','')[:19]} — {msg.get('unit','')} — {msg.get('text','')[:80]}")
        try: play_sound(SOUND_FILE)
        except: pass
        p=QWidget(flags=Qt.WindowStaysOnTopHint|Qt.FramelessWindowHint)
        p.setWindowTitle("P2000 Melding"); p.setFixedSize(520,240)
        p.setLayout(QVBoxLayout())
        p.layout().addWidget(QLabel(f"{msg.get('region','')} — {msg.get('unit','')} — {msg.get('timestamp','')[:19]}"))
        te=QTextEdit(); te.setReadOnly(True); te.setPlainText(msg.get('text','')); te.setMinimumHeight(120)
        p.layout().addWidget(te)
        btn=QPushButton("Wegdrukken"); btn.clicked.connect(p.close); p.layout().addWidget(btn)
        screen=QApplication.primaryScreen().availableGeometry()
        p.move((screen.width()-p.width())//2,(screen.height()-p.height())//2); p.show()

def main():
    q=Queue(); poller=Poller(q); poller.start()
    app=QApplication(sys.argv)
    w=MainWindow(q); w.show()
    try: sys.exit(app.exec())
    finally: poller.stop()

if __name__=="__main__":
    main()
