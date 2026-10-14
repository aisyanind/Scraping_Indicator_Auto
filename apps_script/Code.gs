const CONFIG = {
  TOKEN: 'auto-scraper-dev-2026',
  MASTER_SHEET: 'Master_Indikator_Test',
  RESULT_SHEET: 'Hasil_Indikator_Test',
  JOB_SHEET: 'Scraping_Jobs_Test'
};

// Sheet Rekap di spreadsheet dashboard (katalog resmi indikator).
// Kolom A = kode, kolom B = nama, data mulai baris 4.
// Dropdown form diambil dari sini saja (bukan dari Master).
const REKAP_SHEET_ID =
  '1HQy4V3RyJsuEDAlZ_Qro2oGlCgO-fv3s';
const REKAP_SHEET_NAME = 'Rekap';
const REKAP_DATA_START_ROW = 4;

const WILAYAH_ORDER = [
  'Prov. Kalimantan Selatan',
  'Kab. Tanah Laut',
  'Kab. Kotabaru',
  'Kab. Banjar',
  'Kab. Barito Kuala',
  'Kab. Tapin',
  'Kab. Hulu Sungai Selatan',
  'Kab. Hulu Sungai Tengah',
  'Kab. Hulu Sungai Utara',
  'Kab. Tabalong',
  'Kab. Tanah Bumbu',
  'Kab. Balangan',
  'Kota Banjarmasin',
  'Kota Banjarbaru'
];

const INVALID_SCOPE_WILAYAH = [
  'Kab/Kota Kalimantan Selatan dan Prov. Kalimantan Selatan',
  'Kab/Kota di Kalsel dan Prov. Kalsel',
  'Seluruh Kab/Kota Kalimantan Selatan',
  'Provinsi + seluruh Kab/Kota Kalimantan Selatan'
];

const MASTER_HEADERS = [
  'Kode',
  'Nama_Indikator',
  'Tahun',
  'Wilayah',
  'Sumber_1',
  'Data_Diambil_1',
  'Sumber_2',
  'Data_Diambil_2',
  'Sumber_3',
  'Data_Diambil_3',
  'Rumus'
];

const RESULT_HEADERS = [
  'Kode',
  'Nama_Indikator',
  'Wilayah',
  'Tahun',
  'Nilai_S1',
  'Nilai_S2',
  'Nilai_S3',
  'Hasil',
  'Status',
  'Last_Update',
  'Error'
];

const JOB_HEADERS = [
  'Job_ID',
  'Master_Row',
  'Kode',
  'Status',
  'Progress',
  'Created_At',
  'Started_At',
  'Finished_At',
  'Error',
  'Diagnostics'
];


function doGet(e) {
  const action = String((e && e.parameter && e.parameter.action) || '').trim();

  if (!action) {
    return HtmlService
      .createTemplateFromFile('Index')
      .evaluate()
      .setTitle('Auto Scraping Indikator');
  }

  if (!isAuthorized_(e.parameter.token)) {
    return json_({success: false, error: 'Unauthorized'});
  }

  if (action === 'jobs') {
    return json_({
      success: true,
      data: getPendingJobs_()
    });
  }

  if (action === 'existing_results') {
    return json_({
      success: true,
      data: getSuccessfulResultsByKode_(e.parameter.kode)
    });
  }

  if (action === 'job_status') {
    return json_(
      getJobStatus_(e.parameter.job_id)
    );
  }

  if (action === 'heartbeat') {
    PropertiesService.getScriptProperties().setProperty(
      'worker_heartbeat',
      String(new Date().getTime())
    );
    return json_({success: true});
  }

  return json_({
    success: false,
    error: 'Unknown action'
  });
}


function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents || '{}');

    if (!isAuthorized_(body.token)) {
      return json_({success: false, error: 'Unauthorized'});
    }

    if (body.action === 'update_job') {
      return json_(
        updateJob_(
          body.job_id,
          body.status,
          body.progress,
          body.error,
          body.diagnostics
        )
      );
    }

    if (body.action === 'save_results') {
      return json_(
        saveResults_(body.results || [])
      );
    }

    if (body.action === 'cancel_job') {
      return json_(
        cancelJob(String(body.job_id || '').trim())
      );
    }

    if (body.action === 'delete_indicator') {
      return json_(
        deleteIndicator(String(body.kode || '').trim())
      );
    }

    return json_({
      success: false,
      error: 'Unknown action'
    });

  } catch (err) {
    return json_({
      success: false,
      error: String(err)
    });
  }
}


function setupDevSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const master = ensureSheet_(ss, CONFIG.MASTER_SHEET, MASTER_HEADERS);
  const result = ensureSheet_(ss, CONFIG.RESULT_SHEET, RESULT_HEADERS);
  const jobs = ensureSheet_(ss, CONFIG.JOB_SHEET, JOB_HEADERS);

  // Kode seperti "3.2" WAJIB disimpan sebagai teks.
  master.getRange('A:A').setNumberFormat('@');
  result.getRange('A:A').setNumberFormat('@');
  jobs.getRange('C:C').setNumberFormat('@');
}


function resolveWilayahForm_(form) {
  const scope = String(form.wilayah_scope || 'PROV').trim();

  if (scope === 'KABKOTA') {
    return {
      wilayah: 'Prov. Kalimantan Selatan',
      hint: 'Cakupan: seluruh Kab/Kota di Kalsel'
    };
  }

  if (scope === 'ALL') {
    return {
      wilayah: 'Prov. Kalimantan Selatan',
      hint: 'Cakupan: IDSD Kab/Kota di Kalsel dan Prov. Kalsel'
    };
  }

  if (scope === 'SPECIFIC') {
    const selected = String(form.wilayah_specific || '').trim();

    if (!WILAYAH_ORDER.includes(selected)) {
      throw new Error('Wilayah spesifik tidak valid.');
    }

    return {
      wilayah: selected,
      hint: ''
    };
  }

  return {
    wilayah: 'Prov. Kalimantan Selatan',
    hint: ''
  };
}


function appendScopeHint_(description, hint) {
  const text = String(description || '').trim();

  if (!hint) {
    return text;
  }

  if (!text) {
    return hint;
  }

  return `${text} | ${hint}`;
}


/**
 * Tulis ulang sel Kode sebagai teks murni.
 *
 * Bahkan setValues bisa ter-parse oleh Sheets (mis. "3.2" -> tanggal
 * 3 Februari, "1-1" -> 1 Januari) tergantung locale. Pola aman:
 * format sel diset '@' (Plain text) lebih dulu, nilai ditulis, lalu
 * diverifikasi; jika Sheets tetap mengubahnya menjadi Date/Number,
 * nilai ditulis ulang dengan tanda kutip awalan agar Sheets
 * menyimpannya sebagai teks apa adanya.
 */
function writeKodeAsText_(sheet, row, column, kode) {
  const expected = String(kode || '').trim();

  if (!expected) {
    return;
  }

  const cell = sheet.getRange(row, column);
  cell.setNumberFormat('@');
  cell.setValue(expected);
  SpreadsheetApp.flush();

  const written = cell.getValue();

  if (
    String(written).trim() === expected
    && Object.prototype.toString.call(written) !== '[object Date]'
  ) {
    return;
  }

  // Fallback terakhir: prefix apostrof memaksa mode teks.
  cell.setValue(`'${expected}`);
  SpreadsheetApp.flush();
}


