# ForzaListener

Records Forza Horizon 6 telemetry ("Data Out") to compact `.bin` files — the recordings the
[Project G](https://projectg.g-garage.workers.dev) tuning calculator reads.

[Українською ↓](#українською)

## Download

**[ForzaListener.zip — latest release](https://github.com/86Undertaker86/ProjectG-Listener/releases/latest/download/ForzaListener.zip)**
(Windows 10 / 11, no installation). All versions: [Releases](https://github.com/86Undertaker86/ProjectG-Listener/releases).

## How to use

1. Unzip and run `ForzaListener.exe` (keep the whole folder: the exe needs `_internal` next to it).
   - "Windows protected your PC" → **More info** → **Run anyway** (the program has no paid code
     signature).
   - If the firewall asks, allow access on private networks.
2. The window shows this PC's IP and the port (default `49152`). In the game open
   **Settings → HUD and Gameplay**, scroll down to the **Telemetry** section and there: turn
   **Data Out** on, set the IP address to that IP and the port to that port.
3. **Change…** picks the folder for the recordings (default `Documents\ForzaTelemetry`).
4. **Start** and drive. Only race time is recorded; every car gets its own `.bin` file.
   **Stop** (or closing the window) writes everything to disk.
5. Upload the `.bin` to the [calculator](https://projectg.g-garage.workers.dev/calculator).
   `listener_log.txt` in the same folder is the listener's journal if something goes wrong.

## What is recorded

Each UDP packet the game sends (324 bytes) is filtered: only frames while racing and moving,
and only the fields the calculator needs, are appended as fixed-size binary records. Since 1.0.2
that includes body motion, height and puddles (vertical acceleration, pitch, roll, position Y,
wheel in puddle) for the coming rally, cross-country and drift presets. The file starts with
`FH6T`, a version number and a JSON header (the field list, record size, the car's ID, class,
performance index, drivetrain and car group). Nothing leaves your PC — the listener only
receives packets from the game and writes files.

## Build from source

Python 3.12+ on Windows (tested with 3.14), standard library only (tkinter):

```bat
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install pyinstaller
build_listener.bat
```

→ `dist\ForzaListener\ForzaListener.exe` and `dist\ForzaListener.zip`. Or run it without building:
`.venv\Scripts\python.exe listener_gui.py`.

| File | What |
|---|---|
| `Listener.py` | UDP socket + `Recorder`: packet layout, filtering, the `.bin` writer |
| `listener_gui.py` | The window (tkinter): folder, port, Start / Stop, journal, counters |
| `packaging/` | PyInstaller spec, the exe version resource, the Ukrainian readme put in the zip |
| `build_listener.bat` | Builds the exe, runs its self-check, makes the zip |

## License

[MIT](LICENSE).

---

## Українською

Записує телеметрію Forza Horizon 6 («Data Out») у компактні файли `.bin` — саме їх читає
калькулятор налаштувань [Project G](https://projectg.g-garage.workers.dev).

**[Завантажити ForzaListener.zip — остання версія](https://github.com/86Undertaker86/ProjectG-Listener/releases/latest/download/ForzaListener.zip)**
(Windows 10 / 11, без встановлення).

1. Розпакуйте й запустіть `ForzaListener.exe` (тека має лишатися цілою: exe не працює без
   `_internal` поруч).
   - «Windows захистив ваш ПК» → **Докладніше** → **Все одно запустити** (програма не має
     платного цифрового підпису).
   - Якщо брандмауер спитає — дозвольте доступ у приватних мережах.
2. У вікні видно IP цього ПК і порт (за замовчуванням `49152`). У грі відкрийте
   **Налаштування → HUD та ігровий процес**, прогорніть униз до пункту **Телеметрія** і там:
   увімкніть **Data Out**, IP-адреса = цей IP, порт = цей порт.
3. **Змінити…** — тека для записів (за замовчуванням `Документи\ForzaTelemetry`).
4. **Старт** — і їдьте. Записується лише час заїзду; на кожну машину окремий файл `.bin`.
   **Стоп** (або закриття вікна) дописує все на диск.
5. Завантажте `.bin` у [калькулятор](https://projectg.g-garage.workers.dev/calculator).
   Якщо щось пішло не так, у тій самій теці є `listener_log.txt` — журнал слухача.

Слухач нічого не надсилає в інтернет: він лише приймає пакети від гри й пише файли.
Зібрати самому — див. «Build from source» вище. Ліцензія — [MIT](LICENSE).
