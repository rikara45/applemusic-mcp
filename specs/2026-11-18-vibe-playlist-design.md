# Vibe Playlist — desain fork `applemusic-mcp`

Status: draf untuk disetujui · 2026-11-18
Repo: `C:\Users\Rio Eka Putra\Documents\Project\applemusic-mcp` (fork `rikara45/applemusic-mcp`, upstream `epheterson/applemusic-mcp`)

## Kenapa

Butuh cara cepat mengubah "saya suka lagu A, B, C" menjadi playlist yang isinya
lagu-lagu satu rasa — bukan cuma menambah tiga lagu itu. Apple Music tidak punya
API pinjaman lagu mirip yang terdokumentasi, tapi web player-nya sendiri memakai
radio per lagu. Fitur ini membungkus sinyal itu jadi satu tool.

## Tujuan

- Dari beberapa lagu acuan (seed), menghasilkan daftar lagu yang mirip secara
  genre/era/artis, lengkap dengan alasan tiap lagu masuk.
- Bisa mencampur lagu usulan model (`candidates`) yang diverifikasi ke katalog,
  supaya permintaan spesifik ("lagu Indonesia 2000an mellow") tetap bisa.
- Preview dulu; playlist baru dibuat setelah user setuju.
- Jalan di Windows dengan login browser (tanpa Apple Developer Program).

## Non-tujuan

- Bukan pengganti `discover`/`catalog` yang sudah ada; tool lama tetap.
- Tidak menyentuh playback/queue.
- Tidak melakukan rekomendasi lintas bahasa berbasis model bahasa (LLM) di dalam
  server — itu tugas model yang memanggil tool.

## Anti-tujuan (gagal walau teknis jalan)

- Menghasilkan playlist berisi lagu yang tidak ada di katalog (judul karangan).
- Membuat playlist duplikat saat nama sudah dipakai.
- Menelan error rate limit menjadi "tidak ada lagu mirip".

## Batasan

- Semua `me/...` butuh `Music-User-Token` + developer token hasil harvest; sudah
  ditangani `server.get_headers()` dan `amp_api._headers()`.
- Rate limit Apple bersifat rolling ~60 menit, tanpa header sisa kuota
  (`_THROTTLED_REASON` di `server.py`). Anggaran: maksimal 2 request per seed
  (satu pencarian metadata, satu harvest) dan 5 seed per panggilan.
- Playlist yang baru dibuat butuh waktu propagasi sebelum bisa diisi (catatan di
  README upstream), jadi penambahan track perlu retry singkat.
- Tidak ada dependency baru. Python 3.10+, `requests` sudah ada.

## Pendekatan

| Opsi | Isi | Trade-off |
|---|---|---|
| A. Station harvest | `POST /v1/me/stations/next-tracks/ra.{song_id}` per seed, lalu ranking metadata | Nyata dari Apple, tapi hasilnya selera radio Apple |
| B. Usulan model + verifikasi | Model menulis kandidat, server mencocokkan ke katalog | Peka permintaan spesifik, tapi bergantung ingatan model untuk rilis baru |
| C. Hybrid **(dipilih)** | A sebagai tulang punggung + parameter `candidates` untuk usulan model | Menutup kelemahan masing-masing; biaya tambahan hanya satu parameter |

Dipilih C. Tidak ada mode terpisah: `candidates` kosong = A, terisi = A+B.

## Keputusan yang sudah diambil

1. Satu tool baru bernama `vibe` (bukan action di tool lama), karena alurnya
   menyentuh beberapa resource sekaligus dan punya parameter sendiri.
2. Default `create=false` (preview). Penulisan playlist hanya saat `create=true`.
3. Nama playlist yang sudah ada → ditolak dengan pesan jelas, bukan ditimpa.
4. Storefront mengikuti `get_storefront()` yang sudah ada.
5. Level permission (`--level`) dipertahankan; hanya `music` dan `--allow-ui`
   yang hilang bersama jalur macOS.
6. Urutan kerja: fitur dulu, pemangkasan macOS sesudahnya, supaya jalur playlist
   yang sedang dipakai tidak ikut berisiko.

## Desain fitur

### Tool