function submitIndicator(form) {
  const lock = LockService.getScriptLock();

  // Maksimal menunggu 30 detik jika user lain sedang menulis.
  lock.waitLock(30000);

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    if (!String(form.tahun || '').trim()) {
      throw new Error('Rentang tahun belum dipilih.');
    }

    const master = ensureSheet_(
      ss,
      CONFIG.MASTER_SHEET,
      MASTER_HEADERS
    );

    const jobs = ensureSheet_(
      ss,
      CONFIG.JOB_SHEET,
      JOB_HEADERS
    );

    const wilayahInfo = resolveWilayahForm_(form);

    // Cegah kode seperti 3.2 diubah Google Sheets menjadi tanggal.
    master.getRange('A:A').setNumberFormat('@');
    jobs.getRange('C:C').setNumberFormat('@');

    // appendRow mem-parse teks seperti input manual (mis. "3.2" jadi
    // tanggal 3 Februari di locale Indonesia) dan setNumberFormat('@')
    // saja tidak bisa mencegahnya. Karena itu baris ditulis via
    // setValues, lalu sel Kode dipaksa teks oleh writeKodeAsText_.
    const masterRowNumber = master.getLastRow() + 1;

    master
      .getRange(masterRowNumber, 1, 1, MASTER_HEADERS.length)
      .setValues([[
        form.kode || '',
        form.nama_indikator || '',
        form.tahun || '',
        wilayahInfo.wilayah,
        form.sumber_1 || '',
        appendScopeHint_(form.data_diambil_1, wilayahInfo.hint),
        form.sumber_2 || '',
        appendScopeHint_(form.data_diambil_2, wilayahInfo.hint),
        form.sumber_3 || '',
        appendScopeHint_(form.data_diambil_3, wilayahInfo.hint),
        form.rumus || 'S1'
      ]]);

    writeKodeAsText_(master, masterRowNumber, 1, form.kode);

    SpreadsheetApp.flush();

    const jobId = Utilities.getUuid();
    const now = new Date();
    const jobsRowNumber = jobs.getLastRow() + 1;

    jobs
      .getRange(jobsRowNumber, 1, 1, JOB_HEADERS.length)
      .setValues([[
        jobId,
        masterRowNumber,
        form.kode || '',
        'PENDING',
        0,
        now,
        '',
        '',
        '',
        ''
      ]]);

    writeKodeAsText_(jobs, jobsRowNumber, 3, form.kode);

    SpreadsheetApp.flush();

    // Master bertambah — katalog indikator perlu dibaca ulang.
    try {
      CacheService.getScriptCache().remove(CATALOG_CACHE_KEY);
    } catch (e) {
      // cache best-effort
    }

    return {
      success: true,
      job_id: jobId,
      status: 'PENDING',
      wilayah: wilayahInfo.wilayah,
      cakupan: form.wilayah_scope || 'PROV'
    };

  } finally {
    // Selalu lepaskan lock, termasuk saat terjadi error.
    lock.releaseLock();
  }
}


function getPendingJobs_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const jobs = ensureSheet_(
    ss,
    CONFIG.JOB_SHEET,
    JOB_HEADERS
  );

  const master = ensureSheet_(
    ss,
    CONFIG.MASTER_SHEET,
    MASTER_HEADERS
  );

  const jobValues = jobs.getDataRange().getValues();
  const masterValues = master.getDataRange().getValues();

  if (jobValues.length <= 1) {
    return [];
  }

  const output = [];

  for (let i = 1; i < jobValues.length; i++) {
    const row = objectFromRow_(JOB_HEADERS, jobValues[i]);

    if (String(row.Status).toUpperCase() !== 'PENDING') {
      continue;
    }

    const masterRow = Number(row.Master_Row);

    if (!masterRow || masterRow < 2 || masterRow > masterValues.length) {
      continue;
    }

    const masterObject = objectFromRow_(
      MASTER_HEADERS,
      masterValues[masterRow - 1]
    );

    output.push(
      Object.assign({}, masterObject, row)
    );
  }

  return output;
}



function cancelJob(jobId) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ensureSheet_(
      ss,
      CONFIG.JOB_SHEET,
      JOB_HEADERS
    );

    const values = sheet.getDataRange().getValues();
    const now = new Date();

    for (let i = 1; i < values.length; i++) {
      if (String(values[i][0]) !== String(jobId)) {
        continue;
      }

      const row = i + 1;
      const currentStatus = String(
        values[i][3] || ''
      ).toUpperCase();

      if (currentStatus === 'PENDING') {
        sheet.getRange(row, 4).setValue('CANCELLED');
        sheet.getRange(row, 8).setValue(now);
        sheet.getRange(row, 9).setValue('Dibatalkan pengguna sebelum diproses.');
        SpreadsheetApp.flush();

        return {
          success: true,
          status: 'CANCELLED'
        };
      }

      if (currentStatus === 'PROCESSING') {
        sheet.getRange(row, 4).setValue('CANCEL_REQUESTED');
        sheet.getRange(row, 9).setValue('Permintaan pembatalan diterima.');
        SpreadsheetApp.flush();

        return {
          success: true,
          status: 'CANCEL_REQUESTED'
        };
      }

      if (currentStatus === 'CANCEL_REQUESTED') {
        return {
          success: true,
          status: 'CANCEL_REQUESTED'
        };
      }

      if (currentStatus === 'CANCELLED') {
        return {
          success: true,
          status: 'CANCELLED'
        };
      }

      return {
        success: false,
        error: `Job sudah berstatus ${currentStatus} dan tidak dapat dibatalkan.`
      };
    }

    return {
      success: false,
      error: 'Job_ID tidak ditemukan'
    };

  } finally {
    lock.releaseLock();
  }
}


