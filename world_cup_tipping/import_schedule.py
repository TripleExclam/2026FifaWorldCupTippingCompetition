from __future__ import annotations

import argparse
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import isoformat_z, match_id_for_number, stage_for_match_number
from .storage import PROJECT_ROOT, get_store


NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
A1_RE = re.compile(r"([A-Z]+)([0-9]+)")


@dataclass(frozen=True)
class Sheet:
    rows: dict[int, dict[int, Any]]


def colnum(column: str) -> int:
    value = 0
    for char in column:
        value = value * 26 + ord(char) - 64
    return value


def excel_serial_to_utc(value: str | int | float) -> datetime:
    return (datetime(1899, 12, 30, tzinfo=UTC) + timedelta(days=float(value))).replace(microsecond=0)


def load_workbook_sheet(path: Path, sheet_name: str) -> Sheet:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _read_shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relationship_map = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}

        sheet_path = None
        sheets = workbook.find("m:sheets", NS)
        if sheets is None:
            raise ValueError("Workbook does not contain a sheets collection")
        for sheet in sheets:
            if sheet.attrib.get("name") == sheet_name:
                rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                sheet_path = "xl/" + relationship_map[rel_id].lstrip("/")
                break
        if sheet_path is None:
            raise ValueError(f"Sheet not found: {sheet_name}")

        worksheet = ET.fromstring(archive.read(sheet_path))
        rows: dict[int, dict[int, Any]] = {}
        for row in worksheet.findall(".//m:sheetData/m:row", NS):
            row_index = int(row.attrib["r"])
            values: dict[int, Any] = {}
            for cell in row.findall("m:c", NS):
                match = A1_RE.match(cell.attrib.get("r", ""))
                if not match:
                    continue
                value_node = cell.find("m:v", NS)
                if value_node is None:
                    continue
                value: Any = value_node.text
                if cell.attrib.get("t") == "s":
                    value = shared_strings[int(value)]
                values[colnum(match.group(1))] = value
            if values:
                rows[row_index] = values
    return Sheet(rows=rows)


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall("m:si", NS):
        values.append("".join(text.text or "" for text in item.findall(".//m:t", NS)))
    return values


def import_world_cup_schedule(path: Path) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    sheet = load_workbook_sheet(path, "2026 World Cup")
    groups = _parse_groups(sheet)
    group_by_team = {team: group for group, teams in groups.items() for team in teams}

    fixtures: list[dict[str, Any]] = []
    fixtures.extend(_parse_group_fixtures(sheet, group_by_team))
    fixtures.extend(_parse_knockout_fixtures(sheet))
    fixtures.sort(key=lambda fixture: fixture["match_number"])
    return fixtures, groups


def _parse_groups(sheet: Sheet) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    current_group: str | None = None
    for row_index in range(1, 100):
        value = sheet.rows.get(row_index, {}).get(10)
        if not isinstance(value, str):
            continue
        if value.startswith("Group "):
            current_group = value.replace("Group ", "").strip()
            groups[current_group] = []
            continue
        if current_group and value and value not in {"PL", "W", "DRAW", "L", "GF - GA", "PNT"}:
            groups[current_group].append(value)
            if len(groups[current_group]) == 4:
                current_group = None
    return groups


def _parse_group_fixtures(sheet: Sheet, group_by_team: dict[str, str]) -> list[dict[str, Any]]:
    fixtures = []
    for row_index in range(1, 100):
        row = sheet.rows.get(row_index, {})
        try:
            match_number = int(float(row.get(1)))
        except (TypeError, ValueError):
            continue
        if not 1 <= match_number <= 72:
            continue
        team_a = str(row[5])
        team_b = str(row[8])
        kickoff_at = isoformat_z(excel_serial_to_utc(row[18]))
        fixtures.append(
            {
                "match_id": match_id_for_number(match_number),
                "match_number": match_number,
                "stage": "group",
                "group": group_by_team.get(team_a),
                "team_a": team_a,
                "team_b": team_b,
                "team_a_placeholder": None,
                "team_b_placeholder": None,
                "kickoff_at": kickoff_at,
                "score_a": None,
                "score_b": None,
                "winner": None,
                "status": "scheduled",
            }
        )
    return fixtures


