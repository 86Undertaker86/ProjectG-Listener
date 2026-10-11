"""
FH6 "Data Out" capture: receives UDP datagrams and appends one compact binary
record per useful packet to a .bin file. Nothing is decoded or formatted here —
the fh6tuner package reads the files directly (fh6tuner.capture.parse_capture);
capture never imports it.

What is not stored (see need.txt and fh6tuner):
- datagrams that are too short, have IsRaceOn != 1 or fail the car sanity check;
- fields nothing needs (need.txt F). The per-car constants (CAR_FIELDS) go to the
  file header once instead of into every record;
- frames with Speed <= STATIONARY_SPEED_MPS, except the first and the last frame
  of each such stretch. The tuner ignores all of them (fh6tuner.telemetry.MIN_SPEED_MPS),
  and the two kept frames preserve the run boundaries and timestamp gaps it relies on, so
  the tune computed from the file is the same as from every packet.
A new file is started whenever the car constants change (another car, new PI),
so one file never mixes cars.

File layout (little-endian):
    b"FH6T" | u16 FILE_VERSION | u32 n | n bytes of UTF-8 JSON header | records...
The header holds "fields" ([[name, type], ...] — the record layout, packed in that
order), "record_size", "car" (CAR_FIELDS values), "min_speed_mps" and "started".
"""

from __future__ import annotations

import json
import operator
import re
import socket
import struct
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

# The game's "Data Out" packet, one field per line: <S|U|F|X><bits> <Name>, in byte order
# (S/U = signed / unsigned int, F = float, X = padding). The datagram is 324 bytes; this
# describes 323 (one trailing byte unmapped). If the game changes the packet, edit this — every
# field Recorder uses (IsRaceOn, RECORD_FIELDS, CAR_FIELDS) must stay; Recorder raises otherwise.
PACKET_LAYOUT = """
S32 IsRaceOn
U32 TimestampMS
F32 EngineMaxRpm
F32 EngineIdleRpm
F32 CurrentEngineRpm
F32 AccelerationX
F32 AccelerationY
F32 AccelerationZ
F32 VelocityX
F32 VelocityY
F32 VelocityZ
F32 AngularVelocityX
F32 AngularVelocityY
F32 AngularVelocityZ
F32 Yaw
F32 Pitch
F32 Roll
F32 NormalizedSuspensionTravelFrontLeft
F32 NormalizedSuspensionTravelFrontRight
F32 NormalizedSuspensionTravelRearLeft
F32 NormalizedSuspensionTravelRearRight
F32 TireSlipRatioFrontLeft
F32 TireSlipRatioFrontRight
F32 TireSlipRatioRearLeft
F32 TireSlipRatioRearRight
F32 WheelRotationSpeedFrontLeft
F32 WheelRotationSpeedFrontRight
F32 WheelRotationSpeedRearLeft
F32 WheelRotationSpeedRearRight
S32 WheelOnRumbleStripFrontLeft
S32 WheelOnRumbleStripFrontRight
S32 WheelOnRumbleStripRearLeft
S32 WheelOnRumbleStripRearRight
S32 WheelInPuddleFrontLeft
S32 WheelInPuddleFrontRight
S32 WheelInPuddleRearLeft
S32 WheelInPuddleRearRight
F32 SurfaceRumbleFrontLeft
F32 SurfaceRumbleFrontRight
F32 SurfaceRumbleRearLeft
F32 SurfaceRumbleRearRight
F32 TireSlipAngleFrontLeft
F32 TireSlipAngleFrontRight
F32 TireSlipAngleRearLeft
F32 TireSlipAngleRearRight
F32 TireCombinedSlipFrontLeft
F32 TireCombinedSlipFrontRight
F32 TireCombinedSlipRearLeft
F32 TireCombinedSlipRearRight
F32 SuspensionTravelMetersFrontLeft
F32 SuspensionTravelMetersFrontRight
F32 SuspensionTravelMetersRearLeft
F32 SuspensionTravelMetersRearRight
S32 CarOrdinal
S32 CarClass
S32 CarPerformanceIndex
S32 DrivetrainType
S32 NumCylinders
U32 CarGroup
F32 SmashableVelDiff
F32 SmashableMass
F32 PositionX
F32 PositionY
F32 PositionZ
F32 Speed
F32 Power
F32 Torque
F32 TireTempFrontLeft
F32 TireTempFrontRight
F32 TireTempRearLeft
F32 TireTempRearRight
F32 Boost
F32 Fuel
F32 DistanceTraveled
F32 BestLap
F32 LastLap
F32 CurrentLap
F32 CurrentRaceTime
U16 LapNumber
U8 RacePosition
U8 Accel
U8 Brake
U8 Clutch
U8 HandBrake
U8 Gear
S8 Steer
S8 NormalizedDrivingLine
S8 NormalizedAIBrakeDifference
"""

