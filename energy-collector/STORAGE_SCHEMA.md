# Storage Schema — Redis & PostgreSQL

Referensi struktur data untuk **Energy Meter Collection System**, dipakai sebagai acuan saat membangun endpoint API yang membaca data dari Redis (live) maupun PostgreSQL (historis).

- **Redis** → mirror real-time: sample terbaru, ring buffer, dan device info. Cocok untuk live view / polling cepat.
- **PostgreSQL** → penyimpanan historis: hanya sample saat sesi produksi aktif + tracking sesi/cycle. Cocok untuk laporan & query rentang waktu.

---

## 1. Redis

### 1.1 Pola Key

Semua key mengikuti pola berikut (mendukung multi-collector & multi-meter):

```
{prefix}:{collector_id}:meter:{meter_id}:{suffix}
```

| Komponen        | Sumber                          | Contoh        |
| --------------- | ------------------------------- | ------------- |
| `prefix`        | `REDIS_KEY_PREFIX` (default `energy`) | `energy`  |
| `collector_id`  | `COLLECTOR_ID` (wajib di-set)   | `pi-line-1`   |
| `meter_id`      | `id` di `meters.yaml`           | `meter_01`    |
| `suffix`        | jenis data (lihat di bawah)     | `latest`      |

| Suffix         | Tipe Redis | Isi                                                        |
| -------------- | ---------- | ---------------------------------------------------------- |
| `latest`       | String     | JSON sample terbaru (1 reading)                            |
| `readings`     | List       | Ring buffer JSON sample; **terbaru di index 0** (LPUSH)    |
| `device_info`  | Hash       | Info statis device, ada TTL (`DEVICE_INFO_TTL_SECONDS`)    |

**Contoh key lengkap:**

```
energy:pi-line-1:meter:meter_01:latest
energy:pi-line-1:meter:meter_01:readings
energy:pi-line-1:meter:meter_01:device_info
```

### 1.2 `latest` (String) & `readings` (List)

Keduanya menyimpan JSON dengan struktur yang sama.

- `latest` → `SET` berisi 1 sample paling baru.
- `readings` → `LPUSH` + `LTRIM 0 {BUFFER_MAXLEN-1}`. Index `0` = paling baru, makin besar makin lama. Panjang maksimal = `BUFFER_MAXLEN` (default 240 ≈ 2 menit @ 500ms).

**Struktur JSON:**

```json
{
  "collector_id": "pi-line-1",
  "meter_id": "meter_01",
  "timestamp": "2026-06-30T02:05:31.123456+00:00",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "cycle_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "gpio_state": "SAVING",
  "values": {
    "Uab": 380.1, "Ubc": 379.8, "Uca": 380.4,
    "Ua": 219.5, "Ub": 220.1, "Uc": 219.9,
    "Ia": 10.2, "Ib": 9.8, "Ic": 10.5,
    "Pt": 6500.3, "Pa": 2150.1, "Pb": 2100.5, "Pc": 2249.7,
    "Qt": 800.2, "Qa": 260.0, "Qb": 270.1, "Qc": 270.1,
    "PFt": 0.92, "PFa": 0.92, "PFb": 0.91, "PFc": 0.93,
    "frequency": 50.01,
    "active_power_demand": 6400.0,
    "ImpEp": 1523.45, "ExpEp": 0.0,
    "Q1Eq": 457.0, "Q2Eq": 0.0, "Q3Eq": 0.0, "Q4Eq": 0.0
  }
}
```

| Field          | Tipe                 | Catatan                                                              |
| -------------- | -------------------- | ------------------------------------------------------------------- |
| `collector_id` | string               | ID device collector (Raspberry Pi)                                  |
| `meter_id`     | string               | ID meter                                                            |
| `timestamp`    | string (ISO 8601)    | Waktu pembacaan, UTC                                                 |
| `session_id`   | string (UUID) \| null | `null` saat mesin idle (state `IDLE`)                              |
| `cycle_id`     | string (UUID) \| null | `null` saat mesin idle                                             |
| `gpio_state`   | string               | `IDLE` \| `SAVING` \| `COOLING`                                     |
| `values`       | object               | Map nama field → nilai (float) atau `null` jika register error      |