function deleteIndicator(kode) {
  if (!String(kode || '').trim()) {
    return {success: false, error: 'kode wajib diisi'};
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    const master = ensureSheet_(
      ss,
      CONFIG.MASTER_SHEET,
      MASTER_HEADERS
    );

    const result = ensureSheet_(
      ss,
      CONFIG.RESULT_SHEET,
      RESULT_HEADERS
    );

    const jobs = ensureSheet_(
      ss,
      CONFIG.JOB_SHEET,
      JOB_HEADERS
    );

    // Pastikan Kode dibaca sebagai teks (3.2 jangan jadi tanggal).
    master.getRange('A:A').setNumberFormat('@');
    result.getRange('A:A').setNumberFormat('@');
    jobs.getRange('C:C').setNumberFormat('@');

    const target = String(kode).trim();
    let deletedMaster = 0;
    let deletedResult = 0;
    let deletedJobs = 0;

    // Hapus baris dari bawah ke atas agar nomor baris tidak bergeser.
    const removeMatching = (sheet, columnIndex) => {
      const values = sheet.getDataRange().getValues();
      let removed = 0;

      for (let i = values.length - 1; i >= 1; i--) {
        const cell = String(values[i][columnIndex] || '').trim();

        if (cell === target) {
          sheet.deleteRow(i + 1);
          removed++;
        }
      }

      return removed;
    };

    deletedMaster = removeMatching(master, 0);
    deletedResult = removeMatching(result, 0);
    deletedJobs = removeMatching(jobs, 2);

    try {
      CacheService.getScriptCache().remove(CATALOG_CACHE_KEY);
    } catch (e) {
      // cache best-effort
    }

    return {
      success: true,
      kode: target,
      deleted_master: deletedMaster,
      deleted_result: deletedResult,
      deleted_jobs: deletedJobs
    };

  } finally {
    lock.releaseLock();
  }
}


function getJobStatus_(jobId) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const sheet = ensureSheet_(
    ss,
    CONFIG.JOB_SHEET,
    JOB_HEADERS
  );

  const values = sheet.getDataRange().getValues();

  for (let i = 1; i < values.length; i++) {
    if (String(values[i][0]) !== String(jobId)) {
      continue;
    }

    return {
      success: true,
      job_id: String(jobId),
      status: String(values[i][3] || ''),
      progress: values[i][4],
      error: String(values[i][8] || '')
    };
  }

  return {
    success: false,
    error: 'Job_ID tidak ditemukan'
  };
}


function updateJob_(jobId, status, progress, error, diagnostics) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    const sheet = ensureSheet_(
      ss,
      CONFIG.JOB_SHEET,
      JOB_HEADERS
    );

    const values = sheet.getDataRange().getValues();
    const now = new Date();

    for (let i = 1; i < values.length; i++) {
      if (String(values[i][0]) !== String(jobId)) {
        continue;
      }

      const row = i + 1;
      const currentStatus = String(values[i][3] || '').toUpperCase();
      const incomingStatus = String(status || '').toUpperCase();

      // Jangan biarkan proses lain menimpa permintaan cancel.
      if (
        ['CANCEL_REQUESTED', 'CANCELLED'].includes(currentStatus)
        && incomingStatus !== 'CANCELLED'
      ) {
        return {
          success: true,
          ignored_due_to_cancel: true,
          status: currentStatus
        };
      }

      sheet.getRange(row, 4).setValue(status || '');
      sheet.getRange(row, 5).setValue(
        progress == null ? '' : progress
      );

      if (
        String(status).toUpperCase() === 'PROCESSING'
        && !values[i][6]
      ) {
        sheet.getRange(row, 7).setValue(now);
      }

      if (
        [
          'SUCCESS',
          'PARTIAL',
          'REVIEW',
          'NEED_PARSER',
          'FAILED',
          'CANCELLED'
        ].includes(String(status).toUpperCase())
      ) {
        sheet.getRange(row, 8).setValue(now);
      }

      sheet.getRange(row, 9).setValue(error || '');

      if (diagnostics) {
        sheet.getRange(row, 10).setValue(
          JSON.stringify(diagnostics)
        );
      }

      SpreadsheetApp.flush();

      return {success: true};
    }

    return {
      success: false,
      error: 'Job_ID tidak ditemukan'
    };

  } finally {
    lock.releaseLock();
  }
}


