# Energy Meter Collection System — Plan

> Python · Modbus RTU RS485 (4-channel) · Raspberry Pi 4 · PostgreSQL

---

## Overview

Sistem ini polling data dari 4 DTSU666 energy meter tiap 500ms per channel. Setiap meter terikat ke satu pin GPIO secara 1-to-1.

Ada dua jalur data yang berjalan paralel dan independen:

- **DB path** — data disimpan ke PostgreSQL selama sesi produksi aktif. Sesi aktif ditentukan oleh sinyal GPIO. Setiap cycle ditandai dengan `cycle_id`.
- **Buffer path** — data selalu masuk ke ring buffer tanpa memandang state GPIO. Buffer dipakai untuk live view di frontend.

Sinyal GPIO dari optocoupler dibaca langsung di pin Raspberry Pi 4.

---

## Stack

| Komponen | Pilihan |
|---|---|
| Bahasa | Python 3.11+ |
| Modbus RTU | `pymodbus` |
| GPIO | `RPi.GPIO` |
| Database | PostgreSQL |
| DB driver | `psycopg2` / `asyncpg` |
| Buffer (frontend) | `collections.deque` per meter |
| Async | `asyncio` |
| Config | `pydantic-settings` + `.env` |
| Logging | `structlog` |

---

## Struktur Folder

```
energy-collector/
├── config/
│   ├── settings.py         # Parameter global: DB URL, buffer size, timeout
│   ├── meters.yaml         # Daftar meter: port, slave ID, gpio_pin, label
│   └── registers.yaml      # Register map DTSU666 (shared semua meter)
├── core/
│   ├── modbus_client.py    # Koneksi & polling loop per meter (async task)
│   ├── register_parser.py  # Decode raw register → nilai (float32 / int16)
│   ├── buffer.py           # Ring buffer per meter (untuk frontend)
│   ├── gpio_handler.py     # State machine sesi & cycle per pin
│   └── db.py               # Insert per sample + cycle marker ke PostgreSQL
├── models/
│   └── meter_reading.py    # Dataclass payload per sample
├── main.py                 # Entry point
├── .env
└── requirements.txt
```

---

## Dua Jalur Data

```
[Polling 500ms]
      │
      ├──► Ring buffer (selalu, tanpa kondisi) ──► Frontend live view
      │
      └──► GPIO state check
                ├── AKTIF? → insert ke DB (dengan cycle_id aktif)
                └── IDLE?  → skip insert
```

---

## Alur Program

```
[Startup]
    └─► Load settings + meters.yaml + registers.yaml
            ├─ GAGAL? → Log error, exit
            └─ OK?
                └─► Untuk setiap meter (4x):
                        ├─ Koneksi Modbus RTU
                        ├─ Init ring buffer
                        ├─ Init GPIO handler (pin dari config)
                        └─ Spawn asyncio polling task (500ms):
                                ├─► Read registers meter
                                ├─► Parse → MeterReading
                                ├─► Push ke ring buffer (selalu)
                                └─► Cek GPIO state:
                                        ├── AKTIF → insert ke DB
                                        └── IDLE  → skip

[GPIO callback — event-driven, per pin]
    Falling edge (HIGH → LOW):
        ├── Jika state IDLE → mulai sesi baru (generate session_id, start insert)
        └── Jika state AKTIF → tutup cycle lama (assign cycle_id), buka cycle baru

    Rising edge (LOW → HIGH):
        └── Catat waktu naik → start timeout countdown

    Timeout (HIGH terlalu lama tanpa LOW):
        └── Tutup sesi → state kembali IDLE → berhenti insert ke DB
```

---

## State Machine GPIO (per meter)

### State

```
IDLE
  │  falling edge (HIGH → LOW)
  ▼
SAVING — cycle_id aktif, data masuk DB
  │  rising edge (LOW → HIGH)
  ▼
COOLING — data masih masuk DB, timeout berjalan
  │
  ├── falling edge (HIGH → LOW) sebelum timeout
  │       └── tutup cycle lama → assign cycle_id
  │           buka cycle baru → kembali ke SAVING
  │
  └── timeout tercapai (tidak ada LOW)
          └── tutup sesi → kembali ke IDLE
```

### Visualisasi sinyal

