from __future__ import annotations

import csv
from pathlib import Path


def load_municipality_codes(csv_path: str | Path) -> list[dict[str, str]]:
	"""
	Load Nepal municipality codes from codes.csv.
	"""
	path = Path(csv_path)
	if not path.is_absolute():
		path = Path(__file__).resolve().parent / path

	if not path.exists():
		raise FileNotFoundError(f"Municipality code CSV not found: {path}")

	codes: list[dict[str, str]] = []
	with path.open("r", encoding="utf-8-sig", newline="") as handle:
		reader = csv.DictReader(handle)
		for row in reader:
			code = (row.get("Code") or row.get("code") or row.get("municipality_code") or "").strip()
			if not code:
				continue
			codes.append(
				{
					"code": code.zfill(5),
					"district": (row.get("District") or row.get("district") or "").strip(),
					"name_en": (
						row.get("Update name of the municipality - English")
						or row.get("municipality_name")
						or row.get("name")
						or ""
					).strip(),
					"name_np": (
						row.get("Updated name of the municipality - Nepali")
						or row.get("name_np")
						or ""
					).strip(),
				}
			)

	if not codes:
		raise ValueError(f"No municipality codes found in {path}")

	return codes