MAGIC = b"FH6T"
FILE_VERSION = 1
FILE_HEADER = struct.Struct("<4sHI")   # magic, version, JSON header length

WHEELS = ("FrontLeft", "FrontRight", "RearLeft", "RearRight")


def _x4(base: str) -> tuple:
    return tuple(base + wheel for wheel in WHEELS)


# Stored in every record, grouped by need.txt section. Record order = packet order.
TUNER_FIELDS = (    # A: read by fh6tuner (COLUMN_MAP)
    "TimestampMS", "LapNumber", "Speed", "Steer", "Accel", "Brake", "AccelerationX",
    *_x4("NormalizedSuspensionTravel"), *_x4("TireSlipRatio"), *_x4("TireSlipAngle"), *_x4("TireTemp"),
)
FORMULA_FIELDS = (  # D: optional channels of the diagnostics (OPTIONAL_COLUMN_MAP)
    "VelocityZ", "AngularVelocityY", "CurrentRaceTime",
    *_x4("WheelRotationSpeed"), *_x4("TireCombinedSlip"), *_x4("SuspensionTravelMeters"),
    # FormulasforAI §7 presets: sideslip angle (G12/G13) and gearing (G14)
    "VelocityX", "CurrentEngineRpm", "EngineMaxRpm", "Gear",
)
EXTRA_FIELDS = (    # E: recommended — data-quality filters, CarSpec auto-fill
    "HandBrake", "Power", "Torque", *_x4("SurfaceRumble"),
)
RECORD_FIELDS = TUNER_FIELDS + FORMULA_FIELDS + EXTRA_FIELDS

# B + NumCylinders + CarOrdinal: constant per car -> file header; a change starts a new file.
CAR_FIELDS = ("CarOrdinal", "CarClass", "CarPerformanceIndex", "DrivetrainType", "NumCylinders")
# Sanity ranges: a datagram outside them is garbage and is dropped (not treated as a new car).
# NumCylinders 0 is allowed so that electric cars are not rejected.
CAR_LIMITS = {"CarClass": (0, 7), "CarPerformanceIndex": (100, 999), "NumCylinders": (0, 16)}
DRIVETRAIN_NAMES = {0: "FWD", 1: "RWD", 2: "AWD"}

# Frames at or below this speed are "stationary". Must stay <= fh6tuner.telemetry.MIN_SPEED_MPS
# (the tuner ignores slower frames); written to the header so the tuner can check it.
STATIONARY_SPEED_MPS = 5.0

RECV_BYTES = 2048              # > the 324-byte datagram (Windows raises on a too-small buffer)
SOCKET_BUFFER_BYTES = 4 << 20  # ~12k datagrams queue up while the disk is busy -> none lost
SOCKET_TIMEOUT_S = 0.5         # recv wakes up regularly so Ctrl+C works on Windows
FLUSH_INTERVAL_S = 1.0         # the file lags (and a crash loses) at most this much

STRUCT_CODE = {"S8": "b", "S16": "h", "S32": "i", "S64": "q",
               "U8": "B", "U16": "H", "U32": "I", "U64": "Q", "F32": "f", "F64": "d"}
_FORMAT_LINE = re.compile(r"\s*([SUFX])(\d+)\s+(\w+)", re.IGNORECASE)


def load_layout(text: str = PACKET_LAYOUT) -> dict:
    """{name: (byte offset, type)} of every data field of the packet layout (X = padding)."""
    layout, offset = {}, 0
    for line in text.splitlines():
        m = _FORMAT_LINE.match(line)
        if m is None:
            continue
        kind = f"{m.group(1).upper()}{m.group(2)}"
        if kind[0] != "X":
            layout[m.group(3)] = (offset, kind)
        offset += int(m.group(2)) // 8
    return layout


