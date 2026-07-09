<div align="center">
  
# Panduan Instalasi ARGUS

🌐 [English](INSTALL.md) | [Indonesia](INSTALL.id.md)

</div>

Dokumen ini adalah manual instalasi dan konfigurasi resmi untuk ARGUS. Dokumen ini mencakup semua yang diperlukan untuk menjalankan deployment ARGUS secara menyeluruh: basis data PostgreSQL, dashboard web Flask, bot Telegram, scheduler latar belakang, dan AI Security Copilot.

**Apa saja yang akan terpasang.** Sebuah basis data PostgreSQL, sebuah virtual environment Python berisi dependensi ARGUS, proses dashboard Flask (`app.py`), secara opsional proses bot Telegram (`bot/main.py`), dan — jika Anda menginginkan fitur AI — sebuah server LLM lokal yang kompatibel dengan OpenAI yang dipanggil oleh ARGUS melalui HTTP.

**Perkiraan waktu instalasi.** 30–60 menit untuk instalasi pertama kali pada satu mesin dengan PostgreSQL yang sudah tersedia; 60–120 menit jika Anda juga memasang PostgreSQL dan server LLM lokal dari awal.

**Pengetahuan teknis minimum yang dibutuhkan.** Terbiasa dengan shell command-line (Bash pada Linux/macOS, PowerShell atau Command Prompt pada Windows), pemahaman dasar mengenai penyuntingan file teks, dan pemahaman SQL/PostgreSQL yang cukup untuk menjalankan perintah `psql` seperti yang ditunjukkan. Tidak ada asumsi pengetahuan ARGUS sebelumnya.

