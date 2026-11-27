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


## 12. Tahap 3 — Memori percakapan (2026-11-27)

Implementasi pada branch `agent/security-hardening-workspace`:

- Menambahkan tabel SQLite `conversation_messages` terpisah dari tabel task/evidence/plan dan dari `memory.json` yang dipakai untuk memori fakta/pengalaman proyek.
- Menyimpan pasangan pesan user dan assistant secara atomik hanya setelah model mengembalikan jawaban valid. Kegagalan provider tidak meninggalkan pesan user yatim.
- Memuat riwayat percakapan saat permintaan berikutnya, termasuk setelah proses Agent dimulai ulang.
- Default conversation ID adalah `default`; sesi dapat dipisahkan dengan variabel lingkungan `AI_AGENT_CONVERSATION_ID`.
- Riwayat dibatasi pada 12 pesan terbaru (maksimum 6 giliran) dan konteks yang dikirim ke model dibatasi 24.000 karakter. Pesan individual dibatasi 12.000 karakter.
- Isi riwayat dikirim sebagai data JSON dan secara eksplisit ditandai sebagai data tidak tepercaya. Riwayat tidak dapat mengubah system instruction, kontrak tool, izin, atau kebijakan keamanan.
- Percakapan biasa tidak otomatis menulis ke memori fakta/proyek/tugas. Mekanisme memori hierarkis dan reflection yang sudah ada tetap terpisah.
- Menambahkan tes untuk kesinambungan lintas restart, isolasi conversation ID, batas retensi, kegagalan provider, input kosong/panjang, prompt injection, dan pemisahan dari project memory.

### Verifikasi GitHub Actions

Commit implementasi/tes terakhir saat verifikasi: `4147f39382c6de7106e15521e58952619b1dc54f`.

Workflow PR #17: https://github.com/mrfz011007-creator/Ai-Agent-/actions/runs/45944580095

- Python 3.11: 375 passed, 2 skipped; bootstrap smoke test 1 passed.
- Python 3.14: 375 passed, 2 skipped; bootstrap smoke test 1 passed.
- Dependency consistency, compile, Ruff, dan Bandit sukses.
- Bandit tetap melaporkan peringatan `nosec B608` yang sudah ada pada query SQL berparameter di `core/state_store.py`; tidak ada temuan keamanan baru.
- Workflow PR #17 dan push untuk commit kode `4147f39382c6de7106e15521e58952619b1dc54f` selesai dengan status SUCCESS pada Python 3.11 dan 3.14.

### Batas validasi

- Tes provider live dan uji percakapan lanjutan langsung pada Termux belum dilakukan.
- CI menggunakan gateway/model mock untuk tes memori; kelulusan tes tidak membuktikan kualitas ingatan model live.
- Riwayat percakapan tersimpan lokal dalam SQLite workspace. Gunakan conversation ID berbeda untuk sesi terpisah; API penghapusan tersedia pada StateStore tetapi belum dipasang sebagai perintah CLI.
- Tahap 3 belum dinyatakan selesai sampai workflow push selesai dan uji lanjutan pada Termux memverifikasi konteks benar-benar terbawa ke pesan berikutnya.

## 13. Audit penyelesaian Tahap 3–8 (2026-11-27)

Perubahan lanjutan dibuat pada branch `agent/security-hardening-workspace`, PR #17. Bagian ini memperbarui status implementasi setelah audit Tahap 3–8. Catatan historis di bagian sebelumnya dipertahankan.

### Tahap 3 — Memori percakapan

- Mempertahankan riwayat terpisah di SQLite, bukan mencampurnya dengan memori fakta/proyek/tugas.
- Batas per percakapan tetap 12 pesan dan konteks prompt 24.000 karakter; pesan individual maksimum 12.000 karakter.
- Menambahkan batas global 2.048 pesan lintas seluruh conversation ID sehingga banyak sesi tidak dapat menumbuhkan tabel tanpa batas.
- Menambahkan tes retensi global, di samping kesinambungan setelah restart, isolasi sesi, kegagalan provider, validasi input, dan penanganan riwayat sebagai data tidak tepercaya.
- Batasan: ID default masih `default`; sesi berbeda perlu mengatur `AI_AGENT_CONVERSATION_ID`. API penghapusan riwayat tersedia di `StateStore.clear_conversation()`, tetapi belum disediakan sebagai perintah CLI.

### Tahap 4 — Routing dan batas tools

- Menambahkan pengecualian untuk pertanyaan tujuan seperti “Buat apa AI Agent ini?” agar tidak salah diklasifikasikan sebagai perintah tindakan.
- Menambahkan regresi untuk pertanyaan “buat apa/bisa buat apa” dan perintah tindakan eksplisit.
- Jalur eksekusi tetap melewati execution contract, capability policy, schema validation, GuardEngine, PolicyEngine, konfirmasi, budget, ToolRouter, dan pencatatan evidence.
- Tidak mengubah atau melemahkan batas keamanan eksekusi.

### Tahap 5 — Reliabilitas model/provider

- Menetapkan timeout per request untuk OpenRouter dan Gemini lewat `AI_AGENT_MODEL_TIMEOUT_SECONDS` (default 45 detik; rentang 1–120 detik).
- Gemini SDK menerima timeout dalam milidetik melalui `HttpOptions.timeout`; client sinkron ditutup setelah request.
- Jika API key provider diketahui tidak valid (HTTP 401), fallback tidak mengirim ulang kredensial provider yang sama ke model cadangan provider tersebut. HTTP 403 yang dapat bersifat model-spesifik tetap boleh mencoba model cadangan.
- Rantai fallback yang habis tetap berurutan dan dapat memakan waktu lebih lama daripada satu timeout request; belum ada deadline total end-to-end untuk keseluruhan rantai.
- Mematok dependensi `google-genai>=1.0.0,<3.0.0` untuk menghindari upgrade major tanpa audit kompatibilitas.

