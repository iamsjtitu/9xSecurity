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
- **API Key**: wa.9x.design dashboard se copy karke paste karein (Bearer auth, `wa9x_...`)
- Photo alerts official **`POST /api/v2/sendMessageFile`** (multipart direct upload) se
  jaate hain, text **`POST /api/v2/sendMessage`** se — docs: https://wa.9x.design/docs
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

## 7. App ka build banana (bina Python ke chalane ke liye)
```bat
venv\Scripts\activate
pip install pyinstaller
pyinstaller --noconfirm --windowed --name "9xSecurity" --add-data "yolov8n.pt;." --collect-binaries imageio_ffmpeg main.py
```
`dist\9xSecurity\` folder banega jisme `9xSecurity.exe` hoga — poora folder kisi
bhi Windows PC par copy karke exe chala sakte hain (Python install ki zaroorat
nahi). Folder-style build ka fayda: **app seconds me khulti hai** (single-file
.exe har baar 1.5 GB extract karti thi jisme minutes lagte the).

## 8. GitHub par push → automatic build ⚙️
> **Zaroori:** sirf GitHub par push karne se build **apne aap nahi banta**.
> Iske liye ek **GitHub Actions** workflow add kiya gaya hai:
> `.github/workflows/build-windows.yml`

Kaise chalta hai:
1. Chat input ke **"Save to Github"** button se apna code GitHub par push karein.
2. Bas itna hi! Ab **har "Save to GitHub" push par** workflow apne aap chalega:
   - **Version number apne aap badhta hai** — har build par `1.0.<build number>`
     (jaise 1.0.7 → 1.0.8). Kuch manually badalna nahi padta.
   - GitHub Windows par app build karke ek **Setup installer** banata hai
   - Release me `9xSecuritySetup-v1.0.N.exe` attach ho jaata hai.
   - Manually kuch bhi run karne ki zaroorat nahi. (Chahein to Actions tab →
     "Build 9x Security" → *Run workflow* se bhi chala sakte hain.)

Pehli baar install: `9xSecuritySetup-v1.0.N.exe` download karein → double-click →
install ho jaayega, **desktop shortcut** ban jaayega. Na zip, na extract.

> ⏳ **Note**: Build ~10–18 min leta hai (AI libraries badi hain). Beech me logs
> ruk sakte hain — ye stuck nahi hai. 60 min ki safety timeout lagi hai.

## 9. Software me se Auto-Update 🔄
App ke andar **⚙ Settings → Updates** tab:
- Bas **Check for Updates** dabayein — koi link/repo **nahi daalna** (update
  source build me automatic set hota hai)
- **Repo PRIVATE hai?** GitHub bina token ke private repo ki release nahi
  dikhata (isse "koi release nahi" / 404 aata hai). Do options:
  1. Repo ko **Public** kar dein (GitHub repo → Settings → Change visibility), ya
  2. Settings → Updates me **GitHub Token** daalein:
     github.com/settings/tokens → *Fine-grained token* → sirf apna repo select
     karein → Permissions me **Contents: Read-only** → Generate → paste karein.
- Agar nayi version (release) mili, to app naya **Setup installer download karke
  chupchaap install** kar dega aur nayi version ke saath restart ho jaayega.
  (Ye sirf installed build me kaam karta hai; aapka data — config, snapshots,
  database — safe rehta hai.)

Nayi version release karne ka tarika (sirf 1 kadam):
1. **Save to GitHub** karein — bas! Version apne aap badhega (1.0.N) aur
   release apne aap ban jaayegi.
2. Users apne app se **Check for Updates** dabaa ke turant nayi version paa lenge.

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