**Cakupan.** Dokumen ini mencakup instalasi, konfigurasi, verifikasi, pembaruan, backup/restore, pemecahan masalah, dan pengerasan (hardening) untuk produksi. Untuk apa yang dilakukan ARGUS dan bagaimana arsitekturnya, lihat [`README.md`](./README.md). Untuk detail rute/API, lihat tautan [Dokumentasi](#26-referensi).

> **Catatan tentang akurasi.** Setiap perintah, variabel lingkungan, dan perilaku yang dijelaskan di bawah ini mencerminkan apa yang benar-benar diimplementasikan dalam basis kode ARGUS saat ini, diverifikasi langsung terhadap sumber kode (`app.py`, `bot/main.py`, `bot/database/db.py`, `bot/migrate.py`, `bot/database/schema.sql`, `bot/Ai/llm.py`, `bot/jobs/daily_scan.py`). Di mana pun panduan ini menjelaskan sesuatu yang belum diimplementasikan (paket Docker, misalnya), hal itu ditandai secara eksplisit sebagai **Direncanakan**.

---

## Daftar Isi

1. [Persyaratan Sistem](#1-persyaratan-sistem)
2. [Sistem Operasi yang Didukung](#2-sistem-operasi-yang-didukung)
3. [Dependensi Perangkat Lunak](#3-dependensi-perangkat-lunak)
4. [Instalasi Proyek](#4-instalasi-proyek)
5. [Instalasi PostgreSQL](#5-instalasi-postgresql)
6. [Inisialisasi Basis Data](#6-inisialisasi-basis-data)
7. [Konfigurasi Environment](#7-konfigurasi-environment)
8. [Instalasi AI](#8-instalasi-ai)
9. [Konfigurasi API Eksternal](#9-konfigurasi-api-eksternal)
10. [Konfigurasi Bot Telegram](#10-konfigurasi-bot-telegram)
11. [Konfigurasi Dashboard](#11-konfigurasi-dashboard)
12. [Konfigurasi Scheduler](#12-konfigurasi-scheduler)
13. [Menjalankan ARGUS](#13-menjalankan-argus)
14. [Konfigurasi Pertama Kali](#14-konfigurasi-pertama-kali)
15. [Daftar Periksa Verifikasi](#15-daftar-periksa-verifikasi)
16. [Memperbarui ARGUS](#16-memperbarui-argus)
17. [Backup & Restore](#17-backup--restore)
18. [Pemecahan Masalah](#18-pemecahan-masalah)
19. [Logging](#19-logging)
20. [Rekomendasi Keamanan](#20-rekomendasi-keamanan)
21. [Rekomendasi Performa](#21-rekomendasi-performa)
22. [Instalasi Docker (Dukungan Masa Depan)](#22-instalasi-docker-dukungan-masa-depan)
23. [Deployment Produksi](#23-deployment-produksi)
24. [Uninstalasi](#24-uninstalasi)
25. [Pertanyaan yang Sering Diajukan](#25-pertanyaan-yang-sering-diajukan)
26. [Referensi](#26-referensi)

---

## 1. Persyaratan Sistem

### Persyaratan Minimum

| Komponen | Minimum |
|---|---|
| CPU | 2 core |
| RAM | 4 GB (8 GB jika Anda berencana menjalankan LLM lokal di mesin yang sama) |
| Penyimpanan | 10 GB ruang kosong (bertambah seiring riwayat CVE, laporan, dan data percakapan) |
| GPU | Tidak wajib. Opsional — hanya relevan jika Anda menjalankan server LLM lokal yang dipercepat GPU |
| Sistem Operasi | Windows 10/11 64-bit, atau distribusi Linux 64-bit modern |
| Python | 3.11 atau lebih baru (3.12 direkomendasikan) — dibutuhkan oleh kumpulan dependensi yang dikunci (pinned) di `requirements.txt` |
| PostgreSQL | 14 atau lebih baru |
| Konektivitas Jaringan | Akses HTTPS keluar (outbound) ke NVD API, feed CISA KEV, dan (jika digunakan) FIRST EPSS API; akses keluar ke Telegram Bot API jika menjalankan bot |

### Persyaratan yang Direkomendasikan

| Ukuran deployment | CPU | RAM | Penyimpanan | Catatan |
|---|---|---|---|---|
| **Lab kecil / analis tunggal** | 2–4 core | 8 GB | 20 GB SSD | Semuanya (PostgreSQL, ARGUS, LLM lokal opsional) pada satu mesin sudah cukup pada skala ini |
| **Organisasi menengah** | 4–8 core | 16 GB | 50–100 GB SSD | Jalankan PostgreSQL pada penyimpanan khusus; pertimbangkan host terpisah untuk server LLM jika fitur AI digunakan secara intensif |
| **Deployment enterprise** | 8+ core | 32 GB+ | 200 GB+ SSD, dengan target backup | Host basis data terpisah, host inferensi LLM terpisah, reverse proxy di depan dashboard, monitoring dan agregasi log |

**Catatan perangkat keras berdasarkan beban kerja:**

- **Beban kerja AI** — Inferensi LLM lokal berbasis CPU dapat digunakan untuk chat bervolume rendah dan analisis CVE latar belakang, tetapi akan lambat untuk model yang lebih besar; GPU dengan VRAM yang cukup untuk model pilihan Anda secara dramatis meningkatkan latensi respons. Lihat [§8 Instalasi AI](#8-instalasi-ai) untuk panduan ukuran model.
- **Basis data berukuran besar** — Performa PostgreSQL berskala sesuai RAM yang tersedia untuk shared buffer dan ukuran cache efektif; lihat [§21 Rekomendasi Performa](#21-rekomendasi-performa) untuk panduan tuning setelah jumlah baris tabel `matches`/`cves` Anda tumbuh hingga ratusan ribu.
- **Laporan historis** — Laporan PDF yang dihasilkan terakumulasi di bawah `bot/dashboard/generated_reports/`; alokasikan penyimpanan sesuai kebutuhan jika Anda menyimpan laporan dalam jangka panjang alih-alih mengarsipkannya secara eksternal.
- **Inventaris aset berskala besar** — Durasi pemindaian (scan) berskala sesuai jumlah aset dan batas rate limit NVD API (lihat [§9](#9-konfigurasi-api-eksternal)); kunci API NVD sangat direkomendasikan setelah Anda memiliki lebih dari beberapa aset.

---

## 2. Sistem Operasi yang Didukung

| Platform | Status |
|---|---|
| Ubuntu 22.04 / 24.04 LTS | Didukung |
| Debian 11 / 12 | Didukung |
| Fedora (rilis terbaru) | Didukung |
| Distribusi kompatibel RHEL (RHEL, Rocky Linux, AlmaLinux) | Didukung |
| Windows 10 (64-bit) | Didukung |
| Windows 11 | Didukung |
| macOS | Tidak dibahas secara eksplisit di sini, tetapi seharusnya bekerja dengan instruksi Linux yang disubstitusi dengan ekuivalen Homebrew (belum diuji oleh proyek ini) |

ARGUS adalah aplikasi Python murni tanpa ekstensi terkompilasi khusus OS di luar yang sudah disediakan `psycopg2-binary`, `matplotlib`, dan `pillow` sebagai wheel prebuilt, sehingga tidak memerlukan perubahan kode khusus platform. Instruksi di bawah dipisahkan menjadi **Linux** dan **Windows** di mana pun langkah-langkahnya benar-benar berbeda.

---

## 3. Dependensi Perangkat Lunak

| Dependensi | Mengapa dibutuhkan |
|---|---|
| **Python 3.11+** | Runtime untuk dashboard Flask dan bot Telegram |
| **pip** | Memasang dependensi Python dari `requirements.txt` |
| **PostgreSQL 14+** | Datastore utama untuk seluruh data ARGUS — aset, temuan, CVE, laporan, pengguna, percakapan AI |
| **Git** | Meng-clone dan memperbarui repositori ARGUS |
| **Server LLM yang kompatibel dengan OpenAI** (misalnya server `llama.cpp`, atau Ollama yang mengekspos endpoint kompatibel OpenAI-nya) | Menjalankan chat AI Security Copilot dan analisis CVE otomatis. Opsional — ARGUS berjalan sepenuhnya tanpanya, dengan fitur AI dinonaktifkan |
| **Visual C++ Runtime** (khusus Windows) | Beberapa paket Python yang dikunci (misalnya `psycopg2-binary`, `matplotlib`, `numpy`, `pillow`) menyediakan wheel Windows prebuilt yang ter-link ke Visual C++ runtime; pasang [Microsoft Visual C++ Redistributable](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist) jika Anda mengalami error pemuatan DLL |
| **Build tools** (khusus Linux, kadang-kadang) | Jika wheel prebuilt tidak tersedia untuk kombinasi Python/OS Anda yang spesifik, `pip` akan mencoba mengompilasi paket dari sumber kode, yang memerlukan compiler C dan header client PostgreSQL. Lihat [§18 Pemecahan Masalah](#18-pemecahan-masalah) |

> `requirements.txt` milik ARGUS **tidak** menyertakan server WSGI produksi (misalnya Gunicorn) atau toolchain Docker. Keduanya dibahas terpisah di [§23](#23-deployment-produksi) dan [§22](#22-instalasi-docker-dukungan-masa-depan), karena keduanya merupakan pilihan operasional, bukan dependensi aplikasi.

---

## 4. Instalasi Proyek

### 4.1 Clone repositori

```bash
git clone <repo-url> argus
cd argus
```

Ini membuat direktori `argus/` tingkat atas yang berisi `app.py` (entry point dashboard), `requirements.txt`, dan paket `bot/`, yang berisi entry point bot Telegram (`bot/main.py`) dan setiap modul bersama (akses basis data, scanner, AI, laporan, scheduler).

### 4.2 Buat virtual environment

Mengisolasi dependensi ARGUS dalam virtual environment menghindari konflik dengan proyek Python lain pada mesin yang sama.

**Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell):**
```powershell
py -m venv venv
venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
py -m venv venv
venv\Scripts\activate.bat
```

Anda akan melihat `(venv)` ditambahkan di depan prompt shell Anda setelah diaktifkan. Setiap perintah `pip` dan `python` pada sisa panduan ini mengasumsikan virtual environment sedang aktif.

### 4.3 Pasang dependensi

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Ini memasang Flask, Flask-Login, Flask-WTF, `psycopg2-binary`, APScheduler, `python-telegram-bot`, ReportLab, matplotlib, dan dependensi transitifnya — `requirements.txt` yang sama digunakan baik oleh dashboard maupun bot Telegram, sehingga satu instalasi mencakup keduanya.

### 4.4 Siapkan file environment

ARGUS membaca konfigurasi dari file `.env` di root proyek (dimuat melalui `python-dotenv`). Buat file tersebut sekarang; referensi variabel lengkap ada di [§7](#7-konfigurasi-environment).

```bash
touch .env        # Linux/macOS
type nul > .env    # Windows Command Prompt
```

Jangan commit file ini — `.gitignore` sudah mengecualikan `.env` secara default.

---

## 5. Instalasi PostgreSQL

### Linux (Ubuntu/Debian)

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql
```

### Linux (Fedora/kompatibel RHEL)

```bash
sudo dnf install -y postgresql-server postgresql-contrib
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql
```

### Windows

1. Unduh installer dari [halaman unduhan resmi PostgreSQL](https://www.postgresql.org/download/windows/).
2. Jalankan installer, pertahankan port default (`5432`) kecuali ada konflik.
3. Setel password untuk superuser `postgres` saat diminta — catat password tersebut; Anda akan membutuhkannya di bawah.
4. Pastikan "Command Line Tools" dipilih dalam daftar komponen sehingga `psql` tersedia di `PATH` Anda.

### Pembuatan basis data dan user (semua platform, via `psql`)

Hubungkan sebagai superuser PostgreSQL:

```bash
psql -U postgres
```

Kemudian jalankan:

```sql
CREATE USER argus_user WITH PASSWORD 'change-this-password';
CREATE DATABASE argus_db OWNER argus_user ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE argus_db TO argus_user;
\q
```

**Encoding basis data.** Gunakan `UTF8` seperti ditunjukkan — deskripsi CVE dan catatan aset dapat berisi berbagai karakter Unicode, dan encoding basis data non-UTF8 akan menyebabkan kegagalan penyisipan data (insert).

**Rekomendasi zona waktu.** ARGUS menyimpan timestamp sebagai `TIMESTAMPTZ` di seluruh skemanya, sehingga zona waktu server yang dikonfigurasi tidak memengaruhi kebenaran data, tetapi menyetel zona waktu server PostgreSQL ke `UTC` direkomendasikan untuk korelasi log yang konsisten, karena cron job scheduler (lihat [§12](#12-konfigurasi-scheduler)) berjalan pada zona waktu lokal proses scheduler:

```sql
ALTER SYSTEM SET timezone = 'UTC';
```
Restart PostgreSQL setelah perubahan ini agar berlaku.

### Verifikasi konektivitas

```bash
psql -U argus_user -d argus_db -h localhost -c "SELECT version();"
```

Anda seharusnya melihat string versi PostgreSQL tercetak. Jika ini gagal, lihat [§18 Pemecahan Masalah](#18-pemecahan-masalah).

### Kesalahan umum

| Kesalahan | Konsekuensi | Perbaikan |
|---|---|---|
| Membuat basis data dengan encoding default `SQL_ASCII` | Kegagalan insert pada deskripsi CVE non-ASCII | Buat ulang basis data dengan `ENCODING 'UTF8'` |
| Membiarkan `pg_hba.conf` PostgreSQL pada autentikasi `peer` untuk koneksi TCP | `psql: FATAL: Peer authentication failed` saat menghubungkan dengan `-h localhost` | Setel baris yang relevan ke `md5` atau `scram-sha-256` dan reload PostgreSQL |
| Lupa memberikan hak akses (grant) setelah membuat basis data dengan owner yang berbeda | Error permission-denied saat ARGUS mencoba membuat tabel | Jalankan ulang pernyataan `GRANT ALL PRIVILEGES`, atau buat ulang basis data dengan `OWNER argus_user` seperti ditunjukkan di atas |

---

## 6. Inisialisasi Basis Data

Skema ARGUS bersifat **self-healing** (memperbaiki diri sendiri) — Anda umumnya tidak perlu menjalankan apa pun secara manual sebelum menjalankan aplikasi.

- Saat `app.py` dijalankan, ia memanggil rutinitas internal `_ensure_schema()` yang menerapkan setiap tabel/kolom/indeks yang dibutuhkan dengan pernyataan `CREATE TABLE IF NOT EXISTS` dan `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Ini aman dijalankan setiap kali aplikasi dimulai, termasuk terhadap basis data yang sudah mutakhir.
- `bot/main.py` (bot Telegram) melakukan migrasi setara pada jalur startup-nya sendiri.
- Sebuah skrip migrasi mandiri, `bot/migrate.py`, menerapkan kumpulan migrasi idempoten yang sama dan dapat dijalankan secara manual — berguna untuk menyiapkan basis data terlebih dahulu sebelum pertama kali menjalankan aplikasi, atau untuk pipeline CI/deployment yang ingin skema siap terlebih dahulu:

```bash
cd bot
python migrate.py
```

Setiap langkah migrasi mencetak `OK` atau `FAILED` beserta error yang mendasarinya, dan skrip melanjutkan ke langkah-langkah berikutnya bahkan jika satu langkah gagal, sehingga satu pernyataan yang bermasalah tidak menggagalkan keseluruhan proses.

- `bot/database/schema.sql` juga disertakan sebagai referensi skema dasar (`psql -U argus_user -d argus_db -f bot/database/schema.sql`), berguna untuk meninjau tata letak tabel lengkap secara offline, tetapi **tidak diperlukan** sebagai langkah instalasi mengingat perilaku self-healing di atas.

### Verifikasi

```bash
psql -U argus_user -d argus_db -c "\dt"
```

Setelah pertama kali menjalankan `app.py` atau `bot/migrate.py`, Anda seharusnya melihat setidaknya: `assets`, `cves`, `matches`, `alerts`, `reports`, `users`, `ai_conversations`, `ai_messages`, `cve_ai_analysis`, `risk_snapshots`, dan `ai_response_cache`.

### Pemeriksaan integritas

```sql
SELECT COUNT(*) FROM assets;
SELECT COUNT(*) FROM cves;
SELECT COUNT(*) FROM matches;
```

Pada instalasi baru, semuanya seharusnya mengembalikan `0` tanpa error — error di sini menandakan skema tidak diterapkan dengan benar; jalankan ulang `python migrate.py` dan periksa outputnya.

### Rollback dan pemulihan

ARGUS tidak menyertakan alat down-migration/rollback — migrasi bersifat aditif (`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`) dan tidak dirancang untuk dibalik secara otomatis. Jika sebuah migrasi perlu dibatalkan, pulihkan dari backup yang diambil sebelum migrasi tersebut berjalan (lihat [§17 Backup & Restore](#17-backup--restore)) alih-alih mencoba membalikkan pernyataan `ALTER TABLE` secara manual satu per satu.

---

## 7. Konfigurasi Environment

Seluruh konfigurasi dilakukan melalui variabel environment di file `.env` pada root proyek, dimuat secara otomatis oleh `app.py` maupun `bot/main.py`.

| Variabel | Wajib? | Default | Tujuan |
|---|---|---|---|
| `SECRET_KEY` | **Wajib** (dashboard) | Tidak ada — aplikasi memicu `RuntimeError` dan menolak untuk berjalan jika tidak diset | Kunci penandatanganan sesi Flask. Buat dengan `python -c "import secrets; print(secrets.token_hex(32))"` |
| `ADMIN_PASSWORD` | **Wajib** (dashboard) | Tidak ada — aplikasi menolak untuk berjalan jika tidak diset | Password untuk akun bawaan `admin` |
| `VIEWER_PASSWORD` | **Wajib** (dashboard) | Tidak ada — aplikasi menolak untuk berjalan jika tidak diset | Password untuk akun bawaan `viewer` (hanya-baca) |
| `DB_HOST` | Opsional | `localhost` | Host PostgreSQL |
| `DB_NAME` | Opsional | `argus_db` | Nama basis data PostgreSQL |
| `DB_USER` | Opsional | `postgres` | User PostgreSQL |
| `DB_PASSWORD` | **Wajib** | Tidak ada — koneksi akan gagal tanpanya | Password PostgreSQL |
| `DB_PORT` | Opsional | `5432` | Port PostgreSQL |
| `DB_POOL_MIN_CONN` | Opsional | `2` | Jumlah minimum koneksi yang dijaga tetap terbuka dalam connection pool internal ARGUS |
| `DB_POOL_MAX_CONN` | Opsional | `20` | Jumlah maksimum koneksi dalam pool |
| `NVD_API_KEY` | Opsional tetapi direkomendasikan | Tidak ada (tanpa autentikasi, rate limit rendah) | Menaikkan rate limit NVD API Anda secara substansial; lihat [§9](#9-konfigurasi-api-eksternal) |
| `TOKEN` | Wajib hanya jika menjalankan bot Telegram | Tidak ada — bot memicu `RuntimeError` dan menolak untuk berjalan jika tidak diset | Token Telegram Bot API, dari BotFather |
| `CHAT_ID` | Wajib hanya untuk pengiriman alert Telegram | Tidak ada — alert dilewati secara diam-diam jika tidak diset | ID chat/channel Telegram tujuan tempat alert pemindaian dikirim |
| `LLM_URL` | Opsional | Tidak ada — endpoint chat AI mengembalikan error "belum dikonfigurasi" yang jelas jika tidak diset | URL lengkap endpoint `/v1/chat/completions` yang kompatibel dengan OpenAI (misalnya server `llama.cpp` lokal) |
| `RUN_SCHEDULER` | Opsional | `true` | Setel ke `false` pada satu proses jika Anda menjalankan `app.py` dan `bot/main.py` di bawah supervisor yang sama dan ingin menghindari penjadwalan ganda job latar belakang (lihat [§12](#12-konfigurasi-scheduler)) |
| `SESSION_COOKIE_SECURE` | Opsional | `true` | Setel ke `false` **hanya** untuk pengujian HTTP lokal/LAN; biarkan `true` pada deployment apa pun yang dilayani melalui HTTPS |

**Pertimbangan keamanan:**

- `.env` berisi rahasia (secret) dalam bentuk plaintext (password basis data, password admin/viewer, token bot). File ini sudah dikecualikan dari version control melalui `.gitignore`; pastikan proses deployment Anda (backup, log CI, image container) juga tidak secara tidak sengaja menyertakannya.
- Secara sengaja **tidak ada default yang tidak aman** untuk `SECRET_KEY`, `ADMIN_PASSWORD`, atau `VIEWER_PASSWORD` — aplikasi tidak akan berjalan tanpanya, secara by design.
- `DB_PASSWORD` tidak memiliki default hardcoded; membiarkannya tidak diset akan menghasilkan peringatan startup dan kegagalan koneksi, bukan koneksi tidak aman secara diam-diam.

**Contoh `.env`:**

```ini
# Inti
SECRET_KEY=replace-with-a-long-random-value
ADMIN_PASSWORD=replace-with-a-strong-password
VIEWER_PASSWORD=replace-with-a-different-strong-password

# Basis Data
DB_HOST=localhost
DB_NAME=argus_db
DB_USER=argus_user
DB_PASSWORD=replace-with-your-db-password
DB_PORT=5432

# NVD
NVD_API_KEY=replace-with-your-nvd-api-key

# Telegram (opsional)
TOKEN=replace-with-your-telegram-bot-token
CHAT_ID=replace-with-your-telegram-chat-id

# AI (opsional)
LLM_URL=http://127.0.0.1:8080/v1/chat/completions

# Deployment
SESSION_COOKIE_SECURE=true
RUN_SCHEDULER=true
```

> **Variabel yang tidak digunakan oleh basis kode ini.** Jika Anda pernah melihat proyek manajemen kerentanan lain menggunakan variabel seperti `DATABASE_URL`, `POSTGRES_*`, `OPENCVE_URL`, `TELEGRAM_TOKEN`, `OLLAMA_HOST`, `MODEL_NAME`, `SESSION_TIMEOUT`, `LOG_LEVEL`, atau `REPORT_DIRECTORY`, perhatikan bahwa **ARGUS tidak membaca satu pun dari variabel-variabel ini**. Gunakan nama variabel persis seperti pada tabel di atas — `DB_*` (bukan `POSTGRES_*`), `TOKEN` (bukan `TELEGRAM_TOKEN`), dan `LLM_URL` (bukan `OLLAMA_HOST`/`MODEL_NAME`). Masa berlaku sesi (8 jam) dan tingkat verbositas log saat ini bersifat tetap (fixed) dalam kode alih-alih dapat dikonfigurasi lewat environment, dan direktori output laporan bersifat tetap relatif terhadap aplikasi (`bot/dashboard/generated_reports/`), bukan dapat dikonfigurasi lewat environment.

---

## 8. Instalasi AI

AI Security Copilot (chat dan analisis CVE otomatis) bersifat **opsional** — ARGUS berjalan sepenuhnya normal tanpa variabel ini diset; endpoint `/api/chat` cukup mengembalikan error "belum dikonfigurasi" yang eksplisit, dan job analisis CVE latar belakang tidak memiliki apa pun untuk dikerjakan.

Klien AI milik ARGUS (`bot/Ai/llm.py`) menggunakan skema `/v1/chat/completions` yang kompatibel dengan OpenAI melalui HTTP biasa dan tidak menyematkan SDK vendor tertentu. Klien ini telah dievaluasi terhadap server `llama.cpp` lokal. Berikut adalah jalur instalasi untuk dua opsi lokal yang umum; salah satu (atau server lain mana pun yang mengimplementasikan bentuk API yang sama) berfungsi sebagai pengganti langsung target `LLM_URL`.

### Opsi A: server llama.cpp (konfigurasi yang telah dievaluasi)

**Linux:**
```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
cmake -B build
cmake --build build --config Release -j
```

Unduh model berformat GGUF (lihat rekomendasi model di bawah), lalu jalankan server:

```bash
./build/bin/llama-server -m /path/to/model.gguf --host 0.0.0.0 --port 8080
```

**Windows:** unduh rilis prebuilt dari halaman rilis GitHub llama.cpp, atau build dengan tooling CMake Visual Studio mengikuti langkah `cmake -B build` / `cmake --build build` yang sama dalam Developer Command Prompt.

### Opsi B: Ollama (endpoint kompatibel OpenAI)

Ollama tidak diintegrasikan melalui jalur kode khusus Ollama mana pun di ARGUS, tetapi permukaan API-nya yang kompatibel dengan OpenAI sesuai dengan kontrak `LLM_URL` yang sama.

**Linux:**
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
ollama serve
```

**Windows:** unduh installer dari [ollama.com/download](https://ollama.com/download), lalu dari terminal:
```powershell
ollama pull llama3.1:8b
```
Endpoint kompatibel OpenAI milik Ollama diekspos di `http://localhost:11434/v1/chat/completions` secara default — arahkan `LLM_URL` ke alamat tersebut ditambah nama model Anda di tempat server membutuhkannya.

### Rekomendasi model

| Kasus penggunaan | Kelas model yang disarankan | Perkiraan memori (kuantisasi Q4) |
|---|---|---|
| Ringan / hanya CPU | Model instruction-tuned 7B–8B parameter | ~5–6 GB RAM |
| Seimbang | Model instruction-tuned 13B–14B parameter | ~9–10 GB RAM |
| Kualitas lebih tinggi, tersedia GPU | Model 30B+ parameter | Membutuhkan VRAM yang cukup; konsultasikan kartu model pilihan Anda |

**Kuantisasi.** Model GGUF terkuantisasi 4-bit (Q4_K_M atau serupa) adalah default yang wajar untuk inferensi CPU — mereka menukar sedikit akurasi dengan pengurangan besar dalam jejak memori dan latensi. Gunakan presisi lebih tinggi (Q5/Q6/Q8 atau tanpa kuantisasi) hanya jika Anda memiliki keleluasaan RAM/VRAM dan menginginkan kualitas jawaban maksimum.

**CPU vs. GPU.** Inferensi CPU berfungsi tetapi terasa jauh lebih lambat per respons, yang lebih berpengaruh pada endpoint chat interaktif dibandingkan job analisis batch latar belakang (yang sudah mengatur tempo sendiri dengan jeda antar permintaan — lihat [§12](#12-konfigurasi-scheduler)). GPU dengan VRAM yang cukup untuk menampung model secara signifikan meningkatkan responsivitas chat.

### Memverifikasi server model

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Say OK if you can read this."}]}'
```

Respons yang berhasil berisi field `choices[0].message.content`. Jika ini gagal, fitur AI ARGUS akan gagal dengan cara yang sama — verifikasi di lapisan ini terlebih dahulu sebelum memecahkan masalah di dalam ARGUS.

### Bagaimana ARGUS terhubung ke model

Setel `LLM_URL` di `.env` ke URL completions lengkap (lihat contoh di [§7](#7-konfigurasi-environment)). ARGUS mengirim system prompt beserta pesan pengguna (dan, untuk chat, riwayat percakapan terkini) sebagai array `messages` standar dengan `temperature: 0.3` dan batas atas `max_tokens`, lalu membaca kembali `choices[0].message.content`. Tidak ada konfigurasi lebih lanjut di sisi ARGUS (pemilihan nama model, kunci API) yang dibutuhkan kecuali server spesifik Anda mensyaratkannya sebagai bagian dari permintaan — dalam hal ini hal tersebut berada di luar permukaan konfigurasi ARGUS saat ini dan perlu ditangani pada lapisan server/proxy di depan `LLM_URL`.

---

## 9. Konfigurasi API Eksternal

| Layanan | Autentikasi | Catatan |
|---|---|---|
| **NVD API** | `NVD_API_KEY` opsional | Minta kunci gratis di [halaman permintaan kunci API NVD](https://nvd.nist.gov/developers/request-an-api-key). Permintaan tanpa autentikasi dibatasi rate-nya jauh lebih agresif dibanding yang terautentikasi; kunci sangat direkomendasikan untuk inventaris apa pun yang melebihi segelintir aset. Klien ini melakukan fallback secara otomatis dari CVSS v3.1 → v3.0 → v2 tergantung apa yang dipublikasikan oleh catatan CVE tertentu. |
| **Feed CISA KEV** | Tidak diperlukan | Feed JSON publik, diambil dan di-cache dalam memori selama 24 jam dengan retry/backoff pada kegagalan sementara. Tidak perlu konfigurasi. |
| **FIRST EPSS API** | Tidak diperlukan | API publik, ditanyakan dalam satu permintaan batch per pemindaian aset untuk meminimalkan volume panggilan. Tidak perlu konfigurasi. |
| **OpenCVE** | T/A | Direferensikan dalam dokumentasi ARGUS yang lebih luas sebagai proyek sumber data terkait, tetapi **tidak ada klien OpenCVE atau konfigurasi `OPENCVE_URL` dalam basis kode saat ini.** Jangan menyetel variabel `OPENCVE_URL` dengan berharap ia akan dibaca. |
| **Feed threat intelligence masa depan** | T/A | Direncanakan — lihat `README.md` §17 Peta Jalan. Belum ada permukaan konfigurasi untuk ini. |

### Rate limit dan timeout

- NVD: permintaan tanpa autentikasi dibatasi pada sejumlah kecil permintaan per jendela waktu 30 detik; kunci API menaikkan batas ini secara substansial. ARGUS saat ini tidak mengimplementasikan rate limiting tambahan di sisi klien sendiri di luar apa yang disediakan oleh pengaturan tempo permintaan klien NVD — jika Anda mengelola inventaris yang sangat besar, pemindaian mungkin memakan waktu proporsional lebih lama alih-alih gagal total.
- KEV: cache dalam memori selama 24 jam berarti ARGUS melakukan paling banyak satu permintaan feed KEV per hari dalam operasi normal, terlepas dari jumlah aset.
- Endpoint LLM: permintaan menggunakan timeout 120 detik di `bot/Ai/llm.py`; model lokal yang membutuhkan waktu lebih lama dari ini untuk merespons akan muncul sebagai error timeout ke pemanggil.

### Pengujian konektivitas

```bash
# NVD
curl -s "https://services.nvd.nist.gov/rest/json/cves/2.0?resultsPerPage=1"

# CISA KEV
curl -s "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json" | head -c 200
```

Keduanya seharusnya mengembalikan JSON. Jika salah satu gagal dari lingkungan deployment Anda, periksa aturan firewall/proxy keluar sebelum memecahkan masalah di dalam ARGUS.

---

## 10. Konfigurasi Bot Telegram

Bot Telegram bersifat opsional — dashboard berfungsi sepenuhnya tanpanya.

### 10.1 Membuat bot dan mendapatkan token

1. Di Telegram, kirim pesan ke **@BotFather**.
2. Kirim `/newbot` dan ikuti petunjuknya (pilih nama tampilan dan username unik yang diakhiri `bot`).
3. BotFather mengembalikan token dalam bentuk `123456789:ABCdefGhIJKlmNoPQRsTUVwxyz`. Ini adalah `TOKEN` Anda.

### 10.2 Mendapatkan chat ID untuk alert

1. Kirim pesan apa pun ke bot baru Anda (atau tambahkan ke grup/channel).
2. Kunjungi `https://api.telegram.org/bot<TOKEN>/getUpdates` di browser atau dengan `curl` setelah mengirim pesan.
3. Temukan `"chat":{"id": ...}` pada respons — nilai numerik tersebut (mungkin negatif, untuk grup) adalah `CHAT_ID` Anda.

### 10.3 Izin bot

Untuk chat pribadi satu-lawan-satu, tidak ada izin khusus yang dibutuhkan. Untuk grup atau channel, pastikan bot memiliki izin mengirim pesan (dan, jika Anda membatasi siapa yang dapat memposting, bot ditambahkan sebagai admin atau secara eksplisit diizinkan memposting).

### 10.4 Konfigurasi environment

Setel `TOKEN` dan `CHAT_ID` di `.env` seperti ditunjukkan di [§7](#7-konfigurasi-environment). `TOKEN` wajib agar proses bot dapat berjalan sama sekali; `CHAT_ID` wajib hanya untuk pengiriman alert — perintah interaktif bot berfungsi tanpanya, tetapi alert pemindaian akan dilewati secara diam-diam jika tidak diset.

### 10.5 Menjalankan bot

```bash
cd bot
python main.py
```

### 10.6 Menguji perintah

Di chat Telegram Anda dengan bot, kirim `/start` — Anda seharusnya menerima "Argus Online 🟢". Kemudian coba `/help` untuk daftar perintah lengkap, dan `/status` untuk memastikan bot dapat menjangkau baik PostgreSQL maupun NVD API.

### 10.7 Pemecahan masalah

| Gejala | Penyebab | Perbaikan |
|---|---|---|
| Proses bot langsung keluar dengan `RuntimeError: TOKEN environment variable is not set` | `.env` tidak memiliki `TOKEN`, atau bot tidak dijalankan dari direktori tempat `.env` dapat ditemukan | Setel `TOKEN`; jalankan `python main.py` dari direktori `bot/` |
| Bot sama sekali tidak merespons | Token tidak valid, atau bot diblokir/belum dimulai oleh pengguna | Verifikasi ulang token dengan BotFather; pastikan Anda sudah mengirim `/start` ke bot terlebih dahulu |
| Alert tidak pernah masuk | `CHAT_ID` tidak diset atau salah | Turunkan ulang `CHAT_ID` melalui `getUpdates` seperti ditunjukkan di atas |
| `/status` melaporkan kegagalan basis data | PostgreSQL tidak terjangkau atau kredensial salah | Verifikasi ulang variabel `DB_*` dan pastikan PostgreSQL berjalan (lihat [§18](#18-pemecahan-masalah)) |

---

## 11. Konfigurasi Dashboard

Dashboard adalah aplikasi Flask standar (`app.py`) dengan perilaku berikut yang sudah tertanam:

- **Host/port** — Saat dijalankan langsung (`python app.py`), dashboard terikat (bind) ke `0.0.0.0:5000`. Tidak ada variabel environment untuk mengubah ini pada jalur run-langsung; sunting pemanggilan `app.run(...)` di bagian bawah `app.py`, atau ikat host/port yang berbeda pada lapisan server WSGI di produksi (lihat [§23](#23-deployment-produksi)).
- **Mode debug** — Di-hardcode ke `debug=False` pada jalur run-langsung. Jangan mengaktifkan mode debug Flask pada deployment apa pun yang dapat dijangkau oleh siapa pun selain Anda — mode ini mengekspos debugger interaktif yang mampu melakukan eksekusi kode arbitrer.
- **Mode produksi** — Untuk apa pun di luar pengujian lokal, jalankan di balik Gunicorn alih-alih `python app.py`; lihat [§23](#23-deployment-produksi) untuk perintah yang tepat dan batasan single-worker.
- **Secret key** — `SECRET_KEY` dari `.env`, wajib saat startup (lihat [§7](#7-konfigurasi-environment)).
- **Konfigurasi sesi** — Cookie `HttpOnly`, `SameSite=Lax`, secure-by-default (`SESSION_COOKIE_SECURE`), dan masa berlaku sesi tetap 8 jam.
- **File statis** — Dilayani dari `bot/dashboard/static/` oleh penanganan file statis default Flask; di produksi, pertimbangkan untuk melayani file-file ini langsung dari reverse proxy Anda demi performa yang lebih baik (lihat [§23](#23-deployment-produksi)).
- **Laporan yang dihasilkan** — Ditulis ke `bot/dashboard/generated_reports/`, dibuat secara otomatis saat startup jika belum ada. Path ini saat ini bersifat tetap, bukan dapat dikonfigurasi lewat environment.

---

## 12. Konfigurasi Scheduler

ARGUS menggunakan APScheduler untuk menjalankan job latar belakang berikut, semuanya didefinisikan di `bot/jobs/daily_scan.py`:

| Job | Jadwal | Tujuan |
|---|---|---|
| Pemindaian harian | Setiap hari pukul 06:00 | Memindai ulang semua aset terhadap NVD/KEV/EPSS |
| Snapshot risiko | Setiap hari pukul 06:30 | Mencatat agregat risiko pada satu titik waktu untuk grafik tren |
| Laporan mingguan | Setiap Senin pukul 07:00 | Menghasilkan laporan PDF mingguan |
| Laporan bulanan | Tanggal 1 setiap bulan pukul 07:00 | Menghasilkan laporan PDF bulanan |
| Batch analisis AI | Setiap 5 menit | Memproses hingga 5 CVE yang tertunda (pending) melalui pipeline analisis AI |
| Watchdog analisis AI | Setiap 5 menit | Memulihkan baris analisis yang macet dalam status `processing` setelah terjadi crash |
| Pembersihan cache chat | Setiap 30 menit | Menghapus entri cache respons chat AI yang sudah kedaluwarsa |

**Zona waktu.** Semua jadwal bergaya cron di atas berjalan pada zona waktu host/proses yang menjalankan scheduler (default APScheduler adalah zona waktu sistem lokal kecuali environment proses menentukan lain). Setel zona waktu sistem server Anda secara sengaja — pemindaian harian pukul `06:00` berarti 06:00 **lokal pada mesin tersebut**, bukan UTC, kecuali Anda telah secara eksplisit mengonfigurasi OS ke UTC.

**Siapa yang memulai scheduler.** Baik `app.py` maupun `bot/main.py` mampu memulai scheduler pada startup-nya masing-masing. Jika Anda menjalankan kedua proses secara bersamaan (dashboard + bot) di bawah supervisor yang sama, setel `RUN_SCHEDULER=false` pada **salah satu** dari keduanya — jika tidak, setiap job berjalan dua kali, menggandakan frekuensi pemindaian, duplikasi pembuatan laporan, dan duplikasi batch analisis AI. `RUN_SCHEDULER` defaultnya `true` (aktif) jika tidak diset.

**Verifikasi job.** Log aplikasi (lihat [§19](#19-logging)) mencatat dimulainya scheduler dan setiap pemanggilan job. Untuk memverifikasi job telah terdaftar tanpa menunggu waktu terjadwal, periksa baris log yang dicetak saat startup yang mengonfirmasi scheduler telah dimulai, dan periksa tabel `risk_snapshots` yang bertambah setiap hari sebagai konfirmasi eksternal bahwa job memang benar-benar berjalan:

```sql
SELECT * FROM risk_snapshots ORDER BY id DESC LIMIT 5;
```

**Pemecahan masalah.** Jika job terjadwal tampaknya tidak pernah berjalan: pastikan `RUN_SCHEDULER` tidak diset ke `false` pada setiap proses, pastikan proses tetap berjalan (proses yang crash/restart tidak pernah mencapai penjadwalan steady-state), dan periksa `SchedulerAlreadyRunningError` pada log, yang menandakan modul di-import ulang dan `scheduler.start()` dipanggil dua kali dalam proses yang sama (ini sudah dijaga/di-guard, tetapi patut disingkirkan kemungkinannya jika menggunakan konfigurasi WSGI/reload proses yang tidak biasa).

---

## 13. Menjalankan ARGUS

### Urutan startup

```
PostgreSQL
    ↓
Dashboard (app.py) dan/atau Bot Telegram (bot/main.py)
    ↓
Scheduler (dimulai otomatis oleh yang mana pun dari keduanya yang dimulai lebih dulu, sesuai RUN_SCHEDULER)
    ↓
AI (hanya jika LLM_URL dikonfigurasi — digunakan sesuai permintaan oleh chat dan oleh job analisis AI milik scheduler)
    ↓
Scanner (dipanggil sesuai permintaan melalui dashboard/bot, dan otomatis oleh job pemindaian harian)
```

**Mengapa urutan penting.** PostgreSQL harus dapat dijangkau sebelum dashboard maupun bot dimulai, karena keduanya memanggil logika migrasi skema dan membaca/menulis data segera saat startup — memulai salah satunya terhadap basis data yang tidak terjangkau menghasilkan error koneksi segera (lihat [§18](#18-pemecahan-masalah)). Scheduler, lapisan AI, dan scanner bukan proses terpisah yang Anda mulai sendiri — mereka dipanggil oleh proses dashboard/bot, sehingga tidak ada langkah terpisah "mulai scheduler" atau "mulai scanner" di luar menjalankan `app.py` dan/atau `bot/main.py` yang dikonfigurasi dengan benar.

### Memulai dashboard

```bash
source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
python app.py
```

Kunjungi `http://localhost:5000` (atau host/port yang Anda konfigurasi) di browser.

### Memulai bot Telegram (opsional, proses terpisah)

```bash
source venv/bin/activate
cd bot
python main.py
```

Anda dapat menjalankan dashboard dan bot pada mesin yang sama atau mesin yang berbeda, selama keduanya dapat menjangkau basis data PostgreSQL yang sama dan Anda telah menyetel `RUN_SCHEDULER` dengan benar sesuai [§12](#12-konfigurasi-scheduler) jika menjalankan keduanya.

---

## 14. Konfigurasi Pertama Kali

1. **Masuk (login) sebagai administrator bawaan.** Buka `/login` dan masuk dengan username `admin` dan password yang Anda setel sebagai `ADMIN_PASSWORD`.
2. **(Opsional) Buat pengguna tambahan.** Gunakan `/register` untuk membuat akun swalayan (self-service); akun baru default ke peran `viewer`. Untuk memberikan peran `admin`, perbarui peran tersebut langsung di basis data: `UPDATE users SET role = 'admin' WHERE username = 'namaanda';`
3. **Tambahkan aset pertama Anda.** Dari dashboard, buka `/add_asset` dan isi vendor, produk, versi, dan tipe — atau via Telegram, kirim `/add Vendor Produk Versi [Tipe]`.
4. **Jalankan pemindaian pertama Anda.** Dari halaman detail aset, picu sebuah pemindaian — atau via Telegram, `/scan <asset_id>`.
5. **Tinjau temuan.** Periksa `/findings` di dashboard, atau `/findings <asset_id>` di Telegram, untuk memastikan CVE yang cocok muncul beserta indikator severity dan KEV.
6. **Hasilkan laporan pertama Anda.** Dari `/reports`, picu laporan sesuai permintaan — atau via Telegram, `/report`.
7. **Uji chat AI** (jika `LLM_URL` dikonfigurasi). Buka antarmuka chat dashboard dan ajukan pertanyaan seperti "apa yang harus saya perbaiki terlebih dahulu?" — pastikan Anda mendapatkan jawaban yang berbasis data, merujuk pada temuan aktual Anda, bukan respons generik.
8. **Uji alert** (jika bot dikonfigurasi). Jalankan pemindaian pada aset dengan setidaknya satu temuan dan pastikan pesan alert gabungan tiba di chat `CHAT_ID` yang Anda konfigurasi.
9. **Verifikasi instalasi** menggunakan daftar periksa di [§15](#15-daftar-periksa-verifikasi).

---

## 15. Daftar Periksa Verifikasi

- [ ] Dashboard dimuat di `/` dan `/login` tanpa error
- [ ] Login berhasil dengan akun bawaan `admin` dan `viewer`
- [ ] Konektivitas basis data terkonfirmasi — `psql` berhasil terhubung, dan `\dt` menampilkan daftar tabel ARGUS
- [ ] Sebuah aset dapat ditambahkan dan muncul di `/assets`
- [ ] Sebuah pemindaian selesai dan menghasilkan setidaknya satu baris di `matches` untuk aset dengan perangkat lunak yang diketahui rentan
- [ ] `/findings` menampilkan temuan hasil pemindaian dengan severity dan (jika berlaku) tanda KEV
- [ ] Grafik dirender di `/charts` tanpa error 500
- [ ] Risk engine — skor risiko suatu temuan bernilai bukan nol dan mencerminkan CVSS/kritikalitas/KEV/EPSS sesuai ekspektasi
- [ ] Scheduler aktif — `risk_snapshots` mendapatkan baris baru setelah waktu terjadwal berlalu, atau segera saat startup bot (`bot/main.py` mencatat snapshot langsung saat diluncurkan)
- [ ] Laporan — `/generate_report/<type>` menghasilkan PDF yang dapat diunduh di `/download/<report_id>`
- [ ] Bot Telegram merespons `/start`, `/help`, dan `/status` (jika dikonfigurasi)
- [ ] Alert — pemindaian dengan temuan baru mengirimkan pesan Telegram ke `CHAT_ID` (jika dikonfigurasi)
- [ ] Chat AI merespons pertanyaan dengan konten yang berbasis data aktual Anda, bukan jawaban generik (jika `LLM_URL` dikonfigurasi)
- [ ] Memori percakapan — mengajukan pertanyaan lanjutan dalam percakapan yang sama mencerminkan konteks sebelumnya (jika AI dikonfigurasi)
- [ ] Analisis CVE AI — setelah menambahkan aset dengan CVE yang diketahui, baris `cve_ai_analysis` berpindah dari `pending` ke `done` dalam beberapa siklus scheduler (jika AI dikonfigurasi)

---

## 16. Memperbarui ARGUS

```bash
# 1. Backup terlebih dahulu — lihat §17
# 2. Hentikan proses dashboard dan bot
# 3. Tarik (pull) kode terbaru
git pull

# 4. Aktifkan virtual environment Anda dan perbarui dependensi
source venv/bin/activate
pip install -r requirements.txt --upgrade

# 5. Terapkan migrasi skema baru apa pun
cd bot
python migrate.py
cd ..

# 6. Tinjau .env terhadap referensi variabel terkini di §7 untuk variabel baru apa pun
# 7. Mulai ulang (restart) dashboard dan, jika digunakan, bot
python app.py
```

**Mengapa urutan ini:** dependensi harus mutakhir sebelum kode aplikasi yang bergantung padanya berjalan; migrasi skema harus diterapkan sebelum aplikasi mulai melayani permintaan terhadap basis data yang diasumsikannya sudah mutakhir (baik `app.py` maupun `bot/main.py` juga menerapkan migrasi sendiri saat startup, sehingga langkah 5 adalah langkah pengaman tambahan alih-alih benar-benar wajib, tetapi menjalankannya secara eksplisit memunculkan error migrasi sebelum trafik dari pengguna mengenai aplikasi).

**Pembaruan model AI.** Jika Anda memperbarui server LLM lokal Anda atau mengganti model, tidak ada migrasi di sisi ARGUS yang dibutuhkan — `LLM_URL` dan konfigurasi model itu sendiri independen dari skema basis data ARGUS. Jalankan ulang uji konektivitas di [§8](#8-instalasi-ai) setelah perubahan apa pun pada server model.

**Rollback.** Jika sebuah pembaruan memunculkan regresi: hentikan proses yang terpengaruh, pulihkan kode versi sebelum pembaruan (`git checkout <tag-atau-commit-sebelumnya>`), pasang ulang `requirements.txt` versi tersebut, dan — jika pembaruan tersebut menyertakan migrasi skema yang perlu Anda batalkan — pulihkan basis data dari backup yang diambil pada langkah 1, karena ARGUS tidak menyediakan down-migration otomatis (lihat [§6](#6-inisialisasi-basis-data)).

---

## 17. Backup & Restore

### Apa yang perlu di-backup

| Item | Lokasi | Metode backup |
|---|---|---|
| Basis data (seluruh data aplikasi — aset, temuan, CVE, metadata laporan, pengguna, percakapan AI, cache AI) | PostgreSQL (`argus_db`) | `pg_dump` |
| Laporan PDF yang dihasilkan | `bot/dashboard/generated_reports/` | Salinan tingkat file |
| Konfigurasi / variabel environment | `.env` | Salinan tingkat file, disimpan secara aman (berisi rahasia/secret) |
| Riwayat percakapan AI | Termasuk dalam basis data (`ai_conversations`, `ai_messages`) | Sudah tercakup oleh `pg_dump` di atas — tidak perlu langkah terpisah |
| Cache respons AI | Termasuk dalam basis data (`ai_response_cache`) | Sudah tercakup oleh `pg_dump` di atas; bernilai rendah untuk dipulihkan karena entrinya ber-TTL pendek, tetapi tersertakan secara default dalam dump lengkap |

### Backup basis data

```bash
pg_dump -U argus_user -h localhost -d argus_db -F c -f argus_backup.dump
```

Format khusus (custom) `-F c` mendukung restore selektif dan paralel; gunakan SQL biasa (`-F p`) jika Anda menginginkan file dump yang dapat dibaca/disunting manusia.

### Restore basis data

```bash
# Ke dalam basis data baru yang kosong:
createdb -U postgres -O argus_user argus_db_restored
pg_restore -U argus_user -h localhost -d argus_db_restored argus_backup.dump
```

Arahkan `DB_NAME` ke nama basis data hasil restore setelah Anda memverifikasinya, atau restore langsung menimpa nama basis data asli jika menggantinya sepenuhnya (dalam kasus ini, drop dan buat ulang basis data terlebih dahulu).

### Prosedur restore lengkap

1. Hentikan proses dashboard dan bot.
2. Pulihkan basis data seperti ditunjukkan di atas.
3. Pulihkan `bot/dashboard/generated_reports/` dari backup tingkat file Anda, jika riwayat laporan penting bagi Anda.
4. Pulihkan `.env` dari backup aman Anda (atau buat ulang — lihat [§7](#7-konfigurasi-environment)).
5. Mulai dashboard, pastikan login dan data sesuai ekspektasi, lalu mulai bot jika digunakan.
6. Jalankan [Daftar Periksa Verifikasi](#15-daftar-periksa-verifikasi).

### Rekomendasi pemulihan bencana (disaster recovery)

- Otomatisasi `pg_dump` pada jadwal (cron/Task Scheduler) yang terpisah dari scheduler internal ARGUS sendiri, dan simpan dump di luar host yang menjalankan PostgreSQL.
- Perlakukan `.env` sebagai rahasia yang membutuhkan perlindungan yang sama seperti entri password vault — backup file ini, tetapi jangan bersamaan dengan backup aplikasi yang tidak terenkripsi.
- Uji prosedur restore Anda secara berkala alih-alih mengasumsikan file backup valid; dry run `pg_restore` terhadap basis data scratch adalah asuransi berbiaya rendah.

---

## 18. Pemecahan Masalah

| Gejala | Kemungkinan Penyebab | Resolusi | Verifikasi |
|---|---|---|---|
| Aplikasi gagal dijalankan: `RuntimeError: SECRET_KEY is missing` | `.env` tidak ada, tidak dimuat, atau tidak memiliki `SECRET_KEY` | Setel `SECRET_KEY` di `.env`; pastikan Anda menjalankan `python app.py` dari direktori yang berisi `.env` | Restart; error seharusnya tidak berulang |
| Aplikasi gagal dijalankan: `ADMIN_PASSWORD and VIEWER_PASSWORD must be set` | Kredensial bawaan tidak diset | Setel keduanya di `.env` | Restart; halaman login dimuat |
| `psycopg2.OperationalError: could not connect to server` | PostgreSQL tidak berjalan, host/port salah, atau firewall memblokir | Pastikan PostgreSQL berjalan (`systemctl status postgresql` / panel Services di Windows); verifikasi `DB_HOST`/`DB_PORT` | `psql -U argus_user -d argus_db -h $DB_HOST -p $DB_PORT` berhasil |
| `psycopg2.OperationalError: FATAL: password authentication failed` | `DB_PASSWORD` salah, atau ketidakcocokan metode autentikasi `pg_hba.conf` | Reset password dengan `ALTER USER argus_user WITH PASSWORD '...'`; periksa `pg_hba.conf` menggunakan `md5`/`scram-sha-256` untuk jenis koneksi yang digunakan | Uji `psql` yang sama seperti di atas |
| `/api/chat` mengembalikan "ARGUS AI is not configured" | `LLM_URL` tidak diset | Setel `LLM_URL` ke endpoint yang kompatibel dengan OpenAI yang sedang berjalan | Uji `curl` dari [§8](#8-instalasi-ai) berhasil, lalu coba lagi endpoint chat |
| Permintaan chat/analisis AI mengalami timeout | Server LLM kelebihan beban, model terlalu besar untuk perangkat keras yang tersedia, atau server sebenarnya tidak berjalan | Pastikan server merespons uji `curl` di [§8](#8-instalasi-ai) dalam waktu yang wajar; pertimbangkan model yang lebih kecil/lebih terkuantisasi atau akselerasi GPU | Ulangi uji `curl`; periksa log server LLM itu sendiri |
| "Model not found" dari server LLM | Model belum ditarik/diunduh, atau nama model yang dikonfigurasi di server salah | Jalankan ulang langkah pull/unduh model untuk server pilihan Anda (Ollama: `ollama pull <model>`; llama.cpp: pastikan path `-m` benar) | Perintah daftar model di sisi server berhasil |
| Pemindaian gagal atau tidak mengembalikan hasil untuk perangkat lunak yang diketahui rentan | NVD API tidak terjangkau, kena rate limit, atau string vendor/produk/versi tidak cocok dengan penamaan CPE milik NVD | Verifikasi konektivitas NVD sesuai [§9](#9-konfigurasi-api-eksternal); tambahkan `NVD_API_KEY` jika terkena rate limit; periksa ejaan vendor/produk terhadap UI pencarian NVD sendiri | `curl` langsung ke NVD API berhasil; pemindaian yang dijalankan ulang secara manual menghasilkan kecocokan yang diharapkan |
| Bot Telegram tidak merespons | `TOKEN` tidak valid/tidak ada, bot belum dimulai dengan `/start`, egress jaringan diblokir ke API Telegram | Verifikasi ulang `TOKEN`; kirim `/start` terlebih dahulu; pastikan HTTPS keluar ke `api.telegram.org` diizinkan | Bot membalas `/start` |
| Job scheduler tidak pernah berjalan | `RUN_SCHEDULER=false` pada setiap proses, atau proses terus crash/restart sebelum mencapai waktu terjadwal | Pastikan setidaknya satu proses memiliki `RUN_SCHEDULER` tidak diset atau `true`; periksa uptime proses/log untuk crash loop | Tabel `risk_snapshots` mendapatkan baris baru pada waktu yang diharapkan |
| Job scheduler berjalan dua kali | Baik `app.py` maupun `bot/main.py` berjalan dengan scheduler aktif pada keduanya | Setel `RUN_SCHEDULER=false` pada salah satu proses | Volume laporan/alert ganda berhenti setelah restart |
| `Permission denied` saat menulis ke `generated_reports/` atau `logs/` | Izin filesystem tidak mengizinkan user yang menjalankan proses untuk menulis | `chmod`/`chown` direktori dengan tepat di Linux, atau sesuaikan izin folder di tab Security Windows Explorer | Pembuatan laporan berhasil |
| `Address already in use` / konflik port pada `5000` | Proses lain sudah terikat ke port 5000 | Hentikan proses yang berkonflik, atau jalankan di balik Gunicorn pada port berbeda (lihat [§23](#23-deployment-produksi)) dan sesuaikan reverse proxy Anda | `python app.py` dimulai tanpa error bind |
| `pip install` gagal mengompilasi paket dari sumber kode | Tidak ada wheel prebuilt untuk kombinasi Python/OS/arsitektur Anda yang spesifik, build tools tidak ada | Pasang toolchain compiler (`build-essential` di Debian/Ubuntu, Xcode Command Line Tools di macOS, Visual Studio Build Tools di Windows) dan header dev client PostgreSQL (`libpq-dev` di Debian/Ubuntu) | `pip install -r requirements.txt` selesai |
| `bot/migrate.py` melaporkan `FAILED` untuk migrasi tertentu | Perubahan skema manual yang berkonflik, atau hak akses basis data tidak cukup | Baca error yang dicetak di bawah langkah yang gagal; berikan hak akses yang hilang atau selesaikan konflik objek secara manual, lalu jalankan ulang | Menjalankan ulang `python migrate.py` menunjukkan `OK` untuk langkah tersebut |
| Pembuatan laporan gagal/hang | `matplotlib`/`reportlab` kehilangan font atau dependensi sistem, atau jumlah temuan yang sangat besar menyebabkan rendering lambat | Periksa log aplikasi di sekitar waktu kegagalan untuk exception yang mendasarinya; untuk laporan yang sangat besar, pertimbangkan mempersempit cakupan tanggal/aset laporan jika filter semacam itu tersedia di versi Anda | Jalankan ulang pembuatan laporan; periksa `generated_reports/` untuk file output |
| Penggunaan memori tinggi / OOM pada server LLM | Model terlalu besar untuk RAM/VRAM yang tersedia | Beralih ke model yang lebih kecil atau kuantisasi yang lebih agresif (lihat [§8](#8-instalasi-ai)) | Server model dimulai dan merespons tanpa dimatikan paksa oleh OS |
| Windows: path dengan backslash menyebabkan error pada skrip yang disalin dari contoh Linux | Perbedaan sintaks shell, bukan bug ARGUS | Gunakan varian perintah khusus Windows yang ditunjukkan dalam panduan ini (PowerShell/Command Prompt), bukan sintaks `bash` Linux secara verbatim | Perintah selesai tanpa error parsing path |
| Linux: `Permission denied` saat menjalankan `python main.py` setelah clone | Kepemilikan file/skrip dari `git clone` yang dijalankan sebagai user berbeda, atau `venv` dibuat oleh root | Pastikan virtual environment dan direktori proyek dimiliki oleh user yang menjalankan ARGUS: `chown -R $USER:$USER argus/` | Perintah berjalan tanpa `Permission denied` |

---

## 19. Logging

**Tujuan log (destination).** ARGUS saat ini tidak menulis log ke file secara default. `bot/main.py` mengonfigurasi `logging.basicConfig(...)` dengan format berstempel waktu pada level `INFO`, yang — tanpa file handler eksplisit — mengirimkan output ke konsol (stderr) tempat proses dimulai. `app.py` menggunakan `logging.getLogger(__name__)` tingkat modul tanpa pemanggilan `basicConfig`-nya sendiri, sehingga level log dan handler efektifnya mengikuti perilaku logging default Python/Flask kecuali Anda mengonfigurasi logging secara eksplisit pada tingkat proses/supervisor.

**Direktori `logs/`** ada dalam repositori dan dikecualikan dari version control melalui `.gitignore`, tetapi tidak ada apa pun dalam basis kode saat ini yang menulis ke direktori tersebut secara otomatis — perlakukan direktori ini sebagai cadangan untuk konfigurasi logging Anda sendiri (misalnya, mengalihkan stdout ke sana) alih-alih sebagai sink log aktif secara out-of-the-box.

**Level log.** Proses bot defaultnya `INFO`. Saat ini tidak ada variabel environment `LOG_LEVEL` yang dibaca oleh basis kode — untuk mengubah verbositas, sunting pemanggilan `logging.basicConfig(level=...)` di `bot/main.py`, atau konfigurasikan logging secara eksternal (misalnya, `logging.conf` yang dimuat oleh supervisor proses Anda, atau menangkap stdout melalui logging milik Gunicorn/systemd sendiri).

**Mode debug.** Jalur run-langsung `app.py` di-hardcode ke `debug=False`. Jangan mengubah ini pada lingkungan bersama atau produksi apa pun.

**Melihat log di produksi.** Jika Anda mengikuti deployment systemd di [§23](#23-deployment-produksi), gunakan `journalctl -u argus -f` untuk mengikuti (tail) log. Jika Anda menangkap stdout ke file melalui supervisor proses Anda, gunakan `tail -f` pada file tersebut.

**Rotasi log.** Karena ARGUS tidak mengelola file log-nya sendiri, gunakan rotasi log milik supervisor proses atau OS Anda (`logrotate` di Linux untuk file stdout yang dialihkan, atau pengaturan retensi systemd/journald sendiri) alih-alih mengharapkan ARGUS merotasi apa pun sendiri.

**Informasi sensitif.** Log aplikasi dapat menyertakan detail error (misalnya, pernyataan SQL yang gagal atau isi error HTTP) yang mungkin mereferensikan identifier internal. Log-log tersebut seharusnya tidak menyertakan password mentah atau nilai `SECRET_KEY`/`TOKEN` berdasarkan titik pemanggilan logging yang ditinjau saat ini, tetapi perlakukan semua log aplikasi sebagai internal-only alih-alih aman untuk dibagikan secara sembarangan, karena konten log dapat berubah seiring evolusi basis kode.

---

## 20. Rekomendasi Keamanan

- **Lindungi `.env`.** Izin file harus membatasi akses baca hanya untuk akun user yang menjalankan ARGUS (`chmod 600 .env` di Linux). Jangan pernah meng-commit file ini; `.gitignore` sudah mengecualikannya.
- **Gunakan HTTPS pada deployment apa pun yang dapat dijangkau melalui jaringan yang tidak sepenuhnya Anda kendalikan.** Terminasi TLS pada reverse proxy (lihat [§23](#23-deployment-produksi)) dan pertahankan `SESSION_COOKIE_SECURE=true`.
- **Ubah kredensial default segera.** `ADMIN_PASSWORD` dan `VIEWER_PASSWORD` tidak memiliki default bawaan, tetapi pilih nilai yang kuat dan unik alih-alih sesuatu yang mudah ditebak — kedua akun ini adalah satu-satunya yang ada sebelum registrasi mandiri (self-registration) terjadi.
- **Hak akses basis data.** Gunakan `argus_user` khusus (seperti dibuat di [§5](#5-instalasi-postgresql)) alih-alih terhubung sebagai superuser PostgreSQL; berikan hanya hak akses yang dibutuhkannya pada `argus_db`.
- **Konfigurasi firewall.** Batasi akses masuk (inbound) ke port PostgreSQL (5432) hanya untuk host yang menjalankan ARGUS. Batasi akses masuk ke port dashboard hanya untuk reverse proxy Anda, bukan internet publik secara langsung.
- **Reverse proxy.** Tempatkan nginx atau Caddy di depan Gunicorn alih-alih mengekspos dev server Flask atau Gunicorn secara langsung ke jaringan yang tidak tepercaya (lihat [§23](#23-deployment-produksi)).
- **Least privilege (hak akses minimum).** Gunakan peran `viewer` untuk siapa pun yang tidak perlu mengubah aset atau temuan; cadangkan `admin` untuk mereka yang membutuhkannya.
- **Izin file.** Pastikan `generated_reports/` dan direktori data basis data tidak dapat dibaca oleh semua orang (world-readable), karena laporan dan basis data dapat berisi detail aset internal.
- **Manajemen rahasia (secrets management).** Untuk apa pun di luar deployment lab operator tunggal, pertimbangkan secrets manager (misalnya milik penyedia cloud Anda, atau HashiCorp Vault) untuk menyuntikkan nilai `.env` saat proses dimulai alih-alih menyimpannya dalam file plaintext dalam jangka panjang.
- **Pembaruan rutin.** Jaga PostgreSQL, Python, dan dependensi yang dikunci milik ARGUS tetap mutakhir — jalankan ulang `pip list --outdated` secara berkala di dalam virtual environment Anda dan tinjau changelog sebelum memperbarui, terutama untuk `Flask`, `Flask-Login`, dan `Flask-WTF`, yang relevan dengan keamanan.
- **Rekomendasi deployment produksi, singkatnya:** akun layanan non-root khusus, Gunicorn dengan tepat satu worker (lihat [§23](#23-deployment-produksi) untuk alasannya), reverse proxy dengan HTTPS, basis data yang difirewall, izin `.env` yang dikunci, dan backup rutin sesuai [§17](#17-backup--restore).

---

## 21. Rekomendasi Performa

- **Pengaturan PostgreSQL.** Untuk apa pun di luar instalasi lab kecil, tuning `shared_buffers` (kira-kira 25% dari RAM yang tersedia pada host basis data khusus), `effective_cache_size` (kira-kira 50–75% dari RAM yang tersedia), dan `work_mem` berdasarkan beban query konkuren. Gunakan dokumentasi tuning PostgreSQL sendiri sebagai acuan di sini — ARGUS tidak membutuhkan pengaturan non-standar.
- **Connection pooling.** ARGUS sudah mengimplementasikan connection pooling internal (`bot/database/db.py`, sebuah `ThreadedConnectionPool`) alih-alih membuka koneksi mentah per query — `DB_POOL_MIN_CONN`/`DB_POOL_MAX_CONN` mengontrol ukurannya. Naikkan `DB_POOL_MAX_CONN` jika Anda menjalankan banyak pengguna dashboard konkuren dan melihat kontensi koneksi, tetapi tetap di bawah pengaturan `max_connections` PostgreSQL sendiri (default 100) di seluruh proses ARGUS gabungan.
- **Pemilihan model AI.** Model yang lebih kecil/lebih terkuantisasi merespons lebih cepat dan biasanya sudah cukup untuk analisis terstruktur berbasis konteks yang dilakukan ARGUS (lihat [§10 Kemampuan AI di README.md](./README.md#10-kemampuan-ai)); cadangkan model yang lebih besar untuk kasus-kasus di mana Anda telah mengamati kesenjangan kualitas nyata, bukan sebagai default.
- **Pagination.** Dashboard sudah melakukan pagination pada tampilan daftar temuan dan aset — hindari menonaktifkan atau melewati ini jika Anda menyesuaikan template, karena merender tabel penuh tanpa pagination terhadap tabel `matches` yang besar akan lambat.
- **Job latar belakang.** Pemindaian, laporan, dan analisis AI sudah berjalan melalui APScheduler alih-alih memblokir penanganan permintaan — hindari memicu operasi besar sesuai permintaan (misalnya, pemindaian manual seluruh inventaris) selama jam puncak penggunaan dashboard jika jumlah aset Anda besar.
- **Pembuatan laporan.** Biaya pembuatan PDF berskala sesuai jumlah temuan yang disertakan; laporan mingguan/bulanan terjadwal mengamortisasi biaya ini di luar jam sibuk (07:00) alih-alih selama penggunaan tipikal.
- **Caching.** Cache respons chat AI menghindari panggilan LLM yang redundan untuk pertanyaan berulang terhadap data yang tidak berubah; biarkan job pembersihan cache (setiap 30 menit) tetap berjalan alih-alih menonaktifkannya, sehingga cache tidak tumbuh tanpa batas.
- **Perangkat keras.** Lihat [§1 Persyaratan Sistem](#1-persyaratan-sistem) untuk panduan ukuran berdasarkan skala deployment.

---

## 22. Instalasi Docker (Dukungan Masa Depan)

> **Status: Direncanakan.** ARGUS saat ini tidak menyertakan `Dockerfile` atau `docker-compose.yml` — direktori `docker/` dalam repositori adalah placeholder. Bagian di bawah ini menjelaskan model deployment terkontainerisasi masa depan yang dituju sehingga operator dapat merencanakannya; tidak ada satu pun perintah di bawah ini yang akan berfungsi sampai ini diimplementasikan.

**Arsitektur yang direncanakan.** Sebuah tumpukan Compose multi-container dengan:
- Sebuah layanan `postgres` menggunakan image resmi PostgreSQL, dengan volume bernama untuk persistensi data.
- Sebuah layanan `argus-dashboard` yang dibangun dari `Dockerfile` di root proyek, menjalankan `app.py` di balik Gunicorn.
- Sebuah layanan `argus-bot` opsional yang menjalankan `bot/main.py`, berbagi image yang sama tetapi entrypoint yang berbeda.
- Sebuah layanan `llm` opsional (misalnya, image server `llama.cpp`) untuk fungsionalitas AI yang sepenuhnya self-contained.

**Volume yang direncanakan.**
- `postgres_data` — direktori data PostgreSQL.
- `argus_reports` — dipetakan ke `bot/dashboard/generated_reports/`, sehingga PDF yang dihasilkan bertahan melewati pembuatan ulang container.
- `argus_env` atau direktif `env_file` Compose — untuk `.env`, alih-alih menyematkan rahasia ke dalam image.

**Jaringan yang direncanakan.** Sebuah jaringan Compose internal sehingga `argus-dashboard`/`argus-bot` menjangkau `postgres` dan `llm` berdasarkan nama layanan (misalnya, `DB_HOST=postgres`) tanpa mengekspos port basis data ke host sama sekali.

**Penanganan variabel environment yang direncanakan.** Variabel yang sama seperti didokumentasikan di [§7](#7-konfigurasi-environment) akan diteruskan melalui direktif `env_file:` atau `environment:` Compose — tidak ada variabel khusus Docker baru yang direncanakan di luar substitusi jaringan Compose standar (misalnya, `DB_HOST=postgres` alih-alih `localhost`).

**Sampai ini terwujud,** deploy ARGUS menggunakan pendekatan virtual environment dalam panduan ini, secara opsional di bawah supervisor proses seperti dijelaskan di [§23](#23-deployment-produksi). Ikuti bagian Peta Jalan pada `README.md` untuk status terkini.

---

## 23. Deployment Produksi

### Gunicorn

Pasang Gunicorn ke dalam virtual environment Anda (tidak ada di `requirements.txt`, karena ini adalah pilihan deployment, bukan dependensi aplikasi):

```bash
pip install gunicorn
```

Jalankan dengan **tepat satu worker**:

```bash
gunicorn -w 1 -b 127.0.0.1:5000 app:app
```

**Mengapa harus tepat satu worker.** `app.py` melakukan migrasi skema dan memulai scheduler latar belakang pada saat modul di-import. Model worker default Gunicorn meng-import modul aplikasi satu kali per proses worker — menjalankan banyak worker akan menjalankan migrasi skema dan memulai scheduler (pemindaian harian, laporan, analisis AI) satu kali per worker, menggandakan setiap job terjadwal. Tetap gunakan satu worker sampai basis kode direfaktor ke pola application-factory yang memisahkan efek samping saat import dari penanganan permintaan.

Jika Anda membutuhkan konkurensi penanganan permintaan lebih dari yang disediakan satu worker Gunicorn, skalakan melalui flag `--threads` milik Gunicorn (beberapa thread dalam satu worker) alih-alih menambah proses worker, atau jalankan bot (`bot/main.py`) sebagai proses yang sepenuhnya terpisah dengan `RUN_SCHEDULER=false` sehingga hanya worker tunggal dashboard yang memiliki penjadwalan.

### Reverse proxy (contoh nginx)

```nginx
server {
    listen 80;
    server_name argus.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name argus.example.com;

    ssl_certificate     /etc/letsencrypt/live/argus.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/argus.example.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

### Sertifikat SSL

Gunakan [Certbot](https://certbot.eff.org/) untuk sertifikat Let's Encrypt gratis yang diperbarui otomatis, atau proses penerbitan sertifikat organisasi Anda yang sudah ada untuk deployment internal.

### Layanan systemd (Linux)

```ini
# /etc/systemd/system/argus.service
[Unit]
Description=ARGUS Vulnerability Management Dashboard
After=network.target postgresql.service

[Service]
Type=simple
User=argus
WorkingDirectory=/opt/argus
Environment="PATH=/opt/argus/venv/bin"
ExecStart=/opt/argus/venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now argus
sudo systemctl status argus
```

Buat unit yang setara untuk `bot/main.py` jika menjalankan bot Telegram sebagai layanan, setel `RUN_SCHEDULER=false` pada blok `Environment=`-nya jika layanan dashboard sudah memiliki penjadwalan.

### Restart otomatis

Unit systemd di atas (`Restart=on-failure`) me-restart proses secara otomatis saat crash. Gabungkan dengan `RestartSec` untuk menghindari tight crash-restart loop yang menghabiskan sumber daya jika kegagalan yang mendasarinya bersifat persisten (misalnya, basis data tidak terjangkau).

### Rotasi log

Lihat [§19 Logging](#19-logging) — karena ARGUS mencatat log ke stdout secara default, journal systemd menangani retensi (`journalctl` dengan `SystemMaxUse=` di `journald.conf`), atau alihkan ke file dan kelola dengan `logrotate`.

### Monitoring dan health check

Tidak ada endpoint `/health` khusus yang terpisah dari rute-rute dashboard sendiri dalam basis kode saat ini; gunakan perintah `/status` milik bot Telegram (yang melakukan pemeriksaan basis data dan NVD API sungguhan) sebagai sinyal kesehatan fungsional jika bot sedang berjalan, atau pantau halaman `/login` dashboard untuk respons `200` sebagai pemeriksaan liveness dasar, dikombinasikan dengan memantau PostgreSQL dan server LLM Anda (jika digunakan) secara independen.

### Backup

Otomatisasi prosedur `pg_dump` di [§17](#17-backup--restore) pada jadwal yang independen dari ARGUS itu sendiri (cron/systemd timer), dan verifikasi backup secara berkala.

---

## 24. Uninstalasi

1. **Hentikan layanan.**
   ```bash
   sudo systemctl stop argus       # jika menggunakan systemd
   sudo systemctl disable argus
   ```
   Atau, jika dijalankan secara manual, hentikan proses `app.py`/`main.py` (Ctrl+C, atau kill proses jika berjalan di background).

2. **Hapus environment Python.**
   ```bash
   rm -rf venv/
   ```

3. **Hapus basis data** (tidak dapat dibatalkan — backup terlebih dahulu jika ada kemungkinan Anda akan membutuhkan data tersebut nanti):
   ```sql
   DROP DATABASE argus_db;
   DROP USER argus_user;
   ```

4. **Hapus laporan yang dihasilkan.**
   ```bash
   rm -rf bot/dashboard/generated_reports/*
   ```

5. **Bersihkan cache respons AI dan data percakapan.** Sudah tercakup dengan menghapus basis data pada langkah 3; tidak ada file cache terpisah yang ada di luar PostgreSQL.

6. **Hapus model AI (opsional)** — hanya relevan jika Anda memasang server LLM lokal khusus untuk ARGUS dan tidak membutuhkannya untuk hal lain:
   ```bash
   # llama.cpp — cukup hapus file .gguf yang diunduh
   rm /path/to/model.gguf

   # Ollama
   ollama rm <model-name>
   ```

7. **Pembersihan lengkap.**
   ```bash
   cd .. && rm -rf argus/
   ```
   Hapus file unit systemd apa pun yang dibuat di [§23](#23-deployment-produksi):
   ```bash
   sudo rm /etc/systemd/system/argus.service
   sudo systemctl daemon-reload
   ```

---

## 25. Pertanyaan yang Sering Diajukan

**Bisakah ARGUS berjalan sepenuhnya offline?** Fungsionalitas inti aset/temuan/risiko/dashboard/pelaporan hanya membutuhkan akses jaringan untuk data NVD, KEV, dan EPSS selama pemindaian — tidak membutuhkan koneksi jaringan hanya untuk menelusuri data yang sudah ada. Fitur AI membutuhkan akses ke `LLM_URL` yang dikonfigurasi, tetapi jika endpoint tersebut adalah server lokal pada mesin atau LAN yang sama, tidak dibutuhkan akses internet untuk AI juga.

**Bisakah saya menggunakan LLM yang di-hosting di cloud alih-alih yang lokal?** Ya, secara fungsional — `LLM_URL` menerima endpoint `/v1/chat/completions` yang kompatibel dengan OpenAI mana pun yang dapat dijangkau, termasuk permukaan API kompatibel milik penyedia cloud. Perlu diketahui bahwa ini mengirimkan konteks temuan/aset Anda (apa pun yang dirakit oleh context builder untuk pertanyaan tertentu) ke layanan eksternal tersebut; evaluasi hal ini terhadap persyaratan penanganan data Anda sendiri sebelum melakukannya, karena ARGUS sendiri tidak menyaring atau menyensor konten ini sebelum mengirimkannya.

**Bisakah saya menggunakan mesin basis data yang berbeda (misalnya, MySQL)?** Tidak — seluruh lapisan `database/` ditulis berdasarkan `psycopg2` dan SQL khusus PostgreSQL (misalnya, `ON CONFLICT`, `TIMESTAMPTZ`). Mengganti mesin akan membutuhkan penulisan ulang lapisan tersebut; ini bukan opsi konfigurasi yang didukung.

**Bisakah saya menonaktifkan fitur AI sepenuhnya?** Ya — cukup biarkan `LLM_URL` tidak diset. Endpoint chat mengembalikan pesan eksplisit "belum dikonfigurasi", dan job analisis AI latar belakang tidak memiliki apa pun untuk diproses tanpa LLM yang dapat dijangkau, sehingga efektif idle.

**Bisakah saya menggunakan SQLite alih-alih PostgreSQL?** Tidak, dengan alasan yang sama seperti di atas — skema dan query bergantung pada fitur khusus PostgreSQL (`SERIAL`, `TIMESTAMPTZ`, `ON CONFLICT`, penanganan JSON/array di beberapa tempat) yang tidak dapat dipetakan langsung ke SQLite.

**Bisakah beberapa pengguna terhubung ke dashboard secara bersamaan?** Ya — dashboard adalah aplikasi web Flask multi-pengguna standar dengan autentikasi berbasis sesi per-pengguna (`admin`, `viewer`, dan akun mana pun yang mendaftar sendiri). Akses konkuren dibatasi oleh konfigurasi konkurensi server WSGI Anda (lihat [§23](#23-deployment-produksi)) dan ukuran connection pool basis data (lihat [§21](#21-rekomendasi-performa)).

**Berapa banyak RAM yang sebenarnya dibutuhkan?** Untuk ARGUS itu sendiri (dashboard + bot + PostgreSQL) pada skala kecil, 4 GB sudah dapat digunakan. Tambahkan jauh lebih banyak jika Anda juga menjalankan LLM lokal pada host yang sama — lihat [§8](#8-instalasi-ai) untuk panduan ukuran model terhadap RAM.

**Bisakah saya men-deploy ARGUS di Docker hari ini?** Belum bisa langsung — lihat [§22](#22-instalasi-docker-dukungan-masa-depan). Ini ada dalam peta jalan tetapi belum diimplementasikan dalam basis kode saat ini.

**Bisakah saya mengintegrasikan Active Directory / LDAP / SSO?** Belum saat ini — autentikasi terbatas pada akun bawaan `admin`/`viewer` dan akun lokal yang mendaftar sendiri yang tersimpan di tabel `users`. SSO enterprise terdaftar sebagai item peta jalan di `README.md`, bukan kemampuan yang sudah ada.

---

## 26. Referensi

- [`README.md`](./README.md) — ringkasan proyek, fitur, arsitektur, dan status proyek saat ini
- `API.md` — referensi rute/API dashboard (belum dipublikasikan — lihat `README.md` §16 Dokumentasi untuk status terkini)
- `ARCHITECTURE.md` — dokumentasi arsitektur lanjutan (belum dipublikasikan — lihat `README.md` §5 dan §8 sementara ini)
- `DATABASE.md` — referensi skema lengkap (belum dipublikasikan — lihat langsung `bot/database/schema.sql` dan `bot/migrate.py`)
- `AI.md` — referensi desain AI Security Copilot (belum dipublikasikan — lihat `README.md` §10 dan [§8](#8-instalasi-ai)/[§9](#9-konfigurasi-api-eksternal) dokumen ini)
- `DEPLOYMENT.md` — deployment terkontainerisasi/produksi (belum dipublikasikan — lihat [§22](#22-instalasi-docker-dukungan-masa-depan) dan [§23](#23-deployment-produksi) dokumen ini)
- `SECURITY.md` — model keamanan dan proses pengungkapan bertanggung jawab (responsible disclosure) (lihat `README.md` §11 dan [§20](#20-rekomendasi-keamanan) dokumen ini)
- `ROADMAP.md` — fitur yang direncanakan (belum dipublikasikan — lihat `README.md` §17 Peta Jalan)
