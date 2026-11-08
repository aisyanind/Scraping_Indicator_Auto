from sources.tbc_source import _parse_tbc_case_counts_words


def test_parse_tbc_case_count_chart_rows_and_province_total():
    labels = [
        ("KOTA", "BANJARBARU"),
        ("KOTA", "BANJARMASIN"),
        ("KAB.", "BALANGAN"),
        ("KAB.", "TANAH", "BUMBU"),
        ("KAB.", "TABALONG"),
        ("KAB.", "HULU", "SUNGAI", "UTARA"),
        ("KAB.", "HULU", "SUNGAI", "TENGAH"),
        ("KAB.", "HULU", "SUNGAI", "SELATAN"),
        ("KAB.", "TAPIN"),
        ("KAB.", "BARITO", "KUALA"),
        ("KAB.", "BANJAR"),
        ("KAB.", "KOTABARU"),
        ("KAB.", "TANAH", "LAUT"),
    ]
    totals = [
        (288, 438), (1041, 1726), (165, 195), (194, 320),
        (182, 324), (232, 293), (257, 404), (150, 238),
        (94, 163), (147, 213), (381, 665), (325, 424), (271, 398),
    ]

    words = []
    for index, (label, pair) in enumerate(zip(labels, totals)):
        y = 100 + index * 15
        for offset, token in enumerate(label):
            words.append({
                "text": token,
                "x0": 100 + offset * 12,
                "top": y,
            })
        words.extend([
            {"text": str(pair[0]), "x0": 205, "top": y - 3},
            {"text": str(pair[1]), "x0": 285, "top": y + 2},
        ])

    # Simulasikan teks halaman PDF secara lengkap. Parser memvalidasi
    # bahwa seluruh 13 label wilayah memang terdapat pada halaman grafik.
    all_region_labels = " ".join(
        " ".join(label) for label in labels
    )
    page_text = (
        "JUMLAH SEMUA KASUS TUBERKULOSIS YANG DITEMUKAN DAN DIOBATI "
        "PROV.KALIMANTAN SELATAN TAHUN 2024 "
        + all_region_labels
        + " mencapai sebesar 9.528"
    )
    year, values = _parse_tbc_case_counts_words(words, page_text)

    assert year == 2024
    assert values["Kota Banjarbaru"] == 726
    assert values["Kota Banjarmasin"] == 2767
    assert values["Kab. Banjar"] == 1046
    assert values["Kab. Tanah Laut"] == 669
    assert values["Prov. Kalimantan Selatan"] == 9528
    assert len(values) == 14