```
GPIO:   HIGH ── LOW ────── HIGH ──── LOW ────── HIGH ──── LOW ─── HIGH ──timeout──► IDLE
                │                   │                   │
             session                ▼                   ▼
             start             tutup cycle 1       tutup cycle 2
                               buka cycle 2        (sesi berakhir
                                                    saat timeout)

DB:     skip   [simpan──────────────────────────────────────]   skip

Cycle:         [────────── cycle_1 ──────────][── cycle_2 ──]
```

### Penjelasan tiap fase

| Fase GPIO | State sistem | Data ke DB | Keterangan |
|---|---|---|---|
| HIGH (awal) | IDLE | Tidak | Menunggu produksi mulai |
| Falling edge pertama | IDLE → SAVING | Mulai | Session baru, cycle_1 dibuka |
| LOW | SAVING | Ya | Fase injection |
| Rising edge | SAVING → COOLING | Ya | Fase cooling, timeout mulai |
| HIGH (cooling) | COOLING | Ya | Masih bagian dari cycle aktif |
| Falling edge berikutnya | COOLING → SAVING | Ya | cycle_1 ditutup, cycle_2 dibuka |
| HIGH melebihi timeout | COOLING → IDLE | Berhenti | Sesi selesai, mesin berhenti produksi |

---

## Mapping 1-to-1: Meter ↔ GPIO

```
meter_01  ←→  /dev/ttyACM0  ←→  GPIO pin 17
meter_02  ←→  /dev/ttyACM1  ←→  GPIO pin 27
meter_03  ←→  /dev/ttyACM2  ←→  GPIO pin 22
meter_04  ←→  /dev/ttyACM3  ←→  GPIO pin 23
```

---

## Module Detail

### `config/settings.py`

Parameter global:

- `DB_URL` — PostgreSQL connection string
- `BUFFER_MAXLEN` — kapasitas ring buffer per meter dalam jumlah sample (default `240` = 2 menit @ 500ms, bisa di-set via `.env`)
- `CYCLE_TIMEOUT_SECONDS` — durasi HIGH maksimal sebelum sesi dianggap selesai (default `300` = 5 menit)
- `METERS_CONFIG_PATH` — path ke `meters.yaml`
- `REGISTER_MAP_PATH` — path ke `registers.yaml`
- `LOG_LEVEL` — level logging (default `INFO`)

---

### `config/meters.yaml`

```yaml
meters:
  - id: meter_01
    label: "Mesin Injeksi 1"
    port: /dev/ttyACM0
    baudrate: 9600
    slave_id: 1
    parity: N
    stopbits: 1
    gpio_pin: 17
    gpio_debounce_ms: 50

  - id: meter_02
    label: "Mesin Injeksi 2"
    port: /dev/ttyACM1
    baudrate: 9600
    slave_id: 1
    parity: N
    stopbits: 1
    gpio_pin: 27
    gpio_debounce_ms: 50

  - id: meter_03
    label: "Mesin Injeksi 3"
    port: /dev/ttyACM2
    baudrate: 9600
    slave_id: 1
    parity: N
    stopbits: 1
    gpio_pin: 22
    gpio_debounce_ms: 50

  - id: meter_04
    label: "Mesin Injeksi 4"
    port: /dev/ttyACM3
    baudrate: 9600
    slave_id: 1
    parity: N
    stopbits: 1
    gpio_pin: 23
    gpio_debounce_ms: 50
```

---

### `config/registers.yaml`

Register map DTSU666. Shared untuk semua meter. Semua measurement elektrikal dan energi adalah Float32. Device info (Int16) dibaca sekali saat startup.

Strategi baca — 2 request per polling cycle:
- Request 1: `0x2000`–`0x2051` (82 register, semua electrical measurements)
- Request 2: `0x401E`–`0x4059` (60 register, semua energy)