def _parse_knockout_fixtures(sheet: Sheet) -> list[dict[str, Any]]:
    round_of_32 = _parse_round_of_32(sheet)
    later_rounds = [
        (89, 70, 75, 12, "Winner 74", "Winner 77"),
        (90, 70, 75, 20, "Winner 73", "Winner 75"),
        (91, 70, 75, 44, "Winner 76", "Winner 78"),
        (92, 70, 75, 52, "Winner 79", "Winner 80"),
        (93, 70, 75, 28, "Winner 83", "Winner 84"),
        (94, 70, 75, 36, "Winner 81", "Winner 82"),
        (95, 70, 75, 60, "Winner 86", "Winner 88"),
        (96, 70, 75, 68, "Winner 85", "Winner 87"),
        (97, 77, 82, 16, "Winner 89", "Winner 90"),
        (98, 77, 82, 32, "Winner 93", "Winner 94"),
        (99, 77, 82, 48, "Winner 91", "Winner 92"),
        (100, 77, 82, 64, "Winner 95", "Winner 96"),
        (101, 84, 89, 23, "Winner 97", "Winner 98"),
        (102, 84, 89, 55, "Winner 99", "Winner 100"),
        (103, 91, 96, 48, "Loser 101", "Loser 102"),
        (104, 91, 96, 37, "Winner 101", "Winner 102"),
    ]
    return round_of_32 + [
        _knockout_fixture(sheet, number, match_col, serial_col, row, team_a, team_b)
        for number, match_col, serial_col, row, team_a, team_b in later_rounds
    ]


def _parse_round_of_32(sheet: Sheet) -> list[dict[str, Any]]:
    rows = [18, 10, 22, 42, 14, 46, 50, 54, 34, 38, 26, 30, 66, 58, 70, 62]
    fixtures = []
    for row_index in rows:
        row = sheet.rows[row_index]
        match_number = int(float(row[63]))
        fixtures.append(
            _knockout_fixture(
                sheet,
                match_number,
                63,
                68,
                row_index,
                str(row.get(64) or f"Team A {match_number}"),
                str(sheet.rows.get(row_index + 1, {}).get(64) or f"Team B {match_number}"),
            )
        )
    return fixtures


def _knockout_fixture(
    sheet: Sheet,
    match_number: int,
    match_col: int,
    serial_col: int,
    row_index: int,
    team_a_placeholder: str,
    team_b_placeholder: str,
) -> dict[str, Any]:
    date_row = sheet.rows.get(row_index - 1, {})
    kickoff_value = date_row.get(serial_col)
    if kickoff_value is None:
        raise ValueError(f"Missing kickoff serial for match {match_number}")
    return {
        "match_id": match_id_for_number(match_number),
        "match_number": match_number,
        "stage": stage_for_match_number(match_number),
        "group": None,
        "team_a": None,
        "team_b": None,
        "team_a_placeholder": team_a_placeholder,
        "team_b_placeholder": team_b_placeholder,
        "kickoff_at": isoformat_z(excel_serial_to_utc(kickoff_value)),
        "score_a": None,
        "score_b": None,
        "winner": None,
        "status": "scheduled",
    }


def save_imported_schedule(xlsx_path: Path, data_dir: Path | None = None) -> tuple[int, int]:
    fixtures, groups = import_world_cup_schedule(xlsx_path)
    store = get_store(data_dir)
    with store.locked():
        store.write("fixtures.json", fixtures)
        store.write("groups.json", groups)
    return len(fixtures), len(groups)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the World Cup workbook into JSON data files.")
    parser.add_argument("--xlsx", type=Path, default=PROJECT_ROOT / "world_cup_2026_v1.3.xlsx")
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args()
    fixture_count, group_count = save_imported_schedule(args.xlsx, args.data_dir)
    print(f"Imported {fixture_count} fixtures and {group_count} groups.")


if __name__ == "__main__":
    main()
