# 9x Security — Gate Vehicle Monitor 🚗🚚

Desktop software jo aapke **IP camera (RTSP)** se live feed leta hai, gate par
aane/jaane wale **4-wheeler / truck / bus** ko AI se pehchanta hai, aur har
gaadi ka **snapshot date + time + type + Entry/Exit label + number plate** ke
saath local computer me save karta hai.

- 100% **offline & free** AI (YOLOv8 — internet sirf pehli baar model download ke liye, jo already bundle hai)
- **Ek hi camera** — screen par ek line kheench do, us line ko cross karne ki direction se Entry / Exit tay hota hai
- Saare snapshots + records aapke computer par hi (SQLite `events.db` + `snapshots/` folder)

---

## 1. Requirements (Windows)
- Windows 10 / 11 (64-bit)
- Python 3.10 – 3.11  → https://www.python.org/downloads/  (install karte waqt **"Add Python to PATH"** tick karein)

## 2. Install
Command Prompt kholein, project folder me jaayein:
```bat
cd path\to\9x_security
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
> `easyocr` (number plate) install thoda bada hai. Agar plate feature nahi chahiye
> to `requirements.txt` se `easyocr` line hata sakte hain.

## 3. Run
```bat
python main.py
```

## 4. Kaise use karein
1. Upar RTSP URL daalein, jaise:
   `rtsp://username:password@192.168.1.10:554/Streaming/Channels/101`
   (URL nahi doge to laptop webcam se test chalega.)
2. **Connect** dabayein — live feed dikhega + "Connected" status.
3. **Draw Detection Line** dabayein → video par **2 baar click** karein (start & end point).
   Gate ke aar-paar line kheenchein.
4. **Swap Entry/Exit** se decide karein ki line ka kaun sa taraf "andar" hai
   (ek baar test karke sahi set kar lein).
5. Ab jab bhi koi gaadi line cross karegi:
   - snapshot save hoga `snapshots\YYYY-MM-DD\` folder me
   - right side table me record aayega (thumbnail, date-time, type, Entry/Exit, plate)
   - "ENTRIES TODAY" / "EXITS TODAY" counters update honge
6. Purane records dekhne ke liye **Date** chunein + **Filter**, ya **Show All**.
   Kisi row par **double-click** karke bada snapshot dekh sakte hain.
   **Open Snapshots** button se saari photos ka folder khulta hai.

## 5. Data kahan save hota hai
```
9x_security/
├── snapshots/2026-06-15/Entry_truck_10-42-05-123.jpg   ← photos (date-wise)
└── events.db                                            ← saara record (SQLite)
```

## 6. Ek .exe banana (bina Python ke chalane ke liye)
```bat
venv\Scripts\activate
pip install pyinstaller
pyinstaller --noconfirm --windowed --name "9xSecurity" --add-data "yolov8n.pt;." main.py
```
`.exe` file `dist\9xSecurity\9xSecurity.exe` me milega. Poora `dist\9xSecurity`
folder kisi bhi Windows PC par copy karke chala sakte hain.

---

## Settings (config.json — auto-save hoti hai)
| Key | Matlab |
|-----|--------|
| `rtsp_url` | camera stream URL |
| `line` | detection line (normalized 0..1) |
| `entry_direction` | kaun si direction Entry mani jaaye (`pos`/`neg`) — "Swap" button se badalta hai |
| `confidence` | AI detection threshold (0.4 default) |
| `enable_plate` | number plate OCR on/off |
| `vehicle_classes` | kaunse vehicle detect karein: car / truck / bus |

## Tips
- Kam roshni / raat me achhe results ke liye camera par IR/night-vision on rakhein.
- Line ko gate ke thoda andar rakhein taaki poori gaadi frame me aane ke baad cross ho.
- CPU par slow lage to `config.json` me `detect_frame_skip` badhaa dein (jaise 3).
