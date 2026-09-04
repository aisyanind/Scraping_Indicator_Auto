import argparse
import json

from engine.run_indicator import process_indicator_job
from jobs.worker import run_worker
from models import IndicatorJob


def main():
    parser = argparse.ArgumentParser(
        description="Scraping Indikator Auto"
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser(
        "worker",
        help="Jalankan queue worker Apps Script.",
    )

    test = sub.add_parser(
        "test",
        help="Tes satu source tanpa Sheet.",
    )
    test.add_argument("--kode", default="TEST")
    test.add_argument("--nama", required=True)
    test.add_argument("--tahun", required=True)
    test.add_argument("--wilayah", default="Prov. Kalimantan Selatan")
    test.add_argument("--url", required=True)
    test.add_argument("--ambil", required=True)
    test.add_argument("--rumus", default="S1")

    args = parser.parse_args()

    if args.command == "worker":
        run_worker()
        return

    if args.command == "test":
        job = IndicatorJob(
            job_id="LOCAL-TEST",
            kode=args.kode,
            nama_indikator=args.nama,
            tahun=args.tahun,
            wilayah=args.wilayah,
            sumber_1=args.url,
            data_diambil_1=args.ambil,
            rumus=args.rumus,
        )

        output = process_indicator_job(job)
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
