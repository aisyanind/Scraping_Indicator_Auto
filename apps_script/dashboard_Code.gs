function doGet() {
  return HtmlService
    .createHtmlOutputFromFile('index')
    .setTitle('Dashboard Data Indikator');
}


/* =====================================================
   KONFIGURASI
===================================================== */

const SHEET_NAME = 'Rekap';

/* -----------------------------------------------------
   SUMBER DATA UTAMA (spreadsheet ASLI).
   Isi ID atau URL penuh. Bila dikosongkan, fallback ke
   spreadsheet tempat script ini terpasang.
----------------------------------------------------- */
const DATA_SHEET_ID =
  '1HQy4V3RyJsuEDAlZ_Qro2oGlCgO-fv3s';

const DEFINITION_SHEET_ID =
  '1iDl1cN8kd_gGZhk--9j6j1La23ivCBNdUJzXVw60fto';

const DEFINITION_SHEET_TAB = 'Definisi';

const HEADER_ROW = 2;
const YEAR_ROW = 3;
const DATA_START_ROW = 4;
const DATA_START_COL = 7;


/* =====================================================
   KONFIGURASI PEMENUHAN
===================================================== */

const FULFILLMENT_START_YEAR = 2019;
const FULFILLMENT_END_YEAR = 2025;

const PROVINCE_NAME =
  'Kalimantan Selatan';


/* =====================================================
   AKSES PIC
   -----------------------------------------------------
   Sheet:
   Akses PIC

   A1 = Kode Akses
   A2 = kode tim
===================================================== */

const PIC_ACCESS_SHEET_NAME =
  'Akses PIC';

const PIC_ACCESS_CODE_CELL =
  'A2';


/* =====================================================
   AMBIL SPREADSHEET SUMBER DATA
   -----------------------------------------------------
   Pakai DATA_SHEET_ID bila diisi; kalau kosong, pakai
   spreadsheet aktif (tempat script terpasang).
   Menerima ID polos maupun URL penuh.
===================================================== */

function getDataSpreadsheet() {

  var id = String(DATA_SHEET_ID || '').trim();

  if (!id) {
    return SpreadsheetApp.getActiveSpreadsheet();
  }

  var match = id.match(/\/d\/([a-zA-Z0-9_-]+)/);

  return SpreadsheetApp.openById(
    match ? match[1] : id
  );

}


/* =====================================================
   VALIDASI KODE PIC
===================================================== */

function validatePICAccess(accessCode) {

  var submittedCode =
    String(accessCode || '')
      .trim();

  if (!submittedCode) {
    return false;
  }


  var sheet =
    getDataSpreadsheet()
      .getSheetByName(
        PIC_ACCESS_SHEET_NAME
      );


  if (!sheet) {

    throw new Error(
      'Sheet "' +
      PIC_ACCESS_SHEET_NAME +
      '" tidak ditemukan.'
    );

  }


  var storedCode =
    String(
      sheet
        .getRange(
          PIC_ACCESS_CODE_CELL
        )
        .getDisplayValue() || ''
    ).trim();


  if (!storedCode) {

    throw new Error(
      'Kode akses PIC belum diisi pada ' +
      PIC_ACCESS_SHEET_NAME +
      '!' +
      PIC_ACCESS_CODE_CELL
    );

  }


  return (
    submittedCode ===
    storedCode
  );

}


/* =====================================================
   CEK AKSES PIC
===================================================== */

function checkPICAccess(accessCode) {

  return {
    success:
      validatePICAccess(
        accessCode
      )
  };

}


/* =====================================================
   DATA PIC
===================================================== */