function getSuccessfulResultsByKode_(kode) {
  const targetKode = String(kode || '').trim();

  if (!targetKode) {
    return [];
  }

  const sheet = ensureSheet_(
    SpreadsheetApp.getActiveSpreadsheet(),
    CONFIG.RESULT_SHEET,
    RESULT_HEADERS
  );
  const lastRow = sheet.getLastRow();

  if (lastRow <= 1) {
    return [];
  }

  return sheet
    .getRange(2, 1, lastRow - 1, RESULT_HEADERS.length)
    .getDisplayValues()
    .map(row => objectFromRow_(RESULT_HEADERS, row))
    .filter(row => (
      String(row.Kode || '').trim() === targetKode
      && String(row.Status || '').trim().toUpperCase() === 'SUCCESS'
    ));
}


function saveResults_(results) {
  const lock = LockService.getScriptLock();

  // Mencegah dua proses menulis/upsert/sort hasil pada saat yang sama.
  lock.waitLock(30000);

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    const sheet = ensureSheet_(
      ss,
      CONFIG.RESULT_SHEET,
      RESULT_HEADERS
    );

    // Cegah kode seperti 3.2 diubah menjadi tanggal.
    sheet.getRange('A:A').setNumberFormat('@');

    const values = sheet.getDataRange().getValues();
    const keyToRow = {};

    for (let i = 1; i < values.length; i++) {
      const kode = String(values[i][0] || '').trim();
      const wilayah = normalizeRegion_(values[i][2]);
      const tahun = String(values[i][3] || '').trim();

      if (!kode || !tahun) {
        continue;
      }

      keyToRow[
        `${kode}|||${wilayah}|||${tahun}`
      ] = i + 1;
    }

    let inserted = 0;
    let updated = 0;
    const now = new Date();

    results.forEach(item => {
      if (String(item.status).toUpperCase() !== 'SUCCESS') {
        return;
      }

      const kode = String(item.kode || '').trim();
      const wilayah = String(item.wilayah || '').trim();
      const tahun = String(item.tahun || '').trim();

      const key = (
        `${kode}|||${normalizeRegion_(wilayah)}|||${tahun}`
      );

      const rowData = [
        kode,
        item.nama_indikator || '',
        wilayah,
        item.tahun || '',
        valueOrBlank_(item.nilai_s1),
        valueOrBlank_(item.nilai_s2),
        valueOrBlank_(item.nilai_s3),
        valueOrBlank_(item.hasil),
        'SUCCESS',
        now,
        item.error || ''
      ];

      if (keyToRow[key]) {
        sheet
          .getRange(
            keyToRow[key],
            1,
            1,
            RESULT_HEADERS.length
          )
          .setValues([rowData]);

        updated++;

      } else {
        const newRowIndex = sheet.getLastRow() + 1;

        sheet
          .getRange(newRowIndex, 1, 1, RESULT_HEADERS.length)
          .setValues([rowData]);

        writeKodeAsText_(sheet, newRowIndex, 1, kode);

        // flush agar getLastRow() benar-benar menunjuk row baru
        SpreadsheetApp.flush();

        keyToRow[key] = newRowIndex;
        inserted++;
      }
    });

    // Sorting dilakukan masih di dalam lock.
    sortResults_();

    SpreadsheetApp.flush();

    return {
      success: true,
      total: results.length,
      inserted: inserted,
      updated: updated
    };

  } finally {
    lock.releaseLock();
  }
}


function sortResults_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const sheet = ensureSheet_(
    ss,
    CONFIG.RESULT_SHEET,
    RESULT_HEADERS
  );

  const lastRow = sheet.getLastRow();

  if (lastRow <= 2) {
    return;
  }

  const range = sheet.getRange(
    2,
    1,
    lastRow - 1,
    RESULT_HEADERS.length
  );

  const rows = range.getValues();

  const orderMap = {};
  WILAYAH_ORDER.forEach((name, index) => {
    orderMap[normalizeRegion_(name)] = index;
  });

  rows.sort((a, b) => {
    const kodeA = String(a[0] || '').trim();
    const kodeB = String(b[0] || '').trim();

    const kodeCompare = kodeA.localeCompare(
      kodeB,
      'id',
      {numeric: true, sensitivity: 'base'}
    );

    if (kodeCompare !== 0) {
      return kodeCompare;
    }

    const wilayahA = normalizeRegion_(a[2]);
    const wilayahB = normalizeRegion_(b[2]);

    const orderA = Object.prototype.hasOwnProperty.call(
      orderMap,
      wilayahA
    ) ? orderMap[wilayahA] : 9999;

    const orderB = Object.prototype.hasOwnProperty.call(
      orderMap,
      wilayahB
    ) ? orderMap[wilayahB] : 9999;

    if (orderA !== orderB) {
      return orderA - orderB;
    }

    if (orderA === 9999) {
      const regionCompare = wilayahA.localeCompare(
        wilayahB,
        'id',
        {sensitivity: 'base'}
      );

      if (regionCompare !== 0) {
        return regionCompare;
      }
    }

    const tahunA = Number(a[3]) || 0;
    const tahunB = Number(b[3]) || 0;

    return tahunA - tahunB;
  });

  range.setValues(rows);
}


