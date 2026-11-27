# Catatan Validasi Termux — Ai-Agent

Dokumen ini menjadi catatan kesinambungan untuk pengujian lokal di Termux. Pisahkan hasil yang benar-benar sudah diamati dari kemampuan yang belum diuji.

Terakhir diperbarui: 2026-11-27

## 1. Identitas repo dan checkpoint

- Repo: `mrfz011007-creator/Ai-Agent-`
- Branch kerja: `agent/security-hardening-workspace`
- Pull request: [#17](https://github.com/mrfz011007-creator/Ai-Agent-/pull/17)
- Status PR saat catatan dibuat: OPEN, belum di-merge.
- Commit sumber saat pengujian lokal terakhir: `ce1a05fe94181b03ca7bab323ac28fa3780d38ec`
- Perubahan pada commit tersebut: `tests/test_python_runner.py` (portabilitas pemeriksaan runtime root dan hard-link). Perubahan itu tidak mengubah mekanisme keamanan produksi.

Jangan menganggap catatan ini sebagai bukti bahwa semua fitur agent sudah berfungsi. Ini adalah rekam jejak validasi Termux dan batasan platform yang diketahui.

## 2. Lingkungan Termux

Profil perangkat/lingkungan yang pernah dicatat:

| Komponen | Nilai |
|---|---|
| Perangkat | TECNO BG6 |
| Android | Android 13, SDK 33 |
| Arsitektur | arm64-v8a |
| Prosesor | Unisoc T606 |
| RAM fisik | sekitar 4 GB |
| Python | 3.14.6 |
| Git | 2.56.0 |
| Java | 21.0.12 |
| Direktori home Termux | `/data/data/com.termux/files/home` |

Versi dan kondisi paket dapat berubah setelah pembaruan Termux. Periksa kembali sebelum menjadikan profil ini sebagai kondisi terkini.

## 3. Workspace dan database agent

Konfigurasi yang digunakan saat uji:

```bash
mkdir -p "$HOME/ai-agent-workspace"
export AI_AGENT_WORKSPACE_ROOT="$HOME/ai-agent-workspace"
export AI_AGENT_STATE_DB="$AI_AGENT_WORKSPACE_ROOT/agent_state.sqlite3"
```

Nilai path yang diharapkan:

- Workspace: `~/ai-agent-workspace`
- Database state: `~/ai-agent-workspace/agent_state.sqlite3`

Database tersebut penting untuk diagnosis lanjutan. Jangan menghapus atau membuat ulang database sebelum bukti kegagalan yang relevan diperiksa.

## 4. Konfigurasi OpenRouter

Status yang pernah diverifikasi:

- File penyimpanan lokal: `~/.config/ai-agent/openrouter.key`
- File dibuat dengan izin akses terbatas (`chmod 600`).
- Fungsi shell `setor` disiapkan untuk memasukkan dan menyimpan key tanpa menuliskannya ke repo.
- Lingkungan Termux berhasil memuat `OPENROUTER_API_KEY`.
- Key pernah dibersihkan dari karakter non-ASCII/whitespace tersembunyi.
- Pemeriksaan autentikasi ke endpoint OpenRouter `/api/v1/key` menghasilkan HTTP 200 dan status autentikasi VALID.

**Catatan keamanan:** API key tidak boleh dimasukkan ke dokumen, commit, log, screenshot, atau pesan percakapan. Jangan mencetak nilai variabel key. HTTP 200 dari endpoint pemeriksaan key hanya membuktikan autentikasi key pada saat tes tersebut; itu belum membuktikan seluruh alur pembuatan respons model berfungsi.

## 5. Hasil tes otomatis lokal

Pada checkout commit `ce1a05fe94181b03ca7bab323ac28fa3780d38ec`, hasil tes lokal Termux yang dilaporkan:

```text
357 passed, 4 skipped in 23.25s
```

Interpretasi:

- 357 tes berstatus **passed**.
- 4 tes berstatus **skipped**, bukan passed.
- Hasil ini adalah hasil suite tes otomatis pada lingkungan Termux saat itu.
- Alasan setiap skip belum direkam secara lengkap di dokumen ini. Untuk mengetahui alasan tepatnya, jalankan ulang tes dengan opsi ringkasan alasan skip (misalnya `-rs`) dan catat outputnya.

Jangan mengubah atau menurunkan mekanisme keamanan hanya untuk menghilangkan status skip. Tes yang memang tidak didukung lingkungan harus ditandai sesuai alasan dan tetap aktif pada CI Linux jika lingkungan CI mendukungnya.

## 6. Uji interaktif agent dan kegagalan yang diamati

Agent berhasil dijalankan dari repo dengan:

```bash
cd ~/Ai-Agent-
python agent.py
```

Prompt yang diuji:

```text
Jelaskan kemampuanmu dan model AI yang sedang kamu gunakan.
```

Output yang terlihat di terminal:

```text
Agent sedang merencanakan dan mengeksekusi...

Plan: plan-e84ebe96d724
Status: FAILED
  - explain_capabilities: FAILED

Goal belum selesai.
```

Pemeriksaan database SQLite dilakukan secara read-only setelah uji tersebut. Record yang ditemukan:

- Plan `plan-e84ebe96d724`: status `FAILED`.
- Goal: `Jelaskan kemampuanmu dan model AI yang sedang kamu gunakan.`
- Task `explain_capabilities`: status `FAILED`, percobaan 1.
- Judul task: `Tulis penjelasan kemampuan dan model AI ke file`.
- Execution contract hanya mengizinkan tool `tulis_file`.
- Model execution mengusulkan tool `lihat`.
- Evidence mencatat `success=0`, `result_status=contract_denied`, dan error `Tool not allowed by execution contract: lihat`.
- Checkpoint terakhir mencatat event `failed` dengan alasan yang sama.
- Tidak ada bukti bahwa file berhasil dibuat.

**Akar masalah kegagalan ini terkonfirmasi:** pertanyaan percakapan umum salah diarahkan ke pipeline perencanaan dan eksekusi tugas. Planner membentuk tugas untuk menulis penjelasan ke file, lalu model execution memilih tool `lihat`, yang tidak termasuk dalam kontrak tugas. ToolRouter/kontrak menolak permintaan tersebut dengan benar. Ini bukan bukti kegagalan autentikasi API key. Pengamanan kontrak tidak boleh dilonggarkan untuk mengatasi masalah ini.

Audit kode pada branch kerja menguatkan diagnosis:

- Jalur CLI mengirim input non-lokal ke `runtime.run_goal()`.
- `GoalRunner` membuat rencana melalui model, kemudian menjalankan `ModelExecutionService`.
- `ModelExecutionService` meminta output JSON berupa satu tool/action/arguments, bukan jawaban percakapan bebas.
- `ExecutionContract.allows_tool()` membatasi tool pada daftar `allowed_tools` yang ditetapkan untuk task.

### Diagnosis keterlambatan

Konfigurasi Termux saat pemeriksaan menunjukkan timeout model 45 detik. Pipeline melakukan panggilan model berurutan untuk perencanaan dan proposal eksekusi. Gateway juga memiliki jalur fallback OpenRouter backup dan Gemini ketika error provider tergolong dapat dipulihkan.

Ini menjelaskan sumber latensi arsitektural yang masuk akal, tetapi **durasi aktual setiap panggilan dan apakah fallback benar-benar terjadi pada percobaan ini belum tercatat**. Database hanya membuktikan kegagalan kontrak tool, bukan durasi request. Jangan menyatakan bahwa timeout 45 detik benar-benar habis pada kasus ini.

### Status diagnosis

- Akar masalah kegagalan `explain_capabilities`: **TERKONFIRMASI**.
- Penyebab struktural: pencampuran jalur percakapan dan jalur eksekusi tugas.
- Kontrak keamanan: bekerja sesuai desain; jangan dilemahkan.
- Sumber potensi keterlambatan: dua tahap model berurutan ditambah kemungkinan fallback.
- Pengukuran latensi aktual: belum tersedia; perlu instrumentasi waktu per request pada tahap perbaikan.
- Tidak ada kode produksi yang diubah sebagai bagian diagnosis ini.

## 7. Batasan Termux dan PRoot yang sudah diketahui

- Termux dan Ubuntu di dalam PRoot tidak menyediakan isolasi OS namespace ketat yang disyaratkan oleh Python Runner.
- Karena itu, fitur `jalankan_python` harus tetap ditolak pada lingkungan tersebut.
- Penolakan ini merupakan perilaku fail-closed yang diharapkan, bukan alasan untuk menurunkan persyaratan keamanan.
- Mode perintah biasa yang diperkeras bukan sandbox OS dan tidak boleh dijadikan fallback untuk menjalankan Python.
- Jangan mengubah `write_file_secure`, GuardEngine, validasi path, atau kontrol sandbox untuk membuat fitur yang tidak didukung menjadi tampak berjalan.

## 8. Status CI GitHub pada checkpoint

Pada pemeriksaan GitHub terakhir untuk commit `ce1a05fe94181b03ca7bab323ac28fa3780d38ec`:

- Workflow PR dan push selesai dengan status sukses.
- Job Python 3.11 dan Python 3.14 sukses.
- Pemeriksaan dependency consistency, compile, repository tests, Python Runner bootstrap smoke test, Ruff, dan Bandit sukses.
- Keberhasilan CI bukan sertifikasi keamanan dan tidak membuktikan semua provider/model live tersedia.
- PR #17 tetap OPEN dan belum di-merge.

## 9. Langkah lanjutan yang disarankan

1. Ambil skema database secara read-only dan periksa record yang terkait dengan plan `plan-e84ebe96d724`; ambil hanya status dan pesan error yang diperlukan.
2. Cocokkan penyebab aktual dengan kode di jalur planner/executor/runtime dan registry.
3. Rancang pemisahan jalur pertanyaan biasa dari jalur eksekusi tugas hanya setelah akar masalah diverifikasi.
4. Tambahkan tes regresi untuk pertanyaan percakapan, tugas yang menggunakan tool, kegagalan provider, serta penolakan tindakan berbahaya.
5. Jalankan seluruh tes lokal dan GitHub Actions. Pertahankan semua kontrol keamanan yang sudah ada.

## 10. Aturan pemeliharaan catatan

- Perbarui dokumen ini setelah ada hasil uji Termux baru yang benar-benar dijalankan.
- Catat tanggal, commit SHA, perintah uji, hasil pass/fail/skip, dan alasan skip.
- Pisahkan tes lokal Termux, tes CI, dan tes provider live; jangan menyatukan hasil ketiganya.
- Jangan mencatat API key, token, kredensial, data pribadi, atau output sensitif.
- Pertahankan catatan kegagalan lama sebagai riwayat; tambahkan hasil baru alih-alih menghapus konteks sebelumnya.


## 11. Tahap 2 — Fondasi percakapan (2026-11-27)

Implementasi pada branch `agent/security-hardening-workspace`:

- Menambahkan `core/conversation.py` sebagai jalur percakapan langsung melalui ModelGateway. Jalur ini tidak membuat plan dan tidak memiliki antarmuka eksekusi tools.
- Menambahkan `core/request_router.py` untuk memisahkan pertanyaan biasa dari permintaan tindakan imperatif yang eksplisit. Permintaan ambigu secara default diarahkan ke percakapan, bukan eksekusi.
- Menambahkan `AgentRuntime.chat()` dan mengubah CLI agar pertanyaan biasa mendapat jawaban langsung; jalur GoalRunner tetap dipakai untuk permintaan tindakan yang terdeteksi.
- Instruksi percakapan membatasi klaim akses/eksekusi dan menyertakan konfigurasi provider/model primer serta fallback. Informasi tersebut adalah konfigurasi, bukan bukti model mana yang menangani satu permintaan tertentu.
- Menambahkan `tests/test_conversation.py` untuk percakapan langsung, identitas model/fallback, input kosong/panjang, respons model kosong, klasifikasi intent, dan pemisahan CLI dari GoalRunner.

### Verifikasi GitHub Actions

Commit kode dan tes: `033144c6276a1cb7510b310ff9c7a6e76c9a2f6e`

Workflow run: https://github.com/mrfz011007-creator/Ai-Agent-/actions/runs/45939531121

- Python 3.11: 368 passed, 2 skipped; bootstrap smoke test 1 passed; Ruff dan Bandit sukses.
- Python 3.14: 368 passed, 2 skipped; bootstrap smoke test 1 passed; Ruff dan Bandit sukses.
- Dependency consistency dan compileall sukses pada kedua versi.
- Bandit tidak menemukan isu baru; peringatan B608 yang sudah tercatat pada `core/state_store.py:133` tetap ada sebagai pengecualian `nosec`.
- Tes provider live dan uji interaktif pada Termux belum dilakukan pada tahap ini.

Tahap 2 dinyatakan selesai berdasarkan tes CI di atas. Tahap 3 (memori percakapan) belum dimulai. Kontrol `write_file_secure`, GuardEngine, PolicyEngine, ToolRouter, dan Python Runner tidak dilonggarkan. PR #17 tetap terbuka dan belum di-merge.