> **Catatan:** Sample **selalu** dipublish ke Redis tanpa memandang state GPIO. Saat `IDLE`, `session_id`/`cycle_id` bernilai `null` dan data **tidak** masuk PostgreSQL (hanya live view).

### 1.3 `device_info` (Hash)

Dibaca sekali saat startup, punya TTL (default 86400 detik / 24 jam).

| Field (hash key)     | Tipe   | Asal / Arti                                  |
| -------------------- | ------ | -------------------------------------------- |
| `network_mode`       | string | `0` = 3P4W, `1` = 3P3W                        |
| `meter_type`         | string | Tipe meter                                   |
| `slave_id_register`  | string | Slave ID Modbus                              |
| `baudrate_register`  | string | Baudrate (bps)                               |
| `collector_id`       | string | ID collector                                 |
| `meter_id`           | string | ID meter                                     |
| `updated_at`         | string | ISO 8601, waktu update terakhir              |

> Nilai numerik disimpan sebagai **string** (konversi `HSET`). Nilai yang gagal dibaca disimpan sebagai string kosong `""`.

### 1.4 Contoh Akses (untuk Endpoint)

```python
import json
import redis.asyncio as redis

r = redis.from_url("redis://localhost:6379/0", decode_responses=True)

prefix, collector, meter = "energy", "pi-line-1", "meter_01"
base = f"{prefix}:{collector}:meter:{meter}"

# Sample terbaru
latest = json.loads(await r.get(f"{base}:latest"))

# N sample terakhir (0 = terbaru)
raw = await r.lrange(f"{base}:readings", 0, 49)   # 50 sample teratas
readings = [json.loads(x) for x in raw]

# Device info
info = await r.hgetall(f"{base}:device_info")
```

**Discovery key (tanpa hardcode meter):**

```python
# Cari semua meter milik satu collector
keys = await r.keys("energy:pi-line-1:meter:*:latest")
# → ['energy:pi-line-1:meter:meter_01:latest', ...]
# (untuk produksi besar, lebih baik pakai SCAN daripada KEYS)
```

---

## 2. PostgreSQL

Tiga tabel: `meter_readings` (data sample), `production_sessions` & `production_cycles` (tracking).

> Identifier kolom disimpan **lowercase** oleh PostgreSQL (tidak di-quote). Mis. kolom `Uab` di-query sebagai `uab`.

### 2.1 Tabel `meter_readings`

Insert per sample (real-time) **hanya saat sesi produksi aktif**.

| Kolom        | Tipe          | Keterangan                          |
| ------------ | ------------- | ----------------------------------- |
| `time`       | TIMESTAMPTZ   | Waktu pembacaan (NOT NULL)          |
| `session_id` | UUID          | ID sesi produksi (NOT NULL)         |
| `cycle_id`   | UUID          | ID cycle (NOT NULL)                 |
| `meter_id`   | TEXT          | ID meter (NOT NULL)                 |

**Kolom measurement** (semua `DOUBLE PRECISION`, nullable):

| Grup            | Kolom                              | Unit  |
| --------------- | ---------------------------------- | ----- |
| Voltage L-L     | `uab`, `ubc`, `uca`                | V     |
| Voltage L-N     | `ua`, `ub`, `uc`                   | V     |
| Current         | `ia`, `ib`, `ic`                   | A     |
| Active Power    | `pt`, `pa`, `pb`, `pc`             | W     |
| Reactive Power  | `qt`, `qa`, `qb`, `qc`             | var   |
| Power Factor    | `pft`, `pfa`, `pfb`, `pfc`         | —     |
| Frequency       | `frequency`                        | Hz    |
| Demand          | `active_power_demand`              | W     |
| Active Energy   | `impep`, `expep`                   | kWh   |
| Reactive Energy | `q1eq`, `q2eq`, `q3eq`, `q4eq`     | kvarh |

**Index:**

```sql
idx_readings_meter_cycle_time  ON (meter_id, cycle_id, time DESC)
idx_readings_session_time      ON (session_id, time DESC)
```