```yaml
registers:

  # ── Voltage L-L ──────────────────────────────────────────
  Uab:
    address: 0x2000
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: V
    group: voltage

  Ubc:
    address: 0x2002
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: V
    group: voltage

  Uca:
    address: 0x2004
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: V
    group: voltage

  # ── Voltage L-N ──────────────────────────────────────────
  Ua:
    address: 0x2006
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: V
    group: voltage

  Ub:
    address: 0x2008
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: V
    group: voltage

  Uc:
    address: 0x200A
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: V
    group: voltage

  # ── Current ──────────────────────────────────────────────
  Ia:
    address: 0x200C
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: A
    group: current

  Ib:
    address: 0x200E
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: A
    group: current

  Ic:
    address: 0x2010
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: A
    group: current

  # ── Active Power ─────────────────────────────────────────
  Pt:
    address: 0x2012
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: W
    group: active_power

  Pa:
    address: 0x2014
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: W
    group: active_power

  Pb:
    address: 0x2016
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: W
    group: active_power

  Pc:
    address: 0x2018
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: W
    group: active_power

  # ── Reactive Power ───────────────────────────────────────
  Qt:
    address: 0x201A
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: var
    group: reactive_power

  Qa:
    address: 0x201C
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: var
    group: reactive_power

  Qb:
    address: 0x201E
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: var
    group: reactive_power

  Qc:
    address: 0x2020
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: var
    group: reactive_power

  # ── Power Factor ─────────────────────────────────────────
  PFt:
    address: 0x202A
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: ""
    group: power_factor

  PFa:
    address: 0x202C
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: ""
    group: power_factor

  PFb:
    address: 0x202E
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: ""
    group: power_factor

  PFc:
    address: 0x2030
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: ""
    group: power_factor

  # ── Frequency & Demand ───────────────────────────────────
  frequency:
    address: 0x2044
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: Hz
    group: frequency

  active_power_demand:
    address: 0x2050
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: W
    group: demand

  # ── Active Energy ─────────────────────────────────────────
  ImpEp:
    address: 0x401E
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: kWh
    group: energy

  ExpEp:
    address: 0x4028
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: kWh
    group: energy

  # ── Reactive Energy ───────────────────────────────────────
  Q1Eq:
    address: 0x4032
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: kvarh
    group: energy

  Q2Eq:
    address: 0x403C
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: kvarh
    group: energy

  Q3Eq:
    address: 0x4046
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: kvarh
    group: energy

  Q4Eq:
    address: 0x4050
    type: float32
    count: 2
    byte_order: big
    word_order: big
    scale: 1.0
    unit: kvarh
    group: energy

  # ── Device Info (dibaca sekali saat startup) ──────────────
  network_mode:
    address: 0x0003
    type: int16
    count: 1
    byte_order: big
    word_order: big
    scale: 1.0
    unit: ""
    group: device_info
    note: "0 = 3P4W, 1 = 3P3W"

  meter_type:
    address: 0x000B
    type: int16
    count: 1
    byte_order: big
    word_order: big
    scale: 1.0
    unit: ""
    group: device_info

  slave_id_register:
    address: 0x002D
    type: int16
    count: 1
    byte_order: big
    word_order: big
    scale: 1.0
    unit: ""
    group: device_info

  baudrate_register:
    address: 0x002E
    type: int16
    count: 1
    byte_order: big
    word_order: big
    scale: 1.0
    unit: bps
    group: device_info
```

---

### `core/register_parser.py`

Terima raw result pymodbus, decode berdasarkan `type`, `byte_order`, `word_order` dari register map.

Tanggung jawab:
- `float32` → `BinaryPayloadDecoder` dengan byte/word order yang sesuai
- `int16` → decode signed 16-bit dari satu register
- `uint16` → decode unsigned 16-bit
- Terapkan `scale` setelah decode
- Return `None` per field kalau register error — bukan crash

Catatan byte order: Implementasi awal `byte_order=big, word_order=big`. Kalau live test hasilnya NaN atau nilai ekstrem, flip ke `word_order=little` di `registers.yaml` tanpa ubah kode.

---

### `models/meter_reading.py`

Satu sample = satu `MeterReading`.

Field wajib:
- `timestamp` — waktu baca (UTC, clock RPi NTP sync)
- `meter_id` — dari `id` di `meters.yaml`
- `cycle_id` — UUID cycle aktif saat sample dibaca. `None` kalau state IDLE (tidak akan di-insert ke DB)
- `session_id` — UUID sesi produksi aktif. `None` kalau IDLE

Field dinamis: semua `field_name` dari `registers.yaml` grup electrical + energy.

---

### `core/buffer.py`

