"""Shared document model. Coordinates are PDF points, origin top-left, y down."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _round_box(box: list[float] | tuple[float, ...]) -> list[float]:
    return [round(float(v), 2) for v in box]


@dataclass
class DetectedLine:
    text: str
    bbox: list[float]
    font_size: float
    bold: bool = False
    italic: bool = False
    color: list[int] = field(default_factory=lambda: [20, 20, 20])
    confidence: float = 1.0
    angle: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TextBlock:
    id: str
    page: int
    bbox: list[float]
    draw_bbox: list[float]
    source_boxes: list[list[float]]
    text: str
    translation: str = ""
    kind: str = "paragraph"
    font_size: float = 11.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "left"
    valign: str = "top"
    color: list[int] = field(default_factory=lambda: [20, 20, 20])
    confidence: float = 1.0
    angle: float = 0.0
    table_id: str | None = None
    row: int | None = None
    col: int | None = None
    edited: bool = False
    preserved: bool = False
    fit_scale: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TextBlock:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Issue:
    code: str
    message: str
    severity: str
    page: int | None = None
    block_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Issue:
        return cls(**data)


@dataclass
class PageState:
    index: int
    width_pt: float
    height_pt: float
    ocr_status: str = "pending"
    translation_status: str = "pending"
    render_status: str = "pending"
    engine: str = ""
    error: str | None = None
    approved: bool = False
    width_px: int = 0
    height_px: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PageState:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Job:
    id: str
    filename: str
    source_name: str
    status: str = "queued"
    stage: str = "Queued"
    page_index: int = 0
    page_count: int = 0
    force_ocr: bool = False
    error: str | None = None
    pages: list[PageState] = field(default_factory=list)
    blocks: list[TextBlock] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    export_status: str = "idle"
    export_error: str | None = None
    export_file: str | None = None
    export_name: str | None = None
    revision: int = 1
    created: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "source_name": self.source_name,
            "status": self.status,
            "stage": self.stage,
            "page_index": self.page_index,
            "page_count": self.page_count,
            "force_ocr": self.force_ocr,
            "error": self.error,
            "pages": [p.to_dict() for p in self.pages],
            "blocks": [b.to_dict() for b in self.blocks],
            "issues": [i.to_dict() for i in self.issues],
            "log": self.log[-40:],
            "export_status": self.export_status,
            "export_error": self.export_error,
            "export_file": self.export_file,
            "export_name": self.export_name,
            "revision": self.revision,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        job = cls(
            id=data["id"],
            filename=data["filename"],
            source_name=data.get("source_name", data["filename"]),
            status=data.get("status", "queued"),
            stage=data.get("stage", ""),
            page_index=data.get("page_index", 0),
            page_count=data.get("page_count", 0),
            force_ocr=data.get("force_ocr", False),
            error=data.get("error"),
            pages=[PageState.from_dict(p) for p in data.get("pages", [])],
            blocks=[TextBlock.from_dict(b) for b in data.get("blocks", [])],
            issues=[Issue.from_dict(i) for i in data.get("issues", [])],
            log=list(data.get("log", [])),
            export_status=data.get("export_status", "idle"),
            export_error=data.get("export_error"),
            export_file=data.get("export_file"),
            export_name=data.get("export_name"),
            revision=data.get("revision", 1),
            created=data.get("created", ""),
        )
        return job


def box_union(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def clip_box(box: list[float], width: float, height: float) -> list[float]:
    x0, y0, x1, y1 = box
    return [
        max(0.0, min(width, x0)),
        max(0.0, min(height, y0)),
        max(0.0, min(width, x1)),
        max(0.0, min(height, y1)),
    ]
