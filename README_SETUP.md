# Scraping_Indikator_Auto

Project development baru. Jangan mengganti project lama.

## Arsitektur

Google Apps Script Web Form
→ Master_Indikator_Test
→ Scraping_Jobs_Test (PENDING)
→ Python Worker
→ Legacy source parser (jika source lama sudah didukung)
→ Generic detector/extractor (untuk website baru)
→ SUCCESS / REVIEW / NEED_PARSER
→ Hasil_Indikator_Test

## Prinsip

1. Project lama tetap STABLE.
2. Source lama yang sudah bekerja bisa dipanggil READ-ONLY melalui `sources/legacy_bridge.py`.
3. Website baru tidak langsung dibuat file indikator baru.
4. Generic engine mendeteksi format lalu memakai extractor bersama untuk PDF,
Excel, CSV, HTML, atau JSON.
6. Dashboard kompleks yang gagal menjadi NEED_PARSER.
7. Result upsert memakai Kode + Wilayah + Tahun.
8. Adapter sumber terdaftar deklaratif di `sources/registry.py`
   (AdapterSpec + Clause). Indikator/situs baru = tambah satu entri
   registry, tanpa mengubah if-chain dispatcher.

Setiap URL diproses dengan extractor bersama berdasarkan format kontennya
(termasuk file di dalam ZIP), lalu discovery mencari tabel, API, atau file data
tertaut jika URL awal hanya portal/katalog. Adapter lama dijalankan hanya
sebagai fallback setelah jalur universal tidak menemukan hasil; pemilihan
adapter tidak bergantung pada Kode indikator. Karena itu indikator baru tidak
memerlukan parser baru selama sumbernya menyediakan data yang bisa ditemukan
dan dipetakan terhadap nama metrik, tahun, serta wilayah.

Ekstraksi isi PDF dilakukan oleh satu extractor bersama (`extractors/pdf.py`),
termasuk fallback OCR untuk PDF scan. Konektor BPS menemukan dan mengunduh PDF
dari berbagai halaman BPS, lalu meneruskan byte PDF ke extractor tersebut.
Konektor PDF dijalankan untuk halaman HTML BPS secara umum, bukan hanya URL
press release. Dari halaman awal, konektor memeriksa tautan PDF langsung atau
menelusuri sejumlah kecil halaman BPS terkait yang judul/URL-nya cocok dengan
indikator; konektor tidak merayapi seluruh situs. Konektor tabel BPS tetap
menangani struktur tabel situs BPS. Pemilihan konektor mengikuti URL dan format
sumber; nama indikator dipakai untuk merangking kandidat PDF atau judul tabel.
Untuk listing BPS yang memuat hasil hanya setelah JavaScript berjalan,
konektor merender halaman dan menunggu tautan artikel/unduhan benar-benar
muncul sebelum mengambil PDF. PDF yang terhubung ke beberapa artikel relevan
diperiksa untuk tahun yang diminta, sehingga satu URL pencarian bisa melayani
PDF terpisah per tahun. Byte PDF yang sama dipakai ulang lintas tahun agar tidak
diunduh berulang. Kegagalan koneksi di-cache singkat per URL sehingga URL yang
sama tidak dicoba ulang untuk setiap tahun, tetapi PDF lain pada host yang sama
tetap dicoba.
Unduhan PDF terlebih dahulu memakai header minimal `User-Agent: Mozilla/5.0`,
sesuai downloader BPS lama yang terbukti bekerja; jika gagal, dicoba ulang
dengan `Referer` halaman artikel. Jika DNS sistem mengarahkan host BPS ke alamat
privat, downloader meminta DNS publik melalui HTTPS lalu menghubungkan ke IP
publik dengan nama host asli tetap digunakan untuk SNI, header `Host`, dan
validasi sertifikat TLS. Fallback ini dibatasi ke subdomain `bps.go.id`.
Fallback Chrome biasa (non-headless) membuka artikel dan mencoba klik tautan
unduhan sebelum membuka URL PDF secara langsung. Unduhan browser diberi waktu
hingga 180 detik agar file besar atau koneksi lambat tidak langsung dianggap
gagal.
Skor tabel PDF menggunakan judul/konteks halaman selain nama kolom, agar tabel
deret-waktu dengan kolom bernama tahun tetap dapat dicocokkan ke indikator.
Untuk URL katalog `/statistics-table`, adapter tabel membuka halaman detail
yang judulnya paling cocok lalu membaca payload tabel. Nilai tabel harus tetap
bisa dipetakan ke label wilayah dan tahun; angka lepas tanpa label tidak
dianggap sebagai hasil untuk banyak wilayah. Halaman detail dicoba melalui
payload RSC/Next.js, tabel HTML biasa, lalu fallback `value_raw`. Jika gagal,
diagnostik katalog dan detail BPS diteruskan ke ringkasan job.
Pada tabel BPS yang menyediakan pemilih tahun dinamis, adapter membaca daftar
tahun dan ID tahun dari halaman, lalu meminta data tahun yang dipilih melalui
server action Next.js yang ditemukan dari aset build halaman (tanpa ID aksi
yang di-hardcode).
Worker menggabungkan update progress agar tidak membanjiri penulisan status;
penyimpanan hasil meng-upsert dan mengurutkan data dalam satu batch untuk
mempersingkat waktu lock Apps Script.
Parser memilih data berdasarkan indikator, tahun, dan wilayah; PDF dengan
prioritas pemindaian halaman ditentukan dari kecocokan indikator, tahun, dan
wilayah. PDF digital tidak dirasterisasi untuk OCR; OCR hanya dipakai untuk
halaman scan yang tidak memiliki lapisan teks. Nama indikator yang jelas
biasanya cukup, tetapi deskripsi sumber sebaiknya menyebut label tabel atau
varian yang membedakan metrik (misalnya harga berlaku/konstan atau tahunan/
triwulanan). PDF dengan layout yang tidak dapat dibaca otomatis tetap perlu
pemeriksaan atau aturan parser tambahan.

## Modul Legacy

Modul source lama yang masih dipakai sudah dibundel di `legacy_indicators/`
(`utils`, `ojk`, `bi_i12`, `bi_i19`, `bps`, `brin`, `ibei`). Tidak perlu lagi
folder `Scraping_Indikator\indicators` eksternal — hasil clone di mesin mana
pun sudah lengkap.

`config.py` memakai folder bundel secara default. Jika ingin memakai folder
legacy eksternal, isi `LEGACY_INDICATORS_PATH` di `.env` dan pastikan foldernya
benar-benar ada.

## Menjalankan

Aktifkan virtual environment lalu:

```powershell
python main.py worker
```

Worker harus terus hidup agar job dari Apps Script diproses.

## OCR PDF Scan

PDF yang berisi gambar/scan memakai OCR sebagai fallback terakhir. Paket Python
`pytesseract` sudah tercantum di `requirements.txt`, tetapi Windows juga harus
memiliki binary Tesseract OCR dan bahasa Indonesia (`ind`) terpasang serta
tersedia di `PATH`. Atur `OCR_ENABLED=false` jika OCR tidak ingin digunakan.

Untuk tes satu URL tanpa Google Sheet:

```powershell
python main.py test `
  --nama "Indeks Contoh" `
  --tahun "2024" `
  --url "https://contoh.go.id/data.xlsx" `
  --ambil "Nilai indikator Prov. Kalimantan Selatan"
```

## Production nanti

Saat development stabil, worker bisa dipindah ke:
- VPS/service Windows/Linux; atau
- Cloud Run/VM.

Pada tahap itu Apps Script dapat memicu backend secara langsung atau queue tetap dipakai.
