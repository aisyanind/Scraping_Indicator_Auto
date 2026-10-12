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
4. Generic engine mencoba PDF / Excel / CSV / HTML / JSON.
5. Hasil ambigu menjadi REVIEW.
6. Dashboard kompleks yang gagal menjadi NEED_PARSER.
7. Result upsert memakai Kode + Wilayah + Tahun.

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