function isInvalidScopeWilayah_(value) {
  const normalized = normalizeRegion_(value);

  return INVALID_SCOPE_WILAYAH.some(
    item => normalizeRegion_(item) === normalized
  );
}


function sortResultObjects_(rows) {
  const orderMap = {};

  WILAYAH_ORDER.forEach((name, index) => {
    orderMap[normalizeRegion_(name)] = index;
  });

  rows.sort((a, b) => {
    const kodeA = String(a.Kode || '').trim();
    const kodeB = String(b.Kode || '').trim();

    const kodeCompare = kodeA.localeCompare(
      kodeB,
      'id',
      {numeric: true, sensitivity: 'base'}
    );

    if (kodeCompare !== 0) {
      return kodeCompare;
    }

    const wilayahA = normalizeRegion_(a.Wilayah);
    const wilayahB = normalizeRegion_(b.Wilayah);

    const orderA = Object.prototype.hasOwnProperty.call(
      orderMap,
      wilayahA
    ) ? orderMap[wilayahA] : 9999;

    const orderB = Object.prototype.hasOwnProperty.call(
      orderMap,
      wilayahB
    ) ? orderMap[wilayahB] : 9999;

    if (orderA !== orderB) {
      return orderA - orderB;
    }

    if (orderA === 9999) {
      const wilayahCompare = wilayahA.localeCompare(
        wilayahB,
        'id',
        {sensitivity: 'base'}
      );

      if (wilayahCompare !== 0) {
        return wilayahCompare;
      }
    }

    const tahunA = Number(a.Tahun) || 0;
    const tahunB = Number(b.Tahun) || 0;

    return tahunA - tahunB;
  });

  return rows;
}


/**
 * Jalankan SEKALI jika ingin menghapus baris lama yang kolom Wilayah-nya
 * berisi label cakupan, bukan nama wilayah sebenarnya.
 *
 * Hanya menghapus label pseudo-wilayah yang ada di INVALID_SCOPE_WILAYAH.
 */
function cleanupInvalidScopeRows() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const sheet = ensureSheet_(
    ss,
    CONFIG.RESULT_SHEET,
    RESULT_HEADERS
  );

  const values = sheet.getDataRange().getValues();
  let deleted = 0;

  for (let i = values.length - 1; i >= 1; i--) {
    const wilayah = values[i][2];

    if (isInvalidScopeWilayah_(wilayah)) {
      sheet.deleteRow(i + 1);
      deleted++;
    }
  }

  sortResults_();

  return {
    success: true,
    deleted: deleted
  };
}


/**
 * Jalankan SEKALI untuk memastikan kolom Kode ke depan bertipe Plain text
 * dan merapikan urutan hasil yang sudah ada.
 *
 * Catatan: kode yang SUDAH telanjur berubah menjadi tanggal tidak dapat
 * ditebak kembali secara universal. Untuk data DEV tersebut sebaiknya
 * hapus/resubmit baris indikator yang korup.
 */
function repairResultSheetFormat() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const master = ensureSheet_(ss, CONFIG.MASTER_SHEET, MASTER_HEADERS);
  const result = ensureSheet_(ss, CONFIG.RESULT_SHEET, RESULT_HEADERS);
  const jobs = ensureSheet_(ss, CONFIG.JOB_SHEET, JOB_HEADERS);

  master.getRange('A:A').setNumberFormat('@');
  result.getRange('A:A').setNumberFormat('@');
  jobs.getRange('C:C').setNumberFormat('@');

  sortResults_();

  return {
    success: true,
    message: 'Format Kode diset Plain text dan hasil diurutkan.'
  };
}


