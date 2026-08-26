#!/usr/bin/env python3
"""
Scan <parent_dir>/<structure_id>/*.cif* files, classify Gemmi CIF datablocks,
write:
  - structures.log
  - summary.pdf

Install:
  pip install gemmi reportlab
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from html import escape
import logging
from pathlib import Path
import traceback
from gemmi import cif
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Flowable,
    Paragraph,
    Spacer,
    KeepTogether,
)


COORDINATES_PREFIXES = ("_atom_site",)
REFLECTIONS_PREFIXES = ("_refln", "_diffrn_refln")
CCD_PREFIXES = ("_pdbx_chem_comp_audit",)
RESTRAINTS_PREFIXES = ("_chem_comp_atom",)
CONTACT_AUTHOR_CATEGORY = "_pdbx_contact_author."
ENTRY_AUTHOR_CATEGORY = "_audit_author."
DEPOSIT_GROUP_CATEGORY = "_pdbx_deposit_group."
DATABASE_STATUS_CATEGORY = "_pdbx_database_status."
STRUCT_CATEGORY = "_struct."
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Author:
    name: str
    orcid: str | None = None


@dataclass(frozen=True)
class AuthorLists:
    contact_authors: tuple[Author, ...] = ()
    entry_authors: tuple[Author, ...] = ()

    def merged(self, other: AuthorLists) -> AuthorLists:
        return AuthorLists(
            contact_authors=dedupe_authors(
                self.contact_authors + other.contact_authors
            ),
            entry_authors=dedupe_authors(self.entry_authors + other.entry_authors),
        )


@dataclass(frozen=True)
class DepositGroupInfo:
    group_title: str = ""
    group_description: str = ""
    group_type: str = ""


@dataclass(frozen=True)
class ReleaseInfo:
    coordinates: str = ""
    sequence: str = ""


@dataclass(frozen=True)
class CoordinateBlockMetadata:
    block_id: str
    authors: AuthorLists
    deposit_group: DepositGroupInfo
    release_info: ReleaseInfo
    struct_title: str = ""


@dataclass(frozen=True)
class CoordinateRecord:
    structure_id: str
    filename: str
    metadata: CoordinateBlockMetadata


@dataclass
class ValidationResult:
    name: str
    passed: bool
    message: str
    structure_messages: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ValidationSummary:
    results: list[ValidationResult]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    def messages_for_structure(self, structure_id: str) -> list[str]:
        messages: list[str] = []
        for result in self.results:
            messages.extend(result.structure_messages.get(structure_id, []))
        return messages


@dataclass(frozen=True)
class StructureGroupKey:
    entry_author_names: tuple[str, ...]
    release_info: ReleaseInfo
    status_label: str
    entry_author_display: str = field(default="", compare=False)


@dataclass
class FileResult:
    path: Path
    roles: set[str] = field(default_factory=set)
    block_roles: list[tuple[str, str]] = field(default_factory=list)
    authors: AuthorLists | None = None
    coordinate_blocks: list[CoordinateBlockMetadata] = field(default_factory=list)
    error: str | None = None


@dataclass
class StructureResult:
    structure_id: str
    files: list[FileResult] = field(default_factory=list)


@dataclass(frozen=True)
class StructureStatus:
    label: str
    message: str | None
    fill_color: colors.Color
    stripe_color: colors.Color
    border_color: colors.Color
    text_color: colors.Color


def html_color(color: colors.Color) -> str:
    red = round(color.red * 255)
    green = round(color.green * 255)
    blue = round(color.blue * 255)
    return f"#{red:02X}{green:02X}{blue:02X}"


class StructureSummaryRow(Flowable):
    def __init__(
        self,
        structure_id: str,
        summary_text: str,
        detail_text: str | None,
        status: StructureStatus,
        styles,
    ) -> None:
        super().__init__()
        self.structure_id = structure_id
        self.summary_text = summary_text
        self.detail_text = detail_text
        self.status = status
        self.styles = styles
        self.width = 0
        self.height = 0
        self._summary_para: Paragraph | None = None
        self._detail_para: Paragraph | None = None
        self._message_para: Paragraph | None = None
        self._summary_height = 0
        self._detail_height = 0
        self._message_height = 0

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        pad_x = 6 * mm
        pad_y = 2.4 * mm
        gap = 1.2 * mm if self.status.message else 0
        content_width = availWidth - (2 * pad_x)

        status = (
            f'<font color="{html_color(self.status.text_color)}">'
            f"<b>{escape(self.status.label)}</b></font>"
        )
        summary = (
            f"{status}: <b>{escape(self.structure_id)}</b> - "
            f"({escape(self.summary_text)})"
        )
        self._summary_para = Paragraph(summary, self.styles["StructureSummary"])
        _, self._summary_height = self._summary_para.wrap(
            content_width, availHeight
        )

        if self.detail_text:
            self._detail_para = Paragraph(
                escape(self.detail_text), self.styles["StructureDetail"]
            )
            _, self._detail_height = self._detail_para.wrap(
                content_width, availHeight
            )

        if self.status.message:
            message = (
                f'<font color="{html_color(self.status.text_color)}">'
                f"{escape(self.status.message)}</font>"
            )
            self._message_para = Paragraph(
                message, self.styles["IssueMessage"]
            )
            _, self._message_height = self._message_para.wrap(
                content_width, availHeight
            )

        self.height = max(
            9 * mm,
            pad_y
            + self._summary_height
            + self._detail_height
            + gap
            + self._message_height
            + pad_y,
        )
        return availWidth, self.height

    def draw(self):
        canvas = self.canv
        pad_x = 6 * mm
        pad_y = 2.4 * mm
        stripe_width = 2.5 * mm

        canvas.saveState()
        canvas.setFillColor(self.status.fill_color)
        canvas.setStrokeColor(self.status.border_color)
        canvas.setLineWidth(0.6)
        canvas.roundRect(0, 0, self.width, self.height, 2.4 * mm, stroke=1, fill=1)

        canvas.setFillColor(self.status.stripe_color)
        canvas.roundRect(0, 0, stripe_width, self.height, 2.4 * mm, stroke=0, fill=1)
        canvas.rect(stripe_width / 2, 0, stripe_width, self.height, stroke=0, fill=1)

        y = self.height - pad_y
        if self._summary_para:
            y -= self._summary_height
            self._summary_para.drawOn(canvas, pad_x, y)
        if self._detail_para:
            y -= self._detail_height
            self._detail_para.drawOn(canvas, pad_x, y)
        if self._message_para:
            y -= 1.2 * mm + self._message_height
            self._message_para.drawOn(canvas, pad_x, y)

        canvas.restoreState()


class GroupHeader(Flowable):
    def __init__(self, text: str, styles) -> None:
        super().__init__()
        self.text = text
        self.styles = styles
        self.width = 0
        self.height = 0
        self._paragraph: Paragraph | None = None
        self._paragraph_height = 0

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        pad_x = 5 * mm
        pad_y = 2.4 * mm
        self._paragraph = Paragraph(escape(self.text), self.styles["GroupHeader"])
        _, self._paragraph_height = self._paragraph.wrap(
            availWidth - (2 * pad_x),
            availHeight,
        )
        self.height = max(10 * mm, pad_y + self._paragraph_height + pad_y)
        return availWidth, self.height

    def draw(self):
        canvas = self.canv
        pad_x = 5 * mm
        pad_y = 2.4 * mm
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#EEF2F7"))
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.setLineWidth(0.5)
        canvas.roundRect(0, 0, self.width, self.height, 1.8 * mm, stroke=1, fill=1)
        if self._paragraph:
            self._paragraph.drawOn(canvas, pad_x, self.height - pad_y - self._paragraph_height)
        canvas.restoreState()


def normalize_category(cat: str) -> str:
    return cat.rstrip(".")


def clean_cif_value(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in {"", ".", "?"}:
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def get_mmcif_category(block, category_name: str) -> dict:
    names = [category_name]
    if category_name.endswith("."):
        names.append(category_name[:-1])
    else:
        names.append(f"{category_name}.")

    for name in names:
        try:
            category = block.get_mmcif_category(name)
        except Exception:
            continue
        if category:
            return category
    return {}


def category_rows(category: dict) -> list[dict[str, str]]:
    if not category:
        return []

    row_count = 1
    for values in category.values():
        if isinstance(values, (list, tuple)):
            row_count = max(row_count, len(values))

    rows: list[dict[str, str]] = []
    for row_index in range(row_count):
        row: dict[str, str] = {}
        for column, values in category.items():
            if isinstance(values, (list, tuple)):
                value = values[row_index] if row_index < len(values) else ""
            else:
                value = values if row_index == 0 else ""
            row[column] = clean_cif_value(value)
        rows.append(row)
    return rows


def first_category_value(block, category_name: str, item_name: str) -> str:
    rows = category_rows(get_mmcif_category(block, category_name))
    if not rows:
        return ""
    return rows[0].get(item_name, "")


def author_sort_value(value: str, fallback: int) -> tuple[int, int | str]:
    if value.isdigit():
        return (0, int(value))
    if value:
        return (1, value)
    return (2, fallback)


def sorted_author_rows(rows: list[dict[str, str]], order_key: str) -> list[dict[str, str]]:
    indexed_rows = list(enumerate(rows))
    indexed_rows.sort(
        key=lambda item: author_sort_value(item[1].get(order_key, ""), item[0])
    )
    return [row for _, row in indexed_rows]


def dedupe_authors(authors: tuple[Author, ...]) -> tuple[Author, ...]:
    seen: set[Author] = set()
    unique: list[Author] = []
    for author in authors:
        if author in seen:
            continue
        seen.add(author)
        unique.append(author)
    return tuple(unique)


def contact_author_name(row: dict[str, str]) -> str:
    if row.get("name"):
        return row["name"]
    return " ".join(
        part
        for part in (
            row.get("name_salutation", ""),
            row.get("name_first", ""),
            row.get("name_mi", ""),
            row.get("name_last", ""),
        )
        if part
    )


def extract_contact_authors(block) -> tuple[Author, ...]:
    rows = sorted_author_rows(
        category_rows(get_mmcif_category(block, CONTACT_AUTHOR_CATEGORY)), "id"
    )
    authors: list[Author] = []
    for row in rows:
        name = contact_author_name(row)
        orcid = row.get("identifier_ORCID") or None
        if name or orcid:
            authors.append(Author(name=name or "unnamed author", orcid=orcid))
    return tuple(authors)


def extract_entry_authors(block) -> tuple[Author, ...]:
    rows = sorted_author_rows(
        category_rows(get_mmcif_category(block, ENTRY_AUTHOR_CATEGORY)),
        "pdbx_ordinal",
    )
    authors: list[Author] = []
    for row in rows:
        name = row.get("name", "")
        orcid = row.get("identifier_ORCID") or None
        if name or orcid:
            authors.append(Author(name=name or "unnamed author", orcid=orcid))
    return tuple(authors)


def extract_author_lists(block) -> AuthorLists:
    return AuthorLists(
        contact_authors=extract_contact_authors(block),
        entry_authors=extract_entry_authors(block),
    )


def extract_deposit_group(block) -> DepositGroupInfo:
    return DepositGroupInfo(
        group_title=first_category_value(
            block,
            DEPOSIT_GROUP_CATEGORY,
            "group_title",
        ),
        group_description=first_category_value(
            block,
            DEPOSIT_GROUP_CATEGORY,
            "group_description",
        ),
        group_type=first_category_value(
            block,
            DEPOSIT_GROUP_CATEGORY,
            "group_type",
        ),
    )


def extract_release_info(block) -> ReleaseInfo:
    return ReleaseInfo(
        coordinates=first_category_value(
            block,
            DATABASE_STATUS_CATEGORY,
            "dep_release_code_coordinates",
        ),
        sequence=first_category_value(
            block,
            DATABASE_STATUS_CATEGORY,
            "dep_release_code_sequence",
        ),
    )


def extract_struct_title(block) -> str:
    return first_category_value(block, STRUCT_CATEGORY, "title")


def extract_coordinate_metadata(block, block_id: str) -> CoordinateBlockMetadata:
    return CoordinateBlockMetadata(
        block_id=block_id,
        authors=extract_author_lists(block),
        deposit_group=extract_deposit_group(block),
        release_info=extract_release_info(block),
        struct_title=extract_struct_title(block),
    )


def classify_block(block) -> str:
    categories = {normalize_category(cat) for cat in block.get_mmcif_category_names()}

    # A datablock gets one role; priority prevents shared categories such as
    # _chem_comp_atom from making coordinate or CCD blocks look like restraints.
    if any(cat.startswith(COORDINATES_PREFIXES) for cat in categories):
        return "coordinate"
    if any(cat.startswith(REFLECTIONS_PREFIXES) for cat in categories):
        return "reflections"
    if any(cat.startswith(CCD_PREFIXES) for cat in categories):
        return "ccd"
    if any(cat.startswith(RESTRAINTS_PREFIXES) for cat in categories):
        return "restraints"
    return "metadata"


def classify_file(path: Path) -> FileResult:
    result = FileResult(path=path)
    try:
        doc = cif.read_file(str(path))
        for i, block in enumerate(doc, start=1):
            block_id = f"block_{i}"
            role = classify_block(block)
            result.roles.add(role)
            result.block_roles.append((block_id, role))
            if role == "coordinate":
                metadata = extract_coordinate_metadata(block, block_id)
                result.coordinate_blocks.append(metadata)
                authors = metadata.authors
                result.authors = (
                    authors if result.authors is None else result.authors.merged(authors)
                )

    except Exception as exc:
        tb = traceback.format_exc()
        result.error = f"{type(exc).__name__}: {exc}"

        # Print useful debug info to stdout/stderr
        print(f"\n[ERROR] Failed to read {path}", file=sys.stderr)
        print(result.error, file=sys.stderr)
        print(tb, file=sys.stderr)

    return result


def structure_counts(structure: StructureResult) -> dict[str, int]:
    coord_files = 0
    combined_files = 0
    restraint_only_files = 0
    refl_files = 0

    for f in structure.files:
        roles = f.roles
        has_coord = "coordinate" in roles
        has_restraint = "restraints" in roles
        has_refl = "reflections" in roles

        if has_coord:
            coord_files += 1
        if has_coord and has_restraint:
            combined_files += 1
        elif has_restraint:
            restraint_only_files += 1
        if has_refl:
            refl_files += 1

    return {
        "coord_files": coord_files,
        "combined_files": combined_files,
        "restraint_only_files": restraint_only_files,
        "refl_files": refl_files,
        "total_restraint_bearing": combined_files + restraint_only_files,
    }


def quantity_text(count: int, singular: str, plural: str | None = None) -> str:
    label = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {label}"


def unreadable_files(structure: StructureResult) -> list[FileResult]:
    return [f for f in structure.files if f.error]


def format_name_list(names: list[str], limit: int = 3) -> str:
    if len(names) <= limit:
        return ", ".join(names)
    visible = ", ".join(names[:limit])
    remaining = len(names) - limit
    return f"{visible}, and {remaining} more"


def filename_list(files: list[FileResult], limit: int = 3) -> str:
    return format_name_list([f.path.name for f in files], limit)


def unreadable_summary(files: list[FileResult]) -> str:
    if not files:
        return ""

    count = len(files)
    filename_word = "filename" if count == 1 else "filenames"
    return (
        f"unable to read {quantity_text(count, 'CIF file')} "
        f"({filename_word}: {filename_list(files)})"
    )


def coordinate_files(structure: StructureResult) -> list[FileResult]:
    return [
        f
        for f in structure.files
        if "coordinate" in f.roles and f.error is None
    ]


def coordinate_records(results: list[StructureResult]) -> list[CoordinateRecord]:
    records: list[CoordinateRecord] = []
    for structure in results:
        records.extend(structure_coordinate_records(structure))
    return records


def structure_coordinate_records(structure: StructureResult) -> list[CoordinateRecord]:
    records: list[CoordinateRecord] = []
    for file_result in coordinate_files(structure):
        for metadata in file_result.coordinate_blocks:
            records.append(
                CoordinateRecord(
                    structure_id=structure.structure_id,
                    filename=file_result.path.name,
                    metadata=metadata,
                )
            )
    return records


def first_coordinate_metadata(
    structure: StructureResult,
) -> CoordinateBlockMetadata | None:
    records = structure_coordinate_records(structure)
    if not records:
        return None
    return records[0].metadata


def author_lists_for_file(file_result: FileResult) -> AuthorLists:
    if not file_result.coordinate_blocks:
        return file_result.authors or AuthorLists()

    authors = AuthorLists()
    for metadata in file_result.coordinate_blocks:
        authors = authors.merged(metadata.authors)
    return authors


def format_author(author: Author) -> str:
    if author.orcid:
        return f"{author.name} (ORCID: {author.orcid})"
    return author.name


def format_author_list(authors: tuple[Author, ...]) -> str:
    if not authors:
        return "missing"
    return "; ".join(format_author(author) for author in authors)


def entry_author_names(authors: AuthorLists) -> tuple[str, ...]:
    return tuple(author.name for author in authors.entry_authors)


def format_entry_author_names(names: tuple[str, ...]) -> str:
    if not names:
        return "missing"
    return "; ".join(names)


def format_release_value(value: str) -> str:
    return value or "missing"


def record_label(record: CoordinateRecord) -> str:
    return f"{record.structure_id}/{record.filename}/{record.metadata.block_id}"


def add_structure_message(
    messages: dict[str, list[str]],
    structure_id: str,
    message: str,
) -> None:
    messages.setdefault(structure_id, []).append(message)


def format_deposit_group(info: DepositGroupInfo) -> str:
    return (
        f"title={info.group_title or 'missing'}, "
        f"description={info.group_description or 'missing'}, "
        f"type={info.group_type or 'missing'}"
    )


def missing_deposit_group_fields(info: DepositGroupInfo) -> list[str]:
    missing = []
    if not info.group_title:
        missing.append("group_title")
    if not info.group_description:
        missing.append("group_description")
    if not info.group_type:
        missing.append("group_type")
    return missing


def format_contact_authors(authors: tuple[Author, ...]) -> str:
    return format_author_list(authors)


def structure_detail_text(structure: StructureResult) -> str | None:
    records = structure_coordinate_records(structure)
    if not records:
        return None

    details = []
    for record in records:
        title = record.metadata.struct_title or "missing title"
        if len(records) == 1:
            details.append(f"{title} (coordinate file: {record.filename})")
        else:
            details.append(f"{title} ({record.filename})")

    label = "Title" if len(details) == 1 else "Titles"
    return f"{label}: " + "; ".join(details)


def validation_result(
    name: str,
    passed: bool,
    message: str,
    structure_messages: dict[str, list[str]] | None = None,
) -> ValidationResult:
    return ValidationResult(
        name=name,
        passed=passed,
        message=message,
        structure_messages=structure_messages or {},
    )


def validate_deposit_group_consistency(
    records: list[CoordinateRecord],
) -> ValidationResult:
    name = "deposit group metadata"
    if not records:
        return validation_result(
            name,
            True,
            "No coordinate datablocks found for deposit group validation.",
        )

    standard = records[0].metadata.deposit_group
    mismatches = [
        record for record in records if record.metadata.deposit_group != standard
    ]
    missing_records = [
        record
        for record in records
        if missing_deposit_group_fields(record.metadata.deposit_group)
    ]
    if not mismatches and not missing_records:
        return validation_result(
            name,
            True,
            f"All coordinate datablocks use {format_deposit_group(standard)}.",
        )

    structure_messages: dict[str, list[str]] = {}
    for record in missing_records:
        fields = ", ".join(missing_deposit_group_fields(record.metadata.deposit_group))
        add_structure_message(
            structure_messages,
            record.structure_id,
            f"missing _pdbx_deposit_group {fields} in {record.filename}",
        )

    for record in mismatches:
        add_structure_message(
            structure_messages,
            record.structure_id,
            "_pdbx_deposit_group differs from the first coordinate datablock",
        )

    return validation_result(
        name,
        False,
        "Missing or inconsistent _pdbx_deposit_group group_title, "
        "group_description, or group_type across coordinate datablocks.",
        structure_messages,
    )


def validate_contact_authors_consistency(
    records: list[CoordinateRecord],
) -> ValidationResult:
    name = "contact authors"
    if not records:
        return validation_result(
            name,
            True,
            "No coordinate datablocks found for contact author validation.",
        )

    standard = records[0].metadata.authors.contact_authors
    structure_messages: dict[str, list[str]] = {}
    failed = False

    for record in records:
        authors = record.metadata.authors.contact_authors
        if not authors:
            failed = True
            add_structure_message(
                structure_messages,
                record.structure_id,
                f"missing contact authors in {record.filename}",
            )
        elif authors != standard:
            failed = True
            add_structure_message(
                structure_messages,
                record.structure_id,
                "contact authors differ from the first coordinate datablock",
            )

    if not failed:
        return validation_result(
            name,
            True,
            f"All coordinate datablocks use {format_contact_authors(standard)}.",
        )

    return validation_result(
        name,
        False,
        "Contact authors are missing or inconsistent across coordinate datablocks.",
        structure_messages,
    )


def validate_struct_titles_unique(records: list[CoordinateRecord]) -> ValidationResult:
    name = "structure titles"
    structure_messages: dict[str, list[str]] = {}
    titles: dict[str, list[CoordinateRecord]] = {}
    failed = False

    for record in records:
        title = record.metadata.struct_title
        if not title:
            failed = True
            add_structure_message(
                structure_messages,
                record.structure_id,
                f"missing _struct.title in {record.filename}",
            )
            continue
        titles.setdefault(title, []).append(record)

    for title, matching_records in titles.items():
        if len(matching_records) <= 1:
            continue
        failed = True
        labels = format_name_list([record_label(record) for record in matching_records])
        for record in matching_records:
            add_structure_message(
                structure_messages,
                record.structure_id,
                f"duplicate _struct.title {title!r} also found in {labels}",
            )

    if not failed:
        return validation_result(
            name,
            True,
            "All coordinate datablocks have unique _struct.title values.",
        )

    return validation_result(
        name,
        False,
        "_struct.title values are missing or duplicated across coordinate datablocks.",
        structure_messages,
    )


def report_validations() -> list:
    return [
        validate_deposit_group_consistency,
        validate_contact_authors_consistency,
        validate_struct_titles_unique,
    ]


def run_validations(results: list[StructureResult]) -> ValidationSummary:
    records = coordinate_records(results)
    validation_results = [validate(records) for validate in report_validations()]

    for result in validation_results:
        if result.passed:
            LOGGER.info("Validation passed: %s - %s", result.name, result.message)
        else:
            LOGGER.error("Validation failed: %s - %s", result.name, result.message)
            for structure_id, messages in result.structure_messages.items():
                for message in messages:
                    LOGGER.error("Validation detail [%s]: %s", structure_id, message)

    return ValidationSummary(validation_results)


def first_coordinate_record(
    records: list[CoordinateRecord],
) -> CoordinateRecord | None:
    if not records:
        return None
    return records[0]


def group_key_for_structure(
    structure: StructureResult,
    status: StructureStatus,
) -> StructureGroupKey:
    metadata = first_coordinate_metadata(structure)
    if metadata is None:
        return StructureGroupKey((), ReleaseInfo(), status.label, "missing")

    return StructureGroupKey(
        entry_author_names=entry_author_names(metadata.authors),
        release_info=metadata.release_info,
        status_label=status.label,
        entry_author_display=format_author_list(metadata.authors.entry_authors),
    )


def group_header_text(group_key: StructureGroupKey) -> str:
    return (
        "Entry authors: "
        f"{group_key.entry_author_display or format_entry_author_names(group_key.entry_author_names)} | "
        "Coordinate release: "
        f"{format_release_value(group_key.release_info.coordinates)} | "
        "Sequence release: "
        f"{format_release_value(group_key.release_info.sequence)} | "
        f"Status: {group_key.status_label}"
    )


def grouped_structures(
    results: list[StructureResult],
    validation_summary: ValidationSummary,
) -> list[tuple[StructureGroupKey, list[tuple[StructureResult, StructureStatus]]]]:
    groups: dict[StructureGroupKey, list[tuple[StructureResult, StructureStatus]]] = {}
    for structure in results:
        status = structure_status(
            structure,
            validation_summary.messages_for_structure(structure.structure_id),
        )
        key = group_key_for_structure(structure, status)
        groups.setdefault(key, []).append((structure, status))
    return list(groups.items())


def count_summary_text(counts: dict[str, int]) -> str:
    parts: list[str] = []

    if counts["combined_files"]:
        n = counts["combined_files"]
        parts.append(quantity_text(n, "coordinate+restraints file"))

    coord_only = counts["coord_files"] - counts["combined_files"]
    if coord_only or not counts["combined_files"]:
        parts.append(quantity_text(coord_only, "coordinate file"))

    parts.append(
        quantity_text(
            counts["restraint_only_files"],
            "restraints file",
        )
    )

    parts.append(quantity_text(counts["refl_files"], "reflections file"))

    return ", ".join(parts)


def structure_summary_text(structure: StructureResult) -> str:
    counts = structure_counts(structure)
    parts = [count_summary_text(counts)]
    unreadable = unreadable_summary(unreadable_files(structure))
    if unreadable:
        parts.append(unreadable)
    return ", ".join(parts)


def issue_message(
    structure: StructureResult,
    counts: dict[str, int],
    validation_messages: list[str] | None = None,
) -> str:
    issues: list[str] = []
    unreadable = unreadable_files(structure)

    if unreadable:
        issue = "unreadable CIF file present"
        if len(unreadable) != 1:
            issue = f"{len(unreadable)} unreadable CIF files present"
        issues.append(issue)

    coord_count = counts["coord_files"]
    if coord_count == 0:
        issues.append("no coordinate/model file found")
    elif coord_count > 1:
        issues.append(f"{coord_count} coordinate/model files found")

    restraint_count = counts["total_restraint_bearing"]
    if restraint_count == 0:
        issues.append("missing restraints")

    reflection_count = counts["refl_files"]
    if reflection_count == 0:
        issues.append("no reflections file found")
    elif reflection_count > 1:
        issues.append(f"{reflection_count} reflections files found")

    issues.extend(validation_messages or [])

    return "; ".join(issues)


def structure_status(
    structure: StructureResult,
    validation_messages: list[str] | None = None,
) -> StructureStatus:
    counts = structure_counts(structure)
    unreadable_count = len(unreadable_files(structure))
    validation_messages = validation_messages or []
    has_expected_models = counts["coord_files"] == 1
    has_restraints = counts["total_restraint_bearing"] >= 1
    has_expected_reflections = counts["refl_files"] == 1
    is_green = (
        has_expected_models
        and has_restraints
        and has_expected_reflections
        and unreadable_count == 0
        and not validation_messages
    )

    if is_green:
        return StructureStatus(
            label="OK",
            message=None,
            fill_color=colors.HexColor("#EDF8F1"),
            stripe_color=colors.HexColor("#2EAF6D"),
            border_color=colors.HexColor("#B7E3C8"),
            text_color=colors.HexColor("#155C38"),
        )

    is_yellow = has_expected_models and has_expected_reflections and unreadable_count == 0
    if is_yellow:
        yellow_messages = []
        if not has_restraints:
            yellow_messages.append("Missing restraints.")
        yellow_messages.extend(validation_messages)
        if not yellow_messages:
            yellow_messages.append("Review recommended.")

        if not has_restraints and not validation_messages:
            label = "Missing restraints"
        elif has_restraints and validation_messages:
            label = "Validation review"
        else:
            label = "Needs attention"

        return StructureStatus(
            label=label,
            message="; ".join(yellow_messages),
            fill_color=colors.HexColor("#FFF8E6"),
            stripe_color=colors.HexColor("#E7A728"),
            border_color=colors.HexColor("#F0D693"),
            text_color=colors.HexColor("#7A4D00"),
        )

    return StructureStatus(
        label="Review needed",
        message=issue_message(structure, counts, validation_messages),
        fill_color=colors.HexColor("#FFF0F0"),
        stripe_color=colors.HexColor("#DF5959"),
        border_color=colors.HexColor("#F0B8B8"),
        text_color=colors.HexColor("#8C2B2B"),
    )


def find_structure_dirs(parent_dir: Path) -> list[Path]:
    return sorted(
        [p for p in parent_dir.iterdir() if p.is_dir()],
        key=lambda p: p.name.lower(),
    )


def find_cif_files(structure_dir: Path) -> list[Path]:
    files = []
    for p in sorted(structure_dir.iterdir(), key=lambda x: x.name.lower()):
        name = p.name.lower()
        if p.is_file() and (
            name.endswith(".cif")
            or name.endswith(".mmcif")
            or name.endswith(".cif.gz")
            or name.endswith(".mmcif.gz")
        ):
            files.append(p)
    return files


def scan_parent_dir(parent_dir: Path) -> list[StructureResult]:
    results: list[StructureResult] = []

    for structure_dir in find_structure_dirs(parent_dir):
        structure = StructureResult(structure_id=structure_dir.name)
        for cif_path in find_cif_files(structure_dir):
            structure.files.append(classify_file(cif_path))
        results.append(structure)

    return results


def write_structures_log(results: list[StructureResult], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as fh:
        for structure in results:
            for file_result in structure.files:
                fh.write(f"{structure.structure_id}\n")
                fh.write(f"    {file_result.path.name}\n")


def draw_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#68707C"))
    canvas.drawRightString(
        doc.pagesize[0] - doc.rightMargin,
        8 * mm,
        f"Page {doc.page}",
    )
    canvas.restoreState()


def first_deposit_group_info(results: list[StructureResult]) -> DepositGroupInfo:
    record = first_coordinate_record(coordinate_records(results))
    if record is None:
        return DepositGroupInfo()
    return record.metadata.deposit_group


def first_contact_authors(results: list[StructureResult]) -> tuple[Author, ...]:
    record = first_coordinate_record(coordinate_records(results))
    if record is None:
        return ()
    return record.metadata.authors.contact_authors


def validation_summary_text(validation_summary: ValidationSummary) -> str:
    if validation_summary.passed:
        names = ", ".join(result.name for result in validation_summary.results)
        return f"Validations passed: {names}."

    failures = [
        f"{result.name}: {result.message}"
        for result in validation_summary.results
        if not result.passed
    ]
    return "Validation errors: " + " ".join(failures)


def build_pdf(
    results: list[StructureResult],
    output_path: Path,
    group_id: str = "not provided",
    validation_summary: ValidationSummary | None = None,
) -> None:
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=colors.HexColor("#18212F"),
            spaceAfter=3,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSubtle",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#566172"),
            spaceAfter=1.8 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="StructureSummary",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.8,
            leading=10.6,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="StructureDetail",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=7.7,
            leading=9.2,
            textColor=colors.HexColor("#4B5563"),
            spaceAfter=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="GroupHeader",
            parent=styles["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=8.4,
            leading=10.2,
            textColor=colors.HexColor("#1F2937"),
            spaceAfter=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ValidationOk",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.2,
            textColor=colors.HexColor("#155C38"),
            spaceAfter=1.8 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ValidationError",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.2,
            textColor=colors.HexColor("#8C2B2B"),
            spaceAfter=1.8 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="IssueMessage",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=9.5,
            spaceAfter=0,
        )
    )

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=13 * mm,
        bottomMargin=14 * mm,
        title="PDBe File Transfer summary",
        author="deposition-toolkit",
    )

    story = []
    generated_on = date.today().isoformat()
    structure_count = len(results)
    validation_summary = validation_summary or run_validations(results)
    deposit_group = first_deposit_group_info(results)
    contact_authors = first_contact_authors(results)

    story.append(Paragraph("PDBe File Transfer summary", styles["ReportTitle"]))
    story.append(Paragraph("Generated via deposition-toolkit", styles["ReportSubtle"]))
    story.append(
        Paragraph(
            f"Generated on: {generated_on} - {structure_count} "
            f"{'structure' if structure_count == 1 else 'structures'}",
            styles["ReportSubtle"],
        )
    )
    story.append(Paragraph(f"Group ID: {escape(group_id)}", styles["ReportSubtle"]))
    story.append(
        Paragraph(
            f"Group title: {escape(deposit_group.group_title or 'missing')}",
            styles["ReportSubtle"],
        )
    )
    story.append(
        Paragraph(
            f"Group type: {escape(deposit_group.group_type or 'missing')}",
            styles["ReportSubtle"],
        )
    )
    story.append(
        Paragraph(
            "Group description: "
            f"{escape(deposit_group.group_description or 'missing')}",
            styles["ReportSubtle"],
        )
    )
    story.append(
        Paragraph(
            f"Contact authors: {escape(format_author_list(contact_authors))}",
            styles["ReportSubtle"],
        )
    )
    # validation_style = (
    #     styles["ValidationOk"] if validation_summary.passed else styles["ValidationError"]
    # )
    # story.append(
    #     Paragraph(
    #         escape(validation_summary_text(validation_summary)),
    #         validation_style,
    #     )
    # )
    story.append(Spacer(1, 2 * mm))

    for group_key, structures in grouped_structures(results, validation_summary):
        story.append(KeepTogether([GroupHeader(group_header_text(group_key), styles)]))
        story.append(Spacer(1, 1.2 * mm))
        for structure, status in structures:
            row = StructureSummaryRow(
                structure.structure_id,
                structure_summary_text(structure),
                structure_detail_text(structure),
                status,
                styles,
            )
            story.append(KeepTogether([row, Spacer(1, 1.8 * mm)]))
        story.append(Spacer(1, 1.5 * mm))

    if not results:
        story.append(Paragraph("No structures found.", styles["ReportSubtle"]))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(argv) != 3:
        print("Usage: python dep_report.py <parent_dir> <group_id>", file=sys.stderr)
        return 1

    parent_dir = Path(argv[1]).expanduser().resolve()
    group_id = argv[2]
    if not parent_dir.is_dir():
        print(f"Not a directory: {parent_dir}", file=sys.stderr)
        return 1

    results = scan_parent_dir(parent_dir)
    validation_summary = run_validations(results)

    write_structures_log(results, parent_dir / "structures.log")
    build_pdf(
        results,
        parent_dir / "summary.pdf",
        group_id,
        validation_summary,
    )

    print(f"Wrote: {parent_dir / 'structures.log'}")
    print(f"Wrote: {parent_dir / 'summary.pdf'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
