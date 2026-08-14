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
1. **Login screen** aayega — default:
   - Username: `admin`
   - Password: `9xsecurity`
   (Baad me **⚙ Settings → Login / Security** se badal sakte hain.)
2. Upar RTSP URL daalein, jaise:
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

## 5. WhatsApp Alerts (wa.9x.design) 📲
Har Entry/Exit par gaadi ka **photo + details** turant WhatsApp par bheji jaati hai.

**Setup:** app me **⚙ Settings → WhatsApp** kholein:
- ✅ *Enable WhatsApp alerts* on karein
- **API Base URL**: `https://wa.9x.design` (ya aapka whitelabel URL)
- **X-API-Key**: wa.9x.design dashboard se copy karke paste karein
- **Recipients**: jitne number chahein, **ek line me ek** (country code ke saath), jaise:
  ```
  919876543210
  919812345678
  ```
- *Send photo* ✅ = photo bhejega, ❌ = sirf text alert

Alert format:
```
🚨 9x Security
Entry - TRUCK
Time: 2026-06-15 10:42:05
Plate: HR26AB1234
```
> **Note:** Photo bhejne ke liye multipart upload use hota hai. Agar aapke plan ka
> media-endpoint ka format alag ho aur image na jaaye, to app **automatically
> text alert** bhej deta hai (alert kabhi miss nahi hoga). Har request ka jawab
> `wa_log.txt` me save hota hai — zaroorat pade to usse exact format tune kar sakte hain.

**Settings → Account** tab me aap wa.9x.design ka email/password bhi store kar sakte hain (reference ke liye).

## 6. Data kahan save hota hai
```
9x_security/
├── snapshots/2026-06-15/Entry_truck_10-42-05-123.jpg   ← photos (date-wise)
└── events.db                                            ← saara record (SQLite)
```

## 7. Ek .exe banana (bina Python ke chalane ke liye)
```bat
venv\Scripts\activate
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "9xSecurity" --add-data "yolov8n.pt;." main.py
```
Single `.exe` file `dist\9xSecurity.exe` me milega — use kisi bhi Windows PC par
copy karke chala sakte hain (Python install ki zaroorat nahi).

## 8. GitHub par push → automatic .exe build ⚙️
> **Zaroori:** sirf GitHub par push karne se `.exe` **apne aap nahi banta**.
> Iske liye ek **GitHub Actions** workflow add kiya gaya hai:
> `.github/workflows/build-windows.yml`

Kaise chalta hai:
1. Chat input ke **"Save to Github"** button se apna code GitHub par push karein.
2. Bas itna hi! Ab **har "Save to GitHub" push par** workflow apne aap chalega:
   - GitHub Windows par `.exe` build karega
   - `updater.py` me jo `APP_VERSION` hai, usi naam se **Release** (jaise `v1.0.0`)
     apne aap ban/refresh ho jaayegi aur usme `9xSecurity.exe` attach hoga.
   - Manually kuch bhi run karne ki zaroorat nahi. (Chahein to Actions tab →
     "Build 9x Security" → *Run workflow* se bhi chala sakte hain.)

> ⏳ **Note**: "Build single-file EXE" step **10–25 minute** leta hai kyunki AI
> libraries (torch/easyocr) bahut badi hain. Beech me logs ruk jaate hain —
> ye **stuck nahi hai**, bas archive ban raha hota hai. Sabr rakhein. 90 minute
> ki safety timeout lagi hui hai.

## 9. Software me se Auto-Update 🔄
App ke andar **⚙ Settings → Updates** tab:
- **GitHub Repo**: `owner/repository` daalein (jaise `yourname/9x-security`)
- **Check for Updates** dabayein
- Agar nayi version (release tag) mili, to app seedha nayi `.exe` **download +
  install** kar dega aur restart ho jaayega. (Ye sirf `.exe` mode me kaam karta hai.)

Nayi version release karne ka tarika (sirf 2 kadam):
1. `updater.py` me `APP_VERSION` badhaayein (jaise `1.0.0` → `1.0.1`).
2. **Save to GitHub** karein — build + release (`v1.0.1`) apne aap ban jaayegi.
3. Users apne app se **Check for Updates** dabaa ke turant nayi version paa lenge.

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
| `wa_enabled` / `wa_api_key` / `wa_recipients` | WhatsApp alert settings |
| `github_repo` | auto-update ke liye GitHub `owner/name` |
| `auth_user` / `auth_hash` | app login (default admin / 9xsecurity) |

## Tips
- Kam roshni / raat me achhe results ke liye camera par IR/night-vision on rakhein.
- Line ko gate ke thoda andar rakhein taaki poori gaadi frame me aane ke baad cross ho.
- CPU par slow lage to `config.json` me `detect_frame_skip` badhaa dein (jaise 3).