```python
vibe(
    seeds: list[str],              # "Judul", "Artis - Judul", atau catalog ID
    name: str = "",                # wajib bila create=True
    count: int = 25,               # target jumlah lagu akhir
    candidates: list[str] | None = None,  # usulan model, divalidasi ke katalog
    exclude: list[str] | None = None,     # lagu yang tidak diinginkan
    create: bool = False,
    description: str = "",
    storefront: str = "",          # kosong = storefront akun
)
```

### Alur

1. **Resolve seed** lewat `amp_api.search_catalog_songs()` (satu request per seed,
   sekaligus membawa genre + tahun). Seed gagal dicatat dan dilaporkan; minimal
   satu seed harus berhasil.
2. **Harvest**: untuk tiap seed, `amp_api.next_tracks(song_id)` →
   `POST {AMP}/me/stations/next-tracks/ra.{song_id}?include=artists`.
   Satu request per seed (maksimum 5 seed per panggilan; lebih dari itu ditolak
   dengan saran memecah permintaan).
3. **Kandidat usulan**: tiap string di `candidates` dicari lewat
   `amp_api.search_catalog_songs()`, ambil match terbaik; yang tidak ketemu
   dilaporkan, tidak dipaksakan.
4. **Bersihkan**: buang seed, `exclude` (di-resolve dulu, fallback cocok longgar
   nama+artis), dan duplikat (id katalog; lalu pasangan judul+artis ternormalisasi).
5. **Ranking** (deterministik, tanpa random):
   - `+3 × irisan_genre` — `|genre ∩ genre_seed| / |genre lagu|`
   - `+2` bila artisnya salah satu artis seed
   - `+1 − min(1, |tahun − median_tahun_seed| / 15)`
   - `+0.5` bila berasal dari `candidates`
   - Tie-break: urutan kemunculan asli (usulan dulu, lalu seed berurutan).
6. **Ambil** `count` teratas. Bila hasil kurang dari `count`, laporkan jumlah
   sebenarnya, jangan diam-diam mengulang request.
7. **Preview**: daftar bernomor `Judul — Artis (genre, tahun) · sumber + alasan`,
   ringkasan seed yang dipakai, kandidat yang gagal, dan perintah lanjutan
   (`create=true` dengan nama).
8. **Create**: `amp_api.create_playlist()` → `amp_api.add_tracks()` per batch 25
   (konvensi yang sudah dipakai `server.py`), dengan retry 2× jeda ~1,5 detik
   untuk propagasi. Balikan: nama, id, jumlah berhasil, batch yang gagal.

### Modul dan titik sentuh

- Baru: `src/applemusic_mcp/vibe.py` — resolve-harvest-rank murni (tanpa I/O
  sendiri; HTTP lewat `amp_api`), mudah diuji.
- `amp_api.py`: tambah `next_tracks(song_id, storefront="") -> tuple[list[dict], str]`
  (track dinormalkan: id, name, artist, genres, release_date); perluas
  `search_catalog_songs(term, limit=5, storefront="")` dengan `genres` +
  `release_date` (aditif; pemanggil lama tidak terpengaruh).
- `server.py`: satu fungsi tool `vibe` + registrasi `@mcp.tool`; pakai ulang
  `get_storefront()`, `_resolve_track()`, `_api_error()`,
  `_SESSION_EXPIRED_MSG`, `_THROTTLED_REASON`.
- Tanpa perubahan `auth.py`, `browser.py`, `cli.py`.

### Error dan kejujuran output

- Token habis → pesan `_SESSION_EXPIRED_MSG` (bukan "tidak ada hasil").
- 429 → pesan `_THROTTLED_REASON` lewat `_api_error()`.
- `next-tracks` mengembalikan kosong untuk sebuah seed → seed itu dilewati dan
  dicatat; bukan alasan menggagalkan seluruh panggilan.
- Rate limit tidak boleh membuat hasil tampak kosong tanpa penjelasan.

## Keputusan setelah builder check

Enam hal yang tadinya menggantung, diputuskan di sini supaya builder tidak menebak:

1. **Metadata seed.** `amp_api.search_catalog_songs()` diperluas mengembalikan
   `genres` dan `release_date` (field sudah ada di respons Apple, hanya selama ini
   dibuang) dan mendapat parameter `storefront=""`. Vibe me-resolve seed lewat
   fungsi itu, bukan `_resolve_track()`, sehingga genre/tahun seed tersedia tanpa
   request tambahan. Bila sebuah track tidak punya genre atau `releaseDate`, suku
   terkait dihitung 0 — ranking tetap jalan, alasan di preview menyebut apa adanya.
2. **Kontrak error `next_tracks()`.** Mengembalikan `(tracks, err)` seperti fungsi
   tulis lain. 401 → `_SESSION_EXPIRED_MSG`; 429 → batalkan seluruh panggilan
   dengan `_THROTTLED_REASON` (bukan hasil parsial tanpa penjelasan). Hasil kosong
   untuk satu seed → seed dilewati dan dicatat.
3. **Preview vs create.** `create=true` menjalankan ulang pipeline (radio Apple
   tidak deterministik). Output create menyebutkan daftar track yang benar-benar
   masuk, bukan menyalin preview. Tidak ada cache/state antar-panggilan. Nama
   duplikat yang ditolak adalah pengaman agar tidak lahir playlist kedua.
4. **`storefront` berlaku menyeluruh** — seed, harvest, dan pencarian kandidat.
   Default kosong = storefront akun.
5. **Nama duplikat.** Ditolak hanya bila sama persis (case-insensitive). Nama yang
   mirip (substring) tetap boleh, tapi preview/create menampilkan peringatan bahwa
   operasi destruktif nanti bisa ambigu — konsisten dengan perilaku
   `_resolve_playlist_for_write`.
6. **Efek samping library.** Menambah track katalog ke playlist library ikut
   menambahkannya ke library. Preview dan output create menyatakan ini terang-
   terangan (`N lagu akan/telah ditambahkan ke library`). Tidak ada pengecekan
   per-track "sudah di library" (mahal: satu request per lagu) dan tidak ada mode
   `library_only` — dicatat sebagai fitur yang sengaja tidak dibuat.

Ditambahkan juga satu langkah spike sebelum implementasi: verifikasi bahwa
`POST /me/stations/next-tracks/ra.{id}` benar-benar mengembalikan track dengan
token hasil harvest di akun ini. Kalau ternyata tidak, pendekatan C gugur dan
kembali ke brainstorming — bukan dipaksa jalan.

## Pemangkasan macOS (fase 2, setelah fitur jalan)

Yang dihapus: `applescript.py`, `safari.py`, `safari_player.py`, dan test khusus
ketiganya; `APPLESCRIPT_AVAILABLE` jadi konstanta `False`; cabang native di
`server.py` (pemakaian `asc.*`, `use_applescript`, `resolved.applescript_name`),
level `music` dan `--allow-ui` di `capabilities.py`, opsi `--safari`/`--allow-ui`
di `cli.py`. Yang tetap: `api` + engine Chrome (`browser.py`, `musickit_js.py`)
untuk playback/queue. README dan `docs/` disesuaikan ke Windows/Linux.

Pemangkasan dilakukan per commit kecil dengan `pytest` hijau di tiap langkah;
bila satu cabang ternyata dipakai jalur playlist, cabang itu dipertahankan dan
dicatat.

## Testing

- Unit (`responses`, pola test yang sudah ada): harvest per seed, dedupe,
  ranking, exclude yang tidak ketemu, preview vs create, nama playlist duplikat,
  429 → pesan throttle, hasil kurang dari `count`.
- Live manual sekali dengan akun asli: seed 3 lagu → preview → create →
  cek playlist di app Apple Music.
- `ruff check` + `pytest -q` harus bersih sebelum tiap commit.

## Yang diserahkan ke builder

- Bentuk normalisasi metadata dari `next-tracks` (nama field bisa berbeda dari
  yang didokumentasikan; verifikasi saat implementasi).
- Jumlah track yang dikembalikan satu panggilan `next-tracks` (tentukan buffer
  `count × 1,5` berdasarkan hasil nyata).
- Teks pesan (bahasa Indonesia untuk output tool, identifier tetap Inggris).
- Detail retry propagasi playlist.