function getPICData(accessCode) {

  if (
    !validatePICAccess(
      accessCode
    )
  ) {

    throw new Error(
      'Kode akses PIC tidak valid.'
    );

  }


  var sheet =
    getDataSpreadsheet()
      .getSheetByName(
        SHEET_NAME
      );


  if (!sheet) {

    throw new Error(
      'Sheet "' +
      SHEET_NAME +
      '" tidak ditemukan.'
    );

  }


  var lastRow =
    sheet.getLastRow();

  var lastCol =
    sheet.getLastColumn();


  if (
    lastRow <
    DATA_START_ROW
  ) {

    return {
      pic2026: [],
      indicators: []
    };

  }


  var headers =
    sheet
      .getRange(
        HEADER_ROW,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var pic2026Col =
    findColumn(
      headers,
      'PIC 2026'
    );


  if (pic2026Col < 0) {

    throw new Error(
      'Kolom "PIC 2026" tidak ditemukan.'
    );

  }


  var data =
    sheet
      .getRange(
        DATA_START_ROW,
        1,
        lastRow -
          DATA_START_ROW +
          1,
        pic2026Col + 1
      )
      .getValues();


  var indicators = [];


  for (
    var i = 0;
    i < data.length;
    i++
  ) {

    var code =
      data[i][0];


    if (
      !isIndicatorCode(
        code
      )
    ) {
      continue;
    }


    indicators.push({

      row:
        DATA_START_ROW + i,

      code:
        String(code).trim(),

      name:
        data[i][1] ||
        '(Belum ada nama)',

      pic2026:
        getValue(
          data[i],
          pic2026Col
        )

    });

  }


  return {

    pic2026:
      uniqueValues(
        indicators.map(
          function(item) {
            return item.pic2026;
          }
        )
      ),

    indicators:
      indicators

  };

}


/* =====================================================
   HITUNG "DATA TERISI" UMUM
   -----------------------------------------------------
   BERBEDA dengan Pemenuhan PIC.

   Yang dihitung sebagai kebutuhan:
   1. Sumber Data (Metadata)
   2. Coverage Terkecil
   3. Frekuensi
   4. Sumber Data/Kegiatan
   5. Seluruh cell data tahun yang terdeteksi
      untuk seluruh kab/kota + provinsi.

   Coverage Terkecil TIDAK memfilter perhitungan ini.
   Jadi coverage kosong tetap dihitung sebagai metadata
   yang belum terisi.
===================================================== */

function calculateGeneralDataCompleteness(
  dataRow,
  regionByCol,
  metadataValues
) {

  var filledCells = 0;
  var totalCells = 0;

  metadataValues =
    metadataValues || [];


  /* =====================================================
     EMPAT FIELD METADATA WAJIB
  ===================================================== */

  for (
    var m = 0;
    m < metadataValues.length;
    m++
  ) {

    totalCells++;

    if (
      isFilledValue(
        metadataValues[m]
      )
    ) {
      filledCells++;
    }

  }


  /* =====================================================
     SEMUA CELL WILAYAH x TAHUN
     -----------------------------------------------------
     Tidak dibatasi Coverage Terkecil.
     Tidak dibatasi 2019-2025.
     Semua tahun valid yang ada di row tahun ikut dihitung.
  ===================================================== */

  for (
    var i = 0;
    i < regionByCol.length;
    i++
  ) {

    var meta =
      regionByCol[i];


    if (
      !meta ||
      !meta.region ||
      meta.year === null
    ) {
      continue;
    }


    totalCells++;


    if (
      isFilledValue(
        dataRow[i]
      )
    ) {
      filledCells++;
    }

  }


  var percentage =
    totalCells > 0
      ? (
          filledCells /
          totalCells
        ) * 100
      : 0;


  return {

    filledCells:
      filledCells,

    totalCells:
      totalCells,

    missingCells:
      Math.max(
        0,
        totalCells -
        filledCells
      ),

    percentage:
      percentage

  };

}


/* =====================================================
   HITUNG PEMENUHAN BERDASARKAN COVERAGE
===================================================== */

/* =====================================================
   HITUNG PEMENUHAN BERDASARKAN COVERAGE TERKECIL
   -----------------------------------------------------
   ATURAN:
   1. Coverage kosong       -> tidak dihitung (countable: false)
   2. Coverage Provinsi     -> hanya Kalimantan Selatan
   3. Coverage Kab/Kota     -> semua kab/kota + Kalimantan Selatan
   4. Tahun yang dihitung   -> 2019-2025
   ===================================================== */

function calculateCoverageFulfillment(
  dataRow,
  regionByCol,
  configuredCoverage
) {

  var normalizedCoverage =
    normalizeCoverage(configuredCoverage);

  /* =====================================================
     COVERAGE KOSONG -> TIDAK DIHITUNG
  ===================================================== */

  if (!normalizedCoverage) {
    return {
      configuredCoverage: configuredCoverage ? String(configuredCoverage).trim() : '',
      effectiveCoverage: '',
      coverageSource: 'tidak dihitung',
      countable: false,
      yearStart: FULFILLMENT_START_YEAR,
      yearEnd: FULFILLMENT_END_YEAR,
      filledCells: 0,
      totalCells: 0,
      missingCells: 0,
      percentage: null,
      additionalData: {
        province: { filled: 0, total: 0 },
        kabKota: { filled: 0, total: 0 }
      }
    };
  }

  var filledCells = 0;
  var totalCells = 0;

  var provinceFilled = 0;
  var provinceTotal = 0;

  var kabKotaFilled = 0;
  var kabKotaTotal = 0;

  for (var i = 0; i < regionByCol.length; i++) {

    var meta = regionByCol[i];

    if (!meta || !meta.region || meta.year === null) {
      continue;
    }

    if (meta.year < FULFILLMENT_START_YEAR || meta.year > FULFILLMENT_END_YEAR) {
      continue;
    }

    var value = dataRow[i];
    var filled = isFilledValue(value);
    var isProvince = isProvinceRegion(meta.region);

    if (isProvince) {
      provinceTotal++;
      if (filled) provinceFilled++;
    } else {
      kabKotaTotal++;
      if (filled) kabKotaFilled++;
    }

    var include = false;

    if (normalizedCoverage === 'Provinsi') {
      include = isProvince;
    } else if (normalizedCoverage === 'Kab/Kota') {
      include = true;
    }

    if (!include) continue;

    totalCells++;
    if (filled) filledCells++;
  }

  var percentage = totalCells > 0 ? (filledCells / totalCells) * 100 : 0;

  return {
    configuredCoverage: configuredCoverage ? String(configuredCoverage).trim() : '',
    effectiveCoverage: normalizedCoverage,
    coverageSource: 'metadata',
    countable: true,
    yearStart: FULFILLMENT_START_YEAR,
    yearEnd: FULFILLMENT_END_YEAR,
    filledCells: filledCells,
    totalCells: totalCells,
    missingCells: Math.max(0, totalCells - filledCells),
    percentage: percentage,
    additionalData: {
      province: { filled: provinceFilled, total: provinceTotal },
      kabKota: { filled: kabKotaFilled, total: kabKotaTotal }
    }
  };
}


/* =====================================================
   NORMALISASI COVERAGE
===================================================== */

function normalizeCoverage(value) {

  var text =
    String(value || '')
      .trim()
      .toLowerCase();


  if (!text) {
    return '';
  }


  var compact =
    text
      .replace(/\s+/g, '')
      .replace(/-/g, '')
      .replace(/_/g, '');


  if (
    compact ===
      'provinsi' ||
    compact ===
      'province'
  ) {

    return 'Provinsi';

  }


  if (
    compact ===
      'kab/kota' ||
    compact ===
      'kabkota' ||
    compact ===
      'kabupaten/kota' ||
    compact ===
      'kabupatenkota'
  ) {

    return 'Kab/Kota';

  }


  return '';

}


/* =====================================================
   CEK REGION PROVINSI
===================================================== */

function isProvinceRegion(regionName) {

  return (

    normalizeRegionName(
      regionName
    ) ===

    normalizeRegionName(
      PROVINCE_NAME
    )

  );

}


/* =====================================================
   NORMALISASI NAMA REGION
===================================================== */

function normalizeRegionName(value) {

  return String(value || '')

    .toLowerCase()

    .trim()

    .replace(
      /\bkabupaten\b/g,
      ''
    )

    .replace(
      /\bkab\.\b/g,
      ''
    )

    .replace(
      /\bkab\b/g,
      ''
    )

    .replace(
      /\bkota\b/g,
      ''
    )

    .replace(
      /[^a-z0-9]/g,
      ''
    );

}


/* =====================================================
   CEK VALUE TERISI
===================================================== */

function isFilledValue(value) {

  return (

    value !== '' &&

    value !== null &&

    value !== undefined

  );

}


/* =====================================================
   DASHBOARD META
===================================================== */

function getDashboardMeta() {

  var sheet =
    getDataSpreadsheet()
      .getSheetByName(
        SHEET_NAME
      );


  if (!sheet) {

    throw new Error(
      'Sheet "' +
      SHEET_NAME +
      '" tidak ditemukan.'
    );

  }


  var lastRow =
    sheet.getLastRow();

  var lastCol =
    sheet.getLastColumn();


  if (
    lastRow <
    DATA_START_ROW
  ) {

    return {

      indicators: [],

      wilayah: [],

      years: [],

      pic2025: [],

      picLocked: true

    };

  }


  /* =====================================================
     HEADER
  ===================================================== */

  var headers =
    sheet
      .getRange(
        HEADER_ROW,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var yearRow =
    sheet
      .getRange(
        YEAR_ROW,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  /* =====================================================
     CARI KOLOM
  ===================================================== */

  var pic2025Col =
    findColumn(
      headers,
      'PIC 2025'
    );


  var pic2026Col =
    findColumn(
      headers,
      'PIC 2026'
    );


  var pjkCol =
    findColumn(
      headers,
      'PJK Teknis/Tim 2026'
    );


  var catatanCol =
    findColumn(
      headers,
      'Catatan'
    );


  var checklistCol =
    findColumn(
      headers,
      'Checklist Input SiGADIS'
    );


  var januariCol =
    findColumn(
      headers,
      'Catatan Januari 2026 (Data 2024)'
    );


  var pemeriksaanCol =
    findColumn(
      headers,
      'Catatan Pemeriksaan 29 Januari 2026'
    );


  var tindakLanjutCol =
    findColumn(
      headers,
      'Tindak Lanjut 30 Januari 2026 (angka sd 2024)'
    );


  var sourceMetadataCol =
    findColumn(
      headers,
      'Sumber Data (Metadata)'
    );


  var coverageCol =
    findColumn(
      headers,
      'Coverage Terkecil'
    );


  var frequencyCol =
    findColumn(
      headers,
      'Frekuensi'
    );


  var sourceActivityCol =
    findColumn(
      headers,
      'Sumber Data/Kegiatan'
    );


  var variableCol =
    findColumn(
      headers,
      'Variabel Terkait (Diisi PJK)'
    );


  /* =====================================================
     KOLOM YANG DIBUTUHKAN
  ===================================================== */

  var neededCols = [

    0,

    1,

    pic2025Col,

    pic2026Col,

    pjkCol,

    catatanCol,

    checklistCol,

    januariCol,

    pemeriksaanCol,

    tindakLanjutCol,

    sourceMetadataCol,

    coverageCol,

    frequencyCol,

    sourceActivityCol,

    variableCol

  ];


  var validCols =
    neededCols.filter(
      function(col) {

        return col >= 0;

      }
    );


  var maxNeededCol =
    Math.max.apply(
      null,
      validCols
    ) + 1;


  /* =====================================================
     DATA LABEL
  ===================================================== */

  var labelData =
    sheet
      .getRange(
        DATA_START_ROW,
        1,
        lastRow -
          DATA_START_ROW +
          1,
        maxNeededCol
      )
      .getValues();


  /* =====================================================
     DATA WILAYAH
  ===================================================== */

  var wideData =
    sheet
      .getRange(
        DATA_START_ROW,
        DATA_START_COL,
        lastRow -
          DATA_START_ROW +
          1,
        lastCol -
          DATA_START_COL +
          1
      )
      .getValues();


  var wilayahSet = {};

  var yearSet = {};

  var currentRegionHeader =
    '';

  var regionByCol =
    [];


  for (
    var col =
      DATA_START_COL - 1;

    col < lastCol;

    col++
  ) {

    var region =
      headers[col];


    if (
      region !== '' &&
      region !== null &&
      region !== undefined
    ) {

      currentRegionHeader =
        String(region)
          .trim();

    }


    var year =
      normalizeYear(
        yearRow[col]
      );


    regionByCol.push({

      region:
        currentRegionHeader,

      year:
        year

    });


    if (
      currentRegionHeader &&
      year !== null
    ) {

      wilayahSet[
        currentRegionHeader
      ] = true;


      yearSet[
        year
      ] = true;

    }

  }


  /* =====================================================
     INDIKATOR
  ===================================================== */

  var indicators =
    [];


  for (
    var i = 0;

    i < labelData.length;

    i++
  ) {

    var code =
      labelData[i][0];


    if (
      !isIndicatorCode(
        code
      )
    ) {

      continue;

    }


    var wRow =
      wideData[i];


    /*
     * Coverage yang tertulis di spreadsheet.
     */
    var configuredCoverage =
      getValue(
        labelData[i],
        coverageCol
      );


    /*
     * HITUNG PEMENUHAN BARU.
     */
    var fulfillment =
      calculateCoverageFulfillment(

        wRow,

        regionByCol,

        configuredCoverage

      );


    /*
     * DATA TERISI UMUM:
     * 4 metadata + semua cell tahun seluruh wilayah.
     * Ini sengaja TERPISAH dari Pemenuhan PIC.
     */
    var generalCompleteness =
      calculateGeneralDataCompleteness(

        wRow,

        regionByCol,

        [

          getValue(
            labelData[i],
            sourceMetadataCol
          ),

          getValue(
            labelData[i],
            coverageCol
          ),

          getValue(
            labelData[i],
            frequencyCol
          ),

          getValue(
            labelData[i],
            sourceActivityCol
          )

        ]

      );


    var indicatorItem = {

      row:
        DATA_START_ROW + i,


      code:
        String(code)
          .trim(),


      name:
        labelData[i][1] ||
        '(Belum ada nama)',


      definition:
        '',


      /*
       * =================================================
       * PERHITUNGAN BARU
       * =================================================
       */

      /*
       * DATA TERISI UMUM
       * Dipakai KPI Card dan Daftar Indikator.
       */
      dataCount:
        generalCompleteness.filledCells,


      dataTotal:
        generalCompleteness.totalCells,


      dataPercentage:
        generalCompleteness.percentage,


      dataMissing:
        generalCompleteness.missingCells,


      /*
       * Informasi pemenuhan lengkap.
       */
      fulfillment: {

        configuredCoverage:
          fulfillment
            .configuredCoverage,


        effectiveCoverage:
          fulfillment
            .effectiveCoverage,


        coverageSource:
          fulfillment
            .coverageSource,


        countable:
          fulfillment
            .countable,


        filledCells:
          fulfillment
            .filledCells,


        totalCells:
          fulfillment
            .totalCells,


        missingCells:
          fulfillment
            .missingCells,


        yearStart:
          fulfillment
            .yearStart,


        yearEnd:
          fulfillment
            .yearEnd,


        percentage:
          fulfillment
            .percentage,


        additionalData:
          fulfillment
            .additionalData

      },


      pic2025:
        getValue(
          labelData[i],
          pic2025Col
        ),


      pjk:
        getValue(
          labelData[i],
          pjkCol
        ),


      catatan:
        getValue(
          labelData[i],
          catatanCol
        ),


      checklist:
        getValue(
          labelData[i],
          checklistCol
        ),


      catatanJanuari:
        getValue(
          labelData[i],
          januariCol
        ),


      catatanPemeriksaan:
        getValue(
          labelData[i],
          pemeriksaanCol
        ),


      tindakLanjut:
        getValue(
          labelData[i],
          tindakLanjutCol
        ),


      metadata: {

        sourceMetadata:
          getValue(
            labelData[i],
            sourceMetadataCol
          ),


        coverage:
          getValue(
            labelData[i],
            coverageCol
          ),


        frequency:
          getValue(
            labelData[i],
            frequencyCol
          ),


        sourceActivity:
          getValue(
            labelData[i],
            sourceActivityCol
          ),


        variable:
          getValue(
            labelData[i],
            variableCol
          )

      }

    };


    indicators.push(
      indicatorItem
    );

  }


  /* =====================================================
     DEFINISI
  ===================================================== */

  var definitionsList =
    getDefinitionsList();


  if (
    definitionsList.length !==
    indicators.length
  ) {

    Logger.log(

      'PERINGATAN: jumlah baris definisi (' +

      definitionsList.length +

      ') TIDAK SAMA dengan jumlah indikator di Rekap (' +

      indicators.length +

      ').'

    );

  }


  for (
    var k = 0;

    k < indicators.length;

    k++
  ) {

    if (
      definitionsList[k]
    ) {

      if (
        definitionsList[k].code !==
        indicators[k].code
      ) {

        Logger.log(

          'PERINGATAN urutan ke-' +

          (k + 1) +

          ': Rekap = ' +

          indicators[k].code +

          ', Definisi = ' +

          definitionsList[k].code

        );

      }


      indicators[k].definition =
        definitionsList[k]
          .definition;

    }

  }


  /* =====================================================
     HASIL
  ===================================================== */

  return {

    indicators:
      indicators,


    wilayah:
      Object.keys(
        wilayahSet
      ).sort(),


    years:
      Object.keys(
        yearSet
      )

        .map(Number)

        .sort(
          function(a, b) {

            return a - b;

          }
        ),


    pic2025:
      uniqueValues(

        indicators.map(
          function(item) {

            return item.pic2025;

          }
        )

      ),


    /*
     * PIC 2026 tetap terkunci.
     */
    picLocked:
      true

  };

}


/* =====================================================
   DATA NILAI INDIKATOR
===================================================== */

function getIndicatorValues(
  targetRow
) {

  var sheet =
    getDataSpreadsheet()
      .getSheetByName(
        SHEET_NAME
      );


  if (!sheet) {

    throw new Error(
      'Sheet "' +
      SHEET_NAME +
      '" tidak ditemukan.'
    );

  }


  var lastCol =
    sheet.getLastColumn();


  var headers =
    sheet
      .getRange(
        HEADER_ROW,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var yearRow =
    sheet
      .getRange(
        YEAR_ROW,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var dataRow =
    sheet
      .getRange(
        targetRow,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var result =
    [];


  var currentRegion =
    '';


  for (
    var col =
      DATA_START_COL - 1;

    col < lastCol;

    col++
  ) {

    var region =
      headers[col];


    if (
      region !== '' &&
      region !== null &&
      region !== undefined
    ) {

      currentRegion =
        String(region)
          .trim();

    }


    var year =
      normalizeYear(
        yearRow[col]
      );


    var value =
      dataRow[col];


    if (
      currentRegion &&
      year !== null &&
      isFilledValue(value)
    ) {

      result.push({

        wilayah:
          currentRegion,

        tahun:
          year,

        nilai:
          value

      });

    }

  }


  return result;

}


/* =====================================================
   DEFINISI
===================================================== */

function getDefinitionsList() {

  try {

    var defSheet =
      SpreadsheetApp

        .openById(
          DEFINITION_SHEET_ID
        )

        .getSheetByName(
          DEFINITION_SHEET_TAB
        );


    if (!defSheet) {

      Logger.log(
        'Tab Definisi tidak ditemukan.'
      );

      return [];

    }


    var data =
      defSheet
        .getDataRange()
        .getValues();


    var list =
      [];


    for (
      var i = 1;

      i < data.length;

      i++
    ) {

      var kode =
        data[i][0];


      var definisi =
        data[i][1];


      if (
        isIndicatorCode(
          kode
        )
      ) {

        list.push({

          code:
            String(kode)
              .trim(),


          definition:
            definisi

              ? String(
                  definisi
                ).trim()

              : ''

        });

      }

    }


    return list;

  }
  catch (e) {

    Logger.log(
      'ERROR definisi: ' +
      e.message
    );


    return [];

  }

}


/* =====================================================
   TEST DEFINISI
===================================================== */

function testDefinitions() {

  Logger.log(

    JSON.stringify(
      getDefinitionsList(),
      null,
      2
    )

  );

}


/* =====================================================
   TEST PERHITUNGAN
   -----------------------------------------------------
   Ubah targetRow sesuai nomor baris indikator.
===================================================== */

function testCoverageFulfillment() {

  var targetRow =
    4;


  var sheet =
    getDataSpreadsheet()
      .getSheetByName(
        SHEET_NAME
      );


  var lastCol =
    sheet.getLastColumn();


  var headers =
    sheet
      .getRange(
        HEADER_ROW,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var yearRow =
    sheet
      .getRange(
        YEAR_ROW,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var row =
    sheet
      .getRange(
        targetRow,
        1,
        1,
        lastCol
      )
      .getValues()[0];


  var coverageCol =
    findColumn(
      headers,
      'Coverage Terkecil'
    );


  var configuredCoverage =
    getValue(
      row,
      coverageCol
    );


  var regionByCol =
    [];


  var currentRegion =
    '';


  for (
    var col =
      DATA_START_COL - 1;

    col < lastCol;

    col++
  ) {

    var region =
      headers[col];


    if (
      region !== '' &&
      region !== null &&
      region !== undefined
    ) {

      currentRegion =
        String(region)
          .trim();

    }


    regionByCol.push({

      region:
        currentRegion,

      year:
        normalizeYear(
          yearRow[col]
        )

    });

  }


  var wideRow =
    row.slice(
      DATA_START_COL - 1
    );


  var result =
    calculateCoverageFulfillment(

      wideRow,

      regionByCol,

      configuredCoverage

    );


  Logger.log(

    JSON.stringify(
      result,
      null,
      2
    )

  );

}


/* =====================================================
   HELPER: CARI KOLOM
===================================================== */

function findColumn(
  headers,
  columnName
) {

  return headers.findIndex(
    function(header) {

      return (

        String(header)
          .trim()
          .toLowerCase() ===

        columnName
          .trim()
          .toLowerCase()

      );

    }
  );

}


/* =====================================================
   HELPER: AMBIL VALUE
===================================================== */

function getValue(
  row,
  index
) {

  if (
    index === -1 ||
    index === undefined
  ) {

    return '';

  }


  var value =
    row[index];


  if (
    value === null ||
    value === undefined
  ) {

    return '';

  }


  return String(value)
    .trim();

}


/* =====================================================
   HELPER: NORMALIZE YEAR
===================================================== */

function normalizeYear(value) {

  if (
    value === null ||
    value === undefined ||
    value === ''
  ) {

    return null;

  }


  var number =
    Number(value);


  if (
    !isNaN(number) &&
    number >= 1900 &&
    number <= 2100
  ) {

    return number;

  }


  return null;

}


/* =====================================================
   HELPER: UNIQUE
===================================================== */

function uniqueValues(array) {

  return [
    ...new Set(

      array

        .filter(
          function(value) {

            return (
              value !== '' &&
              value !== null &&
              value !== undefined
            );

          }
        )

        .map(
          function(value) {

            return String(value)
              .trim();

          }
        )

    )
  ].sort();

}


/* =====================================================
   HELPER: KODE INDIKATOR
===================================================== */

function isIndicatorCode(value) {

  if (
    value === null ||
    value === undefined
  ) {

    return false;

  }


  var text =
    String(value)
      .trim();


  if (!text) {
    return false;
  }


  var clean =
    text.replace(
      /\.$/,
      ''
    );


  if (
    !/[a-zA-Z0-9]/.test(
      clean
    )
  ) {

    return false;

  }


  if (
    !/^[a-zA-Z0-9.-]+$/.test(
      text
    )
  ) {

    return false;

  }


  return true;

}