def _size(kind: str) -> int:
    return int(kind[1:]) // 8


def _spans(names, layout: dict) -> list:
    """Merged [start, end) byte ranges of the fields, in packet order."""
    spans = []
    for name in sorted(names, key=lambda n: layout[n][0]):
        start, kind = layout[name]
        end = start + _size(kind)
        if spans and spans[-1][1] == start:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    return spans


def _extractor(spans: list):
    """datagram -> the bytes of the spans concatenated (plain slicing, no decoding)."""
    slices = [slice(start, end) for start, end in spans]
    if len(slices) == 1:
        only = slices[0]
        return lambda data: data[only]
    parts = operator.itemgetter(*slices)
    return lambda data: b"".join(parts(data))


def _field_struct(name: str, layout: dict) -> tuple:
    offset, kind = layout[name]
    return struct.Struct("<" + STRUCT_CODE[kind]), offset


class Recorder:
    """Filters datagrams and appends compact records to .bin files in output_dir."""

    def __init__(self, output_dir: Union[str, Path], log: Callable[[str], None] = print,
                 layout_text: str = PACKET_LAYOUT):
        self.log = log            # where status messages go (console print, or the GUI journal)
        layout = load_layout(layout_text)
        used = ("IsRaceOn",) + RECORD_FIELDS + CAR_FIELDS
        missing = [name for name in used if name not in layout]
        if missing:
            raise ValueError(f"the packet layout has no fields {missing}")
        self.output_dir = Path(output_dir)
        self.fields = sorted(RECORD_FIELDS, key=lambda n: layout[n][0])
        self.types = {name: layout[name][1] for name in self.fields}
        self.record_size = sum(_size(kind) for kind in self.types.values())
        self.min_size = max(layout[name][0] + _size(layout[name][1]) for name in used)
        self._record = _extractor(_spans(RECORD_FIELDS, layout))
        self._car_key = _extractor(_spans(CAR_FIELDS, layout))
        self._car_names = sorted(CAR_FIELDS, key=lambda n: layout[n][0])
        self._car_values = struct.Struct("<" + "".join(STRUCT_CODE[layout[n][1]] for n in self._car_names))
        self._race_on, self._race_on_at = _field_struct("IsRaceOn", layout)
        self._speed, self._speed_at = _field_struct("Speed", layout)

        self.stats = Counter()
        self.files = []
        self._file = None
        self._car = None          # car-constant bytes of the open file
        self._racing = None       # last IsRaceOn state seen (None = no packet yet)
        self._stationary = False  # inside a stretch of frames at <= STATIONARY_SPEED_MPS
        self._pending = None      # latest frame of that stretch, written when it ends
        self._last_flush = 0.0

    def process(self, data: bytes) -> None:
        """Handles one datagram."""
        stats = self.stats
        stats["received"] += 1
        if len(data) < self.min_size:
            if not stats["short"]:
                self.log(f"Пакет {len(data)} байт замість ≥ {self.min_size} — перевірте формат Data Out у грі.")
            stats["short"] += 1
            return
        if self._race_on.unpack_from(data, self._race_on_at)[0] != 1:
            stats["not_racing"] += 1
            if self._racing is not False:
                self.log("Гра не в заїзді (IsRaceOn = 0) — чекаю на заїзд.")
                self._racing = False
            return
        car = self._car_key(data)
        if car != self._car:
            if not self._open_file(car):
                stats["bad_car"] += 1
                return
        elif self._racing is False:
            self.log("Заїзд продовжено — запис триває.")
        self._racing = True

        record = self._record(data)
        if self._speed.unpack_from(data, self._speed_at)[0] <= STATIONARY_SPEED_MPS:
            if self._stationary:            # inside a stretch: keep only its latest frame
                if self._pending is not None:
                    stats["stationary"] += 1
                self._pending = record
                return
            self._stationary = True         # the first frame of a stretch is written
        elif self._stationary:              # the stretch is over: write its last frame
            self._stationary = False
            self._write_pending()
        self._write(record)

    def flush(self) -> None:
        """Pushes buffered records to disk (called while no packets arrive)."""
        if self._file is not None:
            self._file.flush()
            self._last_flush = time.monotonic()

    def close(self) -> None:
        self._close_file()

    @property
    def current_file(self) -> Optional[Path]:
        """The .bin being written right now, or None between cars / before the first packet."""
        return self.files[-1] if self._file is not None else None

    def summary(self) -> str:
        s = self.stats
        size_kb = sum(p.stat().st_size for p in self.files if p.exists()) / 1024
        dropped = ", ".join(f"{label}: {s[key]}" for key, label in (
            ("not_racing", "не в заїзді"), ("stationary", "стоянка"), ("short", "неповні"),
            ("bad_car", "некоректні дані машини"), ("errors", "помилки прийому")) if s[key])
        return (f"Отримано пакетів: {s['received']}, збережено: {s['stored']} "
                f"({size_kb:.0f} КБ, файлів: {len(self.files)})"
                + (f"; відкинуто — {dropped}" if dropped else "") + ".")

    def _open_file(self, car: bytes) -> bool:
        """Starts a new file for these car constants; False if they fail the sanity check."""
        values = dict(zip(self._car_names, self._car_values.unpack(car)))
        if any(not lo <= values[name] <= hi for name, (lo, hi) in CAR_LIMITS.items()):
            return False
        self._close_file()
        started = datetime.now()
        stem = f"{started:%Y-%m-%d_%H-%M-%S}_car{values['CarOrdinal']}_pi{values['CarPerformanceIndex']}"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path, n = self.output_dir / f"{stem}.bin", 2
        while path.exists():
            path, n = self.output_dir / f"{stem}_{n}.bin", n + 1
        header = json.dumps({
            "fields": [[name, self.types[name]] for name in self.fields],
            "record_size": self.record_size,
            "car": values,
            "min_speed_mps": STATIONARY_SPEED_MPS,
            "started": started.isoformat(timespec="seconds"),
        }).encode("utf-8")
        self._file = open(path, "wb")
        self._file.write(FILE_HEADER.pack(MAGIC, FILE_VERSION, len(header)) + header)
        self._file.flush()
        self._last_flush = time.monotonic()
        self._car = car
        self.files.append(path)
        drive = DRIVETRAIN_NAMES.get(values["DrivetrainType"], values["DrivetrainType"])
        self.log(f"Запис у {path.name}: машина {values['CarOrdinal']}, клас {values['CarClass']}, "
                 f"PI {values['CarPerformanceIndex']}, привід {drive}.")
        return True

    def _write(self, record: bytes) -> None:
        self._file.write(record)
        self.stats["stored"] += 1
        now = time.monotonic()
        if now - self._last_flush >= FLUSH_INTERVAL_S:
            self._file.flush()
            self._last_flush = now

    def _write_pending(self) -> None:
        if self._pending is not None:
            self._write(self._pending)
            self._pending = None

    def _close_file(self) -> None:
        if self._file is None:
            return
        self._write_pending()
        self._file.close()
        self._file = None
        self._car = None
        self._stationary = False


class Listener:
    """UDP socket feeding a Recorder. run() blocks until stop() or Ctrl+C."""

    def __init__(self, udp_ip: str, udp_port: int, output_dir: Union[str, Path],
                 log: Callable[[str], None] = print):
        self.recorder = Recorder(output_dir, log)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_BYTES)
            self.sock.bind((udp_ip, udp_port))    # OSError when the port is taken
        except OSError:
            self.sock.close()
            raise
        self.sock.settimeout(SOCKET_TIMEOUT_S)
        self._running = False

    def run(self) -> None:
        recv, process = self.sock.recv, self.recorder.process
        self._running = True
        while self._running:
            try:
                data = recv(RECV_BYTES)
            except TimeoutError:          # no packets for SOCKET_TIMEOUT_S: game paused or closed
                self.recorder.flush()
                continue
            except OSError:               # e.g. an oversized datagram from another sender
                if self.sock.fileno() < 0:
                    break
                self.recorder.stats["errors"] += 1
                continue
            process(data)

    def stop(self) -> None:
        self._running = False

    def close(self) -> None:
        self._running = False
        self.recorder.close()
        self.sock.close()

    def summary(self) -> str:
        return self.recorder.summary()