### 2.2 Tabel `production_sessions`

Satu baris per sesi produksi (rentang mesin aktif sampai timeout).

| Kolom        | Tipe        | Keterangan                                   |
| ------------ | ----------- | -------------------------------------------- |
| `session_id` | UUID        | PRIMARY KEY                                  |
| `meter_id`   | TEXT        | NOT NULL                                     |
| `start_time` | TIMESTAMPTZ | NOT NULL — saat sesi dimulai                 |
| `end_time`   | TIMESTAMPTZ | `NULL` selama sesi masih berjalan            |

### 2.3 Tabel `production_cycles`

Satu baris per cycle (siklus mesin di dalam satu sesi).

| Kolom        | Tipe        | Keterangan                                   |
| ------------ | ----------- | -------------------------------------------- |
| `cycle_id`   | UUID        | PRIMARY KEY                                  |
| `session_id` | UUID        | NOT NULL — relasi ke `production_sessions`   |
| `meter_id`   | TEXT        | NOT NULL                                     |
| `start_time` | TIMESTAMPTZ | NOT NULL                                     |
| `end_time`   | TIMESTAMPTZ | `NULL` selama cycle masih berjalan           |

**Index:** `idx_cycles_session ON (session_id)`

> Relasi logis (tidak ada FK constraint eksplisit di schema):
> `production_sessions` 1—N `production_cycles` 1—N `meter_readings`
> dihubungkan via `session_id` dan `cycle_id`.

### 2.4 Contoh Query (untuk Endpoint)

**Reading historis satu cycle:**

```sql
SELECT time, ua, ub, uc, ia, ib, ic, pt, frequency, impep
FROM meter_readings
WHERE meter_id = $1 AND cycle_id = $2
ORDER BY time ASC;
```

**Daftar sesi terakhir + jumlah cycle:**

```sql
SELECT s.session_id, s.start_time, s.end_time,
       COUNT(c.cycle_id) AS total_cycles
FROM production_sessions s
LEFT JOIN production_cycles c ON c.session_id = s.session_id
WHERE s.meter_id = $1
GROUP BY s.session_id
ORDER BY s.start_time DESC
LIMIT 50;
```

**Konsumsi energi (delta) per cycle:**

```sql
SELECT cycle_id,
       MAX(impep) - MIN(impep) AS energy_kwh
FROM meter_readings
WHERE meter_id = $1 AND session_id = $2
GROUP BY cycle_id;
```

---

## 3. State GPIO & Implikasi Penyimpanan

| State     | Arti                              | Masuk Redis | Masuk PostgreSQL | `session_id`/`cycle_id` |
| --------- | --------------------------------- | ----------- | ---------------- | ----------------------- |
| `IDLE`    | Mesin tidak aktif                 | Ya          | Tidak            | `null`                  |
| `SAVING`  | Mesin aktif (sesi & cycle jalan)  | Ya          | Ya               | terisi (UUID)           |
| `COOLING` | Jeda; tunggu timeout sebelum idle | Ya          | Ya               | terisi (UUID)           |

Transisi: `IDLE --(LOW)--> SAVING --(HIGH)--> COOLING --(LOW)--> SAVING ...`
`COOLING --(timeout `CYCLE_TIMEOUT_SECONDS`)--> IDLE` (sesi & cycle ditutup).

---

## 4. Parameter Konfigurasi Relevan

| Env Var                   | Default  | Pengaruh                                              |
| ------------------------- | -------- | ----------------------------------------------------- |
| `REDIS_KEY_PREFIX`        | `energy` | Prefix semua key Redis                                |
| `COLLECTOR_ID`            | —        | Bagian key Redis; wajib unik per device collector     |
| `BUFFER_MAXLEN`           | `240`    | Panjang maks list `readings` (≈ durasi live view)     |
| `DEVICE_INFO_TTL_SECONDS` | `86400`  | TTL hash `device_info`                                |
| `POLL_INTERVAL_MS`        | `500`    | Interval sample → frekuensi data masuk                |
| `CYCLE_TIMEOUT_SECONDS`   | `300`    | Durasi HIGH sebelum sesi ditutup                      |
