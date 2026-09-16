from utils import *
import re
import time
import ast
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

def normalize_ojk_text(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    value = (
        str(value)
        .replace("\xa0", " ")
        .strip()
        .lower()
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value


def parse_ojk_number(value):
    """
    Format OJK:
        192.911.135.003.613,60
    menjadi:
        192911135003613.60

    Nilai dari getData() biasanya sudah berupa number,
    tetapi parser string tetap dipertahankan sebagai fallback.
    """

    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value).strip()

    if (
        not raw
        or raw in {"-", "–", "—"}
    ):
        return None

    cleaned = re.sub(
        r"[^0-9,.\-]",
        "",
        raw
    )

    if (
        not cleaned
        or cleaned in {"-", ".", ","}
    ):
        return None

    try:
        if "." in cleaned and "," in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = (
                    cleaned
                    .replace(".", "")
                    .replace(",", ".")
                )
            else:
                cleaned = cleaned.replace(",", "")

        elif "," in cleaned:
            cleaned = cleaned.replace(",", ".")

        elif "." in cleaned:
            if re.fullmatch(
                r"-?\d{1,3}(?:\.\d{3})+",
                cleaned
            ):
                cleaned = cleaned.replace(".", "")

        return float(cleaned)

    except ValueError:
        return None


def ojk_dataset_id_from_url(url):
    matches = re.findall(
        r"/Dataset/(\d+)",
        str(url),
        flags=re.IGNORECASE,
    )

    if matches:
        return matches[-1]

    return ""


def ojk_wait_for_pivotgrid(driver, timeout=45):
    """
    Menunggu instance DevExtreme PivotGrid yang memiliki
    field Bulan dan DATI1.
    """

    def has_pivot(d):
        try:
            return bool(
                d.execute_script(
                    """
                    function getInstance(el) {
                        try {
                            if (
                                window.DevExpress &&
                                DevExpress.ui &&
                                DevExpress.ui.dxPivotGrid
                            ) {
                                const inst =
                                    DevExpress.ui.dxPivotGrid.getInstance(el);

                                if (inst) {
                                    return inst;
                                }
                            }
                        } catch (e) {}

                        try {
                            if (window.jQuery) {
                                const inst =
                                    window.jQuery(el)
                                        .dxPivotGrid("instance");

                                if (inst) {
                                    return inst;
                                }
                            }
                        } catch (e) {}

                        return null;
                    }

                    const elements =
                        Array.from(
                            document.querySelectorAll(
                                ".dx-pivotgrid"
                            )
                        );

                    for (const el of elements) {
                        const inst = getInstance(el);

                        if (!inst) {
                            continue;
                        }

                        const ds = inst.getDataSource();

                        if (!ds) {
                            continue;
                        }

                        const fields = ds.fields() || [];

                        const names = fields.map(function(field) {
                            return String(
                                field.caption ||
                                field.dataField ||
                                field.name ||
                                ""
                            ).toLowerCase();
                        });

                        const hasMonth = names.some(
                            item => item.includes("bulan")
                        );

                        const hasDati = names.some(
                            item => item.includes("dati1")
                        );

                        if (hasMonth && hasDati) {
                            return true;
                        }
                    }

                    return false;
                    """
                )
            )

        except Exception:
            return False

    WebDriverWait(
        driver,
        timeout
    ).until(
        has_pivot
    )


def ojk_get_pivot_fields(driver):
    """
    Ambil metadata field DevExtreme PivotGrid.
    """

    return driver.execute_script(
        """
        function normalize(v) {
            return String(v || "").toLowerCase();
        }

        function getInstance(el) {
            try {
                if (
                    window.DevExpress &&
                    DevExpress.ui &&
                    DevExpress.ui.dxPivotGrid
                ) {
                    const inst =
                        DevExpress.ui.dxPivotGrid.getInstance(el);

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            try {
                if (window.jQuery) {
                    const inst =
                        window.jQuery(el)
                            .dxPivotGrid("instance");

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            return null;
        }

        const elements =
            Array.from(
                document.querySelectorAll(
                    ".dx-pivotgrid"
                )
            );

        for (const el of elements) {
            const inst = getInstance(el);

            if (!inst) {
                continue;
            }

            const ds = inst.getDataSource();

            if (!ds) {
                continue;
            }

            const fields = ds.fields() || [];

            const names = fields.map(function(field) {
                return normalize(
                    field.caption ||
                    field.dataField ||
                    field.name
                );
            });

            if (
                !names.some(
                    item => item.includes("bulan")
                )
                ||
                !names.some(
                    item => item.includes("dati1")
                )
            ) {
                continue;
            }

            return fields.map(function(field, index) {
                return {
                    index: index,
                    caption:
                        String(field.caption || ""),
                    dataField:
                        String(field.dataField || ""),
                    name:
                        String(field.name || ""),
                    area:
                        String(field.area || ""),
                    areaIndex:
                        (
                            field.areaIndex === undefined
                            ? null
                            : field.areaIndex
                        ),
                    filterType:
                        String(field.filterType || ""),
                    filterValues:
                        field.filterValues || []
                };
            });
        }

        return [];
        """
    )


def ojk_apply_pivot_filters(
    driver,
    dataset_id,
    tahun
):
    """
    Filter PivotGrid melalui API dataSource.

    Perbaikan V8:
    - Tidak lagi mengasumsikan Desember pasti tersedia.
    - Ambil daftar nilai field Bulan langsung dari
      PivotGridDataSource.getFieldValues().
    - Pilih BULAN TERAKHIR yang benar-benar tersedia
      pada tahun target.
    - Gunakan item.key asli dari DevExtreme sebagai filterValues.
      Ini penting karena pada Dataset 107 nilai Bulan bisa memakai
      key internal yang tidak identik dengan teks 'YYYY-MM'.

    Dataset 62:
        Program Dana Pensiun tetap ROW.
        DATI1 menjadi area FILTER = Kalimantan Selatan.

    Dataset 107:
        DATI1 tetap ROW tanpa filter server-side.
        Python memilih row KALIMANTAN SELATAN dari getData().
    """

    province = "KALIMANTAN SELATAN"

    driver.set_script_timeout(
        90
    )

    result = driver.execute_async_script(
        """
        const datasetId =
            String(arguments[0]);

        const targetYear =
            Number(arguments[1]);

        const province =
            String(arguments[2]);

        const done =
            arguments[
                arguments.length - 1
            ];

        function normalize(v) {
            return String(v || "")
                .trim()
                .toLowerCase();
        }

        function getInstance(el) {
            try {
                if (
                    window.DevExpress &&
                    DevExpress.ui &&
                    DevExpress.ui.dxPivotGrid
                ) {
                    const inst =
                        DevExpress.ui.dxPivotGrid.getInstance(el);

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            try {
                if (window.jQuery) {
                    const inst =
                        window.jQuery(el)
                            .dxPivotGrid("instance");

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            return null;
        }

        function fieldLabel(field) {
            return normalize(
                field.caption ||
                field.dataField ||
                field.name
            );
        }

        function flattenFieldValues(
            items,
            output
        ) {
            items = items || [];
            output = output || [];

            for (const item of items) {
                output.push(item);

                if (
                    item.children
                    &&
                    item.children.length
                ) {
                    flattenFieldValues(
                        item.children,
                        output
                    );
                }
            }

            return output;
        }

        function extractPeriod(item) {
            const candidates = [
                item && item.text,
                item && item.value,
                item && item.key
            ];

            for (const raw of candidates) {
                if (
                    raw === undefined
                    ||
                    raw === null
                ) {
                    continue;
                }

                let text;

                if (
                    raw instanceof Date
                ) {
                    const y =
                        raw.getFullYear();

                    const m =
                        raw.getMonth() + 1;

                    return {
                        year: y,
                        month: m,
                        text:
                            String(y)
                            + "-"
                            + String(m)
                                .padStart(
                                    2,
                                    "0"
                                )
                    };
                }

                text =
                    String(raw);

                // YYYY-MM / YYYY/MM / YYYY.MM
                let match =
                    text.match(
                        /(^|[^0-9])((?:19|20)[0-9]{2})[-/.](0?[1-9]|1[0-2])([^0-9]|$)/
                    );

                if (match) {
                    return {
                        year:
                            Number(
                                match[2]
                            ),
                        month:
                            Number(
                                match[3]
                            ),
                        text:
                            String(
                                match[2]
                            )
                            + "-"
                            + String(
                                Number(
                                    match[3]
                                )
                            )
                            .padStart(
                                2,
                                "0"
                            )
                    };
                }

                // ISO date string.
                match =
                    text.match(
                        /((?:19|20)[0-9]{2})-(0?[1-9]|1[0-2])-[0-9]{1,2}/
                    );

                if (match) {
                    return {
                        year:
                            Number(
                                match[1]
                            ),
                        month:
                            Number(
                                match[2]
                            ),
                        text:
                            String(
                                match[1]
                            )
                            + "-"
                            + String(
                                Number(
                                    match[2]
                                )
                            )
                            .padStart(
                                2,
                                "0"
                            )
                    };
                }
            }

            return null;
        }

        try {
            const elements =
                Array.from(
                    document.querySelectorAll(
                        ".dx-pivotgrid"
                    )
                );

            let instance = null;
            let dataSource = null;
            let fields = null;

            for (const el of elements) {
                const candidate =
                    getInstance(el);

                if (!candidate) {
                    continue;
                }

                const ds =
                    candidate.getDataSource();

                if (!ds) {
                    continue;
                }

                const fs =
                    ds.fields() || [];

                const labels =
                    fs.map(fieldLabel);

                const hasMonth =
                    labels.some(
                        item =>
                            item.includes(
                                "bulan"
                            )
                    );

                const hasDati =
                    labels.some(
                        item =>
                            item.includes(
                                "dati1"
                            )
                    );

                if (
                    hasMonth
                    &&
                    hasDati
                ) {
                    instance =
                        candidate;

                    dataSource =
                        ds;

                    fields =
                        fs;

                    break;
                }
            }

            if (
                !instance
                ||
                !dataSource
                ||
                !fields
            ) {
                done({
                    success: false,
                    error:
                        "DevExtreme PivotGrid "
                        + "yang berisi Bulan dan DATI1 "
                        + "tidak ditemukan."
                });

                return;
            }

            function findFieldIndex(
                needle
            ) {
                needle =
                    normalize(
                        needle
                    );

                for (
                    let i = 0;
                    i < fields.length;
                    i++
                ) {
                    const label =
                        fieldLabel(
                            fields[i]
                        );

                    if (
                        label === needle
                        ||
                        label.includes(
                            needle
                        )
                    ) {
                        return i;
                    }
                }

                return -1;
            }

            const monthIndex =
                findFieldIndex(
                    "bulan"
                );

            const datiIndex =
                findFieldIndex(
                    "dati1"
                );

            const programIndex =
                findFieldIndex(
                    "program dana pensiun"
                );

            if (
                monthIndex < 0
                ||
                datiIndex < 0
            ) {
                done({
                    success: false,
                    error:
                        "Field Bulan/DATI1 "
                        + "tidak ditemukan di dataSource."
                });

                return;
            }

            const fieldValuesResult =
                dataSource.getFieldValues(
                    monthIndex
                );

            function onFieldValues(
                rawItems
            ) {
                try {
                    const items =
                        flattenFieldValues(
                            rawItems || [],
                            []
                        );

                    const parsed = [];

                    for (
                        const item of items
                    ) {
                        const period =
                            extractPeriod(
                                item
                            );

                        if (!period) {
                            continue;
                        }

                        parsed.push({
                            item:
                                item,
                            year:
                                period.year,
                            month:
                                period.month,
                            text:
                                period.text
                        });
                    }

                    const targetItems =
                        parsed.filter(
                            item =>
                                item.year
                                ===
                                targetYear
                        );

                    if (
                        targetItems.length
                        ===
                        0
                    ) {
                        const available =
                            Array.from(
                                new Set(
                                    parsed.map(
                                        item =>
                                            item.text
                                    )
                                )
                            )
                            .sort();

                        done({
                            success: false,
                            error:
                                "Tidak ada periode OJK "
                                + "untuk tahun "
                                + String(
                                    targetYear
                                )
                                + ".",
                            availablePeriods:
                                available.slice(
                                    0,
                                    120
                                )
                        });

                        return;
                    }

                    targetItems.sort(
                        function(a, b) {
                            return (
                                b.month
                                -
                                a.month
                            );
                        }
                    );

                    const selected =
                        targetItems[0];

                    // BULAN:
                    // gunakan item.key ASLI dari DevExtreme.
                    dataSource.field(
                        monthIndex,
                        {
                            area:
                                "column",
                            areaIndex:
                                0,
                            filterType:
                                "include",
                            filterValues: [
                                selected.item.key
                            ]
                        }
                    );

                    // DATI1
                    if (
                        datasetId
                        ===
                        "62"
                    ) {
                        if (
                            programIndex
                            >=
                            0
                        ) {
                            dataSource.field(
                                programIndex,
                                {
                                    area:
                                        "row",
                                    areaIndex:
                                        0
                                }
                            );
                        }

                        dataSource.field(
                            datiIndex,
                            {
                                area:
                                    "filter",
                                areaIndex:
                                    0,
                                filterType:
                                    "include",
                                filterValues: [
                                    province
                                ]
                            }
                        );

                    } else {
                        const datiCurrent =
                            fields[
                                datiIndex
                            ]
                            ||
                            {};

                        dataSource.field(
                            datiIndex,
                            {
                                area:
                                    datiCurrent.area
                                    ||
                                    "row",
                                areaIndex:
                                    (
                                        datiCurrent.areaIndex
                                        ===
                                        undefined
                                        ||
                                        datiCurrent.areaIndex
                                        ===
                                        null
                                    )
                                    ?
                                    0
                                    :
                                    datiCurrent.areaIndex,
                                filterValues:
                                    null
                            }
                        );
                    }

                    const finish =
                        function() {
                            try {
                                if (
                                    instance.repaint
                                ) {
                                    instance.repaint();
                                }
                            } catch (e) {}

                            const current =
                                dataSource.fields()
                                ||
                                [];

                            done({
                                success:
                                    true,
                                selectedPeriod:
                                    selected.text,
                                selectedMonth:
                                    selected.month,
                                fields:
                                    current.map(
                                        function(
                                            field,
                                            index
                                        ) {
                                            return {
                                                index:
                                                    index,
                                                caption:
                                                    String(
                                                        field.caption
                                                        ||
                                                        ""
                                                    ),
                                                dataField:
                                                    String(
                                                        field.dataField
                                                        ||
                                                        ""
                                                    ),
                                                area:
                                                    String(
                                                        field.area
                                                        ||
                                                        ""
                                                    ),
                                                areaIndex:
                                                    (
                                                        field.areaIndex
                                                        ===
                                                        undefined
                                                        ?
                                                        null
                                                        :
                                                        field.areaIndex
                                                    ),
                                                filterValues:
                                                    field.filterValues
                                                    ||
                                                    []
                                            };
                                        }
                                    )
                            });
                        };

                    const failure =
                        function(error) {
                            done({
                                success:
                                    false,
                                error:
                                    String(
                                        error
                                        &&
                                        (
                                            error.message
                                            ||
                                            error
                                        )
                                        ||
                                        "reload gagal"
                                    )
                            });
                        };

                    const reloadResult =
                        dataSource.reload();

                    if (
                        reloadResult
                        &&
                        typeof reloadResult.done
                        ===
                        "function"
                    ) {
                        reloadResult
                            .done(
                                finish
                            )
                            .fail(
                                failure
                            );

                        return;
                    }

                    if (
                        reloadResult
                        &&
                        typeof reloadResult.then
                        ===
                        "function"
                    ) {
                        reloadResult
                            .then(
                                finish
                            )
                            .catch(
                                failure
                            );

                        return;
                    }

                    setTimeout(
                        finish,
                        5000
                    );

                } catch (error) {
                    done({
                        success:
                            false,
                        error:
                            String(
                                error
                                &&
                                (
                                    error.message
                                    ||
                                    error
                                )
                                ||
                                error
                            )
                    });
                }
            }

            function onFieldValuesError(
                error
            ) {
                done({
                    success:
                        false,
                    error:
                        "getFieldValues(Bulan) gagal: "
                        +
                        String(
                            error
                            &&
                            (
                                error.message
                                ||
                                error
                            )
                            ||
                            error
                        )
                });
            }

            if (
                fieldValuesResult
                &&
                typeof fieldValuesResult.done
                ===
                "function"
            ) {
                fieldValuesResult
                    .done(
                        onFieldValues
                    )
                    .fail(
                        onFieldValuesError
                    );

                return;
            }

            if (
                fieldValuesResult
                &&
                typeof fieldValuesResult.then
                ===
                "function"
            ) {
                fieldValuesResult
                    .then(
                        onFieldValues
                    )
                    .catch(
                        onFieldValuesError
                    );

                return;
            }

            onFieldValues(
                fieldValuesResult
                ||
                []
            );

        } catch (error) {
            done({
                success:
                    false,
                error:
                    String(
                        error
                        &&
                        (
                            error.message
                            ||
                            error
                        )
                        ||
                        error
                    )
            });
        }
        """,
        str(dataset_id),
        int(tahun),
        province,
    )

    if not result:
        raise RuntimeError(
            "DevExtreme tidak memberi response "
            "setelah filter diterapkan."
        )

    if not result.get("success"):
        available = (
            result.get(
                "availablePeriods",
                []
            )
            or
            []
        )

        suffix = ""

        if available:
            suffix = (
                " Periode tersedia: "
                +
                ", ".join(
                    str(item)
                    for item
                    in available
                )
            )

        raise RuntimeError(
            "Gagal menerapkan filter OJK: "
            +
            str(
                result.get(
                    "error",
                    "unknown error"
                )
            )
            +
            suffix
        )

    return result



def ojk_get_loaded_pivot_data(driver):
    """
    Ambil data langsung dari PivotGridDataSource.getData().

    Return dibuat sederhana agar aman diserialisasi Selenium:
        rows: [{index, text, value, path}]
        columns: [{index, text, value, path}]
        values: [...]
        grandTotalRowIndex
        grandTotalColumnIndex
    """

    result = driver.execute_script(
        """
        function normalize(v) {
            return String(v || "").toLowerCase();
        }

        function getInstance(el) {
            try {
                if (
                    window.DevExpress &&
                    DevExpress.ui &&
                    DevExpress.ui.dxPivotGrid
                ) {
                    const inst =
                        DevExpress.ui.dxPivotGrid.getInstance(el);

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            try {
                if (window.jQuery) {
                    const inst =
                        window.jQuery(el)
                            .dxPivotGrid("instance");

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            return null;
        }

        function flattenHeaders(items, parentPath, output) {
            items = items || [];
            parentPath = parentPath || [];
            output = output || [];

            for (const item of items) {
                const label =
                    String(
                        item.text !== undefined
                        ?
                        item.text
                        :
                        (
                            item.value !== undefined
                            ?
                            item.value
                            :
                            ""
                        )
                    );

                const path =
                    parentPath.concat([label]);

                output.push({
                    index:
                        item.index,
                    text:
                        String(
                            item.text !== undefined
                            ?
                            item.text
                            :
                            ""
                        ),
                    value:
                        (
                            item.value === undefined
                            ?
                            null
                            :
                            item.value
                        ),
                    path:
                        path.join(" > "),
                    hasChildren:
                        !!(
                            item.children
                            &&
                            item.children.length
                        )
                });

                if (
                    item.children
                    &&
                    item.children.length
                ) {
                    flattenHeaders(
                        item.children,
                        path,
                        output
                    );
                }
            }

            return output;
        }

        const elements =
            Array.from(
                document.querySelectorAll(
                    ".dx-pivotgrid"
                )
            );

        for (const el of elements) {
            const inst =
                getInstance(el);

            if (!inst) {
                continue;
            }

            const ds =
                inst.getDataSource();

            if (!ds) {
                continue;
            }

            const fields =
                ds.fields() || [];

            const labels =
                fields.map(function(field) {
                    return normalize(
                        field.caption ||
                        field.dataField ||
                        field.name
                    );
                });

            const hasMonth =
                labels.some(
                    item =>
                        item.includes("bulan")
                );

            const hasDati =
                labels.some(
                    item =>
                        item.includes("dati1")
                );

            if (
                !hasMonth
                ||
                !hasDati
            ) {
                continue;
            }

            const data =
                ds.getData();

            if (!data) {
                return {
                    success: false,
                    error:
                        "getData() mengembalikan kosong."
                };
            }

            return {
                success: true,
                rows:
                    flattenHeaders(
                        data.rows || [],
                        [],
                        []
                    ),
                columns:
                    flattenHeaders(
                        data.columns || [],
                        [],
                        []
                    ),
                values:
                    data.values || [],
                grandTotalRowIndex:
                    (
                        data.grandTotalRowIndex
                        === undefined
                        ?
                        null
                        :
                        data.grandTotalRowIndex
                    ),
                grandTotalColumnIndex:
                    (
                        data.grandTotalColumnIndex
                        === undefined
                        ?
                        null
                        :
                        data.grandTotalColumnIndex
                    )
            };
        }

        return {
            success: false,
            error:
                "PivotGrid target tidak ditemukan."
        };
        """
    )

    if not result:
        raise RuntimeError(
            "Data PivotGrid OJK kosong."
        )

    if not result.get("success"):
        raise RuntimeError(
            "Gagal membaca PivotGridDataSource.getData(): "
            + str(
                result.get(
                    "error",
                    "unknown error"
                )
            )
        )

    return result


def ojk_program_kind(text):
    normalized = re.sub(
        r"[^a-z0-9]+",
        "",
        normalize_ojk_text(text)
    )

    # Teks portal bisa berupa:
    # DKPPMP / DPPK-PPMP / PPMP
    if "ppmp" in normalized:
        return "PPMP"

    if "ppip" in normalized:
        return "PPIP"

    if "dplk" in normalized:
        return "DPLK"

    if "dapen" in normalized:
        return "DAPEN"

    return ""


def ojk_find_month_column_index(
    pivot_data,
    target_period
):
    """
    Cari index kolom bulan target.

    Bila filter hanya menyisakan satu bulan tetapi caption
    tidak terbaca, grandTotalColumnIndex aman dipakai karena
    total hanya terdiri dari bulan tersebut.
    """

    target = normalize_ojk_text(
        target_period
    )

    columns = (
        pivot_data.get(
            "columns",
            []
        )
        or
        []
    )

    for item in columns:
        combined = " ".join([
            normalize_ojk_text(
                item.get("text")
            ),
            normalize_ojk_text(
                item.get("value")
            ),
            normalize_ojk_text(
                item.get("path")
            ),
        ])

        if target in combined:
            try:
                return int(
                    item.get("index")
                )
            except Exception:
                pass

    grand_index = (
        pivot_data.get(
            "grandTotalColumnIndex"
        )
    )

    if grand_index is not None:
        return int(
            grand_index
        )

    # Kalau cuma satu header kolom.
    distinct = []

    for item in columns:
        index = item.get("index")

        if index is None:
            continue

        try:
            index = int(index)
        except Exception:
            continue

        if index not in distinct:
            distinct.append(index)

    if len(distinct) == 1:
        return distinct[0]

    raise RuntimeError(
        f"Index kolom {target_period} "
        "tidak dapat ditentukan dari getData()."
    )


def ojk_pivot_value(
    pivot_data,
    row_index,
    column_index,
    measure_index=0
):
    values = (
        pivot_data.get(
            "values",
            []
        )
    )

    try:
        raw = (
            values[
                int(row_index)
            ][
                int(column_index)
            ][
                int(measure_index)
            ]
        )

    except (
        IndexError,
        TypeError,
        KeyError
    ):
        return None

    return parse_ojk_number(
        raw
    )


def ojk_extract_dataset_62_from_pivot(
    pivot_data,
    target_period
):
    """
    Dataset 62:
        Rows = Program Dana Pensiun
        Filter DATI1 = Kalimantan Selatan
        Filter Bulan = target_period
        Measure = Aset

    Portal OJK menyediakan row:
        PPMP
        PPIP
        DPLK
        DAPEN

    DAPEN adalah total Dana Pensiun dan menjadi pilihan utama.
    Ini penting karena pada provinsi/periode tertentu komponen
    PPIP atau DPLK bisa tidak tampil sama sekali (praktis nol/tidak ada),
    sedangkan DAPEN tetap tersedia sebagai total agregat.

    Kalau DAPEN tidak tersedia, baru fallback ke penjumlahan
    komponen PPMP + PPIP + DPLK yang tersedia.
    """

    rows = (
        pivot_data.get(
            "rows",
            []
        )
        or
        []
    )

    column_index = (
        ojk_find_month_column_index(
            pivot_data,
            target_period
        )
    )

    found = {
        "PPMP": [],
        "PPIP": [],
        "DPLK": [],
        "DAPEN": [],
    }

    debug_rows = []

    for row in rows:
        label = (
            row.get("path")
            or
            row.get("text")
            or
            row.get("value")
            or
            ""
        )

        kind = ojk_program_kind(
            label
        )

        row_index = row.get(
            "index"
        )

        if row_index is None:
            continue

        value = ojk_pivot_value(
            pivot_data,
            row_index=row_index,
            column_index=column_index,
            measure_index=0,
        )

        if value is not None:
            debug_rows.append(
                f"{label}={format_id(value)}"
            )

        if kind not in found:
            continue

        if (
            value is None
            or
            value < 0
        ):
            continue

        # Hindari salah baca nomor urut 1/2/3/4 sebagai nominal aset.
        if value < 1_000:
            continue

        found[kind].append(
            float(value)
        )

    if debug_rows:
        print(
            "    [OJK] Pivot row/value = "
            +
            " | ".join(
                debug_rows[:12]
            )
        )

    resolved = {}

    for kind, values in found.items():
        unique = []

        for value in values:
            if not any(
                abs(value - existing) < 0.01
                for existing
                in unique
            ):
                unique.append(value)

        if len(unique) == 1:
            resolved[kind] = unique[0]

        elif len(unique) > 1:
            raise RuntimeError(
                f"Dataset 62 memiliki lebih dari satu "
                f"kandidat {kind} untuk {target_period}: "
                +
                ", ".join(
                    format_id(value)
                    for value
                    in unique
                )
            )

    # ========================================================
    # PRIORITAS: DAPEN = TOTAL ASET DANA PENSIUN KONVENSIONAL
    # ========================================================

    if "DAPEN" in resolved:
        total = float(
            resolved["DAPEN"]
        )

        print(
            "    [OJK] Konvensional:"
        )

        for kind in (
            "PPMP",
            "PPIP",
            "DPLK",
        ):
            if kind in resolved:
                print(
                    f"    [OJK]   {kind} = "
                    f"{format_id(resolved[kind])} rupiah"
                )
            else:
                print(
                    f"    [OJK]   {kind} = "
                    "tidak tampil pada pivot "
                    "(diasumsikan 0/tidak ada pada periode ini)"
                )

        print(
            "    [OJK]   DAPEN = "
            f"{format_id(total)} rupiah"
        )

        # Jika semua/lebih dari satu komponen muncul, cek konsistensi.
        component_sum = sum(
            resolved.get(kind, 0.0)
            for kind
            in (
                "PPMP",
                "PPIP",
                "DPLK",
            )
        )

        if component_sum > 0:
            difference = abs(
                total
                -
                component_sum
            )

            tolerance = max(
                1.0,
                abs(total) * 1e-8
            )

            if difference <= tolerance:
                print(
                    "    [OJK] DAPEN konsisten dengan "
                    "jumlah komponen yang tampil."
                )
            else:
                print(
                    "    [OJK] Catatan: jumlah komponen yang tampil "
                    "tidak sama dengan DAPEN. "
                    "DAPEN dipakai karena merupakan total resmi."
                )

        print(
            "    [OJK] Total konvensional = "
            f"{format_id(total)} rupiah"
        )

        return total

    # ========================================================
    # FALLBACK: DAPEN TIDAK ADA
    # ========================================================

    component_values = {
        kind:
            resolved.get(kind)
        for kind
        in (
            "PPMP",
            "PPIP",
            "DPLK",
        )
        if kind in resolved
    }

    if not component_values:
        row_labels = [
            str(
                row.get("path")
                or
                row.get("text")
                or
                row.get("value")
                or
                ""
            )
            for row in rows
        ]

        raise RuntimeError(
            "Dataset 62 tidak menghasilkan DAPEN maupun "
            f"komponen aset untuk {target_period}. "
            "Header row getData(): "
            +
            " | ".join(row_labels[:20])
        )

    total = sum(
        component_values.values()
    )

    print(
        "    [OJK] Konvensional "
        "(fallback jumlah komponen):"
    )

    for kind in (
        "PPMP",
        "PPIP",
        "DPLK",
    ):
        if kind in component_values:
            print(
                f"    [OJK]   {kind} = "
                f"{format_id(component_values[kind])} rupiah"
            )
        else:
            print(
                f"    [OJK]   {kind} = "
                "tidak tampil (diasumsikan 0)"
            )

    print(
        "    [OJK] Total konvensional = "
        f"{format_id(total)} rupiah"
    )

    return total



def ojk_extract_dataset_107_from_pivot(
    pivot_data,
    target_period
):
    """
    Dataset 107:
        Bulan sudah difilter = target_period.
        DATI1 tetap ROW tanpa filter server.

    Python mencari row KALIMANTAN SELATAN secara eksplisit.
    Ini lebih aman daripada filterValues DATI1 yang pada periode
    lama pernah membuat rows=0.
    """

    column_index = (
        ojk_find_month_column_index(
            pivot_data,
            target_period
        )
    )

    rows = (
        pivot_data.get(
            "rows",
            []
        )
        or
        []
    )

    if not rows:
        raise RuntimeError(
            "Dataset 107 getData() tidak memiliki row "
            f"untuk {target_period}."
        )

    row_debug = []

    for row in rows:
        label_raw = (
            row.get("path")
            or
            row.get("text")
            or
            row.get("value")
            or
            ""
        )

        label = normalize_ojk_text(
            label_raw
        )

        row_debug.append(
            str(label_raw)
        )

        if (
            "kalimantan selatan"
            not in label
        ):
            continue

        row_index = row.get(
            "index"
        )

        if row_index is None:
            continue

        value = ojk_pivot_value(
            pivot_data,
            row_index=row_index,
            column_index=column_index,
            measure_index=0,
        )

        if (
            value is not None
            and
            value >= 0
        ):
            print(
                "    [OJK] Row syariah Kalsel = "
                f"{label_raw}"
            )

            return float(value)

    raise RuntimeError(
        "KALIMANTAN SELATAN tidak ditemukan pada row Dataset 107 "
        f"untuk {target_period}. "
        "Provinsi yang terbaca: "
        +
        " | ".join(
            row_debug[:40]
        )
    )



def scrape_ojk_dataset(
    url,
    tahun,
    description
):
    """
    Dataset 62  = Dana Pensiun Konvensional
    Dataset 107 = Dana Pensiun Syariah

    Periode tahunan:
        Bulan terakhir yang tersedia pada tahun target.

    Nilai diambil dari PivotGridDataSource.getData(),
    bukan dari HTML table.

    Nominal:
        rupiah penuh -> miliar rupiah.
    """

    tahun = int(tahun)

    dataset_id = (
        ojk_dataset_id_from_url(
            url
        )
    )

    if dataset_id not in {"62", "107"}:
        raise NotImplementedError(
            f"Dataset OJK {dataset_id} "
            "belum mempunyai parser."
        )

    requested_period = (
        f"{tahun}-bulan-terakhir"
    )

    cache_key = (
        "ojk_getdata",
        dataset_id,
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[
            cache_key
        ]

    source_label = (
        "Dana Pensiun Konvensional"
        if dataset_id == "62"
        else
        "Dana Pensiun Syariah"
    )

    print(
        f"    [OJK] Dataset {dataset_id} "
        f"({source_label})"
    )

    print(
        f"    [OJK] Target = bulan terakhir "
        f"yang tersedia pada tahun {tahun}"
    )

    driver = create_chrome_driver()

    try:
        driver.set_page_load_timeout(
            90
        )

        driver.get(
            url
        )

        WebDriverWait(
            driver,
            45
        ).until(
            lambda d:
            d.execute_script(
                "return document.readyState"
            )
            ==
            "complete"
        )

        # OJK memuat pivot secara AJAX.
        time.sleep(
            8
        )

        ojk_wait_for_pivotgrid(
            driver,
            timeout=45
        )

        fields_before = (
            ojk_get_pivot_fields(
                driver
            )
        )

        print(
            "    [OJK] PivotGrid ditemukan."
        )

        if fields_before:
            short_fields = []

            for field in fields_before:
                label = (
                    field.get("caption")
                    or
                    field.get("dataField")
                    or
                    field.get("name")
                    or
                    "?"
                )

                short_fields.append(
                    f"{label}"
                    f"[{field.get('area') or '-'}]"
                )

            print(
                "    [OJK] Field = "
                +
                ", ".join(
                    short_fields
                )
            )

        filter_result = (
            ojk_apply_pivot_filters(
                driver,
                dataset_id=dataset_id,
                tahun=tahun,
            )
        )

        target_period = (
            filter_result.get(
                "selectedPeriod"
            )
            or
            f"{tahun}-12"
        )

        print(
            "    [OJK] Filter API berhasil:"
        )

        if dataset_id == "62":
            print(
                "    [OJK]   DATI1 = "
                "KALIMANTAN SELATAN "
                "(area filter)"
            )
        else:
            print(
                "    [OJK]   DATI1 = "
                "semua provinsi ditampilkan; "
                "Kalimantan Selatan dipilih dari row getData()"
            )

        print(
            "    [OJK]   Bulan = "
            f"{target_period}"
        )

        filtered_fields = (
            filter_result.get(
                "fields",
                []
            )
            if isinstance(
                filter_result,
                dict
            )
            else []
        )

        if filtered_fields:
            area_parts = []

            for field in filtered_fields:
                label = (
                    field.get("caption")
                    or
                    field.get("dataField")
                    or
                    "?"
                )

                area = (
                    field.get("area")
                    or
                    "-"
                )

                filters = (
                    field.get("filterValues")
                    or
                    []
                )

                extra = ""

                if filters:
                    extra = (
                        "="
                        +
                        ",".join(
                            str(item)
                            for item
                            in filters
                        )
                    )

                area_parts.append(
                    f"{label}[{area}]{extra}"
                )

            print(
                "    [OJK] Field sesudah filter = "
                +
                " | ".join(
                    area_parts
                )
            )

        time.sleep(
            4
        )

        pivot_data = (
            ojk_get_loaded_pivot_data(
                driver
            )
        )

        rows = (
            pivot_data.get(
                "rows",
                []
            )
        )

        columns = (
            pivot_data.get(
                "columns",
                []
            )
        )

        print(
            f"    [OJK] getData rows = "
            f"{len(rows)}"
        )

        print(
            f"    [OJK] getData columns = "
            f"{len(columns)}"
        )

        if rows:
            print(
                "    [OJK] Row headers = "
                +
                " | ".join(
                    str(
                        item.get("path")
                        or
                        item.get("text")
                        or
                        item.get("value")
                        or
                        ""
                    )
                    for item
                    in rows[:12]
                )
            )

        if columns:
            print(
                "    [OJK] Column headers = "
                +
                " | ".join(
                    str(
                        item.get("path")
                        or
                        item.get("text")
                        or
                        item.get("value")
                        or
                        ""
                    )
                    for item
                    in columns[:12]
                )
            )

        if dataset_id == "62":
            value_rupiah = (
                ojk_extract_dataset_62_from_pivot(
                    pivot_data,
                    target_period,
                )
            )

        else:
            value_rupiah = (
                ojk_extract_dataset_107_from_pivot(
                    pivot_data,
                    target_period,
                )
            )

            print(
                "    [OJK] Syariah total = "
                f"{format_id(value_rupiah)} rupiah"
            )

        # Portal OJK: Rupiah (Satuan penuh)
        value_miliar = (
            value_rupiah
            /
            1_000_000_000
        )

        print(
            "    [OJK] Nilai miliar = "
            f"{format_id(value_miliar)}"
        )

        if value_miliar < 0:
            raise RuntimeError(
                "Nilai OJK negatif."
            )

        SCRAPE_CACHE[
            cache_key
        ] = value_miliar

        return value_miliar

    finally:
        try:
            driver.quit()
        except Exception:
            pass




# ============================================================
# OJK DATASET 277 - NILAI TRANSAKSI SAHAM PER PROVINSI
#
# V11
# ------------------------------------------------------------
# Perbaikan utama:
#
# 1. Dataset 277 memang hanya menyediakan tahun tertentu.
#    Jika tahun target tidak ada, fungsi akan memberi error
#    SOURCE_UNAVAILABLE beserta daftar tahun tersedia.
#
# 2. Jangan memasukkan 12 filterValues Bulan sekaligus.
#    Pada Dataset 277, PivotGrid dapat menggabungkan hasil menjadi
#    hanya satu kolom/subtotal walaupun 12 key sudah dipilih.
#
#    V11 mengambil BULAN SATU PER SATU:
#
#        Tahun -> Kalsel -> Januari -> reload -> baca nilai
#                         Februari -> reload -> baca nilai
#                         ...
#                         Desember -> reload -> baca nilai
#
# 3. Hanya dua measure yang dipakai:
#       Nilai Pembelian Saham
#       Nilai Penjualan Saham
#
#    Measure lain (frekuensi/volume) dikeluarkan dari area data
#    agar index measure tidak ambigu.
#
# 4. Nilai transaksi per bulan:
#
#       Nilai Pembelian + Nilai Penjualan
#
#    BUKAN dibagi dua.
#
#    Dataset memisahkan aktivitas beli dan jual; total aktivitas
#    transaksi investor adalah penjumlahan kedua sisi tersebut.
#
# 5. Return fungsi = TOTAL transaksi 12 bulan dalam MILIAR rupiah.
#
#    Jika Master menghitung rata-rata bulanan:
#       S1 / 12
#
#    Jika indikator "per kapita", S2 tetap harus berisi jumlah
#    penduduk dan formula Master harus mengonversi miliar -> rupiah.
# ============================================================


def ojk_stock_detect_unit_to_miliar(
    driver
):
    """
    Coba deteksi satuan Dataset 277 dari teks halaman.

    Return:
        (factor, label)

    Jika label satuan tidak muncul di DOM setelah PivotGrid difilter,
    jangan gagal. Kembalikan (None, "AUTO_FROM_RAW") dan biarkan
    Python menentukan skala dari nilai numerik mentah yang benar-benar
    diterima dari DevExtreme.
    """

    page_text = (
        collect_page_text(
            driver
        )
        or
        ""
    ).lower()

    if (
        "rupiah (satuan penuh)"
        in page_text
        or
        "rupiah satuan penuh"
        in page_text
    ):
        return (
            1 / 1_000_000_000,
            "Rupiah (Satuan penuh)",
        )

    if (
        "rp miliar"
        in page_text
        or
        "rupiah (miliar)"
        in page_text
        or
        "miliar rupiah"
        in page_text
    ):
        return (
            1.0,
            "Rp Miliar",
        )

    if (
        "rp juta"
        in page_text
        or
        "rupiah (juta)"
        in page_text
        or
        "juta rupiah"
        in page_text
    ):
        return (
            1 / 1000,
            "Rp Juta",
        )

    if (
        "rp triliun"
        in page_text
        or
        "rupiah (triliun)"
        in page_text
        or
        "triliun rupiah"
        in page_text
    ):
        return (
            1000.0,
            "Rp Triliun",
        )

    return (
        None,
        "AUTO_FROM_RAW",
    )


def ojk_stock_infer_unit_from_raw(
    buy_raw,
    sell_raw
):
    """
    Infer skala nilai Dataset 277 bila label 'Satuan' tidak terbaca.

    Dataset OJK umumnya menyimpan nominal sebagai salah satu:
      - Rupiah satuan penuh
      - juta rupiah
      - miliar rupiah

    Untuk transaksi provinsi, raw total bulanan memberi pemisahan
    magnitude yang cukup jelas:

      >= 1e8  -> rupiah satuan penuh
      >= 1e4  -> juta rupiah
      lainnya -> miliar rupiah

    Return:
        factor_to_miliar, label
    """

    raw_total = (
        abs(
            float(
                buy_raw
            )
        )
        +
        abs(
            float(
                sell_raw
            )
        )
    )

    if raw_total >= 100_000_000:
        return (
            1 / 1_000_000_000,
            "Rupiah (Satuan penuh) [auto]",
        )

    if raw_total >= 10_000:
        return (
            1 / 1000,
            "Rp Juta [auto]",
        )

    return (
        1.0,
        "Rp Miliar [auto]",
    )



def ojk_stock_prepare_dataset277(
    driver,
    tahun
):
    """
    V16: jangan mengubah layout PivotGrid Dataset 277.

    Struktur asli dibiarkan utuh:
      Frekuensi Pembelian Saham[data]
      Volume Pembelian Saham[data]
      Nilai Pembelian Saham[data]
      Frekuensi Penjualan Saham[data]
      Volume Penjualan Saham[data]
      Nilai Penjualan Saham[data]
      Bulan[column]
      DATI1[row]

    Yang dilakukan hanya:
      1. baca daftar Tahun untuk validasi coverage;
      2. baca semua key Bulan;
      3. pilih 12 key Bulan milik tahun target.

    Tidak ada filter Tahun server-side.
    Tidak ada perubahan DATI1/DATI2.
    Tidak ada penghapusan measure.

    Alasannya: percobaan V14/V15 menunjukkan perubahan layout/filter
    membuat backend DevExtreme mengembalikan getData() kosong total.
    """

    driver.set_script_timeout(
        120
    )

    result = driver.execute_async_script(
        """
        const targetYear =
            Number(arguments[0]);

        const done =
            arguments[
                arguments.length - 1
            ];

        function normalize(v) {
            return String(v || "")
                .trim()
                .toLowerCase();
        }

        function getInstance(el) {
            try {
                if (
                    window.DevExpress &&
                    DevExpress.ui &&
                    DevExpress.ui.dxPivotGrid
                ) {
                    const inst =
                        DevExpress.ui.dxPivotGrid.getInstance(el);

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            try {
                if (window.jQuery) {
                    const inst =
                        window.jQuery(el)
                            .dxPivotGrid("instance");

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            return null;
        }

        function fieldLabel(field) {
            return normalize(
                field.caption ||
                field.dataField ||
                field.name
            );
        }

        function flattenItems(
            items,
            output
        ) {
            items =
                items || [];

            output =
                output || [];

            for (
                const item
                of items
            ) {
                output.push(
                    item
                );

                if (
                    item.children
                    &&
                    item.children.length
                ) {
                    flattenItems(
                        item.children,
                        output
                    );
                }
            }

            return output;
        }

        function resolveAsync(
            value,
            success,
            failure
        ) {
            if (
                value
                &&
                typeof value.done
                ===
                "function"
            ) {
                value
                    .done(success)
                    .fail(failure);

                return;
            }

            if (
                value
                &&
                typeof value.then
                ===
                "function"
            ) {
                value
                    .then(success)
                    .catch(failure);

                return;
            }

            success(
                value || []
            );
        }

        function yearFromItem(
            item
        ) {
            const candidates = [
                item && item.text,
                item && item.value,
                item && item.key
            ];

            for (
                const raw
                of candidates
            ) {
                if (
                    raw === undefined
                    ||
                    raw === null
                ) {
                    continue;
                }

                const match =
                    String(raw)
                    .match(
                        /(^|[^0-9])((?:19|20)[0-9]{2})([^0-9]|$)/
                    );

                if (match) {
                    return Number(
                        match[2]
                    );
                }
            }

            return null;
        }

        function monthPeriodFromItem(
            item
        ) {
            const candidates = [
                item && item.text,
                item && item.value,
                item && item.key
            ];

            for (
                const raw
                of candidates
            ) {
                if (
                    raw === undefined
                    ||
                    raw === null
                ) {
                    continue;
                }

                const text =
                    String(raw);

                // YYYY-MM
                let match =
                    text.match(
                        /(^|[^0-9])((?:19|20)[0-9]{2})[-/.](0?[1-9]|1[0-2])([^0-9]|$)/
                    );

                if (match) {
                    return {
                        year:
                            Number(
                                match[2]
                            ),
                        month:
                            Number(
                                match[3]
                            )
                    };
                }

                // YYYYMM
                match =
                    text.match(
                        /(^|[^0-9])((?:19|20)[0-9]{2})(0[1-9]|1[0-2])([^0-9]|$)/
                    );

                if (match) {
                    return {
                        year:
                            Number(
                                match[2]
                            ),
                        month:
                            Number(
                                match[3]
                            )
                    };
                }
            }

            return null;
        }

        try {
            const elements =
                Array.from(
                    document.querySelectorAll(
                        ".dx-pivotgrid"
                    )
                );

            let dataSource =
                null;

            let fields =
                null;

            for (
                const el
                of elements
            ) {
                const instance =
                    getInstance(el);

                if (!instance) {
                    continue;
                }

                const ds =
                    instance.getDataSource();

                if (!ds) {
                    continue;
                }

                const fs =
                    ds.fields()
                    ||
                    [];

                const labels =
                    fs.map(
                        fieldLabel
                    );

                if (
                    labels.some(
                        x =>
                            x.includes(
                                "bulan"
                            )
                    )
                    &&
                    labels.some(
                        x =>
                            x.includes(
                                "tahun"
                            )
                    )
                    &&
                    labels.some(
                        x =>
                            x.includes(
                                "dati1"
                            )
                    )
                    &&
                    labels.some(
                        x =>
                            x.includes(
                                "nilai pembelian saham"
                            )
                    )
                    &&
                    labels.some(
                        x =>
                            x.includes(
                                "nilai penjualan saham"
                            )
                    )
                ) {
                    dataSource =
                        ds;

                    fields =
                        fs;

                    break;
                }
            }

            if (
                !dataSource
                ||
                !fields
            ) {
                done({
                    success:
                        false,
                    error:
                        "PivotGrid Dataset 277 tidak ditemukan."
                });

                return;
            }

            function findFieldIndex(
                needle
            ) {
                needle =
                    normalize(
                        needle
                    );

                for (
                    let i = 0;
                    i < fields.length;
                    i++
                ) {
                    const label =
                        fieldLabel(
                            fields[i]
                        );

                    if (
                        label === needle
                        ||
                        label.includes(
                            needle
                        )
                    ) {
                        return i;
                    }
                }

                return -1;
            }

            const yearIndex =
                findFieldIndex(
                    "tahun"
                );

            const monthIndex =
                findFieldIndex(
                    "bulan"
                );

            if (
                yearIndex < 0
                ||
                monthIndex < 0
            ) {
                done({
                    success:
                        false,
                    error:
                        "Field Tahun/Bulan tidak ditemukan."
                });

                return;
            }

            resolveAsync(
                dataSource.getFieldValues(
                    yearIndex
                ),

                function(
                    rawYears
                ) {
                    try {
                        const yearItems =
                            flattenItems(
                                rawYears || [],
                                []
                            );

                        const availableYears =
                            [];

                        for (
                            const item
                            of yearItems
                        ) {
                            const year =
                                yearFromItem(
                                    item
                                );

                            if (
                                year !== null
                                &&
                                !availableYears
                                .includes(
                                    year
                                )
                            ) {
                                availableYears.push(
                                    year
                                );
                            }
                        }

                        availableYears.sort(
                            (a, b) =>
                                a - b
                        );

                        if (
                            !availableYears
                            .includes(
                                targetYear
                            )
                        ) {
                            done({
                                success:
                                    false,
                                error:
                                    "SOURCE_UNAVAILABLE: Tahun "
                                    + targetYear
                                    + " tidak tersedia pada Dataset 277.",
                                availableYears:
                                    availableYears
                            });

                            return;
                        }

                        resolveAsync(
                            dataSource.getFieldValues(
                                monthIndex
                            ),

                            function(
                                rawMonths
                            ) {
                                try {
                                    const monthItems =
                                        flattenItems(
                                            rawMonths || [],
                                            []
                                        );

                                    const byMonth =
                                        {};

                                    const rawDebug =
                                        [];

                                    for (
                                        const item
                                        of monthItems
                                    ) {
                                        rawDebug.push({
                                            text:
                                                item.text,
                                            value:
                                                item.value,
                                            key:
                                                item.key
                                        });

                                        const period =
                                            monthPeriodFromItem(
                                                item
                                            );

                                        if (
                                            !period
                                            ||
                                            period.year
                                            !==
                                            targetYear
                                        ) {
                                            continue;
                                        }

                                        if (
                                            !byMonth[
                                                period.month
                                            ]
                                        ) {
                                            byMonth[
                                                period.month
                                            ] = {
                                                key:
                                                    item.key,
                                                text:
                                                    String(
                                                        item.text
                                                        ??
                                                        item.value
                                                        ??
                                                        item.key
                                                    )
                                            };
                                        }
                                    }

                                    const months =
                                        Object.keys(
                                            byMonth
                                        )
                                        .map(
                                            Number
                                        )
                                        .sort(
                                            (a, b) =>
                                                a - b
                                        );

                                    if (
                                        months.length
                                        !==
                                        12
                                    ) {
                                        done({
                                            success:
                                                false,
                                            error:
                                                "Bulan Dataset 277 tahun "
                                                + targetYear
                                                + " tidak lengkap. "
                                                + "Ditemukan "
                                                + months.length
                                                + " bulan.",
                                            months:
                                                months,
                                            rawMonthDebug:
                                                rawDebug.slice(
                                                    0,
                                                    80
                                                ),
                                            availableYears:
                                                availableYears
                                        });

                                        return;
                                    }

                                    done({
                                        success:
                                            true,
                                        year:
                                            targetYear,
                                        mode:
                                            "PRESERVE_ORIGINAL_LAYOUT",
                                        availableYears:
                                            availableYears,
                                        months:
                                            months.map(
                                                month => ({
                                                    month:
                                                        month,
                                                    key:
                                                        byMonth[
                                                            month
                                                        ].key,
                                                    text:
                                                        byMonth[
                                                            month
                                                        ].text
                                                })
                                            )
                                    });

                                } catch (error) {
                                    done({
                                        success:
                                            false,
                                        error:
                                            String(
                                                error
                                                &&
                                                (
                                                    error.message
                                                    ||
                                                    error
                                                )
                                                ||
                                                error
                                            )
                                    });
                                }
                            },

                            function(error) {
                                done({
                                    success:
                                        false,
                                    error:
                                        "getFieldValues(Bulan) gagal: "
                                        +
                                        String(
                                            error
                                            &&
                                            (
                                                error.message
                                                ||
                                                error
                                            )
                                            ||
                                            error
                                        )
                                });
                            }
                        );

                    } catch (error) {
                        done({
                            success:
                                false,
                            error:
                                String(
                                    error
                                    &&
                                    (
                                        error.message
                                        ||
                                        error
                                    )
                                    ||
                                    error
                                )
                        });
                    }
                },

                function(error) {
                    done({
                        success:
                            false,
                        error:
                            "getFieldValues(Tahun) gagal: "
                            +
                            String(
                                error
                                &&
                                (
                                    error.message
                                    ||
                                    error
                                )
                                ||
                                error
                            )
                    });
                }
            );

        } catch (error) {
            done({
                success:
                    false,
                error:
                    String(
                        error
                        &&
                        (
                            error.message
                            ||
                            error
                        )
                        ||
                        error
                    )
            });
        }
        """,
        int(
            tahun
        ),
    )

    if not result:
        raise RuntimeError(
            "Dataset 277 tidak memberi response "
            "ketika konteks dibaca."
        )

    if not result.get(
        "success"
    ):

        details = []

        years = (
            result.get(
                "availableYears",
                []
            )
            or
            []
        )

        if years:
            details.append(
                "Tahun tersedia: "
                +
                ", ".join(
                    str(
                        year
                    )
                    for year
                    in years
                )
            )

        months = (
            result.get(
                "months",
                []
            )
            or
            []
        )

        if months:
            details.append(
                "Bulan terbaca: "
                +
                ", ".join(
                    f"{int(month):02d}"
                    for month
                    in months
                )
            )

        suffix = (
            " "
            +
            " ".join(
                details
            )
            if details
            else
            ""
        )

        raise RuntimeError(
            "Gagal menyiapkan Dataset 277: "
            +
            str(
                result.get(
                    "error",
                    "unknown error"
                )
            )
            +
            suffix
        )

    return result



def ojk_stock_select_single_month(
    driver,
    month_key,
    month_number
):
    """
    Terapkan SATU Bulan ke PivotGrid lalu reload.
    """

    driver.set_script_timeout(
        90
    )

    result = driver.execute_async_script(
        """
        const monthKey =
            arguments[0];

        const monthNumber =
            Number(arguments[1]);

        const done =
            arguments[
                arguments.length - 1
            ];

        function normalize(v) {
            return String(v || "")
                .trim()
                .toLowerCase();
        }

        function getInstance(el) {
            try {
                if (
                    window.DevExpress &&
                    DevExpress.ui &&
                    DevExpress.ui.dxPivotGrid
                ) {
                    const inst =
                        DevExpress.ui.dxPivotGrid.getInstance(el);

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            try {
                if (window.jQuery) {
                    const inst =
                        window.jQuery(el)
                            .dxPivotGrid("instance");

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            return null;
        }

        try {
            const elements =
                Array.from(
                    document.querySelectorAll(
                        ".dx-pivotgrid"
                    )
                );

            let instance =
                null;

            let dataSource =
                null;

            let fields =
                null;

            for (
                const el
                of elements
            ) {
                const candidate =
                    getInstance(
                        el
                    );

                if (!candidate) {
                    continue;
                }

                const ds =
                    candidate
                    .getDataSource();

                if (!ds) {
                    continue;
                }

                const fs =
                    ds.fields()
                    ||
                    [];

                const labels =
                    fs.map(
                        field =>
                            normalize(
                                field.caption
                                ||
                                field.dataField
                                ||
                                field.name
                            )
                    );

                if (
                    labels.some(
                        value =>
                            value.includes(
                                "bulan"
                            )
                    )
                    &&
                    labels.some(
                        value =>
                            value.includes(
                                "nilai pembelian saham"
                            )
                    )
                    &&
                    labels.some(
                        value =>
                            value.includes(
                                "nilai penjualan saham"
                            )
                    )
                ) {
                    instance =
                        candidate;

                    dataSource =
                        ds;

                    fields =
                        fs;

                    break;
                }
            }

            if (
                !instance
                ||
                !dataSource
                ||
                !fields
            ) {
                done({
                    success:
                        false,
                    error:
                        "PivotGrid Dataset 277 tidak ditemukan "
                        + "saat memilih bulan."
                });

                return;
            }

            let monthIndex =
                -1;

            for (
                let i = 0;
                i < fields.length;
                i++
            ) {
                const label =
                    normalize(
                        fields[i].caption
                        ||
                        fields[i].dataField
                        ||
                        fields[i].name
                    );

                if (
                    label === "bulan"
                    ||
                    label.includes(
                        "bulan"
                    )
                ) {
                    monthIndex =
                        i;

                    break;
                }
            }

            if (
                monthIndex < 0
            ) {
                done({
                    success:
                        false,
                    error:
                        "Field Bulan tidak ditemukan."
                });

                return;
            }

            dataSource.field(
                monthIndex,
                {
                    area:
                        "column",
                    areaIndex:
                        0,
                    filterType:
                        "include",
                    filterValues: [
                        monthKey
                    ]
                }
            );

            const reloadResult =
                dataSource.reload();

            function finish() {
                try {
                    if (
                        instance.repaint
                    ) {
                        instance.repaint();
                    }
                } catch (e) {}

                done({
                    success:
                        true,
                    month:
                        monthNumber,
                    monthKey:
                        monthKey
                });
            }

            function failure(error) {
                done({
                    success:
                        false,
                    error:
                        String(
                            error
                            &&
                            (
                                error.message
                                ||
                                error
                            )
                            ||
                            "reload gagal"
                        )
                });
            }

            if (
                reloadResult
                &&
                typeof reloadResult.done
                ===
                "function"
            ) {
                reloadResult
                    .done(
                        finish
                    )
                    .fail(
                        failure
                    );

                return;
            }

            if (
                reloadResult
                &&
                typeof reloadResult.then
                ===
                "function"
            ) {
                reloadResult
                    .then(
                        finish
                    )
                    .catch(
                        failure
                    );

                return;
            }

            setTimeout(
                finish,
                4000
            );

        } catch (error) {
            done({
                success:
                    false,
                error:
                    String(
                        error
                        &&
                        (
                            error.message
                            ||
                            error
                        )
                        ||
                        error
                    )
            });
        }
        """,
        month_key,
        int(
            month_number
        ),
    )

    if not result:
        raise RuntimeError(
            f"Tidak ada response saat memilih bulan "
            f"{int(month_number):02d}."
        )

    if not result.get(
        "success"
    ):
        raise RuntimeError(
            f"Gagal memilih bulan "
            f"{int(month_number):02d}: "
            +
            str(
                result.get(
                    "error",
                    "unknown error"
                )
            )
        )

    return result


def ojk_stock_single_column_index(
    pivot_data
):
    """
    Dataset setelah filter satu bulan seharusnya memiliki satu
    column index transaksi.

    Header parent/child dapat menghasilkan duplikasi index,
    maka gunakan unique index.
    """

    indexes = []

    for item in (
        pivot_data.get(
            "columns",
            []
        )
        or
        []
    ):

        try:
            index = int(
                item.get(
                    "index"
                )
            )

        except (
            TypeError,
            ValueError
        ):
            continue

        if (
            index
            not in indexes
        ):
            indexes.append(
                index
            )

    if len(
        indexes
    ) == 1:
        return indexes[0]

    if len(
        indexes
    ) > 1:
        # Biasanya index terakhir adalah leaf month.
        return indexes[-1]

    # Bila header kolom tidak dikirim tetapi values ada,
    # kolom data pertama adalah index 0.
    values = (
        pivot_data.get(
            "values",
            []
        )
        or
        []
    )

    if values:
        return 0

    raise RuntimeError(
        "Index kolom bulan tidak berhasil ditentukan."
    )


def ojk_stock_total_measure_for_column(
    pivot_data,
    column_index,
    measure_index
):
    """
    Ambil total satu measure pada satu bulan.

    Karena DATI1 sudah menjadi filter, biasanya tidak ada row
    dimensi yang perlu dijumlahkan. Tetap sediakan beberapa
    fallback agar format PivotGrid tidak mudah membuat drama.
    """

    grand_row = (
        pivot_data.get(
            "grandTotalRowIndex"
        )
    )

    if (
        grand_row
        is not None
    ):
        value = ojk_pivot_value(
            pivot_data,
            row_index=int(
                grand_row
            ),
            column_index=int(
                column_index
            ),
            measure_index=int(
                measure_index
            ),
        )

        if value is not None:
            return float(
                value
            )

    rows = (
        pivot_data.get(
            "rows",
            []
        )
        or
        []
    )

    # Explicit total row.
    for row in rows:

        label = normalize_ojk_text(
            row.get(
                "path"
            )
            or
            row.get(
                "text"
            )
            or
            row.get(
                "value"
            )
            or
            ""
        )

        if (
            label == "total"
            or
            label.endswith(
                " > total"
            )
        ):

            row_index = (
                row.get(
                    "index"
                )
            )

            if (
                row_index
                is None
            ):
                continue

            value = ojk_pivot_value(
                pivot_data,
                row_index=int(
                    row_index
                ),
                column_index=int(
                    column_index
                ),
                measure_index=int(
                    measure_index
                ),
            )

            if value is not None:
                return float(
                    value
                )

    # Sum unique leaf rows jika masih ada row dimension.
    leaf_values = []

    seen = set()

    for row in rows:

        if row.get(
            "hasChildren"
        ):
            continue

        row_index = (
            row.get(
                "index"
            )
        )

        if (
            row_index
            is None
        ):
            continue

        try:
            row_index = int(
                row_index
            )

        except (
            TypeError,
            ValueError
        ):
            continue

        if (
            row_index
            in seen
        ):
            continue

        seen.add(
            row_index
        )

        value = ojk_pivot_value(
            pivot_data,
            row_index=row_index,
            column_index=int(
                column_index
            ),
            measure_index=int(
                measure_index
            ),
        )

        if value is not None:
            leaf_values.append(
                float(
                    value
                )
            )

    if leaf_values:
        return sum(
            leaf_values
        )

    # Fallback ke values row pertama.
    raw_values = (
        pivot_data.get(
            "values",
            []
        )
        or
        []
    )

    if raw_values:

        try:
            raw = (
                raw_values[0]
                [
                    int(
                        column_index
                    )
                ]
                [
                    int(
                        measure_index
                    )
                ]
            )

            value = parse_ojk_number(
                raw
            )

            if value is not None:
                return float(
                    value
                )

        except (
            IndexError,
            TypeError,
            KeyError
        ):
            pass

    return None




def ojk_stock_select_all_months(
    driver,
    month_items
):
    """
    Pilih seluruh 12 bulan pada tahun target sekaligus.

    Tujuan:
        meminta PivotGrid/OJK mengembalikan agregat nilai transaksi
        selama satu tahun, bukan memproses bulan satu per satu.

    Layout asli Dataset 277 tetap dipertahankan.
    """

    month_keys = [
        item.get(
            "key"
        )
        for item in month_items
        if item.get(
            "key"
        )
        is not None
    ]

    if len(
        month_keys
    ) != 12:
        raise RuntimeError(
            f"Key bulan yang akan difilter hanya "
            f"{len(month_keys)}, seharusnya 12."
        )

    driver.set_script_timeout(
        90
    )

    result = driver.execute_async_script(
        """
        const monthKeys =
            arguments[0];

        const done =
            arguments[
                arguments.length - 1
            ];

        function normalize(v) {
            return String(v || "")
                .trim()
                .toLowerCase();
        }

        function getInstance(el) {
            try {
                if (
                    window.DevExpress &&
                    DevExpress.ui &&
                    DevExpress.ui.dxPivotGrid
                ) {
                    const inst =
                        DevExpress.ui.dxPivotGrid.getInstance(el);

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            try {
                if (window.jQuery) {
                    const inst =
                        window.jQuery(el)
                            .dxPivotGrid("instance");

                    if (inst) {
                        return inst;
                    }
                }
            } catch (e) {}

            return null;
        }

        try {
            const elements =
                Array.from(
                    document.querySelectorAll(
                        ".dx-pivotgrid"
                    )
                );

            let instance =
                null;

            let dataSource =
                null;

            let fields =
                null;

            for (
                const el
                of elements
            ) {
                const candidate =
                    getInstance(el);

                if (!candidate) {
                    continue;
                }

                const ds =
                    candidate.getDataSource();

                if (!ds) {
                    continue;
                }

                const fs =
                    ds.fields()
                    ||
                    [];

                const labels =
                    fs.map(
                        field =>
                            normalize(
                                field.caption
                                ||
                                field.dataField
                                ||
                                field.name
                            )
                    );

                if (
                    labels.some(
                        value =>
                            value.includes(
                                "bulan"
                            )
                    )
                    &&
                    labels.some(
                        value =>
                            value.includes(
                                "dati1"
                            )
                    )
                    &&
                    labels.some(
                        value =>
                            value.includes(
                                "nilai pembelian saham"
                            )
                    )
                    &&
                    labels.some(
                        value =>
                            value.includes(
                                "nilai penjualan saham"
                            )
                    )
                ) {
                    instance =
                        candidate;

                    dataSource =
                        ds;

                    fields =
                        fs;

                    break;
                }
            }

            if (
                !instance
                ||
                !dataSource
                ||
                !fields
            ) {
                done({
                    success:
                        false,
                    error:
                        "PivotGrid Dataset 277 tidak ditemukan."
                });

                return;
            }

            let monthIndex =
                -1;

            for (
                let i = 0;
                i < fields.length;
                i++
            ) {
                const label =
                    normalize(
                        fields[i].caption
                        ||
                        fields[i].dataField
                        ||
                        fields[i].name
                    );

                if (
                    label === "bulan"
                    ||
                    label.includes(
                        "bulan"
                    )
                ) {
                    monthIndex =
                        i;
                    break;
                }
            }

            if (
                monthIndex < 0
            ) {
                done({
                    success:
                        false,
                    error:
                        "Field Bulan tidak ditemukan."
                });

                return;
            }

            // HANYA filterValues. Jangan ubah area/layout.
            dataSource.field(
                monthIndex,
                {
                    filterType:
                        "include",
                    filterValues:
                        monthKeys
                }
            );

            const reloadResult =
                dataSource.reload();

            function finish() {
                try {
                    if (
                        instance.repaint
                    ) {
                        instance.repaint();
                    }
                } catch (e) {}

                done({
                    success:
                        true,
                    monthCount:
                        monthKeys.length
                });
            }

            function failure(error) {
                done({
                    success:
                        false,
                    error:
                        String(
                            error
                            &&
                            (
                                error.message
                                ||
                                error
                            )
                            ||
                            "reload gagal"
                        )
                });
            }

            if (
                reloadResult
                &&
                typeof reloadResult.done
                ===
                "function"
            ) {
                reloadResult
                    .done(finish)
                    .fail(failure);
                return;
            }

            if (
                reloadResult
                &&
                typeof reloadResult.then
                ===
                "function"
            ) {
                reloadResult
                    .then(finish)
                    .catch(failure);
                return;
            }

            setTimeout(
                finish,
                4000
            );

        } catch (error) {
            done({
                success:
                    false,
                error:
                    String(
                        error
                        &&
                        (
                            error.message
                            ||
                            error
                        )
                        ||
                        error
                    )
            });
        }
        """,
        month_keys,
    )

    if not result:
        raise RuntimeError(
            "Tidak ada response saat memilih 12 bulan."
        )

    if not result.get(
        "success"
    ):
        raise RuntimeError(
            "Gagal memilih 12 bulan Dataset 277: "
            +
            str(
                result.get(
                    "error",
                    "unknown error"
                )
            )
        )

    return result


def ojk_stock_extract_annual_buy_sell(
    pivot_data
):
    """
    Ambil nilai transaksi SETAHUN untuk row KALIMANTAN SELATAN.

    Layout asli Dataset 277:
      measure 2 = Nilai Pembelian Saham
      measure 5 = Nilai Penjualan Saham

    Strategi:
      - cari row KALIMANTAN SELATAN
      - bila hanya ada 1 cell data, anggap itu agregat 12 bulan
      - bila ada 12 cell data, jumlahkan semuanya
      - bila tersedia grandTotalColumnIndex yang valid, pakai grand total
    """

    target = "kalimantan selatan"

    rows = (
        pivot_data.get(
            "rows",
            []
        )
        or
        []
    )

    values = (
        pivot_data.get(
            "values",
            []
        )
        or
        []
    )

    buy_measure_index = 2
    sell_measure_index = 5

    target_row_index = None
    target_label = None

    for row in rows:

        label = normalize_ojk_text(
            row.get(
                "path"
            )
            or
            row.get(
                "text"
            )
            or
            row.get(
                "value"
            )
            or
            ""
        )

        if target not in label:
            continue

        try:
            row_index = int(
                row.get(
                    "index"
                )
            )
        except (
            TypeError,
            ValueError
        ):
            continue

        target_row_index = row_index
        target_label = label
        break

    if target_row_index is None:
        return (
            None,
            None,
            "KALSEL_ROW_NOT_FOUND",
            0,
        )

    try:
        matrix_row = values[
            target_row_index
        ]
    except (
        IndexError,
        TypeError
    ):
        return (
            None,
            None,
            "KALSEL_VALUES_ROW_MISSING",
            0,
        )

    if not isinstance(
        matrix_row,
        (list, tuple)
    ):
        return (
            None,
            None,
            "KALSEL_VALUES_ROW_INVALID",
            0,
        )

    def pair_from_cell(
        cell
    ):
        if not isinstance(
            cell,
            (list, tuple)
        ):
            return None

        if len(
            cell
        ) <= sell_measure_index:
            return None

        buy = parse_ojk_number(
            cell[
                buy_measure_index
            ]
        )

        sell = parse_ojk_number(
            cell[
                sell_measure_index
            ]
        )

        if (
            buy is None
            or
            sell is None
        ):
            return None

        return (
            float(
                buy
            ),
            float(
                sell
            ),
        )

    # Grand total column kalau memang valid.
    grand_col = (
        pivot_data.get(
            "grandTotalColumnIndex"
        )
    )

    if (
        grand_col is not None
    ):
        try:
            grand_col = int(
                grand_col
            )

            if (
                0 <= grand_col < len(
                    matrix_row
                )
            ):
                pair = pair_from_cell(
                    matrix_row[
                        grand_col
                    ]
                )

                if pair is not None:
                    return (
                        pair[0],
                        pair[1],
                        f"GRAND_TOTAL[{target_label}]",
                        1,
                    )
        except (
            TypeError,
            ValueError
        ):
            pass

    pairs = []

    for cell in matrix_row:
        pair = pair_from_cell(
            cell
        )

        if pair is not None:
            pairs.append(
                pair
            )

    if len(
        pairs
    ) == 1:
        return (
            pairs[0][0],
            pairs[0][1],
            f"ANNUAL_AGGREGATE[{target_label}]",
            1,
        )

    if len(
        pairs
    ) == 12:
        return (
            sum(
                item[0]
                for item in pairs
            ),
            sum(
                item[1]
                for item in pairs
            ),
            f"SUM_12_MONTHS[{target_label}]",
            12,
        )

    return (
        None,
        None,
        f"AMBIGUOUS_CELL_COUNT[{target_label}]",
        len(
            pairs
        ),
    )



def ojk_stock_extract_buy_sell_single_month(
    pivot_data
):
    """
    V16 memakai layout asli Dataset 277.

    Urutan measure asli:
      0 Frekuensi Pembelian Saham
      1 Volume Pembelian Saham
      2 Nilai Pembelian Saham
      3 Frekuensi Penjualan Saham
      4 Volume Penjualan Saham
      5 Nilai Penjualan Saham

    DATI1 tetap ROW, maka cari row KALIMANTAN SELATAN.
    Bulan sedang difilter satu per satu.
    """

    target = (
        "kalimantan selatan"
    )

    buy_measure_index = 2
    sell_measure_index = 5

    values = (
        pivot_data.get(
            "values",
            []
        )
        or
        []
    )

    rows = (
        pivot_data.get(
            "rows",
            []
        )
        or
        []
    )

    candidates = []

    for row in rows:

        label = normalize_ojk_text(
            row.get(
                "path"
            )
            or
            row.get(
                "text"
            )
            or
            row.get(
                "value"
            )
            or
            ""
        )

        if target not in label:
            continue

        row_index = (
            row.get(
                "index"
            )
        )

        try:
            row_index = int(
                row_index
            )
        except (
            TypeError,
            ValueError
        ):
            continue

        candidates.append(
            (
                row_index,
                label,
            )
        )

    if not candidates:
        return (
            None,
            None,
            "KALSEL_ROW_NOT_FOUND",
        )

    for row_index, label in candidates:

        try:
            matrix_row = values[
                row_index
            ]
        except (
            IndexError,
            TypeError
        ):
            continue

        if not isinstance(
            matrix_row,
            (list, tuple)
        ):
            continue

        # Satu bulan aktif. Cari cell yang punya minimal 6 measure.
        for cell in matrix_row:

            if not isinstance(
                cell,
                (list, tuple)
            ):
                continue

            if len(
                cell
            ) <= sell_measure_index:
                continue

            buy = parse_ojk_number(
                cell[
                    buy_measure_index
                ]
            )

            sell = parse_ojk_number(
                cell[
                    sell_measure_index
                ]
            )

            if (
                buy is not None
                and
                sell is not None
            ):
                return (
                    float(
                        buy
                    ),
                    float(
                        sell
                    ),
                    f"DATI1_ROW[{label}]",
                )

    return (
        None,
        None,
        "KALSEL_ROW_FOUND_BUT_VALUE_EMPTY",
    )



def ojk_stock_debug_pivot_shape(
    pivot_data
):
    """
    Ringkasan getData() untuk error diagnostics.
    """

    rows = (
        pivot_data.get(
            "rows",
            []
        )
        or
        []
    )

    columns = (
        pivot_data.get(
            "columns",
            []
        )
        or
        []
    )

    values = (
        pivot_data.get(
            "values",
            []
        )
        or
        []
    )

    labels = []

    for row in rows[:20]:
        label = (
            row.get(
                "path"
            )
            or
            row.get(
                "text"
            )
            or
            row.get(
                "value"
            )
            or
            ""
        )

        if label:
            labels.append(
                str(
                    label
                )
            )

    first_shape = []

    for matrix_row in values[:3]:

        if isinstance(
            matrix_row,
            (list, tuple)
        ):
            cell_shapes = []

            for cell in matrix_row[:5]:

                if isinstance(
                    cell,
                    (list, tuple)
                ):
                    cell_shapes.append(
                        len(
                            cell
                        )
                    )
                else:
                    cell_shapes.append(
                        type(
                            cell
                        ).__name__
                    )

            first_shape.append(
                cell_shapes
            )

        else:
            first_shape.append(
                type(
                    matrix_row
                ).__name__
            )

    return {
        "rows":
            len(
                rows
            ),
        "columns":
            len(
                columns
            ),
        "values_rows":
            len(
                values
            ),
        "first_shapes":
            first_shape,
        "row_labels":
            labels,
        "grand_row":
            pivot_data.get(
                "grandTotalRowIndex"
            ),
        "grand_col":
            pivot_data.get(
                "grandTotalColumnIndex"
            ),
    }



def scrape_ojk_stock_transaction_annual(
    url,
    tahun,
    description
):
    """
    Dataset 277.

    Return:
        total transaksi saham Januari-Desember Kalimantan Selatan
        dalam MILIAR RUPIAH.

    Per bulan:
        Nilai Pembelian Saham + Nilai Penjualan Saham
    """

    tahun = int(
        tahun
    )

    dataset_id = (
        ojk_dataset_id_from_url(
            url
        )
        or
        "unknown"
    )

    cache_key = (
        "ojk_stock_annual_v20",
        dataset_id,
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[
            cache_key
        ]

    print(
        f"    [OJK-SAHAM] Parser build = "
        f"{SCRAPER_BUILD}"
    )

    print(
        f"    [OJK-SAHAM] Dataset "
        f"{dataset_id}"
    )

    print(
        f"    [OJK-SAHAM] Tahun = "
        f"{tahun}"
    )

    print(
        "    [OJK-SAHAM] Wilayah = "
        "KALIMANTAN SELATAN"
    )

    driver = (
        create_chrome_driver()
    )

    try:
        driver.set_page_load_timeout(
            90
        )

        driver.get(
            url
        )

        WebDriverWait(
            driver,
            45
        ).until(
            lambda d:
            d.execute_script(
                "return document.readyState"
            )
            ==
            "complete"
        )

        time.sleep(
            8
        )

        ojk_wait_for_pivotgrid(
            driver,
            timeout=45
        )

        fields = (
            ojk_get_pivot_fields(
                driver
            )
        )

        if fields:
            print(
                "    [OJK-SAHAM] Field = "
                +
                ", ".join(
                    (
                        field.get(
                            "caption"
                        )
                        or
                        field.get(
                            "dataField"
                        )
                        or
                        field.get(
                            "name"
                        )
                        or
                        "?"
                    )
                    +
                    "["
                    +
                    (
                        field.get(
                            "area"
                        )
                        or
                        "-"
                    )
                    +
                    "]"
                    for field
                    in fields
                )
            )

        context = (
            ojk_stock_prepare_dataset277(
                driver,
                tahun=tahun,
            )
        )

        month_items = (
            context.get(
                "months",
                []
            )
            or
            []
        )

        available_years = (
            context.get(
                "availableYears",
                []
            )
            or
            []
        )

        print(
            "    [OJK-SAHAM] Tahun tersedia = "
            +
            ", ".join(
                str(
                    year
                )
                for year
                in available_years
            )
        )

        print(
            "    [OJK-SAHAM] Konteks Dataset 277:"
        )

        print(
            f"    [OJK-SAHAM]   Tahun target = "
            f"{tahun} (tidak difilter server-side)"
        )

        print(
            "    [OJK-SAHAM]   Layout = ASLI portal, tidak diubah"
        )

        print(
            "    [OJK-SAHAM]   DATI1 = tetap ROW; "
            "KALIMANTAN SELATAN dipilih dari getData()"
        )

        print(
            "    [OJK-SAHAM]   Bulan = "
            "12 bulan dipilih sekaligus untuk agregat tahunan"
        )

        if len(
            month_items
        ) != 12:
            raise RuntimeError(
                f"Dataset 277 tahun {tahun} "
                f"tidak memiliki 12 key bulan."
            )

        factor, unit_label = (
            ojk_stock_detect_unit_to_miliar(
                driver
            )
        )

        if factor is None:
            print(
                "    [OJK-SAHAM] Satuan portal tidak tampil "
                "di DOM; skala akan dideteksi dari raw value."
            )
        else:
            print(
                "    [OJK-SAHAM] Satuan portal = "
                f"{unit_label}"
            )

        # V16 mempertahankan 6 measure asli Dataset 277.
        # Index Nilai Pembelian/Penjualan dipetakan di extractor.
        # ----------------------------------------------------
        # V18: ambil nilai SETAHUN secara langsung.
        # Tidak ada loop Januari-Desember.
        # ----------------------------------------------------
        ojk_stock_select_all_months(
            driver,
            month_items=month_items,
        )

        time.sleep(
            4
        )

        pivot_data = (
            ojk_get_loaded_pivot_data(
                driver
            )
        )

        (
            buy_raw,
            sell_raw,
            extraction_method,
            cell_count,
        ) = (
            ojk_stock_extract_annual_buy_sell(
                pivot_data
            )
        )

        if (
            buy_raw is None
            or
            sell_raw is None
        ):
            debug = (
                ojk_stock_debug_pivot_shape(
                    pivot_data
                )
            )

            raise RuntimeError(
                "Nilai transaksi tahunan Kalimantan Selatan "
                f"tidak berhasil dibaca. "
                f"method={extraction_method}, "
                f"cell_count={cell_count}, "
                f"rows={debug['rows']}, "
                f"columns={debug['columns']}, "
                f"values_rows={debug['values_rows']}, "
                f"grand_row={debug['grand_row']}, "
                f"grand_col={debug['grand_col']}, "
                f"shape={debug['first_shapes']}, "
                f"row_labels="
                +
                " | ".join(
                    debug[
                        "row_labels"
                    ][:12]
                )
            )

        if factor is None:

            factor, unit_label = (
                ojk_stock_infer_unit_from_raw(
                    buy_raw,
                    sell_raw,
                )
            )

            print(
                "    [OJK-SAHAM] Raw tahunan = "
                f"Beli={buy_raw} | Jual={sell_raw}"
            )

            print(
                "    [OJK-SAHAM] Satuan terdeteksi = "
                f"{unit_label}"
            )

        buy_miliar = (
            float(
                buy_raw
            )
            *
            factor
        )

        sell_miliar = (
            float(
                sell_raw
            )
            *
            factor
        )

        annual_total = (
            buy_miliar
            +
            sell_miliar
        )

        print(
            "    [OJK-SAHAM] Nilai transaksi SETAHUN:"
        )

        print(
            "    [OJK-SAHAM]   Pembelian = "
            f"{format_id(buy_miliar)} miliar"
        )

        print(
            "    [OJK-SAHAM]   Penjualan = "
            f"{format_id(sell_miliar)} miliar"
        )

        print(
            "    [OJK-SAHAM]   Total = "
            f"{format_id(annual_total)} miliar"
        )

        print(
            "    [OJK-SAHAM]   Metode = "
            f"{extraction_method}"
        )

        print(
            "    [OJK-SAHAM]   Rata-rata /12 = "
            f"{format_id(annual_total / 12)} miliar"
        )

        if annual_total < 0:
            raise RuntimeError(
                "Total transaksi saham tahunan negatif."
            )

        SCRAPE_CACHE[
            cache_key
        ] = (
            annual_total
        )

        return annual_total

    finally:

        try:
            driver.quit()
        except Exception:
            pass
