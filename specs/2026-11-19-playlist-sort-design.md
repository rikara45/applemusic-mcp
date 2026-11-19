# Playlist Sort — desain

Status: disetujui · revisi 2026-11-19
Repo: fork `rikara45/applemusic-mcp` (upstream `epheterson/applemusic-mcp`)

## Kenapa

Playlist yang isinya acak sulit dipakai. User ingin mengelompokkan lagu per artis
(A–Z), lalu per album (A–Z), dengan lagu di dalam album mengikuti nomor track —
**di playlist yang sama**, tanpa membuat playlist baru.

## Tujuan

- `playlist(action="sort", playlist=...)` mengurutkan isi playlist yang sudah ada.
- Urutan: `artistName` A–Z (tanpa peduli huruf besar/kecil) → `albumName` A–Z →
  `discNumber` → `trackNumber`; kalau semuanya sama, urutan lama dipertahankan.
- `dry_run=True` menampilkan hasil urutan tanpa menulis apa pun.

## Non-tujuan

- Memindahkan satu lagu ke posisi tertentu, atau urutan manual bebas.
- Sort berdasarkan field lain (judul, tanggal, popularitas).
- Playlist buatan Music.app atau Apple-curated (API tidak bisa menulisnya).
- Membuat playlist baru atau menyentuh folder.

## Batasan

- Apple tidak menyediakan endpoint reorder/move untuk track playlist, dan web
  player tidak menyimpan perubahan urutan. Satu-satunya jalur: baca isi, hapus
  semua track, tambahkan ulang sesuai urutan baru.
- Hanya playlist dengan `amp_api.playlist_kind(pl) == "api"` (canEdit) yang bisa
  ditulis lewat jalur ini.
- Biaya: N request hapus + N/25 request tambah (batch 25). Playlist panjang
  berisiko kena rolling rate limit Apple.
- Playlist kosong sebentar selama proses; metadata "date added" berubah.
- Track di playlist diasumsikan `library-songs` (sama seperti `_playlist_copy`).
- `amp_api.get_tracks` belum mengembalikan album/nomor; perlu ditambah
  `album`, `disc_number`, `track_number` (atribut API: `albumName`,
  `discNumber`, `trackNumber`).

## Pendekatan

**A. In-place rebuild — dipilih.** Pakai endpoint yang sudah terbukti di repo:
`get_tracks` (baca), `remove_track` (hapus per relationship id), `add_tracks`
(tambah batch). Hasil akhir persis urutan target dan playlist ID/nama/folder tetap.

**B. Tambah dulu, hapus yang lama — ditolak.** Lebih aman terhadap playlist
kosong, tapi bergantung pada playlist mengizinkan duplikat sementara; perilaku
itu belum terverifikasi di akun user, jadi tidak dijadikan dasar.

**C. Copy ke playlist baru — ditolak.** Itu persis hal yang user ingin hindari;
ID dan URL playlist ikut berubah.

## Perilaku

1. Resolve `playlist` (nama atau `p.XXX`) ke id; cek `playlist_kind`. Bukan `api`
   → error jelas, tidak ada tulisan.
2. Baca semua track (`amp_api.get_tracks`). `None` → error "tidak bisa dibaca".
   Kosong → "playlist kosong, tidak ada yang diurutkan".
3. Hitung urutan target. Kalau sama dengan urutan sekarang → "sudah urut",
   tidak ada tulisan dan tidak ada backup.
4. Tulis backup JSON urutan asli ke
   `paths.cache_dir()/snapshots/<playlist_id>-sort-<timestamp>.json` sebelum
   mutasi.
5. `dry_run` berhenti sebelum backup dan sebelum mutasi: tampilkan urutan baru.
6. Hapus semua relationship id lama (satu per satu, `amp_api.remove_track`).
7. Tambahkan ulang berurutan dengan `amp_api.add_tracks`, batch 25, item
   `(relationship_id, "library-songs")`.
8. Baca ulang dan bandingkan urutan `(artist, album, name)` yang sudah
   dinormalisasi. Kalau beda, laporkan sebagai peringatan + path backup —
   jangan klaim sukses.
9. Gagal di langkah 6/7 → coba pulihkan: hapus entri baru yang terlanjur masuk,
   lalu tambahkan lagi track yang sudah terhapus, sesuai urutan asli. Laporkan
   hasil restore; kalau restore juga gagal, sebut path backup dan bahwa playlist
   mungkin kehilangan track.
10. Catat `audit_log.log_action("sort_playlist", ...)`.

Sort key:
`(artis kosong?, artist.casefold().strip(), album kosong?, album.casefold().strip(), disc_number, track_number, index)`.
Artis kosong di akhir; album kosong di akhir di dalam grup artis.

## Antarmuka tool

- Action baru `sort` di `playlist()`; parameter yang dipakai: `playlist`, `dry_run`.
- Output sukses: jumlah track, nama playlist, dan daftar urutan baru (maks. 30
  baris, format `Artis — Album — Judul`, lalu "… N lainnya"), diakhiri label
  rail tulis.
- `dry_run`: daftar yang sama + "Preview — nothing written."
- Docstring `playlist()`, `docs/usage.md`, dan baris tabel `README.md` diperbarui.

## Yang diserahkan ke builder

- Nama helper, format persis output, dan pemecahan baris kode.
- Memakai `_resolve_playlist` yang ada untuk nama; lookup kind lewat
  `amp_api.list_playlists()`.

## Verifikasi

- Unit test (mock, tanpa akun): `get_tracks` mengembalikan album/disc/track;
  urutan artis+album+track, case-insensitive, artis/album kosong di akhir,
  duplikat tidak dibuang, no-op, dry_run tanpa tulis, penolakan playlist
  non-API, gagal baca, gagal hapus/tambah → rollback/restore, verifikasi beda →
  peringatan, audit log.
- `python -m pytest -q`, `make coverage` (≥90%), `ruff check .`, `black .`.
- Live test hanya di playlist percobaan, setelah user setuju (`TEST_API=1`).