`deque(maxlen=BUFFER_MAXLEN)` per meter. Berjalan terus tanpa memandang state GPIO.

Kapasitas default `240` sample = 2 menit @ 500ms. Bisa diubah via `BUFFER_MAXLEN` di `.env`.

Tanggung jawab:
- `push(reading)` — selalu append, data lama otomatis drop kalau penuh
- `snapshot()` — return list copy untuk frontend, tidak mengosongkan buffer
- Thread-safe pakai `threading.Lock`

Buffer tidak terlibat dalam alur penyimpanan ke DB sama sekali — murni untuk konsumsi frontend.

---

### `core/gpio_handler.py`

Satu `GPIOHandler` per meter. Handle state machine sesi dan cycle untuk pin yang di-assign.

Setup:
- `GPIO.setmode(GPIO.BCM)` — dipanggil sekali di `main.py`
- `GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)` — optocoupler open-collector, default HIGH
- `GPIO.add_event_detect(pin, GPIO.BOTH, callback=..., bouncetime=debounce_ms)`

State dan transisi:

```
IDLE
  │  falling edge (HIGH → LOW)
  │  → generate session_id baru
  │  → generate cycle_id baru
  ▼
SAVING
  │  rising edge (LOW → HIGH)
  │  → catat waktu naik, start timeout timer
  ▼
COOLING
  │
  ├── falling edge (HIGH → LOW) sebelum timeout
  │       → tutup cycle aktif (simpan cycle_end timestamp)
  │       → generate cycle_id baru
  │       → kembali ke SAVING
  │
  └── timeout tercapai tanpa falling edge
          → tutup sesi (simpan session_end timestamp)
          → cycle_id = None, session_id = None
          → kembali ke IDLE
```

Timeout diimplementasikan dengan `threading.Timer` yang di-cancel kalau ada falling edge sebelum waktu habis.

State saat ini (IDLE / SAVING / COOLING), `session_id`, dan `cycle_id` aktif di-expose ke polling loop via property yang thread-safe.

---

### `core/modbus_client.py`

Satu instance per meter, jalan sebagai asyncio task.

Tanggung jawab:
- Buka serial port sesuai config
- Loop 500ms:
  - Request 1: baca blok `0x2000`–`0x2051`
  - Request 2: baca blok `0x401E`–`0x4059`
  - Parse → `MeterReading` (inject `cycle_id` dan `session_id` dari GPIO handler)
  - Push ke ring buffer (selalu)
  - Cek state GPIO handler:
    - SAVING atau COOLING → insert ke DB
    - IDLE → skip insert
- Reconnect otomatis dengan backoff (3x retry, interval 2s) kalau serial timeout
- Log tiap polling error tanpa mematikan loop

---

### `core/db.py`

Insert per sample langsung ke PostgreSQL saat state aktif. Tidak ada batch per cycle — data masuk real-time.

Tanggung jawab:
- Insert satu `MeterReading` per call (include `session_id`, `cycle_id`, `timestamp`)
- Gunakan connection pool — tidak buka koneksi baru tiap insert
- Log error per insert tanpa crash loop

Tabel tambahan untuk tracking sesi dan cycle:

- `production_sessions` — catat `session_id`, `meter_id`, `start_time`, `end_time`
- `production_cycles` — catat `cycle_id`, `session_id`, `meter_id`, `start_time`, `end_time`

Kedua tabel ini di-update oleh GPIO handler saat ada transisi state, bukan oleh polling loop.

---

### Database Schema (PostgreSQL)