### Tahap 6 — Eksekusi dan pemulihan tugas

- Memastikan kegagalan seluruh provider yang muncul sebagai `MODEL_PROVIDERS_EXHAUSTED` dikenali sebagai kondisi menunggu pemulihan provider, bukan langsung dianggap kegagalan tool.
- Menambahkan tes regresi yang memastikan task berpindah ke `WAITING` dengan `WAIT_FOR_MODEL`.
- Mempertahankan aturan bahwa operasi non-idempoten tidak diulang otomatis; task terputus menunggu rekonsiliasi dan evidence.

### Tahap 7 — Tes komprehensif

Pengujian CI pada commit kode dan dokumentasi `cf027aea1ac6018b0fb2047080ef0d5192651631`:

Workflow PR #17: https://github.com/mrfz011007-creator/Ai-Agent-/actions/runs/45946286364

- Python 3.11: **382 passed, 2 skipped**.
- Python 3.14: **382 passed, 2 skipped**.
- Python Runner bootstrap: 1 passed pada masing-masing job.
- `pip check`, `compileall`, Ruff, dan Bandit sukses pada kedua versi.
- Bandit tetap mencatat satu pengecualian `nosec B608` pada query SQL berparameter `core/state_store.py:213`; tidak ada temuan Bandit level tinggi yang membuat job gagal.
- Dua tes skipped tidak dihitung sebagai passed. Tes namespace nyata bergantung pada ketersediaan fitur isolasi host; dukungan perangkat/host harus dinilai sesuai hasil tes lingkungannya.

### Tahap 8 — Audit arsitektur, optimasi, dan dokumentasi

- Menambahkan `docs/ARCHITECTURE.md` berisi alur request, model memori, batas provider, lifecycle tugas, kapabilitas, batasan, dan invariants keamanan.
- Memperbarui `TERMUX.md` tentang batas memori global, timeout/fallback provider, serta prosedur smoke test setelah gate CI Tahap 8.
- Smoke test interaktif Termux **belum dijalankan** sesuai instruksi pengguna. Pelaksanaan ditunda sampai implementasi Tahap 8 dan GitHub Actions pada head final branch lulus.
- CI provider memakai fake/mock untuk tes deterministik; kelulusan CI bukan bukti key/provider live atau kualitas jawaban model.
- Validator Python Tahap 8 melakukan parsing AST dan pemeriksaan definisi, bukan eksekusi kode. Keberhasilan validator tidak membuktikan perilaku runtime kode hasil generasi.

### Batas keamanan yang dipertahankan

Tidak ada perubahan yang melemahkan `write_file_secure`, descriptor-relative/no-follow workspace checks, GuardEngine, PolicyEngine, ToolRouter, execution contract, capability checks, user confirmation, evidence verification, atau perilaku fail-closed Python Runner. `jalankan_python` tetap ditolak pada Termux/PRoot karena isolasi OS yang diwajibkan tidak tersedia.

### Status checkpoint

- Implementasi Tahap 3–8 dan tes CI: lulus pada commit yang tercantum di atas.
- Uji smoke interaktif Termux: sengaja ditunda, belum diverifikasi.
- PR #17: tetap OPEN dan belum di-merge.
- Kelulusan CI bukan sertifikasi keamanan dan tidak menyatakan seluruh lingkungan/provider telah diuji.


## 14. Uji identitas model pada Termux — observasi sebelum perbaikan (2026-11-27)

Checkout yang diuji: branch `agent/security-hardening-workspace`, commit `b6884f7`.

Kondisi yang diverifikasi dari sesi Termux:
- Agent berhasil berjalan setelah `AI_AGENT_WORKSPACE_ROOT`, `AI_AGENT_STATE_DB`, dan `AI_AGENT_CONVERSATION_ID` diatur pada shell aktif.
- Workspace `~/Projects/Ai-Agent/workspace` terdeteksi dan memiliki izin direktori terbatas.
- Variabel `OPENROUTER_API_KEY` tersedia di proses shell. Nilainya tidak dicetak atau disimpan di log.

Prompt uji: meminta provider dan model yang benar-benar menangani pesan, berdasarkan metadata respons provider.

Hasil yang diamati:
- Teks jawaban model menyatakan tidak memiliki metadata respons provider, lalu menjelaskan model utama dan daftar fallback dari konfigurasi.
- Setelah teks jawaban, CLI menampilkan `Provider : openrouter`, `Model aktual : nvidia/nemotron-3-ultra-550b-a55b:free`, dan status terverifikasi dari metadata provider.
- Prefix provider tercetak sebagai teks literal `\\nProvider`, bukan sebagai baris baru.

Diagnosis:
- Metadata aktual dikembalikan provider dan diteruskan ke `ConversationReply`, tetapi baru tersedia setelah jawaban model dihasilkan.
- System prompt sebelumnya hanya memberikan daftar konfigurasi dan belum secara eksplisit melarang model menebak identitas aktual dari daftar tersebut.
- Bug format berasal dari escape newline ganda pada `agent.py`; bukan kegagalan ekstraksi metadata provider.

Status: ini adalah bukti uji **sebelum perbaikan** pada commit di atas. Pengujian ulang terhadap commit perbaikan tetap wajib dilakukan di Termux setelah CI GitHub lulus.