function getDashboardData(filterKode) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  const resultSheet = ensureSheet_(
    ss,
    CONFIG.RESULT_SHEET,
    RESULT_HEADERS
  );

  const jobSheet = ensureSheet_(
    ss,
    CONFIG.JOB_SHEET,
    JOB_HEADERS
  );

  const filter = String(filterKode || '')
    .trim()
    .toLowerCase();

  let results = [];

  if (resultSheet.getLastRow() > 1) {
    const values = resultSheet
      .getRange(
        2,
        1,
        resultSheet.getLastRow() - 1,
        RESULT_HEADERS.length
      )
      .getDisplayValues();

    results = values
      .map(row => objectFromRow_(RESULT_HEADERS, row))
      // Jangan tampilkan label cakupan sebagai wilayah.
      .filter(item => !isInvalidScopeWilayah_(item.Wilayah));

    if (filter) {
      results = results.filter(item =>
        String(item.Kode || '')
          .toLowerCase()
          .includes(filter)
        ||
        String(item.Nama_Indikator || '')
          .toLowerCase()
          .includes(filter)
      );
    }

    // Dashboard SELALU diurutkan ulang saat dibaca:
    // Kode -> Wilayah -> Tahun.
    // Jadi tidak tergantung urutan fisik baris di Spreadsheet.
    sortResultObjects_(results);
  }

  let jobs = [];

  if (jobSheet.getLastRow() > 1) {
    const jobValues = jobSheet
      .getRange(
        2,
        1,
        jobSheet.getLastRow() - 1,
        JOB_HEADERS.length
      )
      .getDisplayValues();

    jobs = jobValues
      .map(row => objectFromRow_(JOB_HEADERS, row))
      .reverse()
      .slice(0, 10);
  }

  const lastHeartbeat = PropertiesService.getScriptProperties().getProperty('worker_heartbeat') || '0';
  const heartbeatTime = parseInt(lastHeartbeat, 10);
  const now = new Date().getTime();
  const secondsSince = Math.floor((now - heartbeatTime) / 1000);

  return {
    success: true,
    results: results,
    jobs: jobs,
    total_results: results.length,
    last_refresh: Utilities.formatDate(
      new Date(),
      Session.getScriptTimeZone(),
      'dd/MM/yyyy HH:mm:ss'
    ),
    worker_alive: secondsSince < 30,
    worker_heartbeat_ts: secondsSince
  };
}


/* =====================================================
   KATALOG INDIKATOR & SUMBER (untuk dropdown form)
===================================================== */

const CATALOG_CACHE_KEY = 'indicator_catalog_v1';
const CATALOG_CACHE_SECONDS = 300;

function isIndicatorCode_(value) {
  if (value === null || value === undefined) return false;
  const text = String(value).trim();
  if (!text) return false;
  const clean = text.replace(/\.$/, '');
  if (!/[a-zA-Z0-9]/.test(clean)) return false;
  return /^[a-zA-Z0-9.-]+$/.test(text);
}

function readMasterObjects_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const master = ensureSheet_(ss, CONFIG.MASTER_SHEET, MASTER_HEADERS);

  if (master.getLastRow() < 2) {
    return [];
  }

  // Kode sering berubah jadi tanggal; paksa baca sebagai teks.
  master.getRange('A:A').setNumberFormat('@');

  const values = master
    .getRange(2, 1, master.getLastRow() - 1, MASTER_HEADERS.length)
    .getDisplayValues();

  return values.map(row => objectFromRow_(MASTER_HEADERS, row));
}

function readRekapIndicators_() {
  let list = [];

  try {
    const ss = SpreadsheetApp.openById(REKAP_SHEET_ID);
    const sheet = ss.getSheetByName(REKAP_SHEET_NAME);

    if (!sheet || sheet.getLastRow() < REKAP_DATA_START_ROW) {
      return list;
    }

    const lastRow = sheet.getLastRow();
    const values = sheet
      .getRange(REKAP_DATA_START_ROW, 1, lastRow - REKAP_DATA_START_ROW + 1, 2)
      .getDisplayValues();

    for (let i = 0; i < values.length; i++) {
      const kode = String(values[i][0] || '').trim();
      const nama = String(values[i][1] || '').trim();

      if (!isIndicatorCode_(kode)) continue;

      list.push({kode: kode, nama: nama || kode});
    }
  } catch (error) {
    // Rekap gagal dibaca — kembalikan daftar kosong, jangan crash form.
    console.warn('Gagal membaca Rekap: ' + error);
  }

  return list;
}