```sql
-- Tabel utama
meter_readings (
    time                  TIMESTAMPTZ     NOT NULL,
    session_id            UUID            NOT NULL,
    cycle_id              UUID            NOT NULL,
    meter_id              TEXT            NOT NULL,
    -- Voltage L-L
    Uab                   DOUBLE PRECISION,
    Ubc                   DOUBLE PRECISION,
    Uca                   DOUBLE PRECISION,
    -- Voltage L-N
    Ua                    DOUBLE PRECISION,
    Ub                    DOUBLE PRECISION,
    Uc                    DOUBLE PRECISION,
    -- Current
    Ia                    DOUBLE PRECISION,
    Ib                    DOUBLE PRECISION,
    Ic                    DOUBLE PRECISION,
    -- Active Power
    Pt                    DOUBLE PRECISION,
    Pa                    DOUBLE PRECISION,
    Pb                    DOUBLE PRECISION,
    Pc                    DOUBLE PRECISION,
    -- Reactive Power
    Qt                    DOUBLE PRECISION,
    Qa                    DOUBLE PRECISION,
    Qb                    DOUBLE PRECISION,
    Qc                    DOUBLE PRECISION,
    -- Power Factor
    PFt                   DOUBLE PRECISION,
    PFa                   DOUBLE PRECISION,
    PFb                   DOUBLE PRECISION,
    PFc                   DOUBLE PRECISION,
    -- Frequency & Demand
    frequency             DOUBLE PRECISION,
    active_power_demand   DOUBLE PRECISION,
    -- Active Energy
    ImpEp                 DOUBLE PRECISION,
    ExpEp                 DOUBLE PRECISION,
    -- Reactive Energy
    Q1Eq                  DOUBLE PRECISION,
    Q2Eq                  DOUBLE PRECISION,
    Q3Eq                  DOUBLE PRECISION,
    Q4Eq                  DOUBLE PRECISION
)

CREATE INDEX ON meter_readings (meter_id, cycle_id, time DESC);
CREATE INDEX ON meter_readings (session_id, time DESC);

-- Tracking sesi produksi
production_sessions (
    session_id    UUID PRIMARY KEY,
    meter_id      TEXT NOT NULL,
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ           -- NULL selama sesi aktif
)

-- Tracking cycle per sesi
production_cycles (
    cycle_id      UUID PRIMARY KEY,
    session_id    UUID NOT NULL,
    meter_id      TEXT NOT NULL,
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ           -- NULL selama cycle aktif
)
```

---

### `main.py`

Orchestrator:
1. `GPIO.setmode(GPIO.BCM)` — sekali di awal
2. Untuk tiap meter: init buffer → init GPIO handler → init modbus client → spawn asyncio task
3. Handle `SIGINT`/`SIGTERM`: stop semua task → `GPIO.cleanup()` → tutup DB connection pool → log shutdown

---

### Logging

Format JSON via `structlog`. Di-deploy sebagai systemd service dengan `Restart=always` dan `RestartSec=5`.

| Event | Level | Field tambahan |
|---|---|---|
| Startup sukses | INFO | semua meter yang berhasil connect |
| Config invalid | ERROR | field yang bermasalah |
| Polling error (Modbus timeout) | WARNING | meter_id, retry ke-n |
| Reconnect berhasil | INFO | meter_id, downtime_seconds |
| Reconnect gagal (max retry) | ERROR | meter_id |
| Sesi produksi mulai | INFO | meter_id, session_id, gpio_pin |
| Cycle baru dibuka | INFO | meter_id, session_id, cycle_id |
| Cycle ditutup | INFO | meter_id, cycle_id, duration_seconds |
| Sesi selesai (timeout) | INFO | meter_id, session_id, total_cycles, duration_seconds |
| Insert DB sukses | DEBUG | meter_id, cycle_id, timestamp |
| Insert DB gagal | ERROR | meter_id, cycle_id, error_message |
| Shutdown dimulai | INFO | signal yang diterima |
| Shutdown selesai | INFO | — |

---

## Contoh `.env`

```env
DB_URL=postgresql://user:pass@localhost:5432/energy_db
BUFFER_MAXLEN=240
CYCLE_TIMEOUT_SECONDS=300
METERS_CONFIG_PATH=config/meters.yaml
REGISTER_MAP_PATH=config/registers.yaml
LOG_LEVEL=INFO
```

---

## Catatan Final

**Byte order live test:** Cek nilai `Ua` saat pertama jalan — harusnya sekitar 220V. Kalau NaN atau ekstrem, flip `word_order: little` di `registers.yaml`.

**Optocoupler pull-up:** Output open-collector, `PUD_UP` sudah tepat. Pin default HIGH, jadi LOW = mesin aktif.

**Connection pool DB:** Pakai pool (bukan single connection) karena insert terjadi tiap 500ms per meter — 4 meter = potensi 8 insert/detik bersamaan.

**systemd:** Tambahkan `After=postgresql.service network.target` di unit file supaya service tidak jalan sebelum DB ready.