/**
 * Katalog indikator untuk dropdown form:
 * diambil HANYA dari sheet Rekap (katalog resmi), dedupe by kode.
 * Master tidak dipakai untuk dropdown — Master hanya log riwayat
 * submit (bisa berisi salah ketik); Rekap adalah daftar resmi.
 */
function getIndicatorCatalog() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get(CATALOG_CACHE_KEY);

  if (cached) {
    try {
      return JSON.parse(cached);
    } catch (e) {
      // lanjut baca ulang
    }
  }

  const byKode = {};
  const rekapList = readRekapIndicators_();

  for (let i = 0; i < rekapList.length; i++) {
    const item = rekapList[i];
    if (!byKode[item.kode]) {
      byKode[item.kode] = {kode: item.kode, nama: item.nama};
    }
  }

  const indicators = Object.keys(byKode).map(k => byKode[k]);

  indicators.sort((a, b) =>
    a.kode.localeCompare(b.kode, 'id', {numeric: true, sensitivity: 'base'})
  );

  const payload = {success: true, indicators: indicators};

  try {
    cache.put(CATALOG_CACHE_KEY, JSON.stringify(payload), CATALOG_CACHE_SECONDS);
  } catch (e) {
    // cache best-effort
  }

  return payload;
}

function normalizeSourceUrl_(url) {
  return String(url || '')
    .trim()
    .replace(/\/+$/, '')
    .toLowerCase();
}

function stripScopeHint_(text) {
  return String(text || '').replace(/\s*\|\s*Cakupan:\s*.*$/i, '').trim();
}

/**
 * Riwayat sumber (URL + deskripsi) untuk satu kode indikator.
 * Dipakai datalist di form; user tetap bisa ketik link baru.
 */
function getSourceCatalog(kode) {
  const target = String(kode || '').trim();

  if (!target) {
    return {success: true, kode: '', sources: []};
  }

  const rows = readMasterObjects_();
  const byUrl = {};

  // Iterasi dari bawah: baris terbaru menang untuk deskripsi.
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    const rowKode = String(row.Kode || '').trim();
    if (rowKode !== target) continue;

    for (let slot = 1; slot <= 3; slot++) {
      const url = String(row['Sumber_' + slot] || '').trim();
      if (!url) continue;

      const key = normalizeSourceUrl_(url);
      if (!key || byUrl[key]) continue;

      byUrl[key] = {
        url: url,
        desc: stripScopeHint_(row['Data_Diambil_' + slot] || '')
      };
    }
  }

  const sources = Object.keys(byUrl).map(k => byUrl[k]);

  return {success: true, kode: target, sources: sources};
}


/**
 * Ambil input submission terakhir untuk satu kode indikator.
 * Dipakai tombol "Scrap Lagi" untuk mengisi ulang form.
 */
function getLatestMasterInput(kode) {
  const target = String(kode || '').trim();
  if (!target) {
    return {success: false, error: 'Kode kosong.'};
  }

  const rows = readMasterObjects_();

  // Iterasi dari bawah: baris terbaru menang.
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    if (String(row.Kode || '').trim() !== target) continue;

    return {
      success: true,
      kode: target,
      nama: String(row.Nama_Indikator || '').trim(),
      tahun: String(row.Tahun || '').trim(),
      wilayah: String(row.Wilayah || '').trim(),
      sumber_1: String(row.Sumber_1 || '').trim(),
      sumber_2: String(row.Sumber_2 || '').trim(),
      sumber_3: String(row.Sumber_3 || '').trim(),
      rumus: String(row.Rumus || 'S1').trim()
    };
  }

  return {success: false, error: 'Belum ada submission untuk kode ' + target + '.'};
}


function ensureSheet_(ss, name, headers) {
  let sheet = ss.getSheetByName(name);

  if (!sheet) {
    sheet = ss.insertSheet(name);
  }

  if (sheet.getLastRow() === 0) {
    sheet.getRange(
      1,
      1,
      1,
      headers.length
    ).setValues([headers]);
  }

  return sheet;
}


function objectFromRow_(headers, values) {
  const obj = {};

  headers.forEach((header, index) => {
    obj[header] = values[index];
  });

  return obj;
}


function normalizeRegion_(value) {
  return String(value || '')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase();
}


function valueOrBlank_(value) {
  return (
    value === null
    || value === undefined
  ) ? '' : value;
}


function isAuthorized_(token) {
  return String(token || '') === String(CONFIG.TOKEN);
}


function json_